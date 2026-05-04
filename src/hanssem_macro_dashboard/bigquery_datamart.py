from __future__ import annotations

import argparse

from hanssem_macro_dashboard.bq import initialize_bigquery_datamart, sync_bigquery_tables


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize and sync the Hanssem macro datamart in BigQuery.")
    parser.add_argument("command", choices=["init", "sync"], help="init schema/views or sync local data to BigQuery")
    args = parser.parse_args()

    if args.command == "init":
        initialize_bigquery_datamart()
        print("Initialized BigQuery dataset, tables, and views.")
        return 0

    counts = sync_bigquery_tables()
    print("Synced BigQuery tables:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
