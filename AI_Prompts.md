# Second Look — AI Prompt Log

**Purpose:** log of every AI prompt used to build Second Look, for the course report.

**How this file is maintained:**
- Prompts typed into Claude Code are logged automatically by a Claude Code hook (`.claude/hooks/log_prompt.py`), appended at the bottom under "Claude Code prompts". Secrets are redacted by pattern matching before anything is written.
- Prompts used in other tools (Claude chat, ChatGPT, etc.) are added by hand under "Prompts outside Claude Code".
- Earlier entries are never edited or deleted; each completed task adds one `**Result:**` line under its latest entry.

## Prompts outside Claude Code

_Add these by hand: tool, date, and the prompt text._

## Claude Code prompts

### #1 — 2026-10-17 17:19 — Phase 3 — Config, LLM wrapper and cost harness

```text
Prompt 2 — Config, LLM wrapper and cost harness
Build secondlook/config.py, secondlook/budget.py and secondlook/llm.py.

config.py: load .env with python-dotenv, validate with pydantic. If PORTKEY_API_KEY is missing, set demo mode automatically. Provide a redact() helper and a logging.Filter that replaces the key's value with [REDACTED] in all log records. Never expose the key in repr/str of the settings object.

budget.py: a cost harness.
- PRICES dict: model name -> USD per 1M input and output tokens (leave placeholder numbers with a TODO; I'll fill them in).
- A SQLite ledger at .runtime/ledger.db recording timestamp, session_id, agent name, model, input tokens, output tokens, cost, latency, cache hit.
- check_before_call(session_id, est_input_tokens, max_output_tokens, model) raises BudgetExceeded if the call's worst-case cost would exceed MAX per call, SESSION_BUDGET_USD, or DAILY_BUDGET_USD.
- record_after_call() using actual usage from the response.
- A summary function returning cost per agent and per session for a dashboard.

llm.py: the only module that calls Portkey. One function call_agent(agent_name, model, system, messages, max_tokens, response_schema=None). It must: call budget.check_before_call, set max_tokens and a 30s timeout, retry at most once, call budget.record_after_call, cache responses keyed on a hash of (model, system, messages), and in demo mode return recorded responses from demo/recorded/ instead of calling the API. Raise a generic error message on failure; log details through the redacting filter.

Write tests: a fake client proving the budget blocks a call over each limit, the cache returns without a second call, and the key never appears in logs or exception text.
Check: pytest passes, including a test that forces each budget limit.
```

**Result:** Built secondlook/config.py (pydantic settings, auto demo mode, redact() and a redacting log filter), budget.py (price table, SQLite ledger, per-call/session/daily limits, summary) and llm.py (budgeted, cached, single-retry Portkey wrapper with demo replay); all 52 pytest tests passed.
