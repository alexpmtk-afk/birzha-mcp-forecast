"""Run governed M23 data preparation against an already-provisioned YDB schema.

The legacy preparation module keeps the schema-provisioning store classes for
bootstrap compatibility. This runtime entry point swaps only those constructor
bindings for no-DDL variants before executing the unchanged governed logic.
"""

from __future__ import annotations

import run_authorized_ydb_prepare_validation as implementation

from birzha.storage.ydb_runtime_storage import (
    YdbRuntimeHistoricalCandleStore,
    YdbRuntimeHistoricalFlowStore,
    YdbRuntimeSlotPacingGate,
)


def main() -> int:
    implementation.YdbSlotPacingGate = YdbRuntimeSlotPacingGate
    implementation.YdbHistoricalCandleStore = YdbRuntimeHistoricalCandleStore
    implementation.YdbHistoricalFlowStore = YdbRuntimeHistoricalFlowStore
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
