# RI indicator write safety — 2026-11-02

Extends dependent PR299 over PR298. No HOME deployment, merge, Google write or
schedule activation is authorized by this document.

## Existing data and calculation

The 95-column schema and research formulas do not change. Keep PR299's M1
civil-day context and short-opening-bar mapping. Delta-proxy remains an
approximation, never aggressor Delta. New generated rows are ordered by source
time, but stored after the entire existing block. Consumers must join by exact
source key and order by time when needed; physical Google row order is not a
chronological API. Existing 2026 values, row numbers, formatting and references
must remain unchanged.

## No structural insertion

Use native appendCells(fields=userEnteredValue) on the same existing indicator
tab, at most50 rows. No insertDimension, sorting, replacement, clearing,
copying, widening, named-range or formatting mutation is issued. appendCells
extends the grid at its end if necessary. Preflight rejects a changed append
offset; postflight compares the complete expected table. A fixed external
range remains fixed: preserving a link does not prove it includes new history.
Google API semantics: https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#AppendCellsRequest

## Participating writer exclusion

A workbook-keyed OS mutex spans the complete model3 operation: receipt recovery,
source and target reads, prepared receipt, append, readback, status and audit.
Windows uses a Global named mutex, covering different sessions on the host;
POSIX uses an OS flock on an empty lock file. Ownership is released by the OS
on process death, with no expiry or stealing timer. The lock contains no market
data, checkpoint or credentials. Access failure blocks the write.

All native Sheets mutations and pinned-book GoogleSheetsBridge mutations in
this revision use the same guard; M1 publication holds it across Drive/index
work. Tests exercise separate processes, native and legacy entry points,
reentrancy, a different book, and normal/crashed owner release. Global mutex
reference: https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw

This is NOT a distributed Google lock. Old unmodified installations, remote
Apps Script calls, other computers and human editors do not participate.
Their exclusion and authorization isolation remain mandatory live acceptance
conditions, not something sole_writer=true can prove. Old Forecast18082 is
not updated here. Unknown/hidden processes and external callers must be
resolved before actual writes. No flag in this patch claims those are fenced.

## Existing QUALITY_AUDIT only

STARTED is confirmed before calculations; PREPARED stores before/expected-table
hashes and bounded source keys before the target append. COMMITTED is recorded
only after complete value readback. NO_MISSING and STATUS_UNCONFIRMED are
explicit. Exceptions log fixed ERROR, never exception text, arguments, response
bodies, tokens or credentials. An unavailable audit stops the operation.
Dry-runs and requests denied before ownership do not mutate even the audit.
Requests unable to obtain a lease cannot safely append to its audit either;
they fail closed to the caller and are not falsely reported as journalled.

Recovery runs under the same mutex before every authorized apply. An exact
expected table yields RECOVERED_COMMITTED without an indicator write. A table
still equal to the pre-write state does NOT prove failure: the original Google
request may commit late. Log RECOVERY_NOT_OBSERVED and keep PREPARED unresolved;
do not auto-clear, retry or roll back. A different table logs RECOVERY_CONFLICT
and blocks. Repeated recovery attempts have distinct audit IDs. Operator proof
of settlement is required when the result cannot be observed conclusively;
there is no unsafe timeout-based override.

## Acceptance limits

Isolated tests prove serialization among updated participating clients, request
shape, unchanged old values, receipt/error redaction, restart/retry behavior
and delayed-response safety. They do not prove real Google permission/quota,
external writer exclusion, network settlement, workbook-specific render/style
behavior, source publication time, D1 session mapping or predictive quality.
Do not enable an apply based only on these tests. First obtain a new separate
HOME acceptance decision, start18085 read-only at the checked SHA, confirm
all writer routes/permissions, then authorize a small append/readback/repeat.

The existing CI and Real MOEX branch filters include the dependent PR base so
all unchanged jobs run on the final PR299 SHA; no job is weakened or skipped.
