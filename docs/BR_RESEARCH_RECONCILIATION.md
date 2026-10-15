# BR: bounded research reconciliation

The existing collector/research adapter lines (#234/#242) remain separate.
This repair only reconciles an existing SHA-pinned BR proposal; it does not
collect MOEX, resolve contracts, admit Forecast data or install the Sheets exporter.

history.br_research_reconcile(payload, expected_sha256, apply=False) validates
at most5000 BR H1/M15 rows and reports conflicts/idempotent skips. With an explicit
owner-authorised apply=True it exports a same-transaction prewrite database backup,
then writes only br_research_quarantine in the EXISTING history database.
Existing candles, versions, verified ranges and session markers are untouched.
The primary key of accepted history remains free for a future real source load.
A different payload at a research key or conflicting accepted OHLCV/end aborts.

history.br_research_series(secid,timeframe,from_date,till_date) reads that quarantine
by exact BR contract, at most367dates/5000rows. It retains every original source
field. Imported_at is the actual import time and explicitly not historical first_seen.
Forecast admission, calendar and historical availability remain unverified/not granted.
Normal history.coverage/stored_series and Forecast readers do NOT include quarantine.
To inventory all stored evidence inspect both operational and research commands;
never call a research-only row a missing download solely because operational coverage
excludes it. Never concatenate contracts or remove gaps.

The hash is SHA256 of UTF8 json.dumps(payload,ensure_ascii=False,sort_keys=True,
separators=(',',':'),allow_nan=False), not the byte hash of a pretty JSON file.
The command never accepts a filesystem path or downloads a source. Failed or dry
operations create no schema/data. File-backed apply returns a backup_directory with
DuckDB schema.sql/load.sql and Parquet files. Restore only under a separately approved
maintenance plan; do not import a backup into a running production connection.

Google mirror repair uses the existing canonical BR book, preserves its first20
columns and accepted prices/keys, and appends source/quality metadata where the
existing compatible research schema permits. Unavailable minute/calendar/first-receipt
evidence remains explicit. A mirror is not a prediction input gate.

This document describes capability, not an installed release or completed data write.
The existing project map/owner-authorised execution report records actual installation.
## Порядок для следующего исполнителя

1. Прочитать AGENTS.md, CHAT_DATA_OPERATING_RUNBOOK.md и ответ history.session_rules
   фактически работающего сервера. Наличие файла в GitHub не подтверждает установку.
2. Для BR проверить как обычный history.stored_series, так и отдельный
   history.br_research_series. Исследовательский ряд не входит в обычное покрытие.
   Не скачивать его повторно только из-за отсутствия в обычном покрытии.
3. Не смешивать разные контракты и не сдвигать цены между сессиями. Ряд каждого
   контракта упорядочивается по исходному началу свечи, ночные разрывы сохраняются.
4. Зеркало — существующая книга BR из реестра history.session_rules, листы D1/H1/M15.
   Первые 20 колонок A:T сохраняются. Дополнительные U:Z: research_status,
   normalized_sha256, source_archive_sha256, capture_day_status, source_kind, caveat.
   Пустая контрольная сумма означает отсутствие данного доказательства.
5. Неизвестные trade_session_date/session_id не выводить из календарной даты.
   Признак is_complete сохраняется из источника и сам по себе не подтверждает
   наличие всех минут, календарь торгов или доступность данных к моменту прогноза.
6. До дополнения книги создать явно названную резервную копию в подпапке резервов.
   Дописывать только отсутствующие составные ключи; конфликт — остановка.
   Каждую порцию сверять обратным чтением; существующие цены и ключи не заменять.
7. Служебные SESSIONS/VERIFIED_RANGES не заполнять предположениями. Различия
   прежних метаданных не устранять массовой заменой без доказанного источника.
8. Права на прогнозы не возникают после сохранения исследования. Отдельно нужны
   подтверждение полноты, календаря, времени доступности и действующие условия допуска.

Research reads also verify the stored payload SHA before returning a row. Accepted
history preservation hashing streams rows in bounded batches; it does not require
materialising the full history table in Python memory.
