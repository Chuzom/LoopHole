"""SQLite persistence (WAL mode).

Per the council's revision (critique #2): tables are the SOURCE OF TRUTH; the
``events`` table is an append-only audit/debug log, NOT something we rebuild
state from. Resume reads the tables directly.

A single connection guarded by a lock is used. The control loop is the only
writer of state rows; executors return results that the loop persists. This
sidesteps the aiosqlite concurrent-write hazard entirely.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id            TEXT PRIMARY KEY,
    contract      TEXT NOT NULL,           -- JSON GoalContract
    status        TEXT NOT NULL DEFAULT 'running',  -- running|done|paused|failed
    workspace     TEXT NOT NULL,
    base_commit   TEXT,                    -- integration HEAD when run started
    detail        TEXT,                    -- why the run ended (paused/failed reason)
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    goal_id      TEXT NOT NULL REFERENCES goals(id),
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|ready|running|done|failed|abandoned
    depends_on   TEXT NOT NULL DEFAULT '[]',       -- JSON array of task ids
    reads        TEXT NOT NULL DEFAULT '[]',       -- JSON array of globs
    writes       TEXT NOT NULL DEFAULT '[]',       -- JSON array of globs
    result       TEXT,
    artifact_commit TEXT,                          -- commit produced on success
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    plan_hash    TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id  TEXT,
    task_id  TEXT,
    kind     TEXT NOT NULL,
    payload  TEXT,
    ts       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS verify_cache (
    key      TEXT PRIMARY KEY,        -- tree_sha | base_commit | baseline | verifier_fingerprint
    payload  TEXT NOT NULL,           -- JSON: deterministic verify result
    ts       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id);
CREATE INDEX IF NOT EXISTS idx_events_goal ON events(goal_id);
"""


