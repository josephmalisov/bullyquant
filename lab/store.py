"""SQLite persistence for campaigns, generations, validations, and token usage.

State is written every step so a crashed or restarted campaign can be inspected
(and, later, resumed) from `data/bullyquant.db`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    objective     TEXT NOT NULL,
    params        TEXT,
    status        TEXT DEFAULT 'running',
    best_score    REAL,
    best_gen_id   INTEGER,
    created_at    REAL,
    updated_at    REAL
);
CREATE TABLE IF NOT EXISTS generations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   INTEGER NOT NULL,
    gen_number    INTEGER NOT NULL,
    parent_id     INTEGER,
    lineage       INTEGER,
    name          TEXT,
    hypothesis    TEXT,
    code          TEXT,
    project_id    INTEGER,
    backtest_id   TEXT,
    url           TEXT,
    status        TEXT DEFAULT 'pending',
    error         TEXT,
    stats         TEXT,
    score         REAL,
    score_breakdown TEXT,
    validation    TEXT,
    analysis      TEXT,
    created_at    REAL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   INTEGER,
    generation_id INTEGER,
    tier          TEXT,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    created_at    REAL
);
"""


def _dumps(obj) -> str | None:
    return None if obj is None else json.dumps(obj, default=str)


def _loads(s):
    return None if s is None else json.loads(s)


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── campaigns ─────────────────────────────────────────────────────────────

    def create_campaign(self, objective: str, params: dict | None = None) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO campaigns (objective, params, status, created_at, updated_at) "
            "VALUES (?, ?, 'running', ?, ?)",
            (objective, _dumps(params or {}), now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_campaign(self, campaign_id: int, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE campaigns SET {cols} WHERE id = ?",
                          (*fields.values(), campaign_id))
        self.conn.commit()

    def get_campaign(self, campaign_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["params"] = _loads(d["params"])
        return d

    # ── generations ───────────────────────────────────────────────────────────

    def add_generation(self, campaign_id: int, gen_number: int, *, parent_id=None,
                       lineage=None, name="", hypothesis="", code="") -> int:
        cur = self.conn.execute(
            "INSERT INTO generations (campaign_id, gen_number, parent_id, lineage, name, "
            "hypothesis, code, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (campaign_id, gen_number, parent_id, lineage, name, hypothesis, code, time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_generation(self, gen_id: int, **fields) -> None:
        if not fields:
            return
        for jkey in ("stats", "score_breakdown", "validation", "analysis"):
            if jkey in fields and not isinstance(fields[jkey], (str, type(None))):
                fields[jkey] = _dumps(fields[jkey])
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE generations SET {cols} WHERE id = ?",
                          (*fields.values(), gen_id))
        self.conn.commit()

    def get_generation(self, gen_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM generations WHERE id = ?", (gen_id,)).fetchone()
        return self._gen_row(row)

    def get_generations(self, campaign_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM generations WHERE campaign_id = ? ORDER BY gen_number", (campaign_id,)
        ).fetchall()
        return [self._gen_row(r) for r in rows]

    def leaderboard(self, campaign_id: int, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM generations WHERE campaign_id = ? AND score IS NOT NULL "
            "ORDER BY score DESC LIMIT ?", (campaign_id, limit),
        ).fetchall()
        return [self._gen_row(r) for r in rows]

    @staticmethod
    def _gen_row(row) -> dict | None:
        if not row:
            return None
        d = dict(row)
        for jkey in ("stats", "score_breakdown", "validation", "analysis"):
            d[jkey] = _loads(d.get(jkey))
        return d

    # ── usage ─────────────────────────────────────────────────────────────────

    def record_usage(self, campaign_id, generation_id, tier, model,
                     input_tokens, output_tokens) -> None:
        self.conn.execute(
            "INSERT INTO usage (campaign_id, generation_id, tier, model, input_tokens, "
            "output_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, generation_id, tier, model, input_tokens, output_tokens, time.time()),
        )
        self.conn.commit()

    def usage_totals(self, campaign_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT model, tier, SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens, COUNT(*) AS calls "
            "FROM usage WHERE campaign_id = ? GROUP BY model, tier ORDER BY model",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]
