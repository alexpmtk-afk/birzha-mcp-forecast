"""Run M23 validation against provisioned YDB with D1-only durable history.

This wrapper keeps governed validation semantics unchanged while replacing
recurring schema-creating storage classes with runtime no-DDL adapters. The
runtime candle adapter also fail-closes any attempted H1/M15 persistence.
"""

from __future__ import annotations

import run_authorized_ydb_validation as implementation

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
