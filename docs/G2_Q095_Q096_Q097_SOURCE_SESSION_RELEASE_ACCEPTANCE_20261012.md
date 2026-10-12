# Q-095–Q-097: source transport isolation, session breaks, release NO-GO

Based on Draft #215 Q-094. One isolated Draft PR, three separate test
areas and explicit fail-closed acceptance. This is a development-stage
audit, not an approval for live MOEX or production operations.

## Q-095: bounded, memory-only governed transport

Q-091 previously accepted an arbitrary injected Python opener as if it
were a safe mock. A callable could perform actual network traffic. A
narrow exact-typed MemoryOnlyISSOpener and bounded FixtureISSResponse
now replace arbitrary openers. Default live httpx provider and other
injected callable transports are refused BEFORE the governor attempts
an HTTP request. All synthetic responses are returned directly from
memory, under the existing governor's retry ceiling, with no network
method inside this fixture opener. A custom opener must never be called
as part of this research-only entry point. Every native M1 row must
also belong to the exact requested Moscow-local trading day. This
does NOT authenticate an external MOEX network source or authorize any
live capture. The unused source caller's URL/headers are hypotheses.

## Q-096: intraday claimed session break veto

A single opening/closing session pair does not prove that every M15
inside it belongs to continuous trading. A new STRICT normalized JSON
notice (operator supplied original bytes + SHA, market ID and session ID)
contains bounded ordered intraday segments. M15 must lie entirely
inside one continuous segment; claimed auctions/closed breaks, gaps,
overlaps, wrong SECID, invalid times, notice mismatch and swapped Q088
SHA fail closed. The original normalized notice is NOT an official
MOEX-signed calendar; this check certifies only internal consistency
of the content. Real official session/auction/halt feeds and transport
signatures remain external blockers.

## Q-097: real release gate read-only NO-GO

New pure auditor links a memory-governed page source ID/raw SHA/request
route to the retained Q-088 manifest SHA and normalized Q-096 notice
SHA, and separately validates the CANONICAL unmodified release gate
JSON using scripts/g2_release_gate.py. Tests attempt forged source
SHA/SECID/URL, self-certified provenance, replaced Q088 or session
hash, false approval flags and modified release DAG pins. A passing
research report is explicitly a RELEASE NO-GO and MUST retain all
unresolved production gates; it is neither independent external review
nor proof that future outcomes/forecast skill have matured.

## Release boundary

The authoritative frozen 2026-10-10 gate currently has 11 technical
requirements, of which ten remain OPEN/BLOCKED, and two explicit
user permissions: 12 unresolved release blockers total. No change
is made to the release JSON, blocker rules, pinned main/release
branches, Forecast V1, protected OOS or HOME. No merge/deploy/trading,
network MOEX, external TSA calls or real signed calendar assumptions.

These three blocks improve fail-closed engineering for future controlled
live intake; they do NOT finish the entire Forecast Protocol 08 or
turn forward prospective evidence into historical truth. Full
SHA-pinned crossline CI, Public Repository Security, Repository
Preflight and unchanged NO_MAIN/NO_HOME/NO_PRODUCTION gates required.
