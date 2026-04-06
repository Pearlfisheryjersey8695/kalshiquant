"""
Strategy Manager — CRUD operations for user-defined strategies.
Strategies are stored in SQLite for persistence across restarts.
Each strategy runs in isolation with its own P&L ledger.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger("kalshi.strategy_mgr")

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "strategies.db")


class StrategyManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self):
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'CUSTOM',
                status TEXT NOT NULL DEFAULT 'PAUSED',
                description TEXT DEFAULT '',
                markets TEXT DEFAULT '[]',
                parameters TEXT DEFAULT '[]',
                signals_config TEXT DEFAULT '{}',
                risk_limits TEXT DEFAULT '{}',
                pnl REAL DEFAULT 0,
                trades_today INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def list_strategies(self) -> list[dict]:
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM strategies ORDER BY created_at DESC").fetchall()
            conn.close()
            return [self._row_to_dict(r) for r in rows]

    def get_strategy(self, strategy_id: str) -> dict | None:
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
            conn.close()
            return self._row_to_dict(row) if row else None

    def create_strategy(self, data: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        # Auto-generate ID
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            max_id = conn.execute("SELECT COALESCE(MAX(CAST(SUBSTR(id, 7) AS INTEGER)), 0) FROM strategies").fetchone()[0]
            sid = f"STRAT-{max_id + 1:03d}"

            conn.execute("""
                INSERT INTO strategies (id, name, type, status, description, markets,
                    parameters, signals_config, risk_limits, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sid,
                data.get("name", f"Strategy {max_id + 1}"),
                data.get("type", "CUSTOM"),
                data.get("status", "PAUSED"),
                data.get("description", ""),
                json.dumps(data.get("markets", [])),
                json.dumps(data.get("parameters", self._default_params())),
                json.dumps(data.get("signals_config", self._default_signals())),
                json.dumps(data.get("risk_limits", self._default_limits())),
                now, now,
            ))
            conn.commit()
            conn.close()
            logger.info("Created strategy %s: %s", sid, data.get("name"))
            return self.get_strategy(sid) or {"id": sid}

    def update_strategy(self, strategy_id: str, data: dict) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            existing = conn.execute("SELECT id FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
            if not existing:
                conn.close()
                return None

            updates = []
            values = []
            for field in ["name", "type", "status", "description"]:
                if field in data:
                    updates.append(f"{field} = ?")
                    values.append(data[field])
            for json_field in ["markets", "parameters", "signals_config", "risk_limits"]:
                if json_field in data:
                    updates.append(f"{json_field} = ?")
                    values.append(json.dumps(data[json_field]))

            if updates:
                updates.append("updated_at = ?")
                values.append(now)
                values.append(strategy_id)
                conn.execute(f"UPDATE strategies SET {', '.join(updates)} WHERE id = ?", values)
                conn.commit()

            conn.close()
            return self.get_strategy(strategy_id)

    def delete_strategy(self, strategy_id: str) -> bool:
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
            conn.commit()
            conn.close()
            return cursor.rowcount > 0

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        # "markets" and "parameters" default to [] (list), others to {} (dict)
        list_fields = {"markets", "parameters"}
        for json_field in ["markets", "parameters", "signals_config", "risk_limits"]:
            fallback = [] if json_field in list_fields else {}
            try:
                d[json_field] = json.loads(d[json_field]) if d.get(json_field) else fallback
            except (json.JSONDecodeError, TypeError):
                d[json_field] = fallback
        return d

    @staticmethod
    def _default_params() -> list:
        return [
            {"name": "LOOKBACK_PERIOD", "value": 14, "min": 1, "max": 100},
            {"name": "ENTRY_THRESHOLD", "value": 0.65, "min": 0.5, "max": 0.95},
            {"name": "EXIT_THRESHOLD", "value": 0.45, "min": 0.1, "max": 0.8},
            {"name": "SIGNAL_WEIGHT", "value": 1.0, "min": 0.0, "max": 2.0},
            {"name": "REBALANCE_FREQ", "value": 60, "min": 15, "max": 1440},
        ]

    @staticmethod
    def _default_signals() -> dict:
        return {
            "fair_value": True,
            "regime_classifier": True,
            "sentiment_score": False,
            "momentum": True,
            "mean_reversion": False,
            "volume_signal": True,
            "weights": {
                "fair_value": 0.40,
                "regime": 0.30,
                "momentum": 0.30,
            },
        }

    @staticmethod
    def _default_limits() -> dict:
        return {
            "max_position_size": 50,
            "max_daily_loss": -100,
            "max_open_positions": 5,
            "kelly_fraction": 0.5,
            "stop_loss_pct": 15,
            "take_profit_pct": 40,
            "min_edge": 3.0,
            "min_confidence": 0.60,
            "min_tradability": 40,
        }
