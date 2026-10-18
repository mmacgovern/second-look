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
    cached_input_per_1m: float | None = None  # USD per 1M cached input tokens (None = same as input)


# OpenAI standard (list) prices per 1M tokens for prompts under 272K tokens.
# Sources: MGT 409 Lecture 11 course price table (hw6/data/prices.json) for
# gpt-6-luna; genai-prices 0.1.9, citing https://developers.openai.com/api/docs/models,
# for all three. Cached-input discounts are ignored, so recorded costs are an upper bound.
PRICES: dict[str, ModelPrice] = {
    "gpt-6-luna": ModelPrice(input_per_1m=0.10, output_per_1m=0.50, cached_input_per_1m=0.01),  # course default
    "gpt-6-sol": ModelPrice(input_per_1m=2.00, output_per_1m=10.00, cached_input_per_1m=0.20),   # complex work
    "gpt-6-astra": ModelPrice(input_per_1m=10.00, output_per_1m=50.00, cached_input_per_1m=1.00),  # most capable; exceeds the
    # default per-call and session limits for the explainer, so raise them before using it
}

# Demo mode replays recorded answers and costs nothing.
DEMO_MODEL = "demo"
PRICES[DEMO_MODEL] = ModelPrice(input_per_1m=0.0, output_per_1m=0.0)

# Used for any model missing from PRICES: above the most expensive listed
# model, so a mistyped or new model name is budgeted conservatively.
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


def cost_usd(model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
    """Cost of one call. Cached input tokens (part of input_tokens) are billed at the cached rate."""
    price = price_for(model)
    cached = max(0, min(int(cached_input_tokens), int(input_tokens)))
    cached_rate = price.input_per_1m if price.cached_input_per_1m is None else price.cached_input_per_1m
    return ((input_tokens - cached) * price.input_per_1m + cached * cached_rate
            + output_tokens * price.output_per_1m) / 1_000_000


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a ledger was created, keeping every existing row."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(calls)")}
    if "cached_input_tokens" not in columns:
        conn.execute("ALTER TABLE calls ADD COLUMN cached_input_tokens INTEGER NOT NULL DEFAULT 0")


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
            cache_hit INTEGER NOT NULL,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0
        )"""
    )
    _migrate(conn)
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


def check_run_budget(spent_usd: float, worst_case_usd: float, max_usd: float) -> None:
    """For scripts that make many calls: raise BudgetExceeded if the next step could push a run past max_usd."""
    if spent_usd + worst_case_usd > max_usd:
        raise BudgetExceeded("run", worst_case_usd, max_usd - spent_usd)


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
    cached_input_tokens: int = 0,
    db_path: Path | None = None,
) -> float:
    """Write one ledger row from the response's actual usage; return the cost charged."""
    cost = cost_usd(model, input_tokens, output_tokens, cached_input_tokens) if billable and not cache_hit else 0.0
    with _ledger(db_path) as conn:
        conn.execute(
            """INSERT INTO calls (timestamp, session_id, agent, model, input_tokens,
                   output_tokens, cost_usd, latency_s, cache_hit, cached_input_tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                int(cached_input_tokens or 0),
            ),
        )
    return cost


def calls(db_path: Path | None = None, session_prefix: str | None = None) -> list[dict]:
    """Raw ledger rows (oldest first), optionally only sessions whose id starts with session_prefix."""
    where, params = ("WHERE substr(session_id, 1, ?) = ?", (len(session_prefix), session_prefix)) if session_prefix else ("", ())
    with _ledger(db_path) as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM calls {where} ORDER BY id", params)]


def summary(db_path: Path | None = None, session_id: str | None = None, session_prefix: str | None = None) -> dict:
    """Cost per agent and per session, for the dashboard (optionally one session, or all
    sessions whose id starts with session_prefix)."""
    if session_id:
        where, params = "WHERE session_id = ?", (session_id,)
    elif session_prefix:
        # exact prefix match (no LIKE wildcards to escape)
        where, params = "WHERE substr(session_id, 1, ?) = ?", (len(session_prefix), session_prefix)
    else:
        where, params = "", ()
    group_sql = """SELECT {key} AS name, COUNT(*) AS calls,
                          SUM(cache_hit) AS cache_hits,
                          SUM(input_tokens) AS input_tokens,
                          SUM(cached_input_tokens) AS cached_input_tokens,
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
