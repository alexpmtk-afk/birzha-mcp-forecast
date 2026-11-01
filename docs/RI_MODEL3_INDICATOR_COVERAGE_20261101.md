# RI indicator coverage — observed state and safe MCP acceptance (2026-11-01)

## Scope and evidence
- Canonical RI workbook: `1PuPv7Nv_Dpj-KTECPHNHecj34HrR9K5y53OwRcu7RVY`.
- Live D1/H1 candle tabs: 18 existing columns. Live M15: 19 columns including `source_candles`. Do not alter or expand.
- Live RI `D1_INDICATORS`, `H1_INDICATORS`, `M15_INDICATORS`: 95 ordered columns. This is *not* the 119-column current `model3_features.COLUMNS` layout. The removed 24 names are internal/legacy/continuous, not missing market observations.
- The 2025 RI source candles have been appended, but the currently observed indicator rows begin in 2026; check ALL rows by exact `source_record_key`, not merely first date or grid row counts. The prior journal lists 311 D1, 5,220 H1 and 12,592 M15 candle rows for 2025, total 18,123, pending independent exact-key indicator coverage.
- `google_first_archive.collect` currently preserves existing indicators and does **not** recompute them. `Model3ArchiveUpdater` is a separate legacy path with a local backup requirement, old positional candle indices and incompatible schema. It must NOT be attached to Google-first 18085 as an automatic writer.

## What this change does
- Adds exact 95-column indicator header contract and lossless, *in-memory* projection of calculated rows into an already approved destination layout.
- Adds a strict, read-only existing-candle versus indicator source-key comparison. Detects missing indicator rows, duplicate identities, unknown layouts and orphan indicators; never calls MOEX, writes Google, or changes Forecast.
- Exposes `archive.model3_coverage(timeframe='D1'|'H1'|'M15')` on the **separate** Google-first MCP runtime. One timeframe per call to limit memory pressure. The return is record coverage only, NEVER PIT attestation, formula correctness or Forecast readiness.
- Does not activate the actual M3 indicator writer, backfill, sole-writer flag or schedule. A future cloud-only, idempotent/receipt-backed feature writer requires separate engineering and tests before installation.

## Safe acceptance after explicit authorised installation
1. Confirm archive MCP 18085 serves the exact reviewed SHA and `archive.status.runtime=GOOGLE_FIRST`. Check the tools list includes `archive.model3_coverage`. Do not confuse it with the Forecast 18082 or read-only auditor 18083.
2. Keep `sole_writer=false` and disable schedule. Invoke `archive.model3_coverage` separately on D1, H1, M15, with the service account already authorised for the pinned Google book. No other destination.
3. Compare returned `candle_records`, `indicator_records`, `missing_indicator_records`, `orphan_indicator_records` and `indicator_schema` with actual Google keys. Expect `COMPACT_RI_95`. A non-zero missing count is an **open calculation gap**, not a missing MOEX source bar.
4. Require independent formula/volume/PIT/window-validation tests before enabling any M3 writer. Preserve the current 2026 research rows, historical OHLCV and every 95-column header exactly; do not create new tabs.
5. Record actual installed SHA and full readback evidence; do not report PASS based on GitHub CI alone.

## Not yet implemented / not yet proven
- Writing or recalculating historical indicators in Google-first.
- Independent PIT availability, exact Delta or exact price-by-price volume profile from M1 alone.
- Complete M3 signal or OOS validation; the research sheet itself is DESIGN_ONLY.
- Installed HOME18085 version from this GitHub branch; this ChatGPT connection only has an eight-tool read-only HOME audit interface and cannot deploy/restart Windows processes.

This file is an evidence ledger and *not* permission to merge, deploy, or change production data.


## Isolated bounded backfill implementation (PR #298; not installed)

