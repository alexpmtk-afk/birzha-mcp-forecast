# Shared Google Drive bridge — BIRZHA addon

BIRZHA reuses the already deployed Marketplaces Apps Script Web App. Do **not** deploy a second Web App and do **not** create a second Apps Script secret.

## Existing shared bridge

- Web App: the already deployed Marketplaces MCP Drive bridge.
- Authentication: existing `MCP_DRIVE_BRIDGE_SECRET` in Script Properties.
- Existing Marketplace actions stay unchanged.

## Addon

Add `ops/apps_script/birzha_market_mirror_bridge.gs` as a second `.gs` file in the same Apps Script project.

The addon exports only `handleBirzhaAction_(action, body)` plus `birzha*` helpers. It does not define `doGet`, `doPost`, or a secret.

## One dispatcher hook

In the existing authenticated `doPost(e)`, after Marketplace action handlers and immediately before the existing `unknown_action` response, add:

```javascript
const birzhaResult = handleBirzhaAction_(action, body);
if (birzhaResult !== null) return json_(birzhaResult);
```

The existing secret comparison must remain **before** this hook. Therefore both Marketplace and BIRZHA use the same authenticated Web App and the same secret, while BIRZHA has its own hard-scoped Drive root and namespaced actions.

## BIRZHA actions

- `birzha_health`
- `birzha_ensure_archive`
- `birzha_replace_snapshot`
- `birzha_summary`

All Birzha Sheet operations are fenced to `Биржа → Архив рыночных данных` and its declared instrument folders. BR is pinned to the already existing spreadsheet `BIRZHA — BR — Market Data Mirror`; it must not create a duplicate.
