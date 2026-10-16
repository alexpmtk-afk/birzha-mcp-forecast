# G2 — судебно-техническая проверка причин исключения H1 (16.10.2026)

## Корень проблемы

В **неизменённом** первоначальном архиве
`Прогнозный движок — реальный опыт 2025–2026 — 13.10.2026.zip`,
SHA256 `a80b2762d4a7af155af6863790dd50e220b1e49a61db9c330015fec654b13858`,
в скрипте `execute_allowed_real_batch_v2.py`, строки 35–39, есть оператор:

```python
duration=(datetime.fromisoformat(str(e))-datetime.fromisoformat(str(b))).total_seconds()
if tf in ("H1","M15") and duration not in ((3599,3600) if tf=="H1" else (899,900)):
    done=False
    partial+=1
```

Он **перезаписал** исходный `done` значением `False`, если H1 занимала не точно 3599/3600 секунд. При этом не учитывалась длительность законной биржевой сессии, возможные внутридневные паузы и способ формирования MOEX ISS `begin/end`. Данные исходной `historical_candles` таблицы и **первоначальный** флаг `completed` после этой нормализации недоступны в конечном `inputs.json`. Поэтому `completed=false` здесь не является независимым подтверждением открытой на T0 часовой свечи. Обратное утверждение (`completed=true`) тоже не вытекает.

Используем официальный справочник расписаний MOEX для дальнейшей проверки: https://www.moex.com/a1684 ; расписание, доступное сейчас, **не может автоматически доказать точное расписание каждого исторического рынка/board/date**.

## Независимый пересчёт на неизменном архиве

Скрипт `scripts/g2_h1_duration_forensics.py` выполняет read-only проверку `execution-receipt.json` против `inputs.json`, проверяет неизменность входного SHA и соответствие каждого `completed=false` исходному правилу длительности. Не исправляет цены, не включает эти бары в Forecast и не использует защищённые 2023–2024.

| Счётчик | Число |
|---|---:|
| Исследовательские срезы | 30 |
| H1, всего записей во всех точных SECID-контекстах | 10 448 |
| `completed=true` после нормализации | 9 515 |
| `completed=false` после нормализации | **933** |
| Совпадает с `incomplete_duration_marked` из ведомости | **933/933** |
| Среди 933 есть более поздняя H1 в тот же день | 255 |
| Следующая H1 в наборе — другая дата | 656 |
| В данном контексте нет следующей H1 | 22 |
| Отметок `false` среди последних 50 H1 всех срезов | 115 |
| Окна с ≥50 H1 и с флагом `false` в конце | **21** |
| Окна с менее 50 H1 | **7** |
| Окна с 50 последними H1 без `false` | **2** |

Наибольшие закономерности: **406** отметок в H1 18:00 у IMOEX и RTSI (по 203), **347** в H1 18:00/23:00 у SBER, **101** в H1 23:00 у Si/BR, прочие **79** в других часах (главным образом BR).

Официальные расписания MOEX включают завершение непрерывных основных торгов акциями в 18:54:59 и переход к заключительному аукциону; это объясняет, почему длительность H1 нельзя использовать как универсальный свидетель завершённости. Для исторических индексов, фьючерсов и разных периодов необходима **собственная** board/session/date проверка. Эта веб-информация не превращает `completed=false` в `completed=true`.

## Что это меняет в прежнем отчёте

Предыдущее выражение «21 H1 содержит незавершённые свечи» было **слишком сильным**. Доказано только: «21 H1 окно не прошло строгий фильтр длительности в реконструированном исследовательском наборе». Это организационно-методологическая ошибка классификации. Нельзя считать восстановленные H1 надёжно исправленными без исходной архивной базы или повторного чтения MOEX/первого получения.

У семи случаев недостаточно H1: GOLD — все 5 (нет H1 в историческом пакете); BR — 2 (в каждом по 16 H1). **Никакого вывода об общей доступности H1 на текущем HOME** эти данные не дают.

Два допущенных в арифметический анализ случая — IMOEX и RTSI на 31.03.2026 — всё равно остаются `RECONSTRUCTED_RESEARCH_ONLY`: историческое `first-seen` не удостоверено.

## Точная безопасная процедура допуска

