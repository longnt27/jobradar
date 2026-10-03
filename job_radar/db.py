from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]',
  career_url TEXT,
  coverage_status TEXT NOT NULL DEFAULT 'source_discovery',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('linkedin','facebook','career')),
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  employer_id TEXT REFERENCES employers(id),
  enabled INTEGER NOT NULL DEFAULT 1,
  interval_minutes INTEGER NOT NULL DEFAULT 240,
  config TEXT NOT NULL DEFAULT '{}',
  last_attempt_at TEXT,
  last_success_at TEXT,
  last_status TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_runs (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  observed_count INTEGER NOT NULL DEFAULT 0,
  new_count INTEGER NOT NULL DEFAULT 0,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_scan_source ON scan_runs(source_id, started_at DESC);
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  external_id TEXT,
  url TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  payload TEXT NOT NULL,
  published_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  UNIQUE(source_id, url)
);
CREATE INDEX IF NOT EXISTS idx_observations_external ON observations(source_id, external_id);
CREATE TABLE IF NOT EXISTS vacancies (
  id TEXT PRIMARY KEY,
  employer_id TEXT REFERENCES employers(id),
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  location TEXT,
  work_mode TEXT,
  description TEXT NOT NULL,
  apply_url TEXT,
  published_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  score INTEGER,
  score_detail TEXT,
  analysis_status TEXT NOT NULL DEFAULT 'not_configured',
  analysis_model TEXT,
  analysis_error TEXT,
  analyzed_at TEXT,
  state TEXT NOT NULL DEFAULT 'new',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vacancies_rank ON vacancies(score DESC, first_seen_at DESC);
CREATE TABLE IF NOT EXISTS vacancy_observations (
  vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
  observation_id TEXT NOT NULL UNIQUE REFERENCES observations(id),
  merge_reason TEXT NOT NULL,
  PRIMARY KEY(vacancy_id, observation_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS vacancy_fts USING fts5(vacancy_id UNINDEXED, title, company, description);
CREATE TABLE IF NOT EXISTS repository_snapshots (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  local_path TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  owner_context TEXT NOT NULL DEFAULT 'unknown',
  summary TEXT,
  inspected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  claim TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '{}',
  support TEXT NOT NULL DEFAULT '[]',
  repository_id TEXT REFERENCES repository_snapshots(id),
  approved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_drafts (
  id TEXT PRIMARY KEY,
  vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
  status TEXT NOT NULL DEFAULT 'draft',
  provider TEXT NOT NULL,
  provider_mode TEXT NOT NULL,
  evidence_ids TEXT NOT NULL,
  resume_data TEXT NOT NULL,
  message_data TEXT NOT NULL,
  form_data TEXT NOT NULL,
  destination TEXT NOT NULL,
  resume_path TEXT,
  resume_hash TEXT,
  warnings TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL REFERENCES application_drafts(id),
  vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
  package_hash TEXT NOT NULL,
  package_data TEXT NOT NULL DEFAULT '{}',
  destination TEXT NOT NULL,
  status TEXT NOT NULL,
  receipt TEXT,
  error TEXT,
  sent_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submission_vacancy ON submissions(vacancy_id, sent_at DESC);
CREATE TABLE IF NOT EXISTS auto_application_attempts (
  vacancy_id TEXT PRIMARY KEY REFERENCES vacancies(id),
  status TEXT NOT NULL,
  draft_id TEXT REFERENCES application_drafts(id),
  detail TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_application_status ON auto_application_attempts(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS notification_attempts (
  vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
  channel TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_attempt_at TEXT,
  sent_at TEXT,
  PRIMARY KEY(vacancy_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_notification_pending ON notification_attempts(channel, status, last_attempt_at);
CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY,
  vacancy_id TEXT NOT NULL REFERENCES vacancies(id),
  state TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(submissions)")}
            if "package_data" not in columns:
                conn.execute("ALTER TABLE submissions ADD COLUMN package_data TEXT NOT NULL DEFAULT '{}'")
            vacancy_columns = {row[1] for row in conn.execute("PRAGMA table_info(vacancies)")}
            for name, definition in (
                ("analysis_status", "TEXT NOT NULL DEFAULT 'not_configured'"),
                ("analysis_model", "TEXT"),
                ("analysis_error", "TEXT"),
                ("analyzed_at", "TEXT"),
            ):
                if name not in vacancy_columns:
                    conn.execute(f"ALTER TABLE vacancies ADD COLUMN {name} {definition}")
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.connection() as conn:
            conn.execute(sql, params)

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
