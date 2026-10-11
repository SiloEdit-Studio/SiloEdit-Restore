import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager


class History:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, created TEXT NOT NULL, source TEXT, destination TEXT, total INTEGER, recovered INTEGER, partial INTEGER, failed INTEGER, report TEXT, rows TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add(self, created, source, destination, rows, report=""):
        counts = {s: sum(r["status"] == s for r in rows) for s in ("valid", "recovered", "low_quality", "partial", "unrecoverable", "error")}
        with self._connect() as db:
            db.execute("INSERT INTO runs(created,source,destination,total,recovered,partial,failed,report,rows) VALUES(?,?,?,?,?,?,?,?,?)",
                       (created, source, destination, len(rows), counts["recovered"] + counts["valid"], counts["partial"], counts["unrecoverable"] + counts["error"], report, json.dumps(rows, ensure_ascii=False)))

    def list_runs(self):
        with self._connect() as db:
            return db.execute("SELECT id,created,source,destination,total,recovered,partial,failed,report FROM runs ORDER BY id DESC").fetchall()
