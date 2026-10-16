# G2 — прямой запуск Market Structure V2, 30 фиксированных случаев, 17.10.2026

**PASS: воспроизведён исследовательский расчёт фактического кода. NOT ACCEPTED: прогнозная сила, первичная завершённость H1 и момент первого получения данных.**

## Артефакт

- Исходный PR #254 `14e50bc93ff72dbaf192894efe3eccdfdcf3e2a3`; зависимый Draft [#258](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/258).
- GitHub Actions workflow [38967132975](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38967132975): **29 targeted / 521 full tests PASS**, Repository Preflight PASS.
- Код был получен как CI-artifact `g2-exact-source-no-user-data` (artifact id `12075017610`), sha256 ZIP `6c1e6190938f28e0931cef2b6b73b61e5feec55d0beee27e3b09030b700e4acf`. Source checkout merge SHA `131a0e26486703c8153f908e8361709a41bb7f21`; workflow объединяет head PR и target #254, данные пользователя в GitHub не загружены.
- SHA256 `src/birzha/application/market_structure_facts.py` `2f1c46bf9528cd6c15289a43b5d674ae11d2a63c3ff8ba82c6a7534ea8e965b2`
- SHA256 `scripts/g2_market_structure_real_archive_runner.py` `cd9ba297020fc215c5c964dfee0d7310e8065f445af2b94830ab2582fe95669e`
- SHA256 `src/birzha/domain/market.py` `c208c8389e24b4837c3b6e504c092f6cf9d52134474ab2982afb874388e57c55`
- Исходный ZIP `a80b2762d4a7af155af6863790dd50e220b1e49a61db9c330015fec654b13858`
- Точный `allowed-real-run-v2/inputs.json` `dabb1eca56ae69267829b81f9b13318cfb6c142806c3b2229eafd24d684b0c03`
- Старый независимый расчёт `a085bc56be25c4dcc3ff83aa5ea07440c87070e9255c4cc3a57d5a0d6f4d2fb1`
- Итоговый `market_structure_run_30_cases.json` SHA256 `350d44e8b3e45a2ba77ce19440e015218e6c1035a6aff9fea264e0c0b81ff1be`, 30 строк.
- Итоговый `market_structure_run_30_cases.csv` SHA256 `0abef27efa439e8c1696f5d8ab4a96da75035073acf96263593aa5786da37e09`, 30 строк.
- Один и тот же вызов два раза подряд вернул **идентичные** контрольные суммы результатов.

## Код и правила

`MARKET_STRUCTURE_FACTS_V2_20261017` — общий вычислитель `_swings/_wilder/_relative_volume/_describe` плюс два **разных** допуска:

1. `build_market_structure_facts` (строгий): по-прежнему запрещает **любую** `completed=false`; `T0_METADATA_GATED` также требует явное `available_at/observed_at`. Ни один из этих барьеров не ослаблен.
2. `build_archived_h1_descriptive_facts`: только H1, только явный источник `G2_DURATION_NORMALIZED_ARCHIVE_RESEARCH_ONLY`, проверяет точное соответствие флагов историческому правилу длительности, сохраняет `completed=false` и все исходные OHLCV, требует `available_at/observed_at=None`, статус `UNKNOWN`. Описание не имеет права выдавать направление, менять Forecast/Outcome или претендовать на строгую доступность на T0.

Старые naive даты `begin/end` в этом **одном** ZIP интерпретированы как `UTC+03:00` (Москва 2025–26). Это эксплицитная рабочая гипотеза, а не независимая историческая метка. `cutoff_at` архива уже задан в `+03:00`. Нет замены контрактов, дат, цен, отметок `completed` или избирательного удаления строк.

Исправлена единица `atr_pct`: в V2 это **число процентов** `100*ATR/Close`; `atr_ratio=ATR/Close` — дробная доля. Старый V1 не переписывался в исторических исходах.

## Что реально получилось

| Показатель | Значение |
|---|---:|
| D1: признаки по исходным 30 случаям | **30/30** |
| H1: отдельно маркированные описательные расчёты | **23/30** |
| H1: недостаточно истории | **7/30** = GOLD 5×0 H1 + BR 2×16 H1 |
| H1: строгий код отказал при наличии >=50 баров | **23/23** (из-за старых false-флагов где-то в полном переданном ряду) |
| H1: строгий код вернул признаки в достаточном окне | **0** |
| H1: строгий вход не выбросил исключение на пустом GOLD, но вернул `INSUFFICIENT_HISTORY` | **5** |
| Исторические `completed=false` H1 | **933**, не изменены |
| D1 уровни структуры | 12 HH_HL, 11 LH_LL, 7 MIXED |
| H1 восстановленные уровни структуры | 7 HH_HL, 6 LH_LL, 10 MIXED, 7 недоступны |
| Проверенные числовые совпадения с прежним независимым пересчётом | **все 30 D1 и оба ранее считавшихся H1; 0 расхождений** |
| Ранее ошибочно исключённые H1, теперь рассчитанные только описательно | **21** |

Формальный `strict.pass` при нуле H1 **не равен** `research.features.available`. Прежний отчёт «2 H1 были годны» проверял только последние 50, а строгая функция проверяет **полный ряд**. Это различие программы и прежнего независимого анализа, не конфликт математики. Контексты фиксированы в 2025–26, без 2023–24.

## Запуск на разрешённой локальной копии

```bash
PYTHONPATH=src python scripts/g2_market_structure_real_archive_runner.py \
  --archive "/path/Прогнозный движок — реальный опыт 2025–2026 — 13.10.2026.zip" \
  --reference "/path/reconstructed_structure_30_cases.json" \
  --output-dir "/path/results"
```

Программа сама откажется при неверном archive/input/receipt SHA и `case_id`, при несовпадении исходного `completed` с зафиксированным правилом архива и при расхождении с прежними числовыми результатами. Программа **не подгружает новых цен**.

## Внешняя граница проверки

Реальная сетевая `Real MOEX Validation` для исходного #254, [run 38955328330](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38955328330), была **FAIL**, а не PASS: разрешение инструмента → `UpstreamRateLimited`, источник `503`; файл `artifacts/real_moex_validation.json` не сформирован. Никакой новый сетевой прогон ради зелёного значка не выполнялся. Офлайн регрессия нашего блока PASS ≠ E2E/MOEX PASS.

## Продолжение

Ограниченный pilot: заранее зарегистрировать 10–20 **будущих** дат и единую метку T0; фиксировать точные рынки/контракты, признак источника и его SHA, D1/H1 структуры, ATR%, доступность/отказы и 5/10/20 сессий. Ни одну формулу/порог менять после начала нельзя. После созревания исходов оценивать структуру и вероятность следующего движения **отдельно**, рядом с простыми ориентирами на одинаковых случаях. Это **не доказательство прогностической точности** и не основание для сделок.

**Не выполнено и не разрешено:** merge, HOME deploy, runtime MCP installation, production DB, journal rewrites, protected holdout.
