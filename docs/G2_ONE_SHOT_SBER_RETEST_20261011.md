# G2 SBER one-shot categorized live receipt replay — 2026-10-11

## Exact parent and purpose

Standalone **one-time controlled** GitHub-hosted Ubuntu staging retest derived from Draft #184 HEAD `1f33cc79967043c78cb43bc8a86b08d4fa4019f8`, where new provider-error categories passed 388 scoped / 1444 full synthetic pytest in Actions 38088949232. There is no automatic repeat, cron, retry workflow, dispatch, HOME node or subsequent market.

### Prior evidence

- Actions 38088597117 at commit `4d3469a85ba8960bd5f1573f82f62b8c6ae509c8`: genuine governed SBER MOEX source at 2026-10-10 21:42 UTC → 108 D1, 302 H1, 366 valid full M15; SHA-bound atomic disposable Forecast receipt (captures=1/outcomes=0) **PASS**. Local receipt/issued T0 order correct. This is not external authenticated clock or production approval.
- Actions 38088624181 at commit `4a15771a93154786b467a3066c5401b57fa93490`: 37 scoped synthetic tests PASS but network **FAIL**, `UNCLASSIFIED_SOURCE_FAILURE` (insufficient sanitized diagnostic). Do not infer HTTP status or rate-limit from that run.
- Draft #184 maps *typed* `MoexIssError` into sanitized HTTP 429/403/503/5xx codes; distinguishes recognized timeout/transport/governor and keeps unknown failures opaque. Its tests intentionally contain fake secret URLs which must never appear in public logs.

### Conservative one-shot request budget

MOEX public-policy `min_interval_seconds = 0.5`, target utilization 90%, `max_retries = 2`, max requests per operation = 120. These are stricter internal engineering ceilings, **not a publicly certified MOEX API quota**. For one SBER market, source collector caps D1 and H1 to 8 pages each, M1 to 32 pages, with 5-calendar-day minute coverage, native pagination and complete 15×M1 aggregation. Request planner/global process pacing applies to provider requests. A new runner must validate these exact caps before a network request; a mismatch stops without a request.

No cron, automatic rerun, artifact upload of original raw pages, output of untrusted provider exception, secrets or access to protected OOS. Fresh output directories and a disposable single DuckDB are under `RUNNER_TEMP`, cleaned by the ephemeral runner. Release-gate stays BLOCKED and self-hosted Windows/home does not participate.

### Classification

- A single live capture+Forecast atomic commit: PASS only if original governed raw MOEX response bytes and SHA inventory all validate; receipt precedes issuer T0; distinct atomic evidence/Forecast inserted and audited, no Outcome.
- Explicit `safe_reason=...` on failure: record precisely as non-production FAIL/BLOCKED, preserve original untrusted text only in discarded runner temp; **never** substitute synthetic bars.
- If this run is blocked by provider/egress/cadence, stop. Any new live retry requires fresh, separately justified request policy assessment. Do not edit/re-push this network-triggering branch to create accidental repeated runs.

This retest does not authenticate exchange trading-session calendar, issuer clock, original provider time, predictive skill, power-loss recovery, HOME or production readiness.
