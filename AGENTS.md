# AGENTS.md — autonomous market-data work

## Start here

For any candle download, storage, continuation or session-concatenation task,
first read docs/CHAT_DATA_OPERATING_RUNBOOK.md and choose the route supported by
the tools actually available in this chat. Then read
docs/STORED_SESSION_SERIES_20261015.md for candle semantics.
If Birzha MCP is connected, inspect tools/list and call history.session_rules.
If it is absent, do not require a direct HOME connection or retry missing tools:
complete accessible GitHub/Drive work and prepare one exact execution handoff
using .github/ISSUE_TEMPLATE/market-data-execution.md. Search existing issues
first; the project map remains the single operational queue. Publishing an issue
does not execute it: no automatic GitHub-to-HOME consumer is established by this
runbook. Only an authorised executor with the existing local control channel may
report actual HOME results. Do not invent access or put secrets in GitHub.
GitHub main, Draft PRs, isolated execution and installed HOME are different states;
record the actual source SHA. The owner's current task and scope take precedence
over generic historical notes. This route selection qualifies any older blanket
instruction to call history.*; candle and authorisation rules remain mandatory.

## Execute ordinary work independently

Within an authorised task, choose bounded batches, inspect existing coverage,
resume missing portions, run relevant checks and record results without asking
permission for each symbol, timeframe, page, retry or completed batch. Read the
code/runbook and existing reports before asking another chat or the owner.
Do not delegate routine investigation back to the owner. After two or three
failed attempts of the same approach, report the concrete blocker and exact
manual action if one is needed; do not consume limits repeating the same attempt.

## Forecast-method decisions and forbidden reuse

For any future Forecast/Market State/Control/Route or method comparison, consult [the canonical forecast-method decisions](docs/FORECAST_METHOD_DECISIONS_20261016.md) and the read-only module `src/birzha/application/method_registry.py` **before** proposing a candidate.

- OWNER CORRECTION 2026-10-17: Forecast v0.4 and v0.6 are ACTIVE RESEARCH CANDIDATES again. Continue frozen-version paired tests, never refit on known 2025-26 outcomes, and never label either as approved for production. v0.4 lives in main; v0.6 evidence-aware code lives in codex/g2-baseline-decision-contract-20261010, not automatically installed on HOME.
- `CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1` is **prohibited as new directional/Control/Route evidence**. Never quietly replace it by a trades-based profile or relabel historical results.
- SMA/returns/ER/SMA-TR14: existing descriptive context is not an independently accepted directional edge. Wilder ATR must have its own explicit feature/formula version.
- PR #200 Market State and PR #254 structure/Wilder/median-volume are research work, not independently accepted predictions. Do not add a second competing classifier or promote their CI success to predictive accuracy.
- Historic Forecast/Outcome journals are immutable/append-only. No extra DB tables are necessary for retired methods; the registry is versioned source+documentation only. Installed HOME is not changed by draft updates.
- Existing `forecast.create` and method diagnostics are still callable in legacy runtime until a separately authorised and tested cutover; never claim otherwise. A read-only `methods.registry` endpoint becomes available only after explicitly authorised deployment.

## Canonical mechanics

- One exact SECID and one timeframe per stored series. Futures roots require
  historical contract resolution; never join prices of different contracts.
- Reuse history.sync and existing governor/resumable store operations for source
  loading. Reuse history.stored_series/load_exact/store.read for stored reading.
- The persistence key is SECID/timeframe/begin. Existing source version policy
  must remain explicit; identical-key count is not proof of payload agreement.
- Preserve raw OHLCV, times, NULL, completeness and provenance. Do not remove
  overnight price gaps or create bars for closed sessions/missing observations.
- Do not join an incomplete interval to a later session to make it complete.
  M1-to-M15 construction is a separate source/quality contract.
- A chronological row sequence is not independently attested session coverage,
  historical first receipt or a Forecast admission gate.
- Absence is UNKNOWN/UNAVAILABLE, not zero. Preserve prior Forecast/Outcome.

## Storage destinations

Canonical source history is the installed backend; isolated research archives
remain labelled research until separately admitted. Google Sheets are the six
existing canonical books in My Drive/Birzha/Archive market data, not new books
per chat. Read instruction 09 at its existing Drive address and use exact IDs
returned by history.session_rules. Sheet roles: D1/H1/M15 = candles; SESSIONS =
calendar evidence; VERIFIED_RANGES = proven ranges only; SYNC_STATUS = actual
sync evidence. Record quality/provenance separately. Never clear a book or
replace accepted historical prices merely to match a report.

If Google export is requested, inspect installed tools/configuration and explicit
access. Do not claim that draft #244 is installed. Do not manufacture access,
create duplicate books or bypass the authorised writer when mirroring is absent.

## Escalate only real blockers

Ask only for missing access, an unresolved contradiction that changes prices,
calendar identity or admission, a decision outside the task, or separately
controlled actions. Give evidence, attempted checks and the minimum next step.
Merge, HOME installation/restart, production-data mutation, secret access and
paid sources require explicit owner authorisation. Existing authorisation in
the current task persists; do not ask again for the same approved action.
Do not reboot the computer or interfere with unrelated programs.

## Close with evidence

Report exact code/source/dataset versions, counts, exclusions and failures,
relevant test results, actual storage addresses and next unresolved step.
Update the existing project map/instructions in place; never call development,
CI or isolated MCP checks HOME acceptance or forecasting accuracy.
