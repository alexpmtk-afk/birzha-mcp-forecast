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

## Cross-contract futures rollover — draft research, not installed

For requests concerning **different futures SECIDs** (RIH6→RIM6, SiH6→SiM6, etc.), distinguish historical contract rollover from session concatenation within one SECID. Read [the proposed continuous-futures policy](docs/CONTINUOUS_FUTURES_POLICY_RESEARCH_20261029.md) and the [proposed skill](skills/continuous-futures/SKILL.md) as **research instructions only**. Neither document is installed/approved; they do not override the existing accepted session-runbook rules or authorize production changes.

Do not conflate **RI** (RTS futures, multiple exact contracts) and **RTSI** (cash index). No adjusted continuous candle can be stored as raw history, and a D1-only readback is not PIT/Forecast admission. Separate approval is required before merging or installing a shared rollover engine.
