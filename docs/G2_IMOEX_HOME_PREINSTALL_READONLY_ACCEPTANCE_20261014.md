# IMOEX 2025–2026: HOME research import — pre-install acceptance (14.10.2026)

## Actual independent result

A separate GitHub-hosted read-only gate passed on the *existing* archived source, without MOEX requests or writing to any HOME database:

- Workflow: https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38474578976
- Git branch: `chatgpt/imoex-home-readonly-acceptance-20261014`
- Full source SHA-256: `e66262c6d809e7a81b2b91d6e1068e60b3dac114b0310c862d05127c36b29a5c`.
- 457 dates, `2025-01-03..2026-10-12`; 4,740 H1 (4,199 strict + 541 authentic shortened H1); 17,369 M15 (16,073 strict + 1,296 observed partial M15).
- Read-only checks: hash of DuckDB and manifest, archive identity, H1/M15 counts, absence of foreign instrument rows, source quality categories, M15 observed-minute cardinality, duplicate keys, overlapping intervals, OHLCV sanity, bounded date coverage.
- Negative test: a tampered research manifest was rejected.
- Only a JSON receipt was exported by CI, not prices or a modified DuckDB.

Google Drive source folder: `Мой диск / Биржа / Архив рыночных данных / MIX_MX - Индекс МосБиржи / ИСТОРИЧЕСКИЕ СВЕЧИ H1 M15 — 2025-2026 — IMOEX`.
Source link: https://drive.google.com/file/d/1wPKdKZmi-KMbA1YmFm33PmMo_vtlkIRF/view
Canonical mirror: https://docs.google.com/spreadsheets/d/1d5czoih68MxGja0MBJbEq-WSLWVFfhMA3iqdkfe4Plg/edit
Mirror H1/M15: 4,740 and 17,369 data rows respectively, already read back in the separate mirror phase.

## NO HOME INSTALL / NO RELEASE

**Status:** `PREINSTALL_READONLY_PASS`; `HOME_ACCESS_BLOCKED`; `PRODUCTION_IMPORT_NOT_AUTHORIZED`.

This CI ran on a hosted Linux GitHub runner, not Windows HOME. It therefore **does not prove** the actual HOME repository SHA, runtime, schema, free disk, permissions, history DB state, backup state, MCP connectivity, data import or availability after restart.

Security/context blocker documented in PR #104 of the private gpt-powershell-bridge: previous Windows HOME diagnostic attempts failed at absent `pwsh` and Windows execution policy. The existing bridge PR gateway must not be used as an unapproved execution-policy bypass. Resolve the HOME access policy and get the user's separate explicit approval for any production database writes, deploy or restart.

**Do not copy the isolated research DuckDB over** `birzha_history.duckdb` or `birzha_state.duckdb`. Production's `historical_candles` contract is distinct from `research_intraday_candles` and `research_intraday_source_observed_exceptions`. Existing `IMOEXResearchPriceView` reads the V2 source read-only with two explicit modes (`strict` default and `source_compatible=True`). Research exceptions must remain quality-labelled; full-session calendar and original first-receipt/PIT remain unattested.

### Authorized path to actual completion

1. Independently approve the HOME diagnostic mechanism and verify HOME repo root, SHA/dirty state, disk, permissions, runtime config and separate `birzha_history.duckdb` and `birzha_state.duckdb` identity **read-only**.
2. Review a proposed versioned HOME research destination under an approved local path. Do not infer it from this note or silently choose an active production DB.
3. Preserve a verified backup and acquire separate user approval before any HOME mutation.
4. Stage *only* the exact pinned research DuckDB in a dedicated, versioned and isolated research directory. Verify SHA post-transfer and the complete preflight again against the staged **copy**, without opening the main history/state DB for writing.
5. Confirm `IMOEXResearchPriceView` read-only mode and retrieval behavior on the actual HOME code and MCP route in isolated/non-trading execution; no auto-coupling to live Forecast V1.
6. Do not mark `HOME_DEPLOYED` or `LIVE_READY` until approved change, tested MCP readback, backup/recovery and actual relevant check gates pass. Preserve `NO_TRADE`.

The 2025–2026 data are historical research reconstructions. The 1,837 shortened/partial source-observed bars are **not** full bars; history does not carry independently verified original observation time, raw M1 or a complete independent session calendar.
