"""Cancel the accidental October 2026 bulk match re-review.

Restore cached results only when the saved analysis timestamp and model match
the pre-reset snapshot. Defer the remaining previously analyzed jobs so they
cannot consume the normal queue for newly found jobs.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from job_radar.settings import Settings


def plan(conn: sqlite3.Connection) -> tuple[list[tuple], list[tuple[str]]]:
    previous = {
        row[0]: row[1:]
        for row in conn.execute(
            "SELECT id,score,score_detail,analysis_model,analyzed_at "
            "FROM previous.vacancies WHERE analysis_status='done'"
        )
    }
    restore: list[tuple] = []
    defer: list[tuple[str]] = []
    for job_id, model, analyzed_at in conn.execute(
        "SELECT id,analysis_model,analyzed_at FROM vacancies "
        "WHERE analysis_status IN ('pending','running') AND analyzed_at IS NOT NULL"
    ):
        old = previous.get(job_id)
        if old and old[2] == model and old[3] == analyzed_at and old[1]:
            restore.append((old[0], old[1], job_id))
        else:
            defer.append((job_id,))
    return restore, defer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--snapshot", type=Path, help="Safety copy of the live database before writing")
    args = parser.parse_args()
    db_path = Settings.from_env().database_path
    previous = args.previous.expanduser().resolve()
    if args.apply:
        if args.snapshot is None:
            parser.error("--apply requires --snapshot")
        snapshot = args.snapshot.expanduser().resolve()
        if snapshot.exists():
            parser.error(f"snapshot already exists: {snapshot}")
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as source:
            with sqlite3.connect(snapshot) as destination:
                source.backup(destination)
        print(f"Safety snapshot: {snapshot}")

    connection_target = db_path if args.apply else f"file:{db_path}?mode=ro"
    with sqlite3.connect(connection_target, timeout=30, uri=True) as conn:
        conn.execute("ATTACH DATABASE ? AS previous", (f"file:{previous}?immutable=1",))
        if args.apply:
            conn.execute("BEGIN IMMEDIATE")
        restore, defer = plan(conn)
        print(f"Restore cached matches: {len(restore)}; defer unavailable cached matches: {len(defer)}")
        if args.apply:
            conn.executemany(
                "UPDATE vacancies SET score=?,score_detail=?,analysis_status='done',"
                "analysis_stage=NULL,analysis_error=NULL WHERE id=? "
                "AND analysis_status IN ('pending','running')",
                restore,
            )
            conn.executemany(
                "UPDATE vacancies SET analysis_status='deferred',analysis_stage=NULL,"
                "analysis_error=NULL WHERE id=? AND analysis_status IN ('pending','running')",
                defer,
            )
            conn.commit()
            print("Bulk re-review canceled; genuinely new jobs remain queued.")


if __name__ == "__main__":
    main()
