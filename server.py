"""
expense_mcp — Personal Expense Tracker MCP Server
Works with Claude Desktop, Cursor, nanobot, Windsurf, any MCP client.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session as OrmSession

# ── Constants ──────────────────────────────────────────────────────────────────
DB_URL   = os.getenv("DATABASE_URL", "sqlite:///expenses.db")
CURRENCY = os.getenv("CURRENCY", "₹")
USER_ID  = os.getenv("DEFAULT_USER", "default")

# ── Database ───────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass

class Expense(Base):
    __tablename__ = "expenses"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(String(50), default=USER_ID, index=True)
    amount      = Column(Float, nullable=False)
    category    = Column(String(50), nullable=False)
    description = Column(String(200), nullable=False)
    type        = Column(String(10), default="expense")   # expense | income
    date        = Column(String(10), nullable=False)       # YYYY-MM-DD
    source      = Column(String(20), default="mcp")
    created_at  = Column(DateTime, default=datetime.now)

class Budget(Base):
    __tablename__ = "budgets"
    id       = Column(Integer, primary_key=True, autoincrement=True)
    user_id  = Column(String(50), default=USER_ID, index=True)
    category = Column(String(50), nullable=False)
    amount   = Column(Float, nullable=False)
    month    = Column(String(7), nullable=False)           # YYYY-MM

class Goal(Base):
    __tablename__ = "goals"
    id       = Column(Integer, primary_key=True, autoincrement=True)
    user_id  = Column(String(50), default=USER_ID, index=True)
    name     = Column(String(100), nullable=False)
    target   = Column(Float, nullable=False)
    saved    = Column(Float, default=0.0)
    deadline = Column(String(10), nullable=False)          # YYYY-MM-DD

# Tables are created during lifespan startup

# ── Enums ──────────────────────────────────────────────────────────────────────
class Category(str, Enum):
    FOOD          = "Food"
    TRANSPORT     = "Transport"
    BILLS         = "Bills"
    SHOPPING      = "Shopping"
    HEALTH        = "Health"
    ENTERTAINMENT = "Entertainment"
    EDUCATION     = "Education"
    INCOME        = "Income"
    OTHER         = "Other"

class TxType(str, Enum):
    EXPENSE = "expense"
    INCOME  = "income"

class Period(str, Enum):
    TODAY = "today"
    WEEK  = "week"
    MONTH = "month"
    YEAR  = "year"

# ── Pydantic Input Models ──────────────────────────────────────────────────────
_cfg = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

class AddExpenseInput(BaseModel):
    model_config = _cfg
    amount:      float    = Field(..., gt=0, description="Amount in INR (must be > 0)")
    category:    Category = Field(..., description="Spending category")
    description: str      = Field(..., min_length=1, max_length=200, description="Brief description")
    type:        TxType   = Field(TxType.EXPENSE, description="'expense' or 'income'")
    date:        Optional[str] = Field(None, description="Date YYYY-MM-DD (defaults to today)")

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD format")
        return v

class SearchInput(BaseModel):
    model_config = _cfg
    query:      Optional[str]   = Field(None, description="Text search in description")
    category:   Optional[Category] = Field(None, description="Filter by category")
    date_from:  Optional[str]   = Field(None, description="Start date YYYY-MM-DD")
    date_to:    Optional[str]   = Field(None, description="End date YYYY-MM-DD")
    min_amount: Optional[float] = Field(None, ge=0, description="Minimum amount")
    max_amount: Optional[float] = Field(None, ge=0, description="Maximum amount")
    type:       Optional[TxType]= Field(None, description="'expense' or 'income'")
    limit:      int             = Field(20, ge=1, le=100, description="Max results")

class BudgetInput(BaseModel):
    model_config = _cfg
    category: Category     = Field(..., description="Spending category")
    amount:   float        = Field(..., gt=0, description="Budget limit in INR")
    month:    Optional[str]= Field(None, description="YYYY-MM (defaults to current month)")

class GoalInput(BaseModel):
    model_config = _cfg
    name:     str   = Field(..., min_length=1, max_length=100, description="Goal name e.g. 'iPhone 15'")
    target:   float = Field(..., gt=0, description="Target amount in INR")
    deadline: str   = Field(..., description="Target date YYYY-MM-DD")

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("deadline must be YYYY-MM-DD format")
        return v

class UpdateGoalInput(BaseModel):
    model_config = _cfg
    name:   str   = Field(..., description="Goal name to update")
    amount: float = Field(..., gt=0, description="Amount to add toward goal")

class DeleteConfirmation(BaseModel):
    """Schema for expense deletion confirmation elicitation."""
    confirm: bool = Field(default=False, description="Confirm deletion")

# ── Helpers ────────────────────────────────────────────────────────────────────
def _date_range(period: Period) -> tuple[str, str]:
    today = datetime.now()
    end   = today.strftime("%Y-%m-%d")
    if period == Period.TODAY:
        return end, end
    if period == Period.WEEK:
        return (today - timedelta(days=7)).strftime("%Y-%m-%d"), end
    if period == Period.YEAR:
        return today.strftime("%Y-01-01"), end
    return today.strftime("%Y-%m-01"), end  # month default

def _fmt(amount: float) -> str:
    return f"{CURRENCY}{amount:,.0f}"

def _budget_alert(db_session: sessionmaker, category: str) -> str:
    month  = datetime.now().strftime("%Y-%m")
    with db_session() as session:
        budget = session.query(Budget).filter_by(user_id=USER_ID, category=category, month=month).first()
        if not budget:
            return ""
        spent = sum(
            e.amount for e in session.query(Expense).filter(
                Expense.user_id == USER_ID,
                Expense.category == category,
                Expense.date.like(f"{month}%"),
                Expense.type == "expense"
            ).all()
        )
        pct = (spent / budget.amount * 100) if budget.amount > 0 else 0
        if pct >= 100:
            return f"\n🚨 {category} budget exceeded! ({_fmt(spent)}/{_fmt(budget.amount)})"
        if pct >= 80:
            return f"\n⚠️  {category} budget at {pct:.0f}% ({_fmt(budget.amount - spent)} left)"
        return ""

# ── Lifespan ───────────────────────────────────────────────────────────────────
@dataclass
class AppContext:
    """Typed lifespan context — holds DB session factory."""
    db_session: sessionmaker

@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage application lifecycle: create engine, yield session factory, dispose on shutdown."""
    engine = create_engine(
        DB_URL,
        connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {},
    )
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    try:
        yield AppContext(db_session=Session)
    finally:
        engine.dispose()

