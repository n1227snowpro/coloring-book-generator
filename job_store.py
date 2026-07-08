"""SQLite-backed job state for coloring book generation jobs."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


class JobStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    theme TEXT NOT NULL,
                    num_designs INTEGER NOT NULL,
                    page_size TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    completed INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL,
                    thumbnails TEXT NOT NULL DEFAULT '[]',
                    output_path TEXT,
                    error TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def create_job(self, theme, num_designs, page_size):
        job_id = uuid.uuid4().hex[:12]
        with _connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO jobs (id, theme, num_designs, page_size, status, total, created_at)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
                (job_id, theme, num_designs, page_size, num_designs, time.time()),
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
