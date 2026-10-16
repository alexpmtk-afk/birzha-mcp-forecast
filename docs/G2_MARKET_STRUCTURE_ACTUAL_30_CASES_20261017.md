# G2 — ПРЯМОЙ запуск Market Structure V2 на 30 исходных исторических срезах (17.10.2026)

## Результат и границы

**Результат прямого исполнения утверждаемого кода, не независимая копия формул.** Драйвер `scripts/g2_market_structure_archive_direct.py` вызвал `build_market_structure_facts` на всех 30 D1 и отдельно:
- вызвал `build_market_structure_facts` на каждом исходном H1 для доказательства прежних отказов;
- вызвал **новый отдельный исследовательский вход** `build_reconstructed_market_structure_facts`, который пользуется ТЕМИ ЖЕ `_swings`, `_wilder`, `_relative_volume` из исходного модуля, но не объявляет duration-flagged H1 завершёнными.

**D1: 30/30** успешно; **H1: 23/30** имеют 50+ исходных свечей и вычисленные описательные признаки; **7/30** недостаточно исходной H1: GOLD на пяти датах (0 баров), BR 31.03.2025 и 31.03.2026 (по 16 баров). Строгий H1: **25/30 отказы `forming candle not admitted`**, **5/30 возврат `INSUFFICIENT_HISTORY`** (GOLD). Ни один из исходных 933 `completed=false` не превращался в `true`; никаких свечей не удаляли и не досоздавали.

`H1` в этом исследовании — только `RECONSTRUCTED_RESEARCH_ONLY_DURATION_FLAG_UNKNOWN`: исходный архивный скрипт уже перезаписал false по укороченной длительности, первоначальная завершённость и принадлежность соответствующей сессии неизвестны. **Доказанного T0 first receipt, согласованного historical board/calendar, Forecast admission и прогностического преимущества нет.** Проведение этого эксперимента не является ослаблением строгого входа.

## Исполняемые версии и воспроизводимость

- Основа: Draft PR #254 head `14e50bc93ff72dbaf192894efe3eccdfdcf3e2a3`, кодовая поправка с отдельной исследовательской веткой `chatgpt/g2-reconstructed-direct-execution-20261017`, Draft PR #259.
- Изолированный GitHub source artifact от точного commit **`871836e071abe7f23040f6af8a55b94bbb8fe8e5`**, без исходных OHLCV, workflow [38967035389](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38967035389).
- Blob модуля `src/birzha/application/market_structure_facts.py`: `0e6995f7316946e11bbd02ce5d9887064715f9be`, schema `MARKET_STRUCTURE_FACTS_V2_20261017`.
- Blob драйвера: `2dbb6fd38dd3633e4f8342ecbd3e09e0decd1664`.
- Blob неизменённого класса Candle/CandleSeries: `c67c8f33dd4b35de582d45c853e96a0cc8fc036f`.
- Blob forensic receipt verifier #257: `01281fbc62a333876ce291229c3fbb8fea82679b`.
- Артефактный `direct-g2-v2-source.tar.gz`: SHA256 `83773debbcd8d328908e709baa091dc0d95beb8fefdfd9e95ec35cb2d267cfec`.
- Оригинальный ZIP `Прогнозный движок — реальный опыт 2025–2026 — 13.10.2026.zip`: SHA256 `a80b2762d4a7af155af6863790dd50e220b1e49a61db9c330015fec654b13858`.
- `allowed-real-run-v2/inputs.json`: SHA256 `dabb1eca56ae69267829b81f9b13318cfb6c142806c3b2229eafd24d684b0c03`, совпадает с original execution-receipt.
- Результат `module_results_30_cases.json`: SHA256 `7f6d1be1943515607796bbc8166869839f0411557f2bac99b3c32bd6035dee1f`.
- Таблица `module_results_30_cases.csv`: SHA256 `08bcb729bcc3cbbd0e5e516c27de3dd77beaa31c73a991f511b4eb1297515415`.
- Независимый повтор команды в другом каталоге дал **те же SHA обоих выходов**, все 30 ID/SECID/cutoff и порядок сохранены.

```bash
# После извлечения исходников из закреплённого source artifact (#259)
# и получения ровно первоначального пользовательского zip:
PYTHONPATH="src:." python -m scripts.g2_market_structure_archive_direct \
  --archive "Прогнозный движок — реальный опыт 2025–2026 — 13.10.2026.zip" \
  --out-dir output-30
sha256sum output-30/module_results_30_cases.json output-30/module_results_30_cases.csv
```

