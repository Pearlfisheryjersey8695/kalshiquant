"""
Smart alert engine for the fund manager.
Evaluates risk conditions every 30s and fires alerts.
"""

import logging
import time
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger("kalshi.alerts")


class AlertLevel:
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class AlertEngine:
    def __init__(self, maxlen: int = 200):
        self._alerts: deque = deque(maxlen=maxlen)
        self._seq = 0
        self._last_eval = 0
        self._acknowledged: set = set()

    def evaluate(self, position_manager, signals_holder, risk_engine, state, execution_engine=None):
        """Run all alert checks. Called every 30s from scheduler."""
        now = time.time()
        if now - self._last_eval < 25:  # debounce
            return
        self._last_eval = now

        open_positions = position_manager.get_open_positions() if position_manager else []
        signals_data = signals_holder.get() if signals_holder else {}
        signal_list = signals_data.get("signals", [])
        signal_map = {s["ticker"]: s for s in signal_list}

        # 1. Edge decay alerts
        for pos in open_positions:
            ticker = pos.get("ticker", "")
            edge_at_entry = pos.get("edge_at_entry", 0)
            current_signal = signal_map.get(ticker)

            if current_signal and edge_at_entry != 0:
                current_edge = current_signal.get("edge", 0)
                # Check if edge direction flipped
                if edge_at_entry > 0 and current_edge < 0:
                    self._fire(AlertLevel.CRITICAL, ticker,
                               f"SIGNAL FLIP: {ticker} edge was +{edge_at_entry:.3f}, now {current_edge:.3f}")
                elif abs(current_edge) < abs(edge_at_entry) * 0.5:
                    self._fire(AlertLevel.WARN, ticker,
                               f"EDGE DECAY: {ticker} edge decayed from {edge_at_entry:.3f} to {current_edge:.3f}")
            elif not current_signal and ticker:
                self._fire(AlertLevel.WARN, ticker,
                           f"SIGNAL DROPPED: {ticker} no longer in signal set")

        # 2. Expiration proximity
        for pos in open_positions:
            ticker = pos.get("ticker", "")
            market = state.get_market(ticker) if state else None
            if market and market.get("expiration_time"):
                try:
                    exp = datetime.fromisoformat(market["expiration_time"].replace("Z", "+00:00"))
                    hours_left = (exp - datetime.now(timezone.utc)).total_seconds() / 3600
                    if 0 < hours_left < 2:
                        self._fire(AlertLevel.CRITICAL, ticker,
                                   f"EXPIRING: {ticker} expires in {hours_left:.1f}h -- unrealized ${pos.get('unrealized_pnl', 0):.2f}")
                    elif 2 <= hours_left < 6:
                        self._fire(AlertLevel.WARN, ticker,
                                   f"EXPIRY WATCH: {ticker} expires in {hours_left:.1f}h")
                except Exception:
                    pass

        # 3. Heat alerts
        if position_manager:
            heat = position_manager.get_portfolio_heat()
            if heat > 0.40:
                self._fire(AlertLevel.CRITICAL, "",
                           f"HEAT CRITICAL: Portfolio heat at {heat:.0%} -- exceeds 40% limit")
            elif heat > 0.30:
                self._fire(AlertLevel.WARN, "",
                           f"HEAT WARNING: Portfolio heat at {heat:.0%}")

        # 4. Drawdown alerts
        if risk_engine and position_manager:
            risk_status = risk_engine.check_risk_limits(position_manager, signals_holder)
            dd = risk_status.get("drawdown_pct", 0)
            if dd > 0.03:
                self._fire(AlertLevel.CRITICAL, "",
                           f"DRAWDOWN: {dd:.1%} from peak equity ${risk_status.get('peak_equity', 0):.0f}")
            elif dd > 0.02:
                self._fire(AlertLevel.WARN, "",
                           f"DRAWDOWN WATCH: {dd:.1%} from peak -- consider reducing")

        # 5. Concentration risk (category)
        if position_manager:
            cat_exposure = {}
            total_deployed = position_manager.get_total_deployed()
            for pos in open_positions:
                cat = pos.get("category", "Other") or "Other"
                cost = pos.get("entry_cost", 0) * (pos.get("remaining_contracts", 0) / max(pos.get("contracts", 1), 1))
                cat_exposure[cat] = cat_exposure.get(cat, 0) + cost

            for cat, exposure in cat_exposure.items():
                if total_deployed > 0 and exposure / total_deployed > 0.40:
                    self._fire(AlertLevel.WARN, "",
                               f"CONCENTRATION: {cat} is {exposure/total_deployed:.0%} of book (${exposure:.0f})")

    def _fire(self, level: str, ticker: str, message: str):
        """Fire an alert, deduplicating by message within 5 minutes."""
        # Dedup key: message hash within 5 min window
        dedup_key = f"{message[:50]}_{int(time.time() // 300)}"
        if dedup_key in self._acknowledged:
            return
        self._acknowledged.add(dedup_key)
        # Clean old dedup keys (keep last 500)
        if len(self._acknowledged) > 500:
            self._acknowledged = set(list(self._acknowledged)[-200:])

        self._seq += 1
        alert = {
            "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "ticker": ticker,
            "message": message,
        }
        self._alerts.appendleft(alert)

        if level == AlertLevel.CRITICAL:
            logger.warning("ALERT [%s] %s: %s", level, ticker, message)
        else:
            logger.info("ALERT [%s] %s: %s", level, ticker, message)

    def get_alerts(self, limit: int = 50, level: str = None) -> list[dict]:
        """Get recent alerts, optionally filtered by level."""
        alerts = list(self._alerts)
        if level:
            alerts = [a for a in alerts if a["level"] == level]
        return alerts[:limit]

    def get_unacknowledged_count(self) -> dict:
        """Count of alerts by level in last hour."""
        cutoff = time.time() - 3600
        counts = {"CRITICAL": 0, "WARN": 0, "INFO": 0}
        for a in self._alerts:
            try:
                ts = datetime.fromisoformat(a["ts"]).timestamp()
                if ts > cutoff:
                    counts[a["level"]] = counts.get(a["level"], 0) + 1
            except Exception:
                pass
        return counts
