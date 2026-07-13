"""SQLite-backed job state for coloring book generation jobs."""

import json
import sqlite3
import time
import uuid
from pathlib import Path


def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        theme TEXT NOT NULL DEFAULT '',
        style TEXT NOT NULL DEFAULT '',
        keyword TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        page_count INTEGER NOT NULL,
        trim_size TEXT NOT NULL,
        task_id TEXT NOT NULL DEFAULT '',
        callback_url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        completed INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL,
        thumbnails TEXT NOT NULL DEFAULT '[]',
        output_path TEXT,
        error TEXT,
        created_at REAL NOT NULL
    )
"""


class JobStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            if not cols:
                conn.execute(SCHEMA)
            elif "topic" not in cols:
                # One-time migration from the pre-redesign schema (theme/style/
                # num_designs/page_size). Only ever held disposable smoke-test jobs.
                conn.execute("DROP TABLE jobs")
                conn.execute(SCHEMA)
            else:
                for col in ("task_id", "callback_url"):
                    if col not in cols:
                        conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def create_job(
        self, topic, page_count, trim_size, theme="", style="", keyword="", title="",
        task_id="", callback_url="",
    ):
        job_id = uuid.uuid4().hex[:12]
        design_count = page_count // 2
        with _connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, topic, theme, style, keyword, title, page_count, trim_size,
                    task_id, callback_url, status, total, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (job_id, topic, theme, style, keyword, title, page_count, trim_size,
                 task_id, callback_url, design_count, time.time()),
            )
            conn.commit()
        return job_id

    def get_job(self, job_id):
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["thumbnails"] = json.loads(job["thumbnails"] or "[]")
        return job

    def update_job(self, job_id, **fields):
        if not fields:
            return
        if "thumbnails" in fields:
            fields["thumbnails"] = json.dumps(fields["thumbnails"])
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        with _connect(self.db_path) as conn:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", values)
            conn.commit()

    def add_thumbnail(self, job_id, filename):
        job = self.get_job(job_id)
        if job is None:
            return
        thumbs = job["thumbnails"] + [filename]
        self.update_job(job_id, thumbnails=thumbs, completed=len(thumbs))
