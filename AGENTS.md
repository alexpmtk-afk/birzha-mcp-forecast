# AGENTS.md — autonomous market-data work

## Start here

For any candle download, storage, continuation or session-concatenation task,
read docs/STORED_SESSION_SERIES_20261015.md and call history.session_rules on the
connected MCP. Use tools/list to check installed capabilities. GitHub main,
Draft PRs and installed HOME are different states; record the actual source SHA.
The owner's current task and scope take precedence over generic historical notes.

## Execute ordinary work independently

Within an authorised task, choose bounded batches, inspect existing coverage,
resume missing portions, run relevant checks and record results without asking
permission for each symbol, timeframe, page, retry or completed batch. Read the
code/runbook and existing reports before asking another chat or the owner.
Do not delegate routine investigation back to the owner. After two or three
failed attempts of the same approach, report the concrete blocker and exact
manual action if one is needed; do not consume limits repeating the same attempt.

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
