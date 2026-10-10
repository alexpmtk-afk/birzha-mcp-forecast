# G2 — source-bound prospective first issuance gate (10 October 2026)

**Owner:** ChatGPT prospective-forecast line. Stacked on release-audit PR #153; no changes to colleague's TREND/BALANCE or production-feature files. Research-only; no production/HOME/merge/deploy.

## Independently demonstrated risk

The default `MarketSnapshotService.build()` in nonhistorical mode normally uses the latest completed **candle end** as `snapshot.as_of`. `ForecastService.build()` then uses that value as `ForecastRecord.created_at_t0`. Candle-end event time is NOT verified first observation or forecast **issuance time**. This can backdate an apparently new "prospective" prediction and can carry a timezone-naive string.

The isolated original-MOEX six-market receipt script #146 had already used an after-receipt UTC clock; however issuance/T0 rules were embedded in one script rather than reusable, mandatory guard. General live ForecastService is **not** production-authorized by this change.

## New executable guard

- `src/birzha/application/prospective_issuance.py` provides `prepare_source_bound_forecast(snapshot, evidence, *, clock, parameters)`, which **cannot itself fetch data, append to journal or write files**. It requires a source snapshot reconstructed from independently verified raw original pages, `MOEX_ISS_GOVERNED_OBSERVED_INPUT`, exact source payload bytes (not reserialized), a single matching `source_raw_bundle_sha256` marker, quality-contract D1/H1/M15 with 50+ verified finished observations each and matching source last-completed event, exact SECID, missing flow/profile explicitly flagged and a real timezone-aware receipt.
- Requires **completed-bar end ≤ original source observed time ≤ original snapshot preparation T0 ≤ fresh actual issuance UTC T0**. Source-to-issuance age ≤300 s. The issued snapshot is a new immutable object with fresh UTC time; `snapshot_id`, `forecast_id`, and `ForecastRecord.created_at_t0` are calculated consistently from it. The earlier object and raw bytes are unchanged.
- `capture_prepared_in_staging` refuses any non-`CanonicalProspectiveStagingBridge` writer, then delegates original receipt-first + canonical DuckDB journaling to the already existing validated staging bridge.
- `scripts/g2_freeze_real_forecast_from_source_receipts.py` now calls this common guard **after** independently validating original raw MOEX response manifests/OHLC and **before** opening/writing each staging journal. No model parameters, horizon strategy, historical dataset, data source or calibration is silently changed.
- 12 fully synthetic positive/negative tests in `tests/test_g2_source_bound_issuance.py`: fresh UTC T0, source/event clock ordering, stale observation, raw payload SHA tampering, missing/duplicate anchor, exact completed-bar mismatch, naïve clocks, insufficient/wrong quality windows, unreceipted flow/profile, reconstructed source, and inadmissible non-staging writer.
- Existing #146 frozen records remain **immutable**. The new issuance logic is for **subsequent** records; it never overwrites frozen 10 October artifacts or rewrites T0 after the fact. Source bytes must be independently checked upstream; a warning hash marker alone **cannot prove provider authentication or feature correctness**.

## Acceptance and remaining BLOCKERS

New source-bound staging code may be declared a **technical PASS** only with focused and full pytest on **all three pinned release lines** (#152 revision, #151 production features, #147 regime research) and a successful genuine-source independent check. Synthetic green tests alone do NOT close market provenance.

The **production live ForecastService path remains BLOCKED** until every live caller is actually wired to a trustworthy source replay/receipt and authenticated exact-SECID contract. Independent exchange session calendar, signed provider time, historical base vintage, production atomic outbox, model skill, HOME health, and two user permissions remain open. Do not change the release machine manifest to `release_authorized=true` based on this new PR.

The protected HOME Windows runner allowed checkout but blocked PowerShell code execution by security policy. Do not bypass; see separate private bridge diagnostic PR #104. No production actions, calendars, secrets or untouched 2023–2024 holdout used in this development step.

## Source-bound usage (isolated)

```python
# The source assembler already checked exact raw MOEX response bodies and OHLC.
snapshot, original_bundle, source_observed, max_completed, *_ = assemble_snapshot(...)
evidence = CaptureEvidence(source_payload=original_bundle,
    source_observed_at=source_observed,
    latest_completed_event_end=max_completed,
    contract_version=snapshot.contract_version)
prepared = prepare_source_bound_forecast(snapshot, evidence, clock=utc_clock)
# Explicit disposable staging journals/bridge only, never arbitrary production:
receipt = capture_prepared_in_staging(prepared, isolated_canonical_bridge)
```

**Readiness:** implement and test source-bound issuance and staging pilot first; allow actual-source provenance only with GitHub Actions/network source evidence. There is **no implied production integration**.
