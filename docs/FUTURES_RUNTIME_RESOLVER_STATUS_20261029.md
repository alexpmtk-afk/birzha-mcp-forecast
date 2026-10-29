# Futures T−1 resolver — runtime contract and limits (2026-10-29)

## Implemented in code (not proof of HOME installation)

- `src/birzha/providers/continuous_futures.py`: reusable exact-contract previous **completed observed** trade-session selection by `VOLUME`, with expiry safety, instrument-root matching, spread/option exclusion, deterministic ties and evidence-based errors.
- `src/birzha/providers/moex_history.py`: actual historical resolver requests previous observed session, not current-day `VALUE`/OI/volume. It labels `source=MOEX_ISS_HISTORY_T_MINUS_1_RECONSTRUCTED` and `roll_policy=MOEX_T_MINUS_1_VOLUME_EXPIRY_V1`. It queries all calendar dates for source `TRADEDATE` instead of assuming Monday–Friday.
- Exact expiry metadata is fetched from MOEX official security specifications. For expired GDM6, public `/iss/securities/GDM6.json` gives `description.name=LSTTRADE` with `2026-06-19`; the board-specific active-security endpoint correctly returns no row. Use `LSTTRADE`, not presumed `LASTTRADEDATE`, for expired-security description. Never infer expiry from tickers or an arbitrary 'third Thursday' rule.
- Tests: `tests/test_causal_futures_runtime.py` and updated `tests/test_historical_contract_resolution.py`; RI March/June/September, Si/BR/GOLD, no same-day leakage, expiry, spread rejection, weekend source, fail-closed missing data, exact archived security metadata.
- After main PR #279, historical root verification markers are generation-pinned to `ROLLING_HISTORY_V3_T_MINUS_1_EXPIRY`; previous `ROLLING_HISTORY_V2_PREWARM` markers cannot certify the new contract-switch schedule. Both CI and real MOEX validation passed in GitHub. This is a source-code acceptance result, not HOME readback.

## Still NOT guaranteed by this change

- Historical MOEX ISS returns later-reconstructed D1; it does NOT independently attest when the same D1 became available to a historical forecast at `T0`. `RECONSTRUCTED_NOT_PIT_VERIFIED` is deliberately explicit.
- `MarketDataService.candles(root, from, till)` still resolves a **single** SECID as of the end of the period. This is NOT a price-adjusted multi-contract continuous OHLCV series. Never label its cross-rollover output `CONTINUOUS` or silently claim forward/back adjustment.
- `resolve_active_future` still ranks current active root independently. Unifying current and historical resolution requires separate confirmed source-availability / live-day rules and compatibility tests.
- Full session-calendar/night/extra-day mapping, quality-gated M15 and the genuine Model №3 interface must be verified independently before any production Forecast acceptance.
- The presence of this code and a GitHub CI PASS never proves installed HOME SHA. Only local executor with actual access can do an installed readback and safe release.

## Mandatory operational rules

- Preserve RAW per SECID, all source volumes, source end timestamps and observed-at / available-at confidence.
- Keep adjustment basis/ratio in a versioned journal; never substitute synthetic levels for actionable RAW prices.
- When unavailable data or expiry metadata prevents a verified choice, hard fail rather than silently use current-day volume.
- Treat true volume-by-price and aggressive Delta as unknown when only M1 OHLCV is supplied.
- Do not merge code that breaks CI / Real MOEX Validation. Do not deploy to HOME until there is an executable rollback plan and installed SHA verification.
