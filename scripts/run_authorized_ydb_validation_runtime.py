"""Compatibility entrypoint for governed M23 validation.

The implementation itself now uses no-DDL runtime YDB adapters and enforces
D1-only durable price history. H1/M15 remain on-demand inputs.
"""

from __future__ import annotations

import run_authorized_ydb_validation as implementation


def main() -> int:
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