1. Считать `begin/end` фактическими полями исторического источника. Нормализация `duration` сама по себе не удостоверяет закрытие/незакрытие часа.
2. Получить **оригинальный**, не перезаписанный `completed` и provenance для конкретных SECID и `begin` из сохранённой архивной таблицы/первичных MOEX-ответов; сверить SHA/OHLCV, нельзя тихо заменять текущий research bundle.
3. Для H1 неполной календарной длительности проверить принадлежность интервалу конкретного инструмента, board, сессии и даты (календарные перерывы, раннее закрытие, последний короткий интервал).
4. Даже когда есть следующая H1 **в тот же день** (255), это только косвенное доказательство прошедшего времени, а не аттестация исходной свечи или её `first-seen`.
5. Для 656 с последующей H1 в другой день и 22 последних строк отдельная календарная проверка обязательна.
6. Если исходный флаг, календарь или доступность на T0 не подтверждены — `UNKNOWN` / не допускать в строгий prospective Forecast. Для исследовательского reconstructed можно считать отдельно, с новым версионным контрактом качества и чёткими основаниями.
7. Не заполнять пропуски, не отбрасывать «неудобные» свечи ради удобных 50 строк, не смешивать фьючерсные контракты, не переписывать ранее замороженные Forecast и Outcome.

## Контур изменений

Эта ветка содержит только инструмент диагностики, тесты и отчёт; никаких изменений `src/birzha/application/`, MCP, HOME, DuckDB, source candle stores, прогнозов, target OOS или торговых систем. **Установленный приёмник не менялся.** Действующее решение о ретроспективном допуске может быть принято только после проверки исходного источника.

Для реальных данных исходный ZIP остаётся отдельно у владельца; не загружать 5 МБ OHLCV в публичный репозиторий. Команда на локальной копии:

```bash
python -m scripts.g2_h1_duration_forensics --archive "Прогнозный движок — реальный опыт 2025–2026 — 13.10.2026.zip" --output h1-duration-forensics.json
```

Контроль SHA input: `dabb1eca56ae69267829b81f9b13318cfb6c142806c3b2229eafd24d684b0c03`. Следующий этап — read-only сверка первичных H1/сессий в доступном разрешённом архиве, не автоматическое повышение меток.


## Follow-up 16.10.2026: repaired research *interpretation*, not original flags

An independent offline, read-only check of the **same frozen** `inputs.json` used the original 50 H1 rows including 933 duration-flagged records, without relabelling their `completed` fields. It checked each original end <= fixed historical case cutoff (assumed Europe/Moscow UTC+03:00 for 2025–26), chronological unique starts, no overlaps, positive and internally consistent OHLC, nonnegative volume, and exact receipt counts. Summary: **23/30 cases have >=50 structurally valid H1 for reconstructed descriptive analysis**, 7/30 truly lack rows (GOLD five cases have 0; BR two cases have 16). In these 23 cases **zero** temporal/OHLC failures were detected. Breakdown: SBER 5, Si 5, BR 3, IMOEX 5, RTSI 5.

This is a *relaxation only of the false assumption that every H1 must last exactly one full hour*. The historical provenance is still unverified. The 933 flags stay untouched in original JSON. A researcher may calculate **unverified descriptive historical features** using those original OHLC observations; a predictive engine must not treat this as proven first-seen T0 data. The test did not check independent session eligibility, original `completed` from the database, detailed instrument/board/date timetable, or source receipt.

A separate reference example is in the existing Si canonical Google workbook `H1` row 962: `SiM5 2025-03-31T23:00:00+03:00`, end 23:59:41, `is_complete=TRUE` in a **RECONSTRUCTED_RESEARCH** row; the frozen bundle calls the *same shortened time* `completed=false`. This proves **conflicting derived completion labels** across project archives, but the Google row is expressly `SESSION_UNVERIFIED` and its original `source_file_id` was inaccessible during inspection. Neither label establishes primary completeness.

**Methodology question for independent consultant:** Is it acceptable to retain short H1 from the historical MOEX ISS response in a separate RECONSTRUCTED_RESEARCH_ONLY descriptive dataset when chronology, OHLC and interval end <= T0 are satisfied, provided every short interval carries ORIGINAL_COMPLETION_UNKNOWN and never receives PIT/Forecast admission? This is explicitly distinct from authenticating the original source's completion bit and first receipt, which still needs original storage/independent witness. Until affirmative explicit reviewed methodology and original source checks: no strict Forecast admission.
