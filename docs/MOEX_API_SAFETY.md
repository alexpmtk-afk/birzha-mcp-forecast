# MOEX API Safety Contract

Status: required before any MOEX / ALGOPACK provider is enabled remotely.

## What is confirmed

The official `moexalgo` Python client currently uses deliberate request pacing in its session implementation rather than firing requests without delay. Its public source defines a request timeout of `0.1` seconds and adds additional waiting before requests. This is evidence that request pacing is expected behavior, but it is **not** treated by BIRZHA as a contractual MOEX quota.

No stable public MOEX document was found that gives one universal numeric requests-per-second ceiling that is safe to hard-code as the exchange's guaranteed limit for all ISS / authenticated ALGOPACK access. Therefore BIRZHA must not invent such a claim.

## BIRZHA safety ceilings

Until a stricter officially documented endpoint-specific quota is available, BIRZHA uses deliberately conservative internal ceilings:

- public ISS: minimum 0.5 seconds between outbound requests in one server process (maximum 2 requests/second);
- authenticated / ALGOPACK path: minimum 1.0 second between outbound requests in one server process (maximum 1 request/second);
- maximum 120 outbound attempts per logical operation, including retries;
- maximum 2 retries after the first attempt;
- HTTP 429 and transient 500/502/503/504 responses use bounded backoff;
- `Retry-After` is honored when supplied;
- a request-budget overflow fails closed instead of continuing pagination or loops.

These values are internal safety limits, not statements about MOEX capacity.

## Architecture rule

All future MOEX adapters MUST send outbound calls through the shared upstream safety layer. Direct HTTP calls from MCP tools, application services, feature code, or forecast code are prohibited.

When Serverless Container scaling beyond one process/instance is introduced, the aggregate rate must also be protected by a distributed limiter or an equivalent infrastructure-level concurrency boundary before scale-out is allowed.
