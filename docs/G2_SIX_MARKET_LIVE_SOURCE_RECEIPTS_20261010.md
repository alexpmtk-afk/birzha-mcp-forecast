# G2 — Six-market governed source receipts, 10.10.2026

Independent ChatGPT engineering line, stacked on PR #143; does not touch parallel Codex-colleague TREND/BALANCE.

Goal: preserve unmodified governed MOEX ISS responses for SBER, Si, BR, GOLD, IMOEX, RTSI. Resolve exact SECID first (never confuse Si/BR/GOLD rolling root with contract); collect complete D1 (last 115 days), H1 (18 days), and M1 (last completed exchange-local day) evidence for every market with explicit SHA, observed-at, response Date, source routing and original bytes, in disposable scratch. M1 is raw input for future M15 aggregation, **not itself a verified M15 series**.

Code: scripts/g2_six_market_live_source_capture.py uses existing MoexIssClient._request behind the mandatory public MOEX rate governor. Only prior-date completed bars admitted, no forming today. MAX_PAGES=8 per instrument/timeframe. Every inconsistent OHLC, incomplete pagination, repeated/unsorted candle, unknown exact SECID, absent index market, missing D1/H1/M1 or source failure causes SOURCE_CAPTURE_INCOMPLETE with specific error. Does not invent missing data or silently turn partial market into PASS. Manifest lists successful/failed markets and hash of all raw pages, including any pages from partial failures.

Offline fake-provider negative tests: tests/test_g2_six_market_live_source_capture.py. Live-run workflow is scoped to a temporary feature branch with no production secrets, triggers no HOME/MCP/writer, and saves source artifact only. Raw data are public MOEX payloads.

To run: python -m scripts.g2_six_market_live_source_capture --output-dir /fresh/scratch/g2-live-six-market

Six-market source PASS requires exactly six markets with D1/H1/M1, exact SECID and board, coherent pagination, verified original SHA and independent downloaded artifact. Any missing market/timeframe is PARTIAL, never silently reported PASS. The captured provider and local-clock timestamps are not externally cryptographically attested.

**Not a forecast:** This source inventory does not issue ForecastRecord or Outcome, does not establish model accuracy, strict historical known-at-T0, complete 15-minute bars or six-market forecasting. Next implement conservative M1-to-M15 aggregation, bind actual receipts to MarketSnapshot and engine, then in disposable staging freeze one real ForecastRecord after source observed-at and separately evaluate actual future outcome. Holdout 2023–2024 untouched. No merge/deploy/production DB/HOME/timer without separate user approval.

## Actual-source diagnoses, same day

First live probe passed all existing tests but failed the source gate because some MOEX candles begin near local or UTC session-day boundaries. Updated range validation to use the completed exchange-local session end; original bytes are preserved even for rejected responses. Second source probe correctly found that official MOEX ISS caps minute responses at 500 rows and often omits `candles.cursor`; collector now paginates until a short page with a hard eight-page ceiling and duplicate detection. Third live probe retained 4,000 minute rows per each market but hit the eight-page cap: raw archived counts show around 1,000 completed M1 bars per exchange day; the 4-day date window was unnecessarily wide for the 50+ complete M15 observation goal. Reduced M1 to **one previously completed market day**, ~1,000 bars requiring three bounded pages, keeping D1/H1 historical windows unchanged. Do not confuse passing pytest with source acceptance; only the actual manifest supports a six-market source PASS.
