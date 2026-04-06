"""
Automated signal-based execution engine.

Paper trading: simulates fills at mid-price. Designed so real execution
is a one-function swap: replace simulate_fill() with kalshi_client.place_order().

Conservative by default — only trades when expected value is unambiguously
positive after fees, slippage, and model uncertainty.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone

from engine.feed import FeedLog, FeedEventType
from engine.market_state import MarketStateStore
from engine.orderbook import OrderbookStore
from engine.position_manager import PositionManager
from engine.strategies import get_strategy, StrategyConfig
from models.risk_model import RiskModel

logger = logging.getLogger("kalshi.execution")

# ── Entry gates (defaults, overridden by strategy config) ──────────────────
BLOCKED_REGIMES = {"STALE"}  # Only STALE is globally blocked; strategies define their own
MIN_SIGNAL_PERSISTENCE = 1  # Reduced from 2: QuantBrain does its own persistence check
MIN_KELLY_FRACTION = 0.005
MAX_PORTFOLIO_HEAT = 0.40
MAX_CORRELATED_POSITIONS = 3
MAX_SLIPPAGE_RATIO = 0.30
MIN_NET_EV_DOLLARS = 0.50  # Reduced: prediction market edges are small but real
MIN_SHARPE_CONTRIBUTION = 0.3
MAX_POSITION_CONTRACTS = 500
VOLUME_CAP_PCT = 0.02  # 2% of 24h volume

# ── Exit thresholds (defaults, overridden by strategy config) ──────────────
PARTIAL_EXIT_PCT = 0.50  # close 50% at partial TP
EDGE_DECAY_CYCLES = 3  # 15 minutes
EXPIRY_PROXIMITY_HOURS = 1.0
EMERGENCY_HEAT_LIMIT = 0.50


class ExecutionEngine:
    """
    Receives signals from run_live_ensemble() every 5 minutes and decides
    what to do: open new positions, close existing ones, or skip.
    """

    def __init__(
        self,
        position_manager: PositionManager,
        risk_model: RiskModel,
        feed: FeedLog,
        state: MarketStateStore,
        orderbooks: OrderbookStore,
        kalshi_client=None,
    ):
        self.pm = position_manager
        self.risk = risk_model
        self.feed = feed
        self.state = state
        self.orderbooks = orderbooks
        self._kalshi_client = kalshi_client
        self._paused = False

        # Signal persistence tracker: ticker -> count of consecutive cycles
        self._signal_history: dict[str, int] = defaultdict(int)
        # Edge decay tracker: ticker -> cycles since last signal
        self._missing_signal_count: dict[str, int] = defaultdict(int)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self.feed.add(
            FeedEventType.TRADE,
            message="Execution engine PAUSED — no new entries, exits still monitored",
        )
        logger.info("Execution engine paused")

    def resume(self) -> None:
        self._paused = False
        self.feed.add(
            FeedEventType.TRADE,
            message="Execution engine RESUMED — entries enabled",
        )
        logger.info("Execution engine resumed")

    # ── Entry evaluation ────────────────────────────────────────────────────

    def evaluate_entries(self, signals: list[dict]) -> list[dict]:
        """
        Check each signal against all entry gates.
        Returns list of executed entries (for logging/broadcast).
        """
        if self._paused:
            return []

        # Update signal persistence
        current_tickers = {s["ticker"] for s in signals}
        for ticker in current_tickers:
            self._signal_history[ticker] += 1
        # Reset persistence for tickers not in current signals
        for ticker in list(self._signal_history.keys()):
            if ticker not in current_tickers:
                self._signal_history[ticker] = 0

        entries = []
        for signal in signals:
            ticker = signal["ticker"]
            result = self._check_entry_gates(signal)
            if result["pass"]:
                entry = self._execute_entry(signal, result["contracts"], slippage=result.get("slippage", 0))
                if entry:
                    entries.append(entry)
            else:
                logger.debug("SKIP %s: %s", ticker, result["reason"])

        return entries

    def _check_entry_gates(self, signal: dict) -> dict:
        """Run all entry gates. Returns {pass: bool, reason: str, contracts: int}."""
        ticker = signal["ticker"]
        net_edge = signal.get("net_edge", 0)
        meta_quality = signal.get("meta_quality", 0)
        regime = signal.get("regime", "UNKNOWN")
        kelly_frac = signal.get("risk", {}).get("kelly_fraction", 0)
        direction = signal.get("direction", "HOLD")
        current_price = signal.get("current_price", 0)
        volume_24h = signal.get("volume", 0)
        recommended = signal.get("recommended_contracts", 0)

        # Look up strategy config
        strategy_name = signal.get("strategy", "convergence")
        strat = get_strategy(strategy_name)

        # a) net_edge > 0
        if net_edge <= 0:
            self._log_skip(ticker, f"net_edge={net_edge:.4f} (need >0)")
            return {"pass": False, "reason": "negative net edge"}

        # b) meta_model_score > strategy meta gate
        if meta_quality < strat.meta_gate:
            self._log_skip(ticker, f"meta_quality={meta_quality:.2f} (need >{strat.meta_gate})")
            return {"pass": False, "reason": "meta score too low"}

        # c) regime check: must be in strategy's allowed regimes (STALE always blocked)
        if regime in BLOCKED_REGIMES or regime not in strat.allowed_regimes:
            self._log_skip(ticker, f"regime={regime} not in {strat.name} allowed={strat.allowed_regimes}")
            return {"pass": False, "reason": f"regime {regime} not allowed for {strat.name}"}

        # d) signal persistence >= 2
        persistence = self._signal_history.get(ticker, 0)
        if persistence < MIN_SIGNAL_PERSISTENCE:
            self._log_skip(ticker, f"signal_persistence={persistence} (need {MIN_SIGNAL_PERSISTENCE})")
            return {"pass": False, "reason": "insufficient persistence"}

        # e) kelly_fraction > 0.005
        if kelly_frac < MIN_KELLY_FRACTION:
            self._log_skip(ticker, f"kelly={kelly_frac:.4f} (need >{MIN_KELLY_FRACTION})")
            return {"pass": False, "reason": "kelly too small"}

        # f) portfolio heat < 40%
        heat = self.pm.get_portfolio_heat()
        if heat >= MAX_PORTFOLIO_HEAT:
            self._log_skip(ticker, f"portfolio_heat={heat:.1%} (max {MAX_PORTFOLIO_HEAT:.0%})")
            return {"pass": False, "reason": "portfolio heat limit"}

        # g) no existing position
        if self.pm.has_position(ticker):
            self._log_skip(ticker, "already has position")
            return {"pass": False, "reason": "duplicate position"}

        # h) max 3 correlated positions
        corr_count = self.pm.get_correlated_count(ticker, self.risk._correlations)
        if corr_count >= MAX_CORRELATED_POSITIONS:
            self._log_skip(ticker, f"correlated_positions={corr_count} (max {MAX_CORRELATED_POSITIONS})")
            return {"pass": False, "reason": "too many correlated positions"}

        # ── Position sizing ─────────────────────────────────────────────────
        kelly_contracts = recommended if recommended > 0 else 0
        volume_cap = max(1, int(volume_24h * VOLUME_CAP_PCT)) if volume_24h > 0 else MAX_POSITION_CONTRACTS
        contracts = min(kelly_contracts, volume_cap, strat.max_contracts, MAX_POSITION_CONTRACTS)
        if contracts <= 0:
            self._log_skip(ticker, "0 contracts after sizing")
            return {"pass": False, "reason": "zero contracts"}

        # i) slippage check
        ob = self.orderbooks.get(ticker)
        slippage_info = RiskModel.estimate_slippage(ob, contracts, direction)
        slippage = abs(slippage_info.get("slippage", 0))
        if net_edge > 0 and slippage > MAX_SLIPPAGE_RATIO * net_edge:
            self._log_skip(ticker, f"slippage={slippage:.4f} > {MAX_SLIPPAGE_RATIO}*net_edge={net_edge:.4f}")
            return {"pass": False, "reason": "slippage too high"}

        # ── Trade sizing math ───────────────────────────────────────────────
        # net_edge already has fees subtracted, so don't subtract again
        expected_profit = net_edge * contracts
        expected_slippage = slippage * contracts
        net_ev = expected_profit - expected_slippage

        if net_ev < MIN_NET_EV_DOLLARS:
            self._log_skip(ticker, f"net_EV=${net_ev:.2f} (need >${MIN_NET_EV_DOLLARS})")
            return {"pass": False, "reason": "EV too small"}

        # EV/risk ratio check
        risk_per_trade = contracts * current_price * strat.stop_loss_pct
        ev_risk_ratio = net_ev / risk_per_trade if risk_per_trade > 0 else 0
        if ev_risk_ratio < MIN_SHARPE_CONTRIBUTION:
            self._log_skip(ticker, f"ev_risk_ratio={ev_risk_ratio:.2f} (need >{MIN_SHARPE_CONTRIBUTION})")
            return {"pass": False, "reason": "EV/risk ratio too low"}

        return {"pass": True, "reason": "all gates passed", "contracts": contracts, "slippage": slippage}

    def _execute_entry(self, signal: dict, contracts: int, slippage: float = 0) -> dict | None:
        """Execute entry fill and record position."""
        ticker = signal["ticker"]
        direction = signal["direction"]
        current_price = signal["current_price"]

        # Fill order (paper or live depending on LIVE_TRADING env var)
        fill_result = self._fill_order(ticker, contracts, direction, slippage=slippage)
        if fill_result is None:
            return None
        fill_price = fill_result["fill_price"]

        # Annotate signal with persistence count
        signal["_persistence"] = self._signal_history.get(ticker, 0)

        pos = self.pm.open_position(
            ticker=ticker,
            direction=direction,
            contracts=contracts,
            entry_price=fill_price,
            signal=signal,
        )

        heat = self.pm.get_portfolio_heat()
        edge = signal.get("edge", 0)
        net_edge = signal.get("net_edge", 0)

        # Feed event
        self.feed.add(
            FeedEventType.TRADE,
            ticker=ticker,
            message=(
                f"ENTRY {direction} {ticker}: {contracts}ct @ {fill_price:.2f}, "
                f"net_edge={net_edge:.4f}, kelly={signal.get('risk', {}).get('kelly_fraction', 0):.3f}, "
                f"heat={heat:.0%}"
            ),
            data={
                "action": "ENTRY",
                "direction": direction,
                "contracts": contracts,
                "price": fill_price,
                "edge": edge,
                "net_edge": net_edge,
                "heat": heat,
            },
        )

        return {
            "ticker": ticker,
            "direction": direction,
            "contracts": contracts,
            "price": fill_price,
            "edge": edge,
            "heat": heat,
        }

    # ── Exit evaluation ─────────────────────────────────────────────────────

    def evaluate_exits(
        self,
        signals: list[dict],
        regimes: dict[str, str] | None = None,
    ) -> list[dict]:
        """
        Check all open positions for exit conditions.
        Returns list of executed exits.
        """
        exits = []
        signal_tickers = {s["ticker"] for s in signals}

        # Update missing signal counts for edge decay
        for ticker in self.pm.get_open_tickers():
            if ticker in signal_tickers:
                self._missing_signal_count[ticker] = 0
            else:
                self._missing_signal_count[ticker] += 1

        for ticker in list(self.pm.get_open_tickers()):
            pos = self.pm.get_position(ticker)
            if not pos:
                continue

            exit_result = self._check_exit_conditions(pos, signals, regimes)
            if exit_result:
                exit_info = self._execute_exit(
                    ticker,
                    exit_result["reason"],
                    exit_result.get("partial_contracts"),
                )
                if exit_info:
                    exits.append(exit_info)

        # Check portfolio heat emergency — close largest positions until heat is under limit
        heat = self.pm.get_portfolio_heat()
        while heat > EMERGENCY_HEAT_LIMIT:
            largest = self.pm.get_largest_position()
            if not largest or largest.ticker in [e["ticker"] for e in exits]:
                break
            exit_info = self._execute_exit(
                largest.ticker,
                "PORTFOLIO_HEAT_EMERGENCY",
            )
            if exit_info:
                exits.append(exit_info)
            heat = self.pm.get_portfolio_heat()

        return exits

    def _check_exit_conditions(
        self,
        pos,
        signals: list[dict],
        regimes: dict[str, str] | None,
    ) -> dict | None:
        """Check all exit conditions for a position. Returns {reason, partial_contracts?} or None."""
        ticker = pos.ticker
        entry_price = pos.entry_price
        current_price = pos.current_price
        direction = pos.direction
        remaining = pos.remaining_contracts

        # Get strategy-specific thresholds
        strat = get_strategy(pos.strategy_at_entry)
        stop_loss_pct = strat.stop_loss_pct
        take_profit_ratio = strat.take_profit_ratio
        take_profit_partial = take_profit_ratio * 0.75  # partial at 75% of full TP
        max_hold_hours = strat.max_hold_hours

        # ── a) STOP-LOSS: strategy-specific loss threshold ──────────────
        if direction == "BUY_YES":
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
        else:
            cost = 1.0 - entry_price
            pnl_pct = (entry_price - current_price) / cost if cost > 0 else 0

        if pnl_pct < -stop_loss_pct:
            return {"reason": "STOP_LOSS"}

        # ── b) TAKE-PROFIT: strategy-specific reward:risk ───────────────
        # Use actual stop-loss distance as the risk denominator
        if direction == "BUY_YES":
            stop_price = entry_price * (1 - stop_loss_pct)
            stop_distance = entry_price - stop_price
            gain = current_price - entry_price
        else:
            cost = 1.0 - entry_price
            stop_price = entry_price + cost * stop_loss_pct
            stop_distance = stop_price - entry_price
            gain = entry_price - current_price

        risk_amount = max(stop_distance, 0.01)

        if gain > 0 and risk_amount > 0:
            reward_risk = gain / risk_amount

            if reward_risk >= take_profit_ratio:
                return {"reason": "TAKE_PROFIT_FULL"}

            if reward_risk >= take_profit_partial and pos.status != "PARTIAL":
                partial = max(1, int(remaining * PARTIAL_EXIT_PCT))
                return {"reason": "TAKE_PROFIT_PARTIAL", "partial_contracts": partial}

        # ── c) EDGE DECAY: signal disappeared for 3 cycles ──────────────
        # Skip for parlay_arb — parlay signals are generated on scan cycles,
        # not every 5-min signal cycle. Missing signal doesn't mean edge gone.
        if strat.name != "parlay_arb":
            missing = self._missing_signal_count.get(ticker, 0)
            if missing >= EDGE_DECAY_CYCLES:
                return {"reason": f"EDGE_DECAY ({missing} cycles without signal)"}

        # ── d) REGIME CHANGE: regime left strategy's allowed set ────────
        # Skip for parlay_arb — parlay edge is structural (decomposition math),
        # not regime-dependent. Regime changes don't invalidate the thesis.
        if strat.name != "parlay_arb" and regimes:
            current_regime = regimes.get(ticker, "UNKNOWN")
            if current_regime in BLOCKED_REGIMES or current_regime not in strat.allowed_regimes:
                if pos.regime_at_entry in strat.allowed_regimes:
                    return {"reason": f"REGIME_CHANGE ({pos.regime_at_entry}->{current_regime})"}

        # ── e) TIME DECAY: strategy-specific max hold ───────────────────
        hold_minutes = pos._hold_time_minutes()
        if hold_minutes > max_hold_hours * 60:
            return {"reason": f"TIME_DECAY ({hold_minutes / 60:.1f}h)"}

        # ── f) EXPIRY PROXIMITY: < 1 hour to expiry ────────────────────
        market = self.state.get_market(ticker)
        if market:
            exp_str = market.get("expiration_time", "")
            if exp_str:
                try:
                    from pandas import Timestamp
                    exp = Timestamp(exp_str, tz="UTC")
                    now = Timestamp(datetime.now(timezone.utc))
                    hours_to_expiry = (exp - now).total_seconds() / 3600
                    if hours_to_expiry < EXPIRY_PROXIMITY_HOURS:
                        return {"reason": f"EXPIRY_PROXIMITY ({hours_to_expiry:.1f}h)"}
                except Exception:
                    pass

        return None

    def check_stop_losses_only(self) -> list[dict]:
        """Quick stop-loss check on the 30s price cycle (no full exit eval)."""
        exits = []
        for ticker in list(self.pm.get_open_tickers()):
            pos = self.pm.get_position(ticker)
            if not pos:
                continue

            strat = get_strategy(pos.strategy_at_entry)
            entry_price = pos.entry_price
            current_price = pos.current_price

            if pos.direction == "BUY_YES":
                pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            else:
                cost = 1.0 - entry_price
                pnl_pct = (entry_price - current_price) / cost if cost > 0 else 0

            if pnl_pct < -strat.stop_loss_pct:
                exit_info = self._execute_exit(ticker, "STOP_LOSS")
                if exit_info:
                    exits.append(exit_info)

        return exits

    def _execute_exit(
        self,
        ticker: str,
        reason: str,
        partial_contracts: int | None = None,
    ) -> dict | None:
        """Simulate exit fill and close position."""
        pos = self.pm.get_position(ticker)
        if not pos:
            return None

        exit_direction = "SELL_YES" if pos.direction == "BUY_YES" else "SELL_NO"
        exit_contracts = partial_contracts or pos.remaining_contracts
        fill_result = self._fill_order(ticker, exit_contracts, exit_direction)
        if fill_result is not None:
            exit_price = fill_result["fill_price"]
        else:
            exit_price = pos.current_price

        closed = self.pm.close_position(
            ticker=ticker,
            reason=reason,
            exit_price=exit_price,
            partial_contracts=partial_contracts,
        )
        if not closed:
            return None

        contracts_closed = partial_contracts or closed.contracts
        heat = self.pm.get_portfolio_heat()

        self.feed.add(
            FeedEventType.TRADE,
            ticker=ticker,
            message=(
                f"EXIT {ticker}: {reason}, {contracts_closed}ct @ {exit_price:.2f}, "
                f"realized=${closed.realized_pnl:.2f}, heat={heat:.0%}"
            ),
            data={
                "action": "EXIT",
                "reason": reason,
                "contracts": contracts_closed,
                "price": exit_price,
                "realized_pnl": closed.realized_pnl,
                "heat": heat,
            },
        )

        return {
            "ticker": ticker,
            "reason": reason,
            "contracts": contracts_closed,
            "price": exit_price,
            "realized_pnl": closed.realized_pnl,
            "heat": heat,
        }

    # ── Order filling (paper or live) ─────────────────────────────────────

    def _fill_order(
        self,
        ticker: str,
        contracts: int,
        direction: str,
        slippage: float = 0,
    ) -> dict | None:
        """
        Fill an order — paper trade or real Kalshi API depending on LIVE_TRADING env var.

        Returns: {fill_price: float, order_id: str | None, mode: str}
        """
        import os
        live_mode = os.getenv("LIVE_TRADING", "").lower() in ("1", "true", "yes")

        if live_mode and self._kalshi_client:
            return self._live_fill(ticker, contracts, direction)
        else:
            return self._paper_fill(ticker, contracts, direction, slippage)

    def _paper_fill(self, ticker, contracts, direction, slippage=0) -> dict | None:
        """Paper trading: fill at mid-price adjusted for slippage."""
        market = self.state.get_market(ticker)
        if not market or market["price"] <= 0:
            return None
        mid = market["price"]
        if direction in ("BUY_YES", "SELL_NO"):
            fill = mid + slippage
        else:
            fill = mid - slippage
        fill = max(0.01, min(0.99, fill))
        return {"fill_price": fill, "order_id": None, "mode": "paper"}

    def _live_fill(self, ticker, contracts, direction) -> dict | None:
        """Real execution via Kalshi REST API."""
        try:
            # Map direction to Kalshi API params
            if direction == "BUY_YES":
                action, side = "buy", "yes"
            elif direction == "BUY_NO":
                action, side = "buy", "no"
            elif direction == "SELL_YES":
                action, side = "sell", "yes"
            else:  # SELL_NO
                action, side = "sell", "no"

            # Get current orderbook for limit price
            market = self.state.get_market(ticker)
            if not market:
                return None

            # Use market order (no limit price) for immediate fill
            result = self._kalshi_client.place_order(
                ticker=ticker,
                action=action,
                side=side,
                count=contracts,
                order_type="market",
            )

            order = result.get("order", {})
            order_id = order.get("order_id", "")

            # Extract fill price from response
            # Kalshi returns yes_price/no_price in cents
            yes_price = order.get("yes_price", 0)
            no_price = order.get("no_price", 0)
            if side == "yes":
                fill_price = yes_price / 100.0 if yes_price > 1 else yes_price
            else:
                fill_price = no_price / 100.0 if no_price > 1 else no_price

            # Fallback to market price if fill price not in response
            if fill_price <= 0:
                fill_price = market["price"]

            logger.info("LIVE FILL %s %s %s x%d @ %.2f (order=%s)",
                         action, side, ticker, contracts, fill_price, order_id)

            self.feed.add(
                FeedEventType.TRADE,
                ticker=ticker,
                message=f"LIVE ORDER {action.upper()} {side.upper()} {ticker}: {contracts}ct, order_id={order_id}",
            )

            return {"fill_price": fill_price, "order_id": order_id, "mode": "live"}

        except Exception as e:
            logger.error("LIVE FILL FAILED %s: %s", ticker, e)
            self.feed.add(
                FeedEventType.ERROR,
                ticker=ticker,
                message=f"ORDER FAILED {ticker}: {e}",
            )
            return None

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _log_skip(self, ticker: str, reason: str) -> None:
        """Log entry skip with reasoning (appears in feed at debug level)."""
        logger.debug("SKIP %s: %s", ticker, reason)

    def get_status(self) -> dict:
        """Engine status for the dashboard."""
        return {
            "running": not self._paused,
            "paused": self._paused,
            "portfolio_heat": round(self.pm.get_portfolio_heat(), 4),
            "open_positions": self.pm.get_open_count(),
            "total_realized": round(self.pm.get_total_realized(), 2),
            "total_unrealized": round(self.pm.get_total_unrealized(), 2),
            "signal_persistence": dict(self._signal_history),
        }