Исходные `begin/end` в ZIP **без смещения**. Драйвер при адаптации к доменным объектам использует ЯВНОЕ исследовательское предположение `Europe/Moscow UTC+03:00 2025-2026` и проверяет, что end каждой свечи не позже `cutoff_at`. Это преобразование касается только временной интерпретации копии и не доказывает момент фактической доставки биржей. `available_at` и `observed_at` оставлены `None`, original `completed` сохраняется; `source` проставлен `RECONSTRUCTED_RESEARCH_ONLY` как маркировка нового **адаптированного** входа, а не историческое свидетельство. Хэш всего original inputs указан отдельно от data_sha256 адаптированного CandleSeries.

## Полная ведомость (каждый рынок × каждая из пяти прежних дат)

`HH_HL` — прошлые повышающиеся локальные экстремумы, `LH_LL` — понижающиеся, `MIXED` — смешанная структура. ATR — процент от последней цены, не цена и не вероятность прогноза.

| case_id | D1 структура | D1 ATR, % | H1 структура | H1 ATR, % | H1 баров | H1 исходных false | H1 результат |
|---|---|---:|---|---:|---:|---:|---|
| SBER-2025-03-31 | HH_HL | 2.047395 | HH_HL | 0.534320 | 816 | 56 | RESEARCH_ONLY |
| SBER-2025-06-30 | HH_HL | 1.178009 | MIXED | 0.210280 | 860 | 74 | RESEARCH_ONLY |
| SBER-2025-09-30 | LH_LL | 1.598060 | HH_HL | 0.415737 | 893 | 73 | RESEARCH_ONLY |
| SBER-2025-12-30 | LH_LL | 1.089584 | MIXED | 0.217072 | 894 | 80 | RESEARCH_ONLY |
| SBER-2026-03-31 | MIXED | 0.857617 | LH_LL | 0.212930 | 886 | 64 | RESEARCH_ONLY |
| Si-2025-03-31 | MIXED | 2.161665 | MIXED | 0.452100 | 128 | 9 | RESEARCH_ONLY |
| Si-2025-06-30 | LH_LL | 1.327430 | LH_LL | 0.165291 | 128 | 8 | RESEARCH_ONLY |
| Si-2025-09-30 | MIXED | 1.261463 | LH_LL | 0.222677 | 160 | 10 | RESEARCH_ONLY |
| Si-2025-12-30 | HH_HL | 1.627745 | HH_HL | 0.342243 | 144 | 6 | RESEARCH_ONLY |
| Si-2026-03-31 | HH_HL | 1.645656 | HH_HL | 0.346122 | 155 | 9 | RESEARCH_ONLY |
| BR-2025-03-31 | HH_HL | 1.682943 | — | — | 16 | 2 | INSUFFICIENT_HISTORY |
| BR-2025-06-30 | HH_HL | 3.810984 | MIXED | 0.371842 | 320 | 20 | RESEARCH_ONLY |
| BR-2025-09-30 | HH_HL | 2.192006 | LH_LL | 0.363331 | 411 | 55 | RESEARCH_ONLY |
| BR-2025-12-30 | LH_LL | 1.835250 | HH_HL | 0.254045 | 411 | 60 | RESEARCH_ONLY |
| BR-2026-03-31 | HH_HL | 5.905564 | — | — | 16 | 1 | INSUFFICIENT_HISTORY |
| GOLD-2025-03-31 | HH_HL | 1.099192 | — | — | 0 | 0 | INSUFFICIENT_HISTORY |
| GOLD-2025-06-30 | LH_LL | 1.588038 | — | — | 0 | 0 | INSUFFICIENT_HISTORY |
| GOLD-2025-09-30 | HH_HL | 1.239609 | — | — | 0 | 0 | INSUFFICIENT_HISTORY |
| GOLD-2025-12-30 | HH_HL | 1.837461 | — | — | 0 | 0 | INSUFFICIENT_HISTORY |
| GOLD-2026-03-31 | LH_LL | 3.387483 | — | — | 0 | 0 | INSUFFICIENT_HISTORY |
| IMOEX-2025-03-31 | LH_LL | 2.147463 | MIXED | 0.673238 | 425 | 42 | RESEARCH_ONLY |
| IMOEX-2025-06-30 | LH_LL | 1.586415 | HH_HL | 0.353299 | 400 | 40 | RESEARCH_ONLY |
| IMOEX-2025-09-30 | MIXED | 1.640924 | MIXED | 0.540062 | 430 | 43 | RESEARCH_ONLY |
| IMOEX-2025-12-30 | MIXED | 1.463715 | MIXED | 0.375748 | 430 | 43 | RESEARCH_ONLY |
| IMOEX-2026-03-31 | LH_LL | 1.145748 | MIXED | 0.337060 | 420 | 35 | RESEARCH_ONLY |
| RTSI-2025-03-31 | HH_HL | 2.659538 | LH_LL | 0.808524 | 425 | 42 | RESEARCH_ONLY |
| RTSI-2025-06-30 | MIXED | 1.621371 | HH_HL | 0.359957 | 400 | 40 | RESEARCH_ONLY |
| RTSI-2025-09-30 | LH_LL | 1.799358 | LH_LL | 0.565328 | 430 | 43 | RESEARCH_ONLY |
| RTSI-2025-12-30 | LH_LL | 1.815741 | MIXED | 0.464591 | 430 | 43 | RESEARCH_ONLY |
| RTSI-2026-03-31 | MIXED | 1.706336 | MIXED | 0.370733 | 420 | 35 | RESEARCH_ONLY |