# ── MCP Server ─────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="expense_mcp",
    json_response=True,
    instructions=(
        "Personal expense tracker for Indian users. "
        f"Currency: {CURRENCY}. Track expenses, income, budgets and savings goals. "
        "Always confirm before deleting records."
    ),
    lifespan=lifespan,
)

# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="expense_add",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
)
async def expense_add(params: AddExpenseInput, ctx: Context[ServerSession, AppContext]) -> str:
    """Save a new expense or income transaction.

    Args:
        params: AddExpenseInput with amount, category, description, type, date.

    Returns:
        Confirmation string with saved details and optional budget alert.
    """
    db = ctx.request_context.lifespan_context.db_session
    await ctx.info(f"Adding {params.type.value}: {_fmt(params.amount)} in {params.category.value}")
    with db() as session:
        expense = Expense(
            user_id=USER_ID,
            amount=params.amount,
            category=params.category.value,
            description=params.description,
            type=params.type.value,
            date=params.date or datetime.now().strftime("%Y-%m-%d"),
        )
        session.add(expense)
        session.commit()
        session.refresh(expense)

    alert = _budget_alert(db, params.category.value)
    emoji = "💰" if params.type == TxType.INCOME else "💸"
    return (
        f"{emoji} Saved! #{expense.id}\n"
        f"Amount:   {_fmt(params.amount)}\n"
        f"Category: {params.category.value}\n"
        f"Type:     {params.type.value}\n"
        f"Note:     {params.description}\n"
        f"Date:     {expense.date}"
        f"{alert}"
    )


