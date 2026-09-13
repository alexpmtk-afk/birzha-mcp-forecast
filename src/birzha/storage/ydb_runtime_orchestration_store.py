"""YDB orchestration store for long-lived runtime paths.

The base :class:`YdbOrchestrationStore` deliberately bootstraps its tables for
one-shot provisioning and real-YDB smoke setup. Server processes and scheduled
workers must not execute schema DDL on every cold start, so this runtime variant
suppresses only the bootstrap hook and otherwise keeps the exact same durable
store implementation.

The orchestration schema must be provisioned before this store is used.
"""

from __future__ import annotations

from birzha.storage.ydb_orchestration_store import YdbOrchestrationStore


class YdbRuntimeOrchestrationStore(YdbOrchestrationStore):
    """Open an already-provisioned orchestration schema without issuing DDL."""

    def _init_schema(self) -> None:
        # YdbOrchestrationStore.__init__ calls this hook. Runtime cold starts
        # intentionally do nothing here: schema lifecycle belongs to bootstrap,
        # not to the request/timer execution path.
        return None
