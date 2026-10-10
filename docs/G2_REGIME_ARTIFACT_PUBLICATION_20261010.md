# Research artifact publication — 2026-10-10

## Ownership and dependency

Continues the local engineer's G2-TREND-BALANCE-RESEARCH line.
Base: PR #142, 3e3d54ced3ab79976afba679892dd759253fef12.
New module and tests only; no changes to dataset, forecast, prospective capture,
canonical DuckDB journals, or Web colleague files.

## API

- publish_preparation: validate the supplied input bundle and freeze all eleven
  proposed candidates in memory, then exclusively reserve a new directory.
- read_preparation: require an externally anchored receipt SHA256, verify all four
  exact file hashes, reject links and unexpected directory entries.
- publish_evaluation: verify preparation first, exclusively reserve another new
  directory, evaluate the saved candidates, publish report and receipt.
- read_evaluation: verify externally anchored report receipt, exact report hash
  and expected preparation receipt lineage.

Callers supply pathlib.Path directories whose parent already exists.
Neither function chooses data paths, extracts market data, reads production,
approves numerical rules, or automatically runs on real datasets.

Preparation files: manifest.json, accepted.jsonl, exclusions.jsonl, frozen.json.
Evaluation file: report.json. In each directory receipt.json is written last.
Receipt scope is DESCRIPTIVE_PROPOSAL_SIMULATION_ONLY; forecast_admission is false.

## Failure and concurrency contract

mkdir without exist_ok reserves a directory. Existing directories are rejected,
even if empty or left incomplete by an earlier interruption. Every file uses
exclusive creation, flush, fsync and byte-for-byte readback. A receipt is only
written after all payload writes and readbacks finish. An invalid preparation
input creates no directory; evaluation errors after reservation can leave an
incomplete directory. No automatic cleanup, repair, retry, overwrite or reuse.

A missing or untrusted receipt means the publication cannot be accepted.
A published ABORTED report remains an aborted calculation: REPORT_PUBLISHED
means byte publication completed, not that calculations or forecast quality passed.
The application refuses duplicate publication, but operating system permissions
do not prevent another program from editing files. Anchored hashes detect later
changes; they do not prove historical publication time or data availability at T0.

File fsync does not establish Google Drive remote synchronization or a complete
power-loss durability guarantee on every filesystem. The receipt hash must be
stored externally before use. A hash derived solely from the same untrusted
directory is not an independent anchor. No operating system read-only flag is
treated as immutability proof.

## Verification

Synthetic inputs only. Tests cover complete roundtrip and lineage, exclusive
reservation, input/report tampering, wrong anchors, unexpected entries, forbidden
links, interrupted writes at every preparation/report file, readback failure,
invalid input, criteria mismatch, and preservation of aborted report bytes.
No real quantile calibration, MOEX run, reserved-period read, or HOME action.
The underlying proposed quantile procedure still requires separate agreement.
