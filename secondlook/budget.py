"""Cost harness: prices, a SQLite ledger, and budget checks for every LLM call.

llm.call_agent must call check_before_call() before each API attempt and
record_after_call() after each response (including cache hits and demo replays,
which are recorded at zero cost).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from secondlook.config import PROJECT_ROOT, Settings, get_settings, get_logger

LEDGER_PATH = PROJECT_ROOT / ".runtime" / "ledger.db"

log = get_logger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m: float   # USD per 1M input tokens
    output_per_1m: float  # USD per 1M output tokens


# TODO: replace these placeholder names and numbers with the real models and prices.
PRICES: dict[str, ModelPrice] = {
    "TODO-fast-model": ModelPrice(input_per_1m=1.00, output_per_1m=4.00),
    "TODO-smart-model": ModelPrice(input_per_1m=5.00, output_per_1m=20.00),
}

# Used for any model missing from PRICES. Deliberately high so unknown models
# are budgeted conservatively. TODO: revisit once PRICES is filled in.
FALLBACK_PRICE = ModelPrice(input_per_1m=15.00, output_per_1m=75.00)


class BudgetExceeded(Exception):
    """A call was blocked because its worst-case cost would break a limit."""

    def __init__(self, limit: str, worst_case_usd: float, remaining_usd: float) -> None:
        self.limit = limit
        self.worst_case_usd = worst_case_usd
        self.remaining_usd = remaining_usd
        super().__init__(
            f"Blocked by the {limit} limit: this call could cost up to "
            f"${worst_case_usd:.4f} but only ${max(remaining_usd, 0):.4f} remains."
        )


def price_for(model: str) -> ModelPrice:
    price = PRICES.get(model)
    if price is None:
        log.warning("No price for model %r; using the conservative fallback price.", model)
        return FALLBACK_PRICE
    return price


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = price_for(model)
    return (input_tokens * price.input_per_1m + output_tokens * price.output_per_1m) / 1_000_000


@contextmanager
def _ledger(db_path: Path | None):
    """Open the ledger, commit on success, and always close (Windows locks open files)."""
    path = Path(db_path or LEDGER_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            session_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cost_usd REAL NOT NULL,
            latency_s REAL NOT NULL,
            cache_hit INTEGER NOT NULL
        )"""
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _start_of_local_day_utc(now: datetime | None = None) -> str:
    local = (now or datetime.now(timezone.utc)).astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat()


def spent_session(session_id: str, db_path: Path | None = None) -> float:
    with _ledger(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE session_id = ?", (session_id,)
        ).fetchone()
    return float(row[0])


def spent_today(db_path: Path | None = None, now: datetime | None = None) -> float:
    with _ledger(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE timestamp >= ?",
            (_start_of_local_day_utc(now),),
        ).fetchone()
    return float(row[0])


def check_before_call(
    session_id: str,
    est_input_tokens: int,
    max_output_tokens: int,
    model: str,
    *,
    settings: Settings | None = None,
    db_path: Path | None = None,
) -> float:
    """Raise BudgetExceeded if the worst case breaks a limit; else return the worst-case cost."""
    s = settings or get_settings()
    worst = cost_usd(model, est_input_tokens, max_output_tokens)

    if max_output_tokens > s.max_tokens_per_call:
        raise BudgetExceeded("per-call token", worst, 0.0)
    if worst > s.max_cost_per_call_usd:
        raise BudgetExceeded("per-call cost", worst, s.max_cost_per_call_usd)

    session_left = s.session_budget_usd - spent_session(session_id, db_path)
    if worst > session_left:
        raise BudgetExceeded("session", worst, session_left)

    daily_left = s.daily_budget_usd - spent_today(db_path)
    if worst > daily_left:
        raise BudgetExceeded("daily", worst, daily_left)

    return worst


def record_after_call(
    session_id: str,
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_s: float,
    cache_hit: bool = False,
    *,
    billable: bool = True,
    db_path: Path | None = None,
) -> float:
    """Write one ledger row from the response's actual usage; return the cost charged."""
    cost = cost_usd(model, input_tokens, output_tokens) if billable and not cache_hit else 0.0
    with _ledger(db_path) as conn:
        conn.execute(
            """INSERT INTO calls (timestamp, session_id, agent, model, input_tokens,
                   output_tokens, cost_usd, latency_s, cache_hit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                session_id,
                agent,
                model,
                int(input_tokens),
                int(output_tokens),
                cost,
                float(latency_s),
                int(bool(cache_hit)),
            ),
        )
    return cost


def summary(db_path: Path | None = None, session_id: str | None = None) -> dict:
    """Cost per agent and per session, for the dashboard."""
    where, params = ("WHERE session_id = ?", (session_id,)) if session_id else ("", ())
    group_sql = """SELECT {key} AS name, COUNT(*) AS calls,
                          SUM(cache_hit) AS cache_hits,
                          SUM(input_tokens) AS input_tokens,
                          SUM(output_tokens) AS output_tokens,
                          ROUND(SUM(cost_usd), 6) AS cost_usd,
                          ROUND(AVG(latency_s), 3) AS avg_latency_s
                   FROM calls {where} GROUP BY {key} ORDER BY cost_usd DESC"""
    with _ledger(db_path) as conn:
        by_agent = [dict(r) for r in conn.execute(group_sql.format(key="agent", where=where), params)]
        by_session = [dict(r) for r in conn.execute(group_sql.format(key="session_id", where=where), params)]
        total = conn.execute(f"SELECT COALESCE(SUM(cost_usd), 0) FROM calls {where}", params).fetchone()[0]
    return {
        "total_usd": round(float(total), 6),
        "today_usd": round(spent_today(db_path), 6),
        "by_agent": by_agent,
        "by_session": by_session,
    }
