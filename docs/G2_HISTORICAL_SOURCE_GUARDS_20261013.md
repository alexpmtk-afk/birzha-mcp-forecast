# Independent historical Development guard corrections (2026-10-13)

Scope: continue Q-120–Q-123 retrospective **2021–2022** research, without
changing the existing Forecast V1 or permitting release/trading.

## Confirmed archive identity (read-only external verification)

Found the preserved non-production 2026-09-18 HOME transfer archive
`birzha/data/birzha_history.duckdb`, size **14,954,496 bytes**.
SHA-256 verified against the original transfer MANIFEST:
`8dcd4bbf560d1123575403b0c63d0c21891c4d152abbadc7a3bf9434ccbe68e1`.

Also located the matching `d1_reconstructed_warmup_audit_20261008_v2.json`
under the Codex coordination project's archival scratch directory.
Its SHA-256 is
`80478c30c116f43f03ed9b992c353096801fa18144cd4cc908355f1d8042a7a0`.
Both `source_sha256_before` and `source_sha256_after` match the archive.
Independent JSON validation confirms 133 unique expected-date lists (BR 79,
Si 27, GOLD 27), each with 20 sorted distinct pre-active dates, and the
`evidence_manifest_sha256` recomputes to
`15a2dd0a81547be5033213be16454999f051706f670fe40e618b6860fc3f717b`.
This **does not** independently certify the original MOEX data or calendar.

## Three concrete fixes (not assigned canonical Q IDs)

- **Chronology:** previously a completed D1 candle could be
  accepted with an end before its begin (or a span >24h), as long as both
  calendar dates looked plausible. Reject reversed, zero-duration,
  unparseable and excessive intervals before scoring an outcome.
- **Protected Development:** previously the historical evaluator
  and extractor queried *all* archived root calendar sessions before
  filtering in Python. They now bind 2021-01-01..the Development cutoff
  in SQL, including the relevant verified-range metadata. Dates in
  reserved 2023–2024 are not enumerated by these scoped queries.
- **Output preflight:** a pre-existing directory or symlink at a
  later output target could permit earlier result files to be written
  before the run failed. All three target paths are now checked before
  writing; incompatible files, directories and symlinks fail closed.

New negative tests cover all three cases; existing synthetic 6-market
fixtures and SHA-determinism tests remain in place.

## Explicit limits

The archive + V2 pair is available and verified by hashes. However,
this ChatGPT execution container does not have DuckDB installed, and
package installation was unavailable due to DNS/network restrictions.
Therefore **no real 2021–2022 outcome scoring was performed here**.
GitHub CI runs source-code and synthetic test fixtures only. Do not
publish forecast accuracy numbers from these checks.

These are reconstructed non-PIT results: historical first-receipt/vintage
is unproven. Do not unlock 2023–2024 holdout, change `main` or HOME,
merge, deploy, issue a LIVE Forecast or switch release gates. All 12
release requirements remain open until separately evidenced.

## Next verified acceptance boundary

1. Confirm exact-head CI (crossline tests, full suite, security, preflight).
2. In an isolated environment with DuckDB available, run the offline
   diagnostic read-only against a disposable byte-identical archival copy
   and the matching V2 JSON.
3. Verify pre/post SHA-256, counts by market and horizon, excluded rollover
   reasons, and deterministic output manifest.
4. Only then perform separately labeled retrospective Forecast V1
   evaluation; never promote synthetic diagnostic hit-rates to V1 accuracy.