**Все исходные SECID, D1/H1 признаки объёма и статусы отдельных измерений:** точный CSV/JSON локального прогона из source artifact. В GitHub **не загружены** оригинальные 5 MB свечей и не размещены будущие котировки.

## Отличие от предыдущего независимого подсчёта

Предыдущий отдельно написанный скрипт `reconstructed_structure_30_cases.json` до вызова расчётов отвергал целое H1-окно, если среди **последних 50** записей находилось `completed=false`. Поэтому отображал 2 доступных H1, 21 `INCOMPLETE_BAR`, 7 `INSUFFICIENT_HISTORY`. Новый отдельный исследовательский путь, разрешённый координатором только для описательных признаков, не делает такой предварительный отказ, но сохраняет исходные false. Изменены только **21 прежде отсутствовавший H1-результат**, а не цена, OHLC или известные результаты: все 30 D1 структуры совпали, две ранее рассчитанные H1 структуры тоже совпали, независимые Wilder ATR и volume ratio по 32 ранее рассчитанным D1/H1 совпали в точности до записанного округления. H1 новое распределение: **7 HH_HL, 6 LH_LL, 10 MIXED**; D1: **12 HH_HL, 11 LH_LL, 7 MIXED**.

**Особое расхождение:** strict #254 при прямом вызове сканирует **все** H1 свечи, не только последние 50, поэтому отвергает **25** непустых контекстов, хотя прежний независимый подсчёт называл **2** окна доступными на последних 50; обе эти записи содержат false вне последних 50. Пять GOLD H1 имеют 0 свечей, так что strict возвращает недостаток истории без ValueError. Ни одна из 30 H1 серий не принята нынешним строгим путём как полноценные T0-прогнозные данные.

## Единицы ATR (контракт V1 → V2)

В старом PR #254 V1 поле `atr_pct` было **ATR/Close** (доля, например 0.04), а документация ошибочно называла его процентами. В **V2**:
- `atr_pct = round(100 * WilderATR14/LastClose, 6)` — **проценты, например 4.0**;
- `atr_ratio = round(WilderATR14/LastClose, 8)` — **доля, например 0.04**;
- `atr_price` — абсолютная шкала цены.

V1 результаты/поля не переписаны: имя схемы изменено `MARKET_STRUCTURE_FACTS_V2_20261017` и тесты обоих путей подтверждают единицы. ATR14 — Wilder seed SMA14 на фиксированном хвосте 50; не подменяет историческую SMA-TR14 ценового Forecast.

## Границы проверок GitHub

Код #254 на раннем запуске прошёл целевые и общие unit/CI. Но **Real MOEX Validation run 38955328330 — FAIL**, код UpstreamRateLimited, last_status=503 на resolution инструмента; следующий evidence upload тоже FAIL, итогового отчёта нет. Нельзя писать «все проверки» для этого SHA. Причина сетевая/источника, **не исправлялась повторным MOEX запросом** в данном исследовании.

Наш локальный блок использует только пользовательский замороженный ZIP и не обращается к MOEX/тестовому серверу. Workflow `G2 Direct Market Structure Research` [38967035389](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38967035389): **36 целевых / 528 полных pytest PASS**, Preflight PASS, source artifact PASS; локальное выполнение исходников commit `871836e...` на исходном ZIP — **30/30**, хэш-повтор воспроизводим. Это **CODE/RECONSTRUCTED STUDY PASS**, не Real MOEX source validation и не прогностическая приёмка.

## Ближайший ограниченный опыт

По заранее замороженным правилам, без оптимизации порогов по этим 30 известным датам, собирать новые prospective **пары D1/H1 факт-векторов** только с реальным first-receipt и явно проверенным календарём. На каждой будущей дате сохранять версию и SHA, HH_HL/LH_LL/MIXED, Wilder ATR%, volume median20, качество данных, abstention и доказуемое закрытие свечи. Исход через 5/10/20 сессий считать после горизонта отдельно: (1) частоты future UP/DOWN по категориям состояния, (2) первые границы уровней, (3) покрытие и недоступные данные; сравнение с неизменной ценой и простой инерцией только после фиксации правил. 10–20 новых T0 — **пилот процесса**, не статистическое доказательство точности.

**Запрещено:** ретроактивно менять v0.4/v0.6, использовать protected 2023–24, делать merge/deploy в HOME, изменять OHLCV, старые Forecast/Outcome, выдавать directional probability / торговые сигналы.
