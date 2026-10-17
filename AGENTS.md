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

## G2 strict/reconstructed boundaries and preregistered pilot (coord-20261017-02)

Before touching candidate methods, read [the actual 30-case report](docs/G2_MARKET_STRUCTURE_ACTUAL_30_CASES_20261017.md), [fixed input identity manifest](scripts/g2_frozen_input_identity_20261017.py), and [predeclared future pilot](docs/G2_FIRST_FORECAST_STRUCTURE_PILOT_PREREG_20261017.md). The frozen archive SHA and inputs SHA are independent of the receipt: verify both *before calculating*, then verify receipt separately. `build_reconstructed_market_structure_facts` does NOT admit historical incomplete flags to strict Forecast. `g2_structure_pilot_plan.py` only writes an exclusive **offline plan**, not source capture. No model weights fitting on already-known 30 cases, no merge/deploy, no first-seen claims. Future candidate rule is D1/H1 concordant HH_HL → UP, LH_LL → DOWN, otherwise ABSTAIN; fixed horizons 5/10/20, paired flat/momentum comparison and missing-data reasons. No release or accuracy claim.

## Execute ordinary work independently

Within an authorised task, choose bounded batches, inspect existing coverage,
resume missing portions, run relevant checks and record results without asking
permission for each symbol, timeframe, page, retry or completed batch. Read the
code/runbook and existing reports before asking another chat or the owner.
Do not delegate routine investigation back to the owner. After two or three
failed attempts of the same approach, report the concrete blocker and exact
manual action if one is needed; do not consume limits repeating the same attempt.

## Исследовательские методы и исторические свечи H1 (17.10.2026)

Перед прогнозным шагом проверять [реестр решений #255](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/255) и [доказательную ведомость исполнения 30 контекстов](docs/G2_MARKET_STRUCTURE_ACTUAL_30_CASES_20261017.md). По распоряжению владельца 17.10.2026 v0.4/v0.6 ВОЗВРАЩЕНЫ к новым парным исследованиям (v0.4 в main, v0.6 в codex/g2-baseline-decision-contract-20261010), однако нельзя подгонять формулы по известным результатам или выдавать за принятую продакшн-модель; свечной псевдопрофиль объёмов запрещён как фактор направления. V2 Market Structure и six-regime Market State исследовательские; не представлять тех. тесты как predictive PASS.

**Критический контракт:** `build_market_structure_facts` строг: отклоняет любую незавершённую свечу; отдельно выделенный `build_reconstructed_market_structure_facts` разрешён только для исторического описательного исследования на неизменённом ZIP с исходными false, явным московским часовым поясом и неизвестным first receipt. Он не даёт допуск к Forecast/MCP/trading. Укороченная свеча не равна формирующейся; в старом архиве `completed=false` создано правилом duration, а первоначальная полнота источника неизвестна. Не исправлять метки массово.

Для обсуждения процентов ATR: V2 `atr_pct=100*ATR/Close`, отдельный `atr_ratio=ATR/Close`; исторический V1 обозначал долю под именем `atr_pct`, поэтому нельзя молча смешивать версии. Резервный период 2023–24 по-прежнему закрыт; main/HOME не менять без отдельного разрешения.

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
