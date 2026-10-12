# Q-089: Offline RFC3161 proof for original M1/M15 staging manifest

A new explicit, opt-in verifier re-runs Q-087 and Q-088 from the same
source-page bytes, exact Instrument/Candle and notice candidate, and requires
IDENTICAL canonical commitment bytes and SHA-256 before checking ANY TSA
artifacts. It cannot accept a swapped or rehashed original without replay.

It then uses an explicitly supplied OpenSSL executable and caller-provided
query/response/trust CA file paths (not built-in trusted roots). No TSA network
requests, database writes, journal append, forecast or live MOEX calls.
The RFC3161 query must contain a nonce of >=64 bits (16 hex digits). Both
queryfile+nonce and data-imprint checks must pass against the same retained
signed token, with signature and signer-chain checked under the CA provided
by the caller. The decoded TSA generation time must be between the
preflight's *claimed local evaluation* timestamp and 5 minutes thereafter.

Important distinctions:
- A correctly validated TSA proves content commitment existed by TSA
  generation time if the signing CA is independently trusted, not the
  original arrival time of the source.
- The CA's independent approval is not performed here; the user-selected
  trust file and the claimed MOEX URLs/calendar remain unverified.
- An operator can forge source pages and session candidate together before
  applying for a real TSA signature. The signature will NOT prove their truth.
- Historic replay cannot be backdated into an original first receipt, and
  the previously saved Q-085 local clock is not a remote trusted clock.
- Tests use a synthetic command runner and synthetic dummy CA/query/token;
  successful test-only simulation is not a real signed proof.

Output remains fail-closed for real LevelEventPilot admission, trusted
forecast issuing, trading, production and all 12 release gates.
Next work requires a dedicated first-receipt capture event tied atomically
to original HTTP transport bytes, approved independent signer/CA, and real
market session validity; no silent promotion to EXACT.

Never touch Forecast V1, old storage, protected OOS, main/HOME or production.
