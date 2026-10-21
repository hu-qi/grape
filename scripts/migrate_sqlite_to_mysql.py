"""Copy a stopped grape SQLite database into an initialized MySQL schema.

Run with DEVOPS_DB_BACKEND=mysql and DEVOPS_DB_HOST/PORT/USER/PASSWORD/NAME
after creating the MySQL tables with src.api.db_setup.init_database(). The
source file must be an online SQLite backup taken after application writes stop.
"""

from __future__ import annotations

import argparse
import re
import sqlite3

import pymysql

from src.mysql_compat import mysql_config


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quoted(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"Unsafe database identifier: {name!r}")
    return f"`{name}`"


def plan_columns(source_columns: list[str], target_columns: list[str]) -> list[str]:
    """Require every source column to exist in the initialized target table."""
    missing = sorted(set(source_columns) - set(target_columns))
    if missing:
        raise ValueError(f"Target schema is missing source columns: {', '.join(missing)}")
    return source_columns


def migrate(source_path: str, *, preflight_only: bool = False) -> dict[str, int]:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite source failed integrity_check: {integrity}")

        cfg = mysql_config()
        target = pymysql.connect(
            host=cfg["host"], port=cfg["port"], user=cfg["user"],
            password=cfg["password"], database=cfg["database"],
            charset="utf8mb4", autocommit=False, connect_timeout=10,
        )
        try:
            tables = [name for (name,) in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            counts: dict[str, int] = {}
            with target.cursor() as cursor:
                # SQLite's alphabetical table order may place parent rows after children.
                # Validate the complete source schema first, then copy in one transaction.
                cursor.execute("SHOW TABLES")
                target_tables = {row[0] for row in cursor.fetchall()}
                missing_tables = sorted(set(tables) - target_tables)
                if missing_tables:
                    raise ValueError("MySQL schema is missing source tables: " + ", ".join(missing_tables))

                # Preflight all columns before changing any target rows.
                columns_by_table: dict[str, list[str]] = {}
                for table in tables:
                    source_columns = [row[1] for row in source.execute(f"PRAGMA table_info({_quoted(table)})")]
                    cursor.execute(f"SHOW COLUMNS FROM {_quoted(table)}")
                    target_columns = [row[0] for row in cursor.fetchall()]
                    columns_by_table[table] = plan_columns(source_columns, target_columns)

                if preflight_only:
                    return {
                        table: source.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0]
                        for table in tables
                    }

                cursor.execute("SET FOREIGN_KEY_CHECKS=0")
                for table in tables:
                    quoted_table = _quoted(table)
                    columns = columns_by_table[table]
                    cursor.execute(f"DELETE FROM {quoted_table}")
                    rows = source.execute(f"SELECT * FROM {quoted_table}")
                    names = ", ".join(_quoted(column) for column in columns)
                    placeholders = ", ".join(["%s"] * len(columns))
                    insert = f"INSERT INTO {quoted_table} ({names}) VALUES ({placeholders})"
                    copied = 0
                    while batch := rows.fetchmany(500):
                        cursor.executemany(insert, batch)
                        copied += len(batch)
                    cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
                    actual = cursor.fetchone()[0]
                    if actual != copied:
                        raise ValueError(f"Row count mismatch for {table}: SQLite={copied}, MySQL={actual}")
                    counts[table] = copied
                cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            target.commit()
            return counts
        except Exception:
            target.rollback()
            raise
        finally:
            target.close()
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Path to a verified SQLite backup")
    parser.add_argument("--preflight", action="store_true", help="Check schema and row counts without writing to MySQL")
    args = parser.parse_args()
    counts = migrate(args.source, preflight_only=args.preflight)
    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
