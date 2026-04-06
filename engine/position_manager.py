"""
Position manager: tracks open/closed positions with real-time P&L.

Persists to SQLite so positions survive server restart.
Paper trading only — no real order placement.
"""

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger("kalshi.positions")

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "positions.db")


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"


@dataclass
class PositionState:
    ticker: str
    direction: str  # BUY_YES or BUY_NO
    entry_price: float
    entry_time: str
    contracts: int
    remaining_contracts: int  # for partial exits
    entry_cost: float
    current_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    entry_fee: float = 0.0  # original entry fee (immutable after open)
    signal_persistence_at_entry: int = 0
    regime_at_entry: str = ""
    edge_at_entry: float = 0.0
    meta_quality_at_entry: float = 0.0
    kelly_fraction_at_entry: float = 0.0
    status: str = PositionStatus.OPEN
    exit_reason: str = ""
    exit_price: float = 0.0
    exit_time: str = ""
    title: str = ""
    category: str = ""
    strategy_at_entry: str = ""
    confidence_at_entry: float = 0.0
    fair_value_at_entry: float = 0.0
    signal_source: str = ""
    regime_at_exit: str = ""
    journal_notes: str = ""
    estimated_slippage: float = 0.0
    net_edge_at_entry: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        # Add computed fields
        d["hold_time_minutes"] = self._hold_time_minutes()
        d["pnl_pct"] = self._pnl_pct()
        return d

    def _hold_time_minutes(self) -> float:
        try:
            entry = datetime.fromisoformat(self.entry_time)
            # Ensure entry is timezone-aware for consistent comparison
            if entry.tzinfo is None:
                entry = entry.replace(tzinfo=timezone.utc)
            if self.exit_time:
                end = datetime.fromisoformat(self.exit_time)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
            else:
                end = datetime.now(timezone.utc)
            return (end - entry).total_seconds() / 60.0
        except Exception:
            return 0.0

    def _pnl_pct(self) -> float:
        if self.entry_cost <= 0:
            return 0.0
        remaining_cost = (
            self.remaining_contracts * self.entry_price
            if self.direction == "BUY_YES"
            else self.remaining_contracts * (1 - self.entry_price)
        )
        if remaining_cost <= 0:
            # Fully closed — use realized only
            return self.realized_pnl / self.entry_cost
        total_pnl = self.unrealized_pnl + self.realized_pnl
        return total_pnl / self.entry_cost

    def compute_unrealized_pnl(self) -> float:
        """Recalculate unrealized P&L from current price."""
        if self.status == PositionStatus.CLOSED:
            self.unrealized_pnl = 0.0
            return 0.0

        if self.direction == "BUY_YES":
            pnl = (self.current_price - self.entry_price) * self.remaining_contracts
        else:  # BUY_NO
            pnl = (self.entry_price - self.current_price) * self.remaining_contracts

        # Do NOT subtract fees_paid here — entry fees are already deducted
        # from bankroll at entry time. Subtracting again would double-count.
        self.unrealized_pnl = round(pnl, 4)
        return self.unrealized_pnl


