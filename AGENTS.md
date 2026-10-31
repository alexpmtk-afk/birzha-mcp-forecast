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

## RI canonical workbook: README_STATUS retired (owner decision, 31.10.2026)

For the **RI futures** workbook `MOEX_HISTDATA_RI_MCP_CANONICAL`
(`1PuPv7Nv_Dpj-KTECPHNHecj34HrR9K5y53OwRcu7RVY`), the
`README_STATUS` tab (former sheetId `2101800019`) was intentionally
**deleted** on 31.10.2026. It has no required runtime role and must **not**
be created, recreated, copied from a backup, required by schema validation,
or silently restored by a chat, local Codex, Apps Script or MCP workflow.
Treat its absence as the **expected** RI schema, not as missing data.

This later, explicit owner decision **supersedes** older RI tab inventories
and generic `README_STATUS` requirements in the Drive originals
`docs/DATA_TABLE_FORMAT.md`, `docs/DATA_UPDATE_POLICY.md`, research plans,
backup books and handoff messages. When those sources differ, never repair
the RI book to match the old list: report the stale reference and update the
original instruction instead. Historical copies remain evidence only.
For the proposed future unified template, do not introduce a
`README_STATUS` tab by default. **Do not delete or change tabs in the other
five canonical books under this RI-specific decision.**

Use existing `SYNC_STATUS` for operational synchronization evidence,
`QUALITY_AUDIT` for quality checks, and project documentation for
methods and policy. Do not invent new status sheets or migrate historical
notes automatically. Any tool needing `README_STATUS` should fail closed,
name the exact caller/dependency, and request a separately authorized design
change; it must never perform implicit `addSheet`/restore/replacement.
Preserve all existing candle, indicator and service sheets, accepted OHLCV
and existing formulas. This change does not authorize HOME deployment,
database writes, or changes to RI/RTSI routing.

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

## Cross-contract futures rollover — source code rules (T−1 v3)

For futures root (RI, Si, BR, GOLD and other expressly supported MOEX roots) use **the single installed-in-GitHub-code** selector `src/birzha/providers/continuous_futures.py` through `MoexHistoricalFutureResolver`. It selects by prior completed historical `TRADEDATE` volume T−1 and exact officially documented expiration, rejects spreads and missing data, and marks its result as `MOEX_ISS_HISTORY_T_MINUS_1_RECONSTRUCTED`. **Do not** reactivate the previous same-day `VALUE/OI/VOLUME` resolver or re-use `ROLLING_HISTORY_V2_PREWARM` verification markers; source policy and verification generation are `MOEX_T_MINUS_1_VOLUME_EXPIRY_V1` and `ROLLING_HISTORY_V3_T_MINUS_1_EXPIRY`.

Distinguish three different operations: (1) join successive trading **sessions of one exact SECID** unchanged (`history.stored_series`), (2) **choose active exact contract** by a versioned T−1 ledger, and (3) create derived additive/forward/ratio **continuous OHLCV** from those verified RAW contracts. Only (1) and the historical selector part of (2) are wired into main code as of 29.10.2026. An ordinary `market.candles(root, from, till)` returns ONE SECID, **not** an adjusted continuous multi-contract series. Never claim (3) or strict PIT `available_at` is operational on HOME without separate evidence and source SHA readback.

Read [the research and acceptance policy](docs/CONTINUOUS_FUTURES_POLICY_RESEARCH_20261029.md), [the source runtime contract](docs/FUTURES_RUNTIME_RESOLVER_STATUS_20261029.md), and [the GitHub agent skill](.agents/skills/birzha-continuous-futures/SKILL.md). GitHub source availability **does not mean** the skill or binary is installed on HOME. Preserve RAW, original per-SECID volume, OHLCV, gaps and historical confidence; forbid exact volume-profile/Delta reconstruction from M1 OHLCV alone. **RI** (RTS futures) must not be confused with **RTSI** (cash index).

Local rollout remains tracked in [issue #277](https://github.com/alexpmtk-afk/birzha-mcp-forecast/issues/277); do not call it complete based only on CI and main merge.

## Единое хранилище данных — решение владельца 31.10.2026

Основной архив каждого инструмента — его существующая Google Таблица. Свечи и расчётные показатели принадлежат инструменту и доступны всем прогнозным моделям; не создавать архивы по номерам моделей. Локальная база может служить источником/кэшем наблюдений для сверки, но отсутствие строк в ней не разрешает повторно скачивать заполненный Google-архив. Ранее допущенный оперативный источник Forecast и исследовательская таблица не взаимозаменяемы; обратный импорт в production и допуск прогноза остаются отдельными решениями.

Перед архивной задачей читать docs/INSTRUMENT_ARCHIVE_MCP_20261031.md. Реальные интерфейсы: archive.contract, archive.contract_plan, archive.collect, archive.update. Действующий общий загрузчик и M1-to-M15 агрегатор переиспользуются. Расчёт показателей выбирается из зарегистрированных наборов; новые требования моделей добавляются в общий набор, а не в параллельные книги.

Команды считаются установленными только после отдельного подтверждения source SHA и tools/list работающего сервера. Наличие этого раздела или кода в ветке не является HOME-приёмкой. При неизвестной полноте существующего окна не скачивать его заново и не выдумывать нулевые интервалы. При конфликте адресов RI/RTSI блокировать запись до установления верного адреса индекса.
