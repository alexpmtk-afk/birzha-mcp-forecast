"""YDB orchestration store for long-lived runtime paths.

The base YdbOrchestrationStore bootstraps its tables for provisioning/smoke.
Server processes and scheduled workers must not execute schema DDL on every cold start.
"""

from __future__ import annotations

from birzha.storage.ydb_orchestration_store import YdbOrchestrationStore


class YdbRuntimeOrchestrationStore(YdbOrchestrationStore):
    """Open an already-provisioned orchestration schema without issuing DDL."""

    def _init_schema(self) -> None:
        return None
