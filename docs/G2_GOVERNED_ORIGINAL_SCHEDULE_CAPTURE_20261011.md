# G2 — bounded original official MOEX schedule-page collection, 2026-10-11

## Concrete checked source pages, NO automatic full-calendar assumptions

The exchange's public pages and press releases demonstrate that **weekend session lists can change**. In particular, the base 2026 weekend schedule in [the Nov 2025 notice](https://www.moex.com/n95564) was updated by [the Sep 4, 2026 notice](https://www.moex.com/n103931): it swapped the Dec 5–6 weekend opening with Nov 28–29 closure. A separate [Aug 12, 2026 announcement](https://www.moex.com/n103333?nt=107) confirms no Aug 15–16 sessions. The exchange's [current 2026 trading calendar](https://www.moex.com/ru/tradingcalendar?year=2026) is another distinct page, and its [weekend stock session rules](https://www.moex.com/a8920) show eligibility varies by instrument/trading mode.

These pages show why a saved annual timetable or weekday filter cannot guarantee that a specific SECID's future event starts at a particular hour. **No actual original MOEX page HTML or PDF bytes were captured from these links during this implementation stage.** Web search excerpts are noncanonical documentation, not exact HTTP receipt evidence.

## New deliberately opt-in collector

`scripts/g2_capture_official_schedule_pages.py` implements `capture_official_schedule_pages(urls)` with no import-time activity, no scheduled action and **no CLI that launches traffic automatically**. The caller must explicitly supply a bounded tuple of no more than 12 fully qualified HTTPS MOEX URLs.

- Host must be exactly `moex.com` or `www.moex.com`, no credentials, fragment, non-HTTPS URL, embedded `token/api_key/auth/secret` fields, unbounded URL or duplicate URL.
- When the collector manages its own HTTP client, it uses `httpx.Client(verify=True, trust_env=False, follow_redirects=False, timeout=10s)`; the HTTP transport validates certificates normally but **the collector neither pins the MOEX certificate nor independently attests authorship**, so a TLS-verified page is not independent market-time proof.
- Each original HTTP page's complete body is bounded to 1.5 MB and retained byte-for-byte with SHA-256, local observed start/end UTC, content type, original URL and untrusted HTTP `Date`. Unsupported response code, redirected response, wrong MIME, missing/too large page, transport/time-out or exhausting retry/delay refuses the **whole inventory** instead of creating a partial schedule snapshot.
- The shared `ProcessUpstreamControlPlane` enforces the existing conservative MOEX public rate policy and `Retry-After` processing; all physical HTTP attempts count against an active `CaptureScope` where one exists, **otherwise** a new bounded one-shot scope of at most 36 attempts/60s is created. No bypass of a more constrained outer source-capture budget.
- `prepare_schedule_envelope` converts explicitly captured source bytes into #196's immutable format only when an independently reviewed, explicit `revisions` list is supplied. It **does not parse or fabricate** publication dates, special session changes, SECID eligibility, event cutoff or assigned trading-day from HTML/PDF. The resulting exact byte envelope must pass #196's pre-T0/notice-chain validation before later optional attachment to the canonical disposable DuckDB.
- The #196 verifier now refuses **unreviewed captured official documents** omitted from the revision list. This is a structural inventory check, **not evidence that every published official notice was discovered**. Neither recent page change monitoring, catalog completeness nor effective instrument-specific calendar is certified by the collector.

## Requirements for any future real source verification

1. Separately approve the precise list of official MOEX schedule pages to fetch and the legal/rate/network scope. This must include a rule for discovering/recording *all applicable revisions* as of the desired Forecast T0, not simply a static list of old pages.
2. Run one bounded **isolated nonproduction** first-party capture under the same or stricter logical attempt scope; retain originals, TLS/transport audit, UTC receipt and SHA. If unauthorized or missing, mark `BLOCKED`, never infer from a different instrument or weekday formula.
3. Independently review each notice's extracted effective schedule, SECID eligibility, any cancellations and changes to following trading-day assignment before the Forecast T0. Keep the raw originals and the reviewer decision; re-run the verifier.
4. Obtain an approved independent RFC 3161 TSA signature on the newly issued immutable Forecast commitment *before the separately authenticated future market event*. The already-implemented TSA verifier and timestamp retention are insufficient to select or approve the external trust root.
5. Only then consider the associated evidence items; **do not auto-change** any of the 12 production release blockers. HOME/main/production remain out of scope without separate authorization.

**This PR contains no live MOEX schedule fetch, no TSA call or third-party hash transmission, no secret or protected holdout, no trading, and no changes to production Forecast/Outcome.**