class PositionManager:
    """Manages all paper-trade positions with SQLite persistence."""

    def __init__(self, bankroll: float = 10000.0):
        self.bankroll = bankroll
        self._open: dict[str, PositionState] = {}  # ticker -> position
        self._closed: list[PositionState] = []
        self._lock = threading.Lock()
        self._init_db()
        self._load_from_db()

    def _init_db(self) -> None:
        """Create SQLite tables if they don't exist."""
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                contracts INTEGER NOT NULL,
                remaining_contracts INTEGER NOT NULL,
                entry_cost REAL NOT NULL,
                current_price REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                fees_paid REAL DEFAULT 0,
                signal_persistence_at_entry INTEGER DEFAULT 0,
                regime_at_entry TEXT DEFAULT '',
                edge_at_entry REAL DEFAULT 0,
                meta_quality_at_entry REAL DEFAULT 0,
                kelly_fraction_at_entry REAL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'OPEN',
                exit_reason TEXT DEFAULT '',
                exit_price REAL DEFAULT 0,
                exit_time TEXT DEFAULT '',
                title TEXT DEFAULT '',
                category TEXT DEFAULT ''
            )
        """)
        # Migrate: add strategy_at_entry column if missing
        try:
            conn.execute("SELECT strategy_at_entry FROM positions LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE positions ADD COLUMN strategy_at_entry TEXT DEFAULT ''")
        # Migrate: add trade journal / execution tracking columns
        for col, coltype in [
            ("confidence_at_entry", "REAL DEFAULT 0"),
            ("fair_value_at_entry", "REAL DEFAULT 0"),
            ("signal_source", "TEXT DEFAULT ''"),
            ("regime_at_exit", "TEXT DEFAULT ''"),
            ("journal_notes", "TEXT DEFAULT ''"),
            ("estimated_slippage", "REAL DEFAULT 0"),
            ("net_edge_at_entry", "REAL DEFAULT 0"),
        ]:
            try:
                conn.execute(f"SELECT {col} FROM positions LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {coltype}")
        conn.commit()
        conn.close()

    def _load_from_db(self) -> None:
        """Load positions from SQLite on startup."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM positions ORDER BY id").fetchall()
            conn.close()

            for row in rows:
                pos = PositionState(
                    ticker=row["ticker"],
                    direction=row["direction"],
                    entry_price=row["entry_price"],
                    entry_time=row["entry_time"],
                    contracts=row["contracts"],
                    remaining_contracts=row["remaining_contracts"],
                    entry_cost=row["entry_cost"],
                    current_price=row["current_price"],
                    unrealized_pnl=row["unrealized_pnl"],
                    realized_pnl=row["realized_pnl"],
                    fees_paid=row["fees_paid"],
                    signal_persistence_at_entry=row["signal_persistence_at_entry"],
                    regime_at_entry=row["regime_at_entry"],
                    edge_at_entry=row["edge_at_entry"],
                    meta_quality_at_entry=row["meta_quality_at_entry"],
                    kelly_fraction_at_entry=row["kelly_fraction_at_entry"],
                    status=row["status"],
                    exit_reason=row["exit_reason"],
                    exit_price=row["exit_price"],
                    exit_time=row["exit_time"],
                    title=row["title"],
                    category=row["category"],
                    strategy_at_entry=row["strategy_at_entry"] or "",
                    confidence_at_entry=row["confidence_at_entry"] if "confidence_at_entry" in row.keys() else 0.0,
                    fair_value_at_entry=row["fair_value_at_entry"] if "fair_value_at_entry" in row.keys() else 0.0,
                    signal_source=row["signal_source"] if "signal_source" in row.keys() else "",
                    regime_at_exit=row["regime_at_exit"] if "regime_at_exit" in row.keys() else "",
                    journal_notes=row["journal_notes"] if "journal_notes" in row.keys() else "",
                    estimated_slippage=row["estimated_slippage"] if "estimated_slippage" in row.keys() else 0.0,
                    net_edge_at_entry=row["net_edge_at_entry"] if "net_edge_at_entry" in row.keys() else 0.0,
                )
                if pos.status in (PositionStatus.OPEN, PositionStatus.PARTIAL):
                    self._open[pos.ticker] = pos
                else:
                    self._closed.append(pos)

            logger.info(
                "Loaded %d open, %d closed positions from DB",
                len(self._open), len(self._closed),
            )
        except Exception as e:
            logger.warning("Failed to load positions from DB: %s", e)

    def _save_position(self, pos: PositionState) -> None:
        """Upsert position to SQLite."""
        try:
            conn = sqlite3.connect(DB_PATH)
            # Check if exists
            existing = conn.execute(
                "SELECT id FROM positions WHERE ticker = ? AND entry_time = ?",
                (pos.ticker, pos.entry_time),
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE positions SET
                        remaining_contracts = ?, current_price = ?,
                        unrealized_pnl = ?, realized_pnl = ?, fees_paid = ?,
                        status = ?, exit_reason = ?, exit_price = ?, exit_time = ?,
                        regime_at_exit = ?, journal_notes = ?
                    WHERE ticker = ? AND entry_time = ?
                """, (
                    pos.remaining_contracts, pos.current_price,
                    pos.unrealized_pnl, pos.realized_pnl, pos.fees_paid,
                    pos.status, pos.exit_reason, pos.exit_price, pos.exit_time,
                    pos.regime_at_exit, pos.journal_notes,
                    pos.ticker, pos.entry_time,
                ))
            else:
                conn.execute("""
                    INSERT INTO positions (
                        ticker, direction, entry_price, entry_time, contracts,
                        remaining_contracts, entry_cost, current_price,
                        unrealized_pnl, realized_pnl, fees_paid,
                        signal_persistence_at_entry, regime_at_entry, edge_at_entry,
                        meta_quality_at_entry, kelly_fraction_at_entry,
                        status, exit_reason, exit_price, exit_time, title, category,
                        strategy_at_entry, confidence_at_entry, fair_value_at_entry,
                        signal_source, regime_at_exit, journal_notes,
                        estimated_slippage, net_edge_at_entry
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pos.ticker, pos.direction, pos.entry_price, pos.entry_time,
                    pos.contracts, pos.remaining_contracts, pos.entry_cost,
                    pos.current_price, pos.unrealized_pnl, pos.realized_pnl,
                    pos.fees_paid, pos.signal_persistence_at_entry,
                    pos.regime_at_entry, pos.edge_at_entry,
                    pos.meta_quality_at_entry, pos.kelly_fraction_at_entry,
                    pos.status, pos.exit_reason, pos.exit_price, pos.exit_time,
                    pos.title, pos.category, pos.strategy_at_entry,
                    pos.confidence_at_entry, pos.fair_value_at_entry,
                    pos.signal_source, pos.regime_at_exit, pos.journal_notes,
                    pos.estimated_slippage, pos.net_edge_at_entry,
                ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Failed to save position to DB: %s", e)

    # ── Public API ──────────────────────────────────────────────────────────

    def open_position(
        self,
        ticker: str,
        direction: str,
        contracts: int,
        entry_price: float,
        signal: dict,
    ) -> PositionState:
        """Record a new paper-trade entry."""
        from models.risk_model import kalshi_fee

        if direction == "BUY_NO":
            cost_per = 1.0 - entry_price
        else:
            cost_per = entry_price
        entry_cost = round(contracts * cost_per, 4)
        entry_fee = round(contracts * kalshi_fee(entry_price), 4)

        pos = PositionState(
            ticker=ticker,
            direction=direction,
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc).isoformat(),
            contracts=contracts,
            remaining_contracts=contracts,
            entry_cost=entry_cost,
            current_price=entry_price,
            fees_paid=entry_fee,
            entry_fee=entry_fee,
            signal_persistence_at_entry=signal.get("_persistence", 0),
            regime_at_entry=signal.get("regime", ""),
            edge_at_entry=signal.get("edge", 0),
            meta_quality_at_entry=signal.get("meta_quality", 0),
            kelly_fraction_at_entry=signal.get("risk", {}).get("kelly_fraction", 0),
            title=signal.get("title", ""),
            category=signal.get("category", ""),
            strategy_at_entry=signal.get("strategy", "convergence"),
            confidence_at_entry=signal.get("confidence", 0),
            fair_value_at_entry=signal.get("fair_value", 0),
            signal_source=signal.get("_signal_source", ""),
            net_edge_at_entry=signal.get("net_edge", 0),
            estimated_slippage=signal.get("_slippage", 0),
        )

        with self._lock:
            if ticker in self._open:
                logger.warning(
                    "Overwriting existing position for %s (old entry_price=%.2f)",
                    ticker, self._open[ticker].entry_price,
                )
            # Deduct cost of contracts + entry fee from bankroll
            self.bankroll -= (entry_cost + entry_fee)
            self._open[ticker] = pos
            self._save_position(pos)

        logger.info(
            "OPEN %s: %s %d @ %.2f, cost=$%.2f, fee=$%.2f, edge=%.4f",
            ticker, direction, contracts, entry_price, entry_cost, entry_fee,
            signal.get("edge", 0),
        )
        return pos

    def update_prices(self, state) -> None:
        """Refresh unrealized P&L from live market prices."""
        with self._lock:
            for ticker, pos in self._open.items():
                market = state.get_market(ticker)
                if market and market["price"] > 0:
                    pos.current_price = market["price"]
                    pos.compute_unrealized_pnl()

    def close_position(
        self,
        ticker: str,
        reason: str,
        exit_price: float | None = None,
        partial_contracts: int | None = None,
        regime_at_exit: str = "",
    ) -> PositionState | None:
        """Close (or partially close) a position."""
        from models.risk_model import kalshi_fee

        with self._lock:
            pos = self._open.get(ticker)
            if not pos:
                return None

            if exit_price is None:
                exit_price = pos.current_price

            close_contracts = partial_contracts or pos.remaining_contracts
            close_contracts = min(close_contracts, pos.remaining_contracts)

            # Calculate realized P&L for the closed portion
            if pos.direction == "BUY_YES":
                pnl = (exit_price - pos.entry_price) * close_contracts
            else:
                pnl = (pos.entry_price - exit_price) * close_contracts

            exit_fee = round(close_contracts * kalshi_fee(exit_price), 4)
            # Pro-rate the ORIGINAL entry fee (not accumulated fees_paid which includes exit fees)
            entry_fee_portion = round(pos.entry_fee * (close_contracts / pos.contracts), 4) if pos.contracts > 0 else 0
            # Realized P&L must account for both entry and exit fees
            realized = round(pnl - exit_fee - entry_fee_portion, 4)

            # Add exit proceeds back to bankroll
            if pos.direction == "BUY_YES":
                exit_proceeds = exit_price * close_contracts
            else:
                exit_proceeds = (1.0 - exit_price) * close_contracts
            self.bankroll += (exit_proceeds - exit_fee)

            pos.realized_pnl += realized
            pos.fees_paid += exit_fee
            pos.remaining_contracts -= close_contracts

            if pos.remaining_contracts <= 0:
                # Fully closed
                pos.status = PositionStatus.CLOSED
                pos.exit_reason = reason
                pos.regime_at_exit = regime_at_exit
                pos.exit_price = exit_price
                pos.exit_time = datetime.now(timezone.utc).isoformat()
                pos.unrealized_pnl = 0.0
                self._closed.append(pos)
                del self._open[ticker]
            else:
                # Partial close
                pos.status = PositionStatus.PARTIAL
                pos.compute_unrealized_pnl()

            self._save_position(pos)

        logger.info(
            "CLOSE %s: %s %d @ %.2f, reason=%s, realized=$%.2f, fee=$%.2f",
            ticker, pos.direction, close_contracts, exit_price, reason,
            realized, exit_fee,
        )
        return pos

    def has_position(self, ticker: str) -> bool:
        return ticker in self._open

    def get_position(self, ticker: str) -> PositionState | None:
        return self._open.get(ticker)

    def get_open_positions(self) -> list[dict]:
        with self._lock:
            return [p.to_dict() for p in self._open.values()]

    def get_closed_positions(self) -> list[dict]:
        with self._lock:
            return [p.to_dict() for p in self._closed]

    def get_portfolio_heat(self) -> float:
        """Sum of all open position costs / total capital (bankroll + deployed)."""
        total_deployed = self.get_total_deployed()
        total_capital = self.bankroll + total_deployed
        if total_capital <= 0:
            return 1.0
        return total_deployed / total_capital

    def get_total_deployed(self) -> float:
        """Sum of all open position costs (pro-rated for partial)."""
        return sum(
            p.entry_cost * (p.remaining_contracts / p.contracts) if p.contracts > 0 else 0
            for p in self._open.values()
        )

    def get_total_unrealized(self) -> float:
        return sum(p.unrealized_pnl for p in self._open.values())

    def get_total_realized(self) -> float:
        return sum(p.realized_pnl for p in self._closed) + sum(
            p.realized_pnl for p in self._open.values()
        )

    def get_today_pnl(self) -> float:
        """Today's P&L: unrealized + only today's realized (not all-time)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_realized = sum(
            p.realized_pnl for p in self._closed
            if p.exit_time and p.exit_time.startswith(today)
        )
        return today_realized + self.get_total_unrealized()

    def get_open_count(self) -> int:
        return len(self._open)

    def get_open_tickers(self) -> list[str]:
        return list(self._open.keys())

    def get_smallest_position(self) -> PositionState | None:
        """Return the open position with the smallest cost (for forced close)."""
        if not self._open:
            return None
        return min(self._open.values(), key=lambda p: p.entry_cost)

    def get_largest_position(self) -> PositionState | None:
        """Return the open position with the largest cost (for emergency liquidation)."""
        if not self._open:
            return None
        return max(self._open.values(), key=lambda p: p.entry_cost)

    def get_correlated_count(self, ticker: str, correlations: dict) -> int:
        """Count how many open positions are correlated (|corr| > 0.6) with ticker."""
        count = 0
        for open_ticker in self._open:
            if open_ticker == ticker:
                continue
            key = (ticker, open_ticker)
            rev_key = (open_ticker, ticker)
            corr = correlations.get(key, correlations.get(rev_key, 0.0))
            if abs(corr) > 0.6:
                count += 1
        return count

    def get_all_positions_chronological(self) -> list[dict]:
        """Return all positions (open + closed) sorted by entry_time, for analytics."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM positions ORDER BY entry_time ASC"
            ).fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.warning("Failed to load chronological positions: %s", e)
            return []

    def add_journal_note(self, ticker: str, entry_time: str, note: str) -> bool:
        """Add PM annotation to a trade."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE positions SET journal_notes = ? WHERE ticker = ? AND entry_time = ?",
                (note, ticker, entry_time),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("Failed to add journal note: %s", e)
            return False

    def query_journal(self, filters: dict = None, sort_by: str = "entry_time", limit: int = 100) -> list[dict]:
        """Query positions with filters for the trade journal."""
        filters = filters or {}
        conditions = []
        params = []

        if filters.get("category"):
            conditions.append("category = ?")
            params.append(filters["category"])
        if filters.get("regime"):
            conditions.append("regime_at_entry = ?")
            params.append(filters["regime"])
        if filters.get("strategy"):
            conditions.append("strategy_at_entry = ?")
            params.append(filters["strategy"])
        if filters.get("ticker"):
            conditions.append("ticker LIKE ?")
            params.append(f"%{filters['ticker']}%")
        if filters.get("exit_reason"):
            conditions.append("exit_reason = ?")
            params.append(filters["exit_reason"])
        if filters.get("from_date"):
            conditions.append("entry_time >= ?")
            params.append(filters["from_date"])
        if filters.get("to_date"):
            conditions.append("entry_time <= ?")
            params.append(filters["to_date"])
        if filters.get("status"):
            conditions.append("status = ?")
            params.append(filters["status"])
        if filters.get("min_pnl") is not None:
            conditions.append("realized_pnl >= ?")
            params.append(float(filters["min_pnl"]))
        if filters.get("max_pnl") is not None:
            conditions.append("realized_pnl <= ?")
            params.append(float(filters["max_pnl"]))

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        allowed_sort = {"entry_time", "exit_time", "realized_pnl", "ticker", "strategy_at_entry"}
        sort_col = sort_by if sort_by in allowed_sort else "entry_time"

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM positions {where} ORDER BY {sort_col} DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            d = dict(row)
            # Add computed fields
            try:
                entry = datetime.fromisoformat(d["entry_time"])
                if d.get("exit_time"):
                    exit_t = datetime.fromisoformat(d["exit_time"])
                    d["hold_time_minutes"] = round((exit_t - entry).total_seconds() / 60, 1)
                else:
                    d["hold_time_minutes"] = 0
            except Exception:
                d["hold_time_minutes"] = 0
            d["pnl_pct"] = round(d["realized_pnl"] / d["entry_cost"], 4) if d.get("entry_cost", 0) > 0 else 0
            d["regime_changed"] = d.get("regime_at_entry", "") != d.get("regime_at_exit", "") and d.get("regime_at_exit", "") != ""
            results.append(d)

        return results

    def sync_bankroll(self, kalshi_client) -> float:
        """Sync bankroll with real Kalshi API balance.
        If API returns 0 or fails, keep the existing bankroll.
        """
        try:
            balance_data = kalshi_client.get_balance()
            real_balance = balance_data.get("balance", 0) / 100.0  # cents to dollars
            if real_balance <= 0:
                logger.info("Kalshi API returned $0 balance — keeping default bankroll $%.2f", self.bankroll)
                return self.bankroll + self.get_total_deployed()
            old = self.bankroll
            total_deployed = self.get_total_deployed()
            self.bankroll = real_balance - total_deployed
            logger.info("Bankroll synced: $%.2f -> $%.2f (API=$%.2f, deployed=$%.2f)",
                         old, self.bankroll, real_balance, total_deployed)
            return real_balance
        except Exception as e:
            logger.warning("Bankroll sync failed (keeping default): %s", e)
            return self.bankroll + self.get_total_deployed()

    def get_summary(self) -> dict:
        """Summary stats for the dashboard header."""
        return {
            "open_positions": len(self._open),
            "total_deployed": round(self.get_total_deployed(), 2),
            "total_unrealized": round(self.get_total_unrealized(), 2),
            "total_realized": round(self.get_total_realized(), 2),
            "portfolio_heat": round(self.get_portfolio_heat(), 4),
            "bankroll": self.bankroll,
            "today_pnl": round(self.get_today_pnl(), 2),
        }
