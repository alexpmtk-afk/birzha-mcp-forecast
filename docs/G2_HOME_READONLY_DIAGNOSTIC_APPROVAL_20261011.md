# G2 — HOME read-only diagnostic approval plan (2026-10-11)

## Verified state (no HOME execution in this review)

Repository `alexpmtk-afk/gpt-powershell-bridge`, private Draft PR #104 contains a strictly read-only inventory intended to inspect `C:\MCP-HOME\birzha-mcp-forecast` and `C:\MCP-HOME\data\birzha` without imports/DB connection or writes.

- Windows HOME self-hosted runner accepted checkout, but run `38070736976` stopped because `pwsh` was unavailable.
- Run `38070781907` using native `powershell` stopped before user audit commands because generated `.ps1` was rejected by PowerShell's execution policy (`PSSecurityException`).
- Therefore **no HOME git commit, dirty state, runtime, disk free or database-readiness data are proved**. HOME is `BLOCKED`, not `FAIL`.

A read-only audit of existing `gpt-powershell-bridge/main` also found `.github/workflows/birzha-control-pr-gateway.yml`, an authenticated typed gateway restricted to repository actor/PR and explicit operations (including `repo_status`). **Important**: this workflow currently specifies `powershell -NoProfile -ExecutionPolicy Bypass -File "{0}"`. Its existence does NOT authorize treating the exception as an approved alternate execution-policy route. This review DID NOT call it, open a control PR or execute a HOME action.

## Proposed safe path — approval required

1. Independent review of the preexisting gateway's controller and broker semantics, confirming `repo_status` **only** reads the target Birzha repository, its exact root and branch/SHA, tracked modifications and configured path identity. Verify no shell escape, arbitrary command, secret, DB write, service invocation, branch mutation or deployment can occur.
2. Security/owner approval of any pre-existing PowerShell policy exception. If denied, identify a separately sanctioned diagnostic mechanism with the Windows administrator; **do not** change ExecutionPolicy, fall back to cmd/bash, create a launcher, or adapt GitHub Actions to force the prohibited `.ps1`.
3. After approval, issue exactly one typed, scoped, audited read-only request using the reviewed mechanism. Capture job source/identity, GitHub run id, time, branch, Git HEAD, tracked-dirty boolean, existence of runtime/data paths and available storage. Treat unverified/missing output as BLOCKED.
4. Compare observed HOME state to the intended (still unmerged) reviewed GitHub commit *without* making HOME changes or importing Python. A working HOME `main` with an old SHA is not, by itself, a defect; it establishes a deployment difference.
5. Add independent acceptance evidence and only then consider `home_windows_readonly_acceptance=PASS`. It does NOT authorize merging, installing, restarting or reading/deleting production database records.

## Minimum acceptance evidence

`HOME_READONLY_ROUTE_APPROVED`, named reviewed action/operation and permission, successful returned UTC timestamp, identity of executor, actual `BIRZHA_REPO_EXISTS`, `BIRZHA_GIT_SHA`, `BIRZHA_GIT_BRANCH`, `BIRZHA_TRACKED_DIRTY`, `BIRZHA_DATA_DIR_EXISTS`, `BIRZHA_VENV_EXISTS`, local disk margin and proof of no database writes/service changes. A missing value or failed job remains BLOCKED.

No secrets, personal directories or database content need to be exposed in the public `birzha-mcp-forecast` repository. Record only sanitized facts and protected result links.

## Explicit non-authorization

This is a **plan**, not an instruction to run the gateway. Current user approval to do nonproduction GitHub analysis cannot be interpreted as permission to bypass execution policy, modify HOME or deploy. On PR #180, HOME and production release gates stay BLOCKED until the separate approved route proves them.
