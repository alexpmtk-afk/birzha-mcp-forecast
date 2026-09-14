# Birzha Forecast MCP → Shared Google Drive Bridge v1

Status: integration guide based on branch `m25-market-data-mirror`. Production cutover is forbidden until Birzha has a dedicated Bridge v1 deployment and passes the common + Sheets acceptance matrix.

Source of truth for the shared bridge implementation:
`alexpmtk-afk/mcp-yandex-cloud-infra/shared/google-drive-bridge/`

## Required architecture

```text
Yandex Timer / private Birzha Worker
→ YDB orchestration
→ project_id=birzha
→ dedicated Birzha Apps Script deployment
→ dedicated Birzha bridge secret
→ dedicated Birzha Lockbox binding
→ fixed root: Архив рыночных данных
→ Google Drive / Google Sheets
```

After cutover Birzha MUST NOT use:
- Marketplaces Apps Script deployment URL;
- Marketplaces shared secret;
- Marketplaces Lockbox credential;
- Marketplaces Drive root;
- the old `birzha_*` addon embedded in the Marketplaces deployment.

## Client protocol rules

Every protected request includes:
- `project_id=birzha`
- unique `request_id`
- action payload

Every mutation also includes a stable `idempotency_key`; retry of the same logical mutation reuses it.

Before mirror writes, deep health must prove:
- `protocol_version=1`
- `project_id=birzha`
- expected Birzha `root_id`
- `google_sheets_chunked=true`
- `fixed_root_file_id_guard=true`

Mismatch means fail closed.

## Responsibilities that remain inside Birzha

Do not move into the shared bridge:
- MOEX/ALGOPACK access;
- YDB Data Foundation;
- SECID/contract selection;
- D1/H1/M15 policy;
- `D1 / SESSIONS / VERIFIED_RANGES / SYNC_STATUS` domain meaning;
- instrument/archive mapping including pre-bound BR;
- Forecast/Snapshot/Outcome;
- M23 validation;
- YDB orchestration/job state;
- project parity rules and decision of what/when to mirror.

## Google Sheets write contract

Birzha uses the Bridge v1 Sheets extension:
1. ensure/find approved spreadsheet under the fixed root;
2. begin staged update;
3. write bounded chunks/ranges;
4. read back and verify row count/digest and approved first/last-date parity;
5. commit only after verification;
6. abort/rollback staged state on failure.

Do not directly destructively overwrite canonical Sheets before staging verification.

## Security and concurrency

- every `file_id`/spreadsheet ID operation must prove ancestry under the Birzha fixed root;
- Drive shortcuts are forbidden unless a future bridge protocol explicitly supports safe double validation;
- secret must never be logged or returned to ChatGPT;
- no global whole-request Apps Script `ScriptLock`;
- resource-level serialization stays in YDB/client orchestration.

## Expected migration points

After Bridge v1 is accepted, adapt primarily:
- `src/birzha/storage/google_sheets_bridge.py`
- `src/birzha/application/market_mirror_sync.py`
- `src/birzha/worker.py`
- `src/birzha/config.py`
- Terraform M25/M24 bridge bindings in `mcp-yandex-cloud-infra`
- M25 tests/acceptance

The current `ops/apps_script/birzha_market_mirror_bridge.gs` shared-addon route becomes legacy after successful cutover.

## Cutover gate

Production switching is allowed only after PASS for:
1. dedicated Birzha deployment and dedicated secret/Lockbox;
2. correct project/root/protocol health;
3. wrong secret rejection;
4. wrong `project_id` rejection;
5. foreign-root file/spreadsheet rejection;
6. shortcut escape rejection;
7. chunked staged Sheets write;
8. read-back parity/digest;
9. safe idempotency replay;
10. mid-write abort/rollback;
11. concurrent operation with Marketplaces without shared locking.
