# 💰 expense-mcp

> Personal Expense Tracker as an MCP Server — works with Claude Desktop, Cursor, nanobot, Windsurf, and any MCP-compatible client.

[![Python](https://img.shields.io/badge/python-≥3.12-blue)](https://python.org)
[![MCP SDK](https://img.shields.io/badge/MCP_SDK-1.26.0-green)](https://github.com/modelcontextprotocol/python-sdk)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<!-- mcp-name: io.github.justfsl50/expense-mcp -->
---

## Features

- 📝 **Track expenses & income** with categories and descriptions
- 📊 **Spending summaries** — today, week, month, year
- 💳 **Budget management** — set limits per category, get alerts at 80%/100%
- 🎯 **Savings goals** — create goals, track progress with visual bars
- 📈 **Spending insights** — top categories, daily averages, biggest days
- 🗑️ **Safe deletion** — Pydantic-based elicitation for confirmation
- 🔄 **Dual transport** — stdio (local) + streamable HTTP (remote)

---

## Install

```bash
# with uv (recommended)
uv pip install git+https://github.com/justfsl50/expense-mcp.git

# with pip
pip install git+https://github.com/justfsl50/expense-mcp.git

# from source
git clone https://github.com/justfsl50/expense-mcp.git
cd expense-mcp
pip install -e .
```

---

## Quick Start

### Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac):

```json
{
  "mcpServers": {
    "expense-mcp": {
      "command": "uv",
      "args": ["run", "expense-mcp"],
      "env": {
        "DATABASE_URL": "sqlite:///expenses.db",
        "CURRENCY": "₹",
        "DEFAULT_USER": "me"
      }
    }
  }
}
```

### Cursor / Windsurf

Same config — paste into MCP settings under the respective app.

### nanobot

```json
{
  "mcp": {
    "servers": [{
      "name": "expense-mcp",
      "command": "uv run expense-mcp"
    }]
  }
}
```

### HTTP mode (remote / multi-client)

```bash
python server.py http
# Server runs at http://127.0.0.1:8000/mcp
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///expenses.db` | SQLite or PostgreSQL URL |
| `CURRENCY` | `₹` | Currency symbol |
| `DEFAULT_USER` | `default` | User ID for multi-user setups |

PostgreSQL example:
```
DATABASE_URL=postgresql://user:pass@localhost:5432/expenses
```

---

## Tools

| Tool | Description | Read-only |
|---|---|---|
| `expense_add` | Save expense or income | ❌ |
| `expense_search` | Filter by text, date, category, amount | ✅ |
| `expense_summary` | today / week / month / year totals | ✅ |
| `expense_delete` | Delete with Pydantic confirmation prompt | ❌ |
| `expense_insights` | Spending patterns and top categories | ✅ |
| `budget_set` | Set monthly category budget | ❌ |
| `budget_list` | View budgets with usage % | ✅ |
| `goal_create` | Create savings goal | ❌ |
| `goal_update` | Add money toward goal | ❌ |
| `goal_list` | View goals with progress bars | ✅ |

## Resources

| URI | Description |
|---|---|
| `expense://summary/month` | Current month summary |
| `expense://budgets/current` | This month's budgets |
| `expense://goals/all` | All savings goals |

## Prompts

| Prompt | Title | Description |
|---|---|---|
| `monthly_review` | Monthly Review | Start a full month spending review |
| `budget_setup` | Budget Setup | Auto-suggest budgets from history |
| `savings_plan` | Savings Plan | Create a plan for a savings goal |

---

## Usage Examples

Just talk naturally in any MCP client:

```
"spent 500 on groceries"
"show food expenses this week"
"how much did I spend last month?"
"set food budget to 5000"
"am I within budget?"
"save 1000 toward my iPhone goal"
"give me spending insights"
"delete expense #12"
```

---

## Architecture

- **MCP SDK** v1.26.0 with `FastMCP` + `json_response=True`
- **Typed lifespan** — DB engine managed via `AppContext` dataclass
- **SQLAlchemy 2.0** — `DeclarativeBase`, `sessionmaker`
- **Pydantic v2** — input validation, elicitation schemas
- **Tool annotations** — `readOnlyHint`, `destructiveHint`, `idempotentHint`
- **Context logging** — `ctx.info()`, `ctx.warning()` in tools

### Database Schema

```
expenses  — id, user_id, amount, category, description, type, date, source, created_at
budgets   — id, user_id, category, amount, month
goals     — id, user_id, name, target, saved, deadline
```

---

## License

MIT — free to use, modify, and distribute.
