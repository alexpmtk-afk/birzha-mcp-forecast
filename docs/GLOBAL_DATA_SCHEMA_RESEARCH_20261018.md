# Global data standard and research preparation — 18.10.2026

This is a reversible research candidate, not a production migration or Forecast admission.
Base main: fc2572d174be435647fb6a5ea95a5b1611d3d74a.
Transport dependency: PR262, 972df0b (full SHA in git log).
Canonical Google book IDs and existing accepted candle cells remain unchanged.

## Contract

`application/data_standard.py` defines the ten logical sheets and forty candle columns.
The twenty original required fields retain their order. Eight existing provenance fields,
including the two Si-specific fields, remain represented. Twelve additional metadata fields
make exact identity, unknown units, applicability and completion evidence explicit.
The workbook/key supplies the root symbol; README_STATUS supplies the provider clock.
The active native transport has a 40-column limit: do not deploy a larger physical schema.
Unknown custom columns, conflicting SECID/timeframe, values, intervals or identity stop the plan.
Existing 20/26/28-column headers continue to project without an implicit migration.

Index raw volume is preserved. The research analytical view sets its volume to NULL and
keeps raw_volume separately. An absent receipt, unit or calendar proof is never imputed.
No strict historical admission is granted by a successful data integrity check.

## Isolated pipeline

1. Export the six existing Google books and retain exact file hashes.
2. `audit_canonical_workbooks.py --snapshots INPUT --output NEW_AUDIT`
3. `build_research_data_unification.py --audit NEW_AUDIT --home-backup APPROVED_EXISTING_BACKUP --output NEW_PRIVATE_LOCAL_DIR`
4. `consolidate_research_flow.py --audit NEW_AUDIT --database NEW_PRIVATE_LOCAL_DIR/research_data_unification.duckdb`
5. `merge_research_observations.py --research NEW_PRIVATE_LOCAL_DIR`
6. Read the missing tail through the existing history.stored_series tool, exact contracts only,
   retaining the broker envelopes locally in home_tail; do not refresh the source just to read it.
7. `append_live_research_tail.py --research NEW_PRIVATE_LOCAL_DIR`
8. `write_data_audit_reports.py --audit AUDIT_ROOT --research NEW_PRIVATE_LOCAL_DIR --output AGGREGATE_REPORT_DIR`

Dependencies: the pinned project environment plus openpyxl for read-only exported workbook
inspection. No XLSX is authored. HOME files stay in place as read-only Parquet inputs;
raw server payloads and derived data must not be copied into cloud-synchronised storage
without a separately approved, scoped operation. The scripts reject existing research outputs.
Use a new directory for reproduction rather than deleting old artifacts.

## Formula and source semantics

Existing main TimeframeFeatureEngine formulas are reused with an explicit warmup/window policy.
Incomplete or invalid bars reset the window; unknown volume does not compress its sequence.
No contract, source scope or indicator formula is silently mixed. VWAP20 is explicitly a candle
approximation. Wilder ATR, median volume and confirmed HH/HL from other open PRs are not
reimplemented or claimed installed. A deterministic indicator hash excludes batch calculation
time, and includes source identities/windows, formula, numerical values and missingness status.
New batch timestamps are calculation/finalization clocks, never historical receipt evidence.

FUTOI is underlying-wide; preserve the provider ticker, not the storage alias (GD versus GOLD).
Signed long/short fields remain signed. Partial public-trade aggregates are not ALGOPACK history.
Exact observed trade-price bins and VWAP are exact only for the captured subset. Si logical
session times and actual calendar fields stay distinct. A first-touch outcome, training run,
backtest, forecast record or new scheduler is outside this pipeline.

## Production gate

First finish and backread the isolated SBER pilot; then obtain the separate merge/deploy/restart
and data-transfer decision required by the owner. Install the compatible writer before changing
physical candle headers. Take fresh canonical backups, recompute plans, apply one book at a time,
backread full data, repeat with zero additions, and verify the actual existing scheduled cycle.
No script in this change activates that cycle or changes the live DuckDB.
