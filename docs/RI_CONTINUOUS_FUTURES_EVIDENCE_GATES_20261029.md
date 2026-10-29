# RI / Continuous Futures — текущая матрица доказательств и допуска, 29.10.2026

> **Исследовательский этап. Ничего не объединено в main и не установлено на HOME.**
> Нельзя выдавать арифметический PASS за доказанную историческую доступность или улучшение прогнозов.

| Gate | Текущий статус | Подтверждено | Что ещё нужно |
| --- | --- | --- | --- |
| G1 RAW/source | **PASS в проверенном архиве** | 5 контрактно-месячных наборов RI Jan–Apr: H1 1772/1772 совпали с M1 по OHLCV, ноль расхождений | Для будущих периодов повторять SHA и сверку |
| G2 MOEX sessions | **PARTIAL** | 3 окна марта/июня/сентября сопоставлены с опубликованными выходными/праздничными сессиями | Полная независимая карта торговых дат/ночных сессий по всей исследуемой истории |
| G3 T−1 selector / PIT | **PARTIAL / BLOCKED для исторического PIT** | Отдельный `decision_ledger()` не читает целевой день; отрицательные тесты на late/inferred/non-final source PASS | Исторические `first_seen` / `available_at` отсутствуют; реальные данные T0 не аттестованы |
| G4 correction mathematics | **PASS в исследовании** | Три перехода, 188 выбранных D1; Additive Back и Forward Spread сохраняют одинаковые движения/ATR в пунктах, но имеют разные шкалы | Согласовать координаты реальных уровней и входов Модели №3 |
| G5 H1/M15 data quality | **PASS пересборка / PARTIAL допуск модели** | Из 78 666 M1: 3 408 строгих + 3 316 разреженных M15, 574 334 объёма вне строгого M15 (12.2763%). **402 H1 (22.6862%)** с нестандартным source-end, объём 163 192 (3.4882%), все совпали с M1; 306 непосредственно имеют следующий час | Отделить флаг `source_end_non_nominal` от фактической финальности на T0; установить причину пропусков минут, согласовать роль sparse M15 |
| G6 Model №3 signals | **BLOCKED для полного сравнения** | Принятая методика: закрытые H1 — основной побарный анализ, D1 контекст, M15 уточнение | Проверить реально установленный engine / 54-польный контракт; сравнить прогнозы при фиксированном T0 и версию логики, затем OOS |
| G7 Release / automation | **BLOCKED по полномочиям и приёмке** | Draft PR #734 и Draft PR #276; исследовательские тесты PASS, нет записи в HOME | Независимый review, явное согласование merge/deploy, подтверждение installed SHA и проверка настоящего сервера |
| OI & tick-level tape | **OPTIONAL / DATA-DEPENDENT** | Контрактный объём D1 уже достаточен для базового T−1 теста | OI для сравнительного исследования; сделки нужны только для точного volume-at-price/real Delta, доступ/условия уточнить отдельно |

## Результаты без финансовых обещаний

- Исходные минутные данные **не были потеряны** в архиве: исходные M1 сохранились.
- Исправлена исследовательская методика извлечения данных: отдельное `M15_observed_buckets` хранит sparse OHLCV + `observed_m1` + список пропущенных минут; нельзя выдавать эти M15 за 15/15 полноту.
- Реальный объём, ранее не отражённый в строгом M15, равен 574334 контрактам **по пяти отдельным контрактно-месячным наборам**; из-за пересечения мартовских контрактов это не единый непрерывный рыночный объём.
- Проверка арифметики **не доказывает** дополнительную точность прогноза или финансовую прибыль.

## Точки кода и контроля

- Source PR: https://github.com/alexpmtk-afk/msp-server-bridge/pull/734
- Unified policy PR: https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/276
- Sparse aggregation code: `msp-server-bridge/research/ri_m15_sparse_reconcile.py` (только исследование).
- Pure causal selector: `birzha-mcp-forecast/research/continuous_futures_reference.py` (не рабочий runtime).
- Agent skill proposal: `.agents/skills/birzha-continuous-futures/SKILL.md` (не установлен).
- Official MOEX weekends: https://www.moex.com/n95564 ; holidays: https://www.moex.com/n96571
- Official MOEX historical full-tape products are separate from free candles; paid/trade-source use **not authorized**: https://www.moex.com/a2863

**Next safe implementation step:** full scoped `trade_session_date` mapping and test Matrix for M15 source gaps, then pin Model №3 input contract. No automatic continuous-series/Forecast activation until strict-PIT evidence is available or the owner explicitly approves a separate clearly-labeled reconstructed-only research regime.