@dataclass
class Task:
    id: str
    goal_id: str
    description: str
    status: str = "pending"
    depends_on: List[str] = field(default_factory=list)
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)
    result: Optional[str] = None
    artifact_commit: Optional[str] = None
    attempts: int = 0
    last_error: Optional[str] = None
    plan_hash: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        return cls(
            id=row["id"],
            goal_id=row["goal_id"],
            description=row["description"],
            status=row["status"],
            depends_on=json.loads(row["depends_on"]),
            reads=json.loads(row["reads"]),
            writes=json.loads(row["writes"]),
            result=row["result"],
            artifact_commit=row["artifact_commit"],
            attempts=row["attempts"],
            last_error=row["last_error"],
            plan_hash=row["plan_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:12]


class Store:
    """Thread-safe SQLite store. One connection, one lock."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(SCHEMA)
            # migrate pre-existing DBs (CREATE IF NOT EXISTS won't add columns)
            try:
                self._conn.execute("ALTER TABLE goals ADD COLUMN detail TEXT")
            except sqlite3.OperationalError:
                pass                      # column already present
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- events ----------------------------------------------------------
    def log(self, kind: str, goal_id: Optional[str] = None,
            task_id: Optional[str] = None, payload: Any = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(goal_id, task_id, kind, payload, ts) VALUES (?,?,?,?,?)",
                (goal_id, task_id, kind,
                 json.dumps(payload) if payload is not None else None, _now()),
            )
            self._conn.commit()

    def events(self, goal_id: str) -> List[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(
                "SELECT * FROM events WHERE goal_id=? ORDER BY seq", (goal_id,)))

    # ---- verify cache (ARCH-5: persistent so it survives resume; LRU-bounded) ----
    def get_verify_cache(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM verify_cache WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            self._conn.execute("UPDATE verify_cache SET ts=? WHERE key=?", (_now(), key))
            self._conn.commit()
            return row["payload"]

    def put_verify_cache(self, key: str, payload: str, max_entries: int = 50) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO verify_cache(key, payload, ts) VALUES (?,?,?)",
                (key, payload, _now()))
            # LRU eviction: keep only the most-recently-used max_entries rows
            self._conn.execute(
                "DELETE FROM verify_cache WHERE key NOT IN "
                "(SELECT key FROM verify_cache ORDER BY ts DESC LIMIT ?)", (max_entries,))
            self._conn.commit()

    # ---- goals -----------------------------------------------------------
    def create_goal(self, contract_json: str, workspace: str,
                    base_commit: Optional[str] = None) -> str:
        gid = new_id("goal")
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO goals(id, contract, status, workspace, base_commit, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (gid, contract_json, "running", workspace, base_commit, now, now),
            )
            self._conn.commit()
        self.log("goal_created", goal_id=gid, payload={"workspace": workspace})
        return gid

    def get_goal(self, gid: str) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM goals WHERE id=?", (gid,))
            return cur.fetchone()

    def list_goals(self) -> List[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute("SELECT * FROM goals ORDER BY created_at DESC"))

    def set_goal_status(self, gid: str, status: str,
                        detail: Optional[str] = None) -> None:
        with self._lock:
            if detail is not None:
                self._conn.execute(
                    "UPDATE goals SET status=?, detail=?, updated_at=? WHERE id=?",
                    (status, detail, _now(), gid))
            else:
                self._conn.execute(
                    "UPDATE goals SET status=?, updated_at=? WHERE id=?",
                    (status, _now(), gid))
            self._conn.commit()
        payload = {"status": status}
        if detail is not None:
            payload["detail"] = detail
        self.log("goal_status", goal_id=gid, payload=payload)

    def set_goal_base_commit(self, gid: str, commit: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE goals SET base_commit=?, updated_at=? WHERE id=?",
                               (commit, _now(), gid))
            self._conn.commit()

    # ---- tasks -----------------------------------------------------------
    def add_task(self, goal_id: str, description: str,
                 depends_on: Optional[List[str]] = None,
                 reads: Optional[List[str]] = None,
                 writes: Optional[List[str]] = None,
                 plan_hash: Optional[str] = None,
                 task_id: Optional[str] = None) -> str:
        tid = task_id or new_id("task")
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks(id, goal_id, description, status, depends_on, reads, writes,"
                " attempts, plan_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tid, goal_id, description, "pending",
                 json.dumps(depends_on or []), json.dumps(reads or []),
                 json.dumps(writes or []), 0, plan_hash, now, now),
            )
            self._conn.commit()
        self.log("task_created", goal_id=goal_id, task_id=tid,
                 payload={"description": description, "depends_on": depends_on or []})
        return tid

    def get_task(self, tid: str) -> Optional[Task]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return Task.from_row(row) if row else None

    def tasks_for_goal(self, goal_id: str) -> List[Task]:
        with self._lock:
            rows = list(self._conn.execute(
                "SELECT * FROM tasks WHERE goal_id=? ORDER BY created_at", (goal_id,)))
        return [Task.from_row(r) for r in rows]

    _UPDATABLE_TASK_COLS = frozenset({
        "status", "result", "artifact_commit", "attempts", "last_error",
        "plan_hash", "depends_on", "reads", "writes", "description"})

    def update_task(self, tid: str, **fields: Any) -> None:
        if not fields:
            return
        # SEC-5: column names are interpolated into the SQL, so allowlist them —
        # never build SQL from caller-controlled field names.
        bad = set(fields) - self._UPDATABLE_TASK_COLS
        if bad:
            raise ValueError("update_task: unknown column(s): {}".format(sorted(bad)))
        cols = []
        vals: List[Any] = []
        for k, v in fields.items():
            if k in ("depends_on", "reads", "writes"):
                v = json.dumps(v)
            cols.append(k + "=?")
            vals.append(v)
        cols.append("updated_at=?")
        vals.append(_now())
        vals.append(tid)
        with self._lock:
            self._conn.execute("UPDATE tasks SET " + ", ".join(cols) + " WHERE id=?", vals)
            self._conn.commit()

    def set_task_status(self, tid: str, status: str, goal_id: Optional[str] = None) -> None:
        self.update_task(tid, status=status)
        self.log("task_status", goal_id=goal_id, task_id=tid, payload={"status": status})

    def incr_attempts(self, tid: str) -> int:
        with self._lock:
            self._conn.execute("UPDATE tasks SET attempts = attempts + 1, updated_at=? WHERE id=?",
                               (_now(), tid))
            self._conn.commit()
            row = self._conn.execute("SELECT attempts FROM tasks WHERE id=?", (tid,)).fetchone()
        return row["attempts"] if row else 0