@mcp.tool(
    name="expense_search",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def expense_search(params: SearchInput, ctx: Context[ServerSession, AppContext]) -> str:
    """Search and filter expenses with flexible criteria.

    Args:
        params: SearchInput with optional query, category, dates, amounts, type, limit.

    Returns:
        Formatted list of matching transactions with total.
    """
    db = ctx.request_context.lifespan_context.db_session
    with db() as session:
        q = session.query(Expense).filter(Expense.user_id == USER_ID)
        if params.query:
            q = q.filter(Expense.description.ilike(f"%{params.query}%"))
        if params.category:
            q = q.filter(Expense.category == params.category.value)
        if params.date_from:
            q = q.filter(Expense.date >= params.date_from)
        if params.date_to:
            q = q.filter(Expense.date <= params.date_to)
        if params.min_amount is not None:
            q = q.filter(Expense.amount >= params.min_amount)
        if params.max_amount is not None:
            q = q.filter(Expense.amount <= params.max_amount)
        if params.type:
            q = q.filter(Expense.type == params.type.value)
        results = q.order_by(Expense.created_at.desc()).limit(params.limit).all()

    if not results:
        return "No expenses found matching your criteria."

    total = sum(r.amount for r in results)
    rows  = "\n".join(
        f"• #{r.id} {r.date} | {_fmt(r.amount)} | {r.category} | {r.description}"
        for r in results
    )
    return f"Found {len(results)} results — Total: {_fmt(total)}\n\n{rows}"


@mcp.tool(
    name="expense_summary",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def expense_summary(
    period: Period = Period.MONTH,
    ctx: Context[ServerSession, AppContext] = None,  # type: ignore[assignment]
) -> str:
    """Get spending summary for a time period.

    Args:
        period: 'today', 'week', 'month', or 'year'.

    Returns:
        Summary with totals, balance, and category breakdown.
    """
    date_from, date_to = _date_range(period)
    labels = {Period.TODAY: "Today", Period.WEEK: "This Week",
              Period.MONTH: "This Month", Period.YEAR: "This Year"}

    db = ctx.request_context.lifespan_context.db_session
    with db() as session:
        exps = session.query(Expense).filter(
            Expense.user_id == USER_ID,
            Expense.date.between(date_from, date_to),
            Expense.type == "expense",
        ).all()
        incs = session.query(Expense).filter(
            Expense.user_id == USER_ID,
            Expense.date.between(date_from, date_to),
            Expense.type == "income",
        ).all()

    total_exp = sum(e.amount for e in exps)
    total_inc = sum(e.amount for e in incs)
    by_cat: dict[str, float] = {}
    for e in exps:
        by_cat[e.category] = by_cat.get(e.category, 0) + e.amount

    cat_lines = "\n".join(
        f"  {k}: {_fmt(v)} ({v / total_exp * 100:.0f}%)"
        for k, v in sorted(by_cat.items(), key=lambda x: -x[1])
    ) if by_cat else "  No expenses"

    return (
        f"📊 {labels[period]}\n\n"
        f"💸 Expenses: {_fmt(total_exp)}\n"
        f"💰 Income:   {_fmt(total_inc)}\n"
        f"💵 Balance:  {_fmt(total_inc - total_exp)}\n\n"
        f"By Category:\n{cat_lines}"
    )


@mcp.tool(
    name="expense_delete",
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
)
async def expense_delete(expense_id: int, ctx: Context[ServerSession, AppContext]) -> str:
    """Permanently delete an expense by ID. Asks for confirmation first.

    Args:
        expense_id: The ID of the expense to delete (from expense_search results).

    Returns:
        Confirmation or cancellation message.
    """
    db = ctx.request_context.lifespan_context.db_session
    with db() as session:
        expense = session.query(Expense).filter_by(id=expense_id, user_id=USER_ID).first()
        if not expense:
            return f"❌ Expense #{expense_id} not found."

        await ctx.warning(f"Requesting confirmation to delete expense #{expense_id}")
        result = await ctx.elicit(
            f"Delete #{expense_id}: {_fmt(expense.amount)} | {expense.category} | {expense.description}?",
            schema=DeleteConfirmation,
        )
        if result.action != "accept" or not result.data or not result.data.confirm:
            return "❌ Deletion cancelled."

        session.delete(expense)
        session.commit()
    return f"🗑️ Deleted expense #{expense_id}"


@mcp.tool(
    name="budget_set",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
)
async def budget_set(params: BudgetInput, ctx: Context[ServerSession, AppContext]) -> str:
    """Set or update a monthly budget for a spending category.

    Args:
        params: BudgetInput with category, amount, and optional month.

    Returns:
        Confirmation of budget set.
    """
    db = ctx.request_context.lifespan_context.db_session
    month = params.month or datetime.now().strftime("%Y-%m")
    with db() as session:
        existing = session.query(Budget).filter_by(
            user_id=USER_ID, category=params.category.value, month=month
        ).first()
        if existing:
            existing.amount = params.amount
        else:
            session.add(Budget(
                user_id=USER_ID, category=params.category.value,
                amount=params.amount, month=month,
            ))
        session.commit()
    return f"✅ Budget set: {params.category.value} = {_fmt(params.amount)} for {month}"


@mcp.tool(
    name="budget_list",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def budget_list(ctx: Context[ServerSession, AppContext]) -> str:
    """Get all budgets and current spending for this month.

    Returns:
        Formatted budget list with usage percentages and status indicators.
    """
    db = ctx.request_context.lifespan_context.db_session
    month = datetime.now().strftime("%Y-%m")
    with db() as session:
        budgets = session.query(Budget).filter_by(user_id=USER_ID, month=month).all()
        if not budgets:
            return "No budgets set this month. Use budget_set to create one."
        lines = []
        for b in budgets:
            spent = sum(
                e.amount for e in session.query(Expense).filter(
                    Expense.user_id == USER_ID,
                    Expense.category == b.category,
                    Expense.date.like(f"{month}%"),
                    Expense.type == "expense",
                ).all()
            )
            pct    = (spent / b.amount * 100) if b.amount > 0 else 0
            status = "🚨" if pct >= 100 else "⚠️ " if pct >= 80 else "✅"
            lines.append(f"{status} {b.category}: {_fmt(spent)}/{_fmt(b.amount)} ({pct:.0f}%)")

    return "📊 Budgets — " + month + "\n\n" + "\n".join(lines)


@mcp.tool(
    name="goal_create",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
)
async def goal_create(params: GoalInput, ctx: Context[ServerSession, AppContext]) -> str:
    """Create a new savings goal.

    Args:
        params: GoalInput with name, target amount, and deadline.

    Returns:
        Confirmation of goal creation.
    """
    db = ctx.request_context.lifespan_context.db_session
    with db() as session:
        session.add(Goal(
            user_id=USER_ID, name=params.name,
            target=params.target, deadline=params.deadline,
        ))
        session.commit()
    return f"🎯 Goal created: {params.name} — {_fmt(params.target)} by {params.deadline}"


@mcp.tool(
    name="goal_update",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
)
async def goal_update(params: UpdateGoalInput, ctx: Context[ServerSession, AppContext]) -> str:
    """Add money toward a savings goal.

    Args:
        params: UpdateGoalInput with goal name and amount to add.

    Returns:
        Updated goal progress.
    """
    db = ctx.request_context.lifespan_context.db_session
    with db() as session:
        goal = session.query(Goal).filter_by(user_id=USER_ID, name=params.name).first()
        if not goal:
            return f"❌ Goal '{params.name}' not found. Use goal_list to see existing goals."
        goal.saved += params.amount
        pct = (goal.saved / goal.target * 100) if goal.target > 0 else 0
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        result = (
            f"✅ {goal.name}\n"
            f"[{bar}] {pct:.1f}%\n"
            f"{_fmt(goal.saved)} / {_fmt(goal.target)} | Due: {goal.deadline}"
        )
        session.commit()
    return result


@mcp.tool(
    name="goal_list",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def goal_list(ctx: Context[ServerSession, AppContext]) -> str:
    """Get all savings goals with progress bars.

    Returns:
        Formatted list of goals with progress and deadline.
    """
    db = ctx.request_context.lifespan_context.db_session
    with db() as session:
        goals = session.query(Goal).filter_by(user_id=USER_ID).all()
        if not goals:
            return "No goals yet. Use goal_create to start saving."
        lines = []
        for g in goals:
            pct = (g.saved / g.target * 100) if g.target > 0 else 0
            bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
            lines.append(f"🎯 {g.name}\n   [{bar}] {pct:.0f}%\n   {_fmt(g.saved)}/{_fmt(g.target)} · {g.deadline}")

    return "\n\n".join(lines)


@mcp.tool(
    name="expense_insights",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)
async def expense_insights(ctx: Context[ServerSession, AppContext]) -> str:
    """Get spending patterns and insights for the current month.

    Returns:
        Key spending metrics: top category, daily average, biggest day, anomalies.
    """
    db = ctx.request_context.lifespan_context.db_session
    month = datetime.now().strftime("%Y-%m")
    with db() as session:
        expenses = session.query(Expense).filter(
            Expense.user_id == USER_ID,
            Expense.date.like(f"{month}%"),
            Expense.type == "expense",
        ).all()

    if not expenses:
        return "No expenses this month yet."

    total   = sum(e.amount for e in expenses)
    by_cat: dict[str, float] = {}
    by_day: dict[str, float] = {}
    for e in expenses:
        by_cat[e.category] = by_cat.get(e.category, 0) + e.amount
        by_day[e.date]     = by_day.get(e.date, 0) + e.amount

    top_cat     = max(by_cat, key=by_cat.get)  # type: ignore
    biggest_day = max(by_day, key=by_day.get)  # type: ignore
    avg_daily   = total / max(len(by_day), 1)
    cat_lines   = "\n".join(f"  {k}: {_fmt(v)}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1]))

    return (
        f"📈 Insights — {month}\n\n"
        f"Total:        {_fmt(total)}\n"
        f"Transactions: {len(expenses)}\n"
        f"Daily avg:    {_fmt(avg_daily)}\n"
        f"Top category: {top_cat} ({_fmt(by_cat[top_cat])})\n"
        f"Biggest day:  {biggest_day} ({_fmt(by_day[biggest_day])})\n\n"
        f"Breakdown:\n{cat_lines}"
    )

# ── Resources ──────────────────────────────────────────────────────────────────
# Resources don't receive tool Context, so they use a lightweight helper.

def _resource_session() -> sessionmaker:
    """Create a one-off session factory for resource handlers."""
    engine = create_engine(
        DB_URL,
        connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {},
    )
    return sessionmaker(bind=engine)

@mcp.resource("expense://summary/month")
async def resource_month_summary() -> str:
    """Current month expense summary."""
    date_from = datetime.now().strftime("%Y-%m-01")
    date_to   = datetime.now().strftime("%Y-%m-%d")
    db = _resource_session()
    with db() as session:
        exps = session.query(Expense).filter(
            Expense.user_id == USER_ID,
            Expense.date.between(date_from, date_to),
            Expense.type == "expense",
        ).all()
        incs = session.query(Expense).filter(
            Expense.user_id == USER_ID,
            Expense.date.between(date_from, date_to),
            Expense.type == "income",
        ).all()
    total_exp = sum(e.amount for e in exps)
    total_inc = sum(e.amount for e in incs)
    by_cat: dict[str, float] = {}
    for e in exps:
        by_cat[e.category] = by_cat.get(e.category, 0) + e.amount
    cat_lines = "\n".join(
        f"  {k}: {_fmt(v)} ({v / total_exp * 100:.0f}%)"
        for k, v in sorted(by_cat.items(), key=lambda x: -x[1])
    ) if by_cat else "  No expenses"
    return (
        f"📊 This Month\n\n"
        f"💸 Expenses: {_fmt(total_exp)}\n"
        f"💰 Income:   {_fmt(total_inc)}\n"
        f"💵 Balance:  {_fmt(total_inc - total_exp)}\n\n"
        f"By Category:\n{cat_lines}"
    )

@mcp.resource("expense://budgets/current")
async def resource_budgets() -> str:
    """Current month budgets and usage."""
    db = _resource_session()
    month = datetime.now().strftime("%Y-%m")
    with db() as session:
        budgets = session.query(Budget).filter_by(user_id=USER_ID, month=month).all()
        if not budgets:
            return "No budgets set this month."
        lines = []
        for b in budgets:
            spent = sum(
                e.amount for e in session.query(Expense).filter(
                    Expense.user_id == USER_ID,
                    Expense.category == b.category,
                    Expense.date.like(f"{month}%"),
                    Expense.type == "expense",
                ).all()
            )
            pct    = (spent / b.amount * 100) if b.amount > 0 else 0
            status = "🚨" if pct >= 100 else "⚠️ " if pct >= 80 else "✅"
            lines.append(f"{status} {b.category}: {_fmt(spent)}/{_fmt(b.amount)} ({pct:.0f}%)")
    return "📊 Budgets — " + month + "\n\n" + "\n".join(lines)

@mcp.resource("expense://goals/all")
async def resource_goals() -> str:
    """All savings goals with progress."""
    db = _resource_session()
    with db() as session:
        goals = session.query(Goal).filter_by(user_id=USER_ID).all()
        if not goals:
            return "No goals yet."
        lines = []
        for g in goals:
            pct = (g.saved / g.target * 100) if g.target > 0 else 0
            bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
            lines.append(f"🎯 {g.name}\n   [{bar}] {pct:.0f}%\n   {_fmt(g.saved)}/{_fmt(g.target)} · {g.deadline}")
    return "\n\n".join(lines)

# ── Prompts ────────────────────────────────────────────────────────────────────

@mcp.prompt(title="Monthly Review")
def monthly_review() -> str:
    """Start a monthly expense review session."""
    return f"Review my {datetime.now().strftime('%B %Y')} spending. Identify where I'm overspending and suggest ways to cut costs."

@mcp.prompt(title="Budget Setup")
def budget_setup() -> str:
    """Set up budgets based on recent spending."""
    return "Analyse my recent expenses and suggest monthly budgets for each category."

@mcp.prompt(title="Savings Plan")
def savings_plan(goal: str, amount: float) -> str:
    """Create a savings plan for a specific goal."""
    return f"Help me save {CURRENCY}{amount:,.0f} for '{goal}'. Show a month-by-month plan based on my current spending."

# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "http":
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
    else:
        mcp.run(transport="stdio")
