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
