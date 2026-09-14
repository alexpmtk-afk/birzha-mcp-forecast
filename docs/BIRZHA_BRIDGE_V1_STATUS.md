# Birzha Google Drive / Sheets Bridge v1 status

Release target: `protocol_version=1`, `bridge_release=1.0.0`.

## Code state

**CODE COMPLETE. No Marketplaces runtime dependency remains in the Birzha Bridge client.**

The M25 client implements:
- project-pinned `project_id=birzha`;
- strict `bridge_release=1.0.0` deep-health validation;
- fixed Drive root validation;
- stable idempotency keys for every mutation;
- instrument-to-archive target mapping;
- `sheet_ensure`;
- non-destructive `sheet_stage_begin` replay;
- bounded chunk writes;
- stage row/column/digest verification;
- atomic `sheet_commit`;
- independent post-commit `sheet_inspect` parity verification;
- `sheet_abort` on pre-commit failure;
- YDB remains primary; Google Sheets remains the mandatory audit mirror when configured as required;
- autonomous worker fail-closes if the mandatory mirror is absent or parity does not pass.

The old `birzha_*` Marketplaces Apps Script addon is not part of this release path.

## Runtime activation order

1. Authorize/deploy the dedicated Birzha Apps Script Bridge v1.0.0.
2. Store the Birzha-only secret in the Birzha project Lockbox under `gdrive_bridge_v1_secret`.
3. Run live Birzha health/root/security acceptance.
4. Run staged/chunked Sheets acceptance including replay, abort, commit and inspect.
5. Publish the worker image from the tested M25 source SHA.
6. Bind only the Birzha Bridge URL/root/secret to that worker revision.
7. Enable `BIRZHA_MARKET_MIRROR_REQUIRED=true`.
8. Run one controlled D1 mirror sync and prove YDB↔Sheets row/date/digest parity.
9. Only after PASS may the retired shared Marketplaces Apps Script addon be removed.

Interactive Google owner authorization is an external runtime permission gate, not unfinished code.
