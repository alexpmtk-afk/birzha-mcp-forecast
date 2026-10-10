# G2 current-head #171 integration acceptance — 2026-10-10

## Precise scope

CI-only follow-up stacked on ChatGPT #172 (strict future D1 completion chronology). This integration pins ChatGPT #168 and colleague #171 (which includes the prior saved six-market #169, linked #167) plus independent regime #147. No change to colleague-owned source modules, first Forecast, future Outcome or existing market data. No release/main/HOME authority.

| Line | Exact SHA | Meaning |
| --- | --- | --- |
| ChatGPT storage foundation #168 | `93c1639a23203dc5005fdf78515053813ba8273e` | Disposable single-DuckDB source/Outcome immutability, with #172 stricter session chronology in our stacked branch |
| Codex-colleague #171 | `16edfb353332cdf22aa8b5be28ab58fd676acfc7` | Factual D1/H1/M15 directional comparisons, neither full ALIGNMENT nor predictive skill |
| Codex-colleague research #147 | `82af27e1f9d79f02f8062c5756047fe1fb35ef20` | Descriptive Development TREND/BALANCE |

CI-only [run 38084842292](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38084842292) at workflow commit `bd090c1f44d14ccc5a33a9c29094a7cb5a327a99`:
- SHA pin and ancestry checks PASS.
- Two isolated local merges PASS with no push.
- 239 targeted pytest PASS; 1,252 full pytest PASS (29.52s).
- Python compilation, repository Preflight, public security PASS.

The audit and machine manifest were amended AFTER that run; final-head CI still required to confirm the complete PR state. A passing CI establishes **software crossline compatibility**, not independent semantic signoff.

Release decision remains **BLOCKED**, with 12 underlying requirements after converting the latest CI compatibility gate from PENDING to PASS: authentic source/clock/calendar, matured prospective 5/10/20 outcomes, approved methodology and OOS, full Protocol 08, production storage durability/migration/restore, HOME read-only acceptance, human review and separate user permissions for main merge and HOME deployment. No protected 2023–24 history opened.

## Next verification

Perform independent domain review of D1/M15 session/calendar conventions and one read-only HOME acceptance through an explicitly authorized route. Real future outcomes must only be appended once independently verified. Do not use synthetic or saved offline D1/H1/M15 movements to claim genuine ex-ante results.