The same Draft PR now provides a **manual** Google-first research backfill path:
\`archive.model3_backfill(timeframe, from_date, till_date, max_rows=50, dry_run=true, owner_confirmed=false)\`.

The existing \`model3_features.research_features\` calculates OHLCV-derived research
metrics. The new planner projects its 119-column intermediate result into the
**observed exact ordered 95-column Google indicator schema**, preserving 2026
rows. The source is read from the pinned RI D1/H1/M15 sheets. The planner
uses prior history and resets state per exact contract, limits each window to
31 civil days and at most 50 new records, and inserts one contiguous chronological
gap instead of appending old 2025 rows behind 2026 rows.

A real insert requires all of the following:
- Deployed **separate** archive runtime 18085 at the reviewed SHA, with
  \`sole_writer=true\` enabled only under explicit owner authorisation;
- \`owner_confirmed=true\` **on the call**, \`dry_run=false\`, and a completed
  historical time window;
- Identical source/indicator preflight reads, sorted existing indicator keys,
  no duplicate/orphan source identity, and no formulas in existing indicator cells;
- One Google Sheets atomic \`insertDimension\` + \`updateCells\` request (maximum
  50 rows), followed by a complete readback that proves all old rows unchanged.
  A lost result is never automatically retried; inspect keys first. Successful
  data insertion with failed status update is explicitly \`READBACK_PASS_STATUS_UNCONFIRMED\`.

All new features are labelled \`RECONSTRUCTED_RESEARCH_ONLY\`,
\`CALENDAR_UNVERIFIED\`, historical availability \`UNKNOWN\`, and
\`p3_signal_eligible=false\`. This is **not** signal admission.

IMPORTANT: Current backfill only calculates existing OHLCV-derived fields.
It does **not** yet import exact-contract M1 monthly ZIP into per-bar
horizontal volume/delta proxy calculations; those fields remain UNAVAILABLE.
Minute close-sign proxy is not true aggressor Delta. Actual price-at-trade
profiles require a different upstream feed. Existing 2026 feature values are
preserved, not retroactively proven correct after adding 2025 history.

Offline tests cover chronological insert ahead of 2026, idempotent second
call, unknown-response reconciliation, formula/unknown header rejection,
source-key checks, and one native Google batch without clear/delete. Exact
head \`8d1fa88449620eba7777e1f3b5e6fce4749f0ad3\`: 832 tests PASS,
Docker MCP PASS, four Github workflows PASS. These are isolated CI results,
**not** evidence of an installed HOME or a successful real Google write.

No merge, deploy, HOME restart, production batch, or schedule was performed
by this ChatGPT branch. Enable real writes only after a separately controlled
HOME/Google acceptance including actual owner permission and one real
small-batch readback; then iterate 2025 months without creating other sheets
or changing original OHLCV.


## Optional RI M1 evidence for H1/M15

\`archive.model3_backfill(..., include_minute_proxies=true)\` additionally reads
the **existing** monthly \`RI_SECID_YYYY-MM_SOURCE_M1_OHLCV.zip\` from the pinned
Drive folder through the established \`GoogleArchiveTransport.read_m1\` path.
It requires an indexed exact SECID/month and verifies the ZIP body SHA and
contents with the existing monthly source validator. No new ZIP/folder/tab,
minute download from MOEX or local cache is created.

The already-existing \`model3_minutes.minute_groups/profiles\` transforms each
minute to a bounded H1/M15 bucket, with a strict volume conservation check
against the exact canonical candle. It fills approximate POC/VAH/VAL/HVN/LVN,
minute VWAP and signed-minute-close Delta proxy only when actually supported.
It refuses missing/indexless ZIP, missing positive-volume buckets and volume
conflicts rather than replacing absent observations with zero.

The response and cloud \`SYNC_STATUS\` receipt retain exact source
\`secid\`, \`month\`, ZIP \`sha256\` and \`file_id\`. These are **approximations**:
the minute grid spreads one-minute volume evenly over its minute high–low
range and the close-change sign is not the buyer/seller aggressor side.
The model remains RESEARCH_ONLY and \`p3_signal_eligible=false\`.

By default \`include_minute_proxies=false\` and this extra Drive read is not
performed. For a full RI research backfill, perform separate H1 and M15
dry-runs with the option enabled, confirm matching sources/volumes, then
seek explicit authorization for bounded production writes.

The first restored 2025 source files were independently confirmed present by
read-only Drive metadata: RIH5 January/February 2025 have names matching
the expected archive prefix; this does NOT prove every monthly record,
session calendar or source-availability timestamp.

The HOME 18085 installation, sole-writer claim and real Google acceptance
remain unverified from this chat's read-only HOME audit connection.
