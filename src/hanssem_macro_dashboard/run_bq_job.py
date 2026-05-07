from __future__ import annotations

from hanssem_macro_dashboard.bq import initialize_bigquery_datamart
from hanssem_macro_dashboard.pipeline import run_bigquery


def main() -> int:
    initialize_bigquery_datamart()
    return run_bigquery()


if __name__ == "__main__":
    raise SystemExit(main())
