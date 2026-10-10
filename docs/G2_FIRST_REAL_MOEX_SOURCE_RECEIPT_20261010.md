# G2: First Genuine MOEX Raw D1 Source Receipt — 2026-10-10

## Actual source evidence (not synthetic)

Owner: ChatGPT independent engineering line. GitHub Actions run https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38060438310 on code commit ef449fc6d91038e98cd104482103ce281d6db3ac successfully used existing MoexIssClient._request behind ProcessUpstreamControlPlane, without bypassing the mandatory MOEX public ISS request governor.

Official MOEX endpoint: /engines/stock/markets/shares/boards/TQBR/securities/SBER/candles.json ; D1 interval=24, window 2026-09-26 through 2026-10-09, exact SBER SECID, bounded first page, selected open/close/high/low/value/volume/begin/end columns. One HTTP 200 returned **14 actual D1 candle rows**. Original response body: **1,638 bytes**. SHA256:

0e18abd9de04c3e9e3656629bd2b0b13286d68ee29846fca1ac822443e5b8b98

Source receipt window (UTC): 2026-10-10T14:39:49.131132Z to 2026-10-10T14:39:49.985637Z; provider HTTP Date header = 2026-10-10T14:39:49Z. Last completed returned candle ends 2026-10-09T20:59:59Z. No forming/incomplete D1 row was admitted by the probe.

GitHub workflow artifact 11672843330 held two original files: moex_sber_d1_raw_response.json and moex_sber_d1_receipt.json. ChatGPT downloaded the artifact, independently compared source-byte SHA, response size and row count, and uploaded it to durable project Google Drive under:

https://drive.google.com/drive/folders/1TE_RfJpoOrpg2lSw45G3kP21CpwUlgOs

Individual archive: https://drive.google.com/file/d/1dkW1hJwVrGAWnk4DYM3jlLqKMYbhN7TV/view

The copy was re-downloaded from Drive and verified again: exact body SHA 0e18abd9de04c3e9e3656629bd2b0b13286d68ee29846fca1ac822443e5b8b98; 1,638 bytes, 14 candles, receipt date matches.

## Implementation and negative tests

New script scripts/g2_moex_raw_capture_probe.py stores **unmodified provider response bytes**, a JSON manifest with observed_at_start and observed_at_end taken by the runtime wall clock before/after the real governed request, original SHA, source endpoint/parameters and HTTP Date header. Exact output files use exclusive write to fresh scratch only; not an agent/tool or production scheduler. tests/test_g2_moex_raw_capture_probe.py covers forged/naive clocks, incomplete/missing bars, formed-after-cutoff rows, invalid session ordering, duplicates, pagination and overwrites. The added tests are synthetic negative inputs, separate from the real network capture.

**This is NOT a forecast.** is_real_forecast=false, strict_pit_proven=false, independent_clock_attestation=false. HTTP Date corroborates the timestamp second but is not an independently signed/anchored time authority. The probe saved a single genuine SBER D1 response, not six-market snapshots or 5/10/20 forecast outcomes. It did not modify or even open HOME/production data, does not create ForecastRecord, does not use untouched 2023–2024 holdout and does not test predictive quality.

The separate canonical-staging bridge PR #143 still has a SQLite sidecar and canonical DuckDB journal in **two transactions**: recoverable under staging tests, NOT a globally atomic production commit.

## Next proof gate

A governed, bounded source snapshot needs all six resolved instruments and required D1/H1/M15 data at actual receipt times, original response hashes, source observed_at and event finality, exact SECID/calendar lineage and snapshot identity. Only after an as-of verified input snapshot is captured may a forecast be frozen with that receipt; the later real outcome uses separately observed completed future sessions. Check external time/durability and single-canonical-journal atomic outbox before HOME activation. User permission is required for merge/deploy/production DB changes, scheduling or irreversible actions. Codex-colleague independently maintains TREND/BALANCE; no dependency on that work for this source evidence.
