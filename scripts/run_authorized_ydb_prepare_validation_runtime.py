"""Compatibility entrypoint for governed M23 D1-only data preparation.

The implementation itself now uses no-DDL runtime YDB adapters and cannot
persist H1/M15 price history.
"""

from __future__ import annotations

import run_authorized_ydb_prepare_validation as implementation


def main() -> int:
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
