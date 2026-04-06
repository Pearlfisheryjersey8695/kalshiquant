"""
QuantBrain — Autonomous Quant Trading Agent (v2)

Architecture:
  1. PERCEIVE  — aggregate all market data into a coherent world state
  2. REASON    — evaluate opportunities against thesis + risk framework
  3. DECIDE    — generate trade decisions with full audit trail
  4. EXECUTE   — pass decisions to execution engine with position sizing
  5. REFLECT   — after each trade closes, analyze what went right/wrong
  6. LEARN     — update policy weights via reinforcement learning

Philosophy (SIG-style):
  - Every trade needs a THESIS: why does this edge exist and why hasn't it been arbed away?
  - Question every assumption: is the fair value model right? Is the regime correct?
  - Risk first: never risk more than you can afford to lose on any single idea
  - Fee-awareness: Kalshi fees eat edges — only trade when EV after fees is compelling
  - Contrarian check: if everyone agrees, the edge is probably gone
  - Time decay: prediction markets converge — time is your friend near expiry, enemy far out
  - NEVER trade a dead market: if price isn't moving, your edge can't resolve

v2 fixes:
  - Hard fee gate at 40% (was soft penalty at 50%)
  - Price movement gate: skip markets with < 0.5c std dev
  - Per-ticker cooldown after flat exits
  - Rank all theses by risk-adjusted score, take best
  - Store hours_to_expiry in RL experience
  - Flat trade penalty in reward function
  - Counterfactual skip tracking for Q(skip)
"""

import json
import logging
import math
import os
import random
import time
import threading
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict

from engine.feed import FeedEventType

logger = logging.getLogger("kalshi.brain")


# ── Trade Thesis ──────────────────────────────────────────────────────────

@dataclass
class TradeThesis:
    """Every trade must have a thesis — why does this edge exist?"""
    ticker: str
    direction: str
    edge_source: str
    thesis: str
    confidence_reasons: list
    risk_factors: list
    contrarian_check: str
    time_horizon: str
    invalidation: str
    fair_value: float = 0.0
    market_price: float = 0.0
    edge: float = 0.0
    net_edge: float = 0.0
    fee_impact: float = 0.0
    sentiment_edge: float = 0.0
    regime: str = ""
    conviction: float = 0.0
    hours_to_expiry: float = 999.0  # v2: track for RL
    spread: float = 0.0  # v2: bid-ask spread
    price_volatility: float = 0.0  # v2: recent price movement

    def to_dict(self):
        return asdict(self)


# ── Market Perception ─────────────────────────────────────────────────────

class MarketPerception:
    """Aggregates all available data into a coherent world state."""

    def __init__(self):
        self.market_states: dict = {}
        self.signal_map: dict = {}
        self.regime_map: dict = {}
        self.portfolio_state: dict = {}
        self.alerts: list = []
        self.price_history: dict = defaultdict(lambda: deque(maxlen=200))
        self.edge_history: dict = defaultdict(lambda: deque(maxlen=50))
        self.regime_transitions: dict = defaultdict(list)

    def update(self, state, signals_holder, position_manager, orderbooks=None, feed=None):
        """Refresh perception from all data sources."""
        if state:
            for m in state.get_all_markets():
                ticker = m["ticker"]
                self.market_states[ticker] = m
                self.price_history[ticker].append({
                    "ts": time.time(),
                    "price": m.get("price", 0),
                    "bid": m.get("yes_bid", 0),
                    "ask": m.get("yes_ask", 0),
                    "volume": m.get("volume", 0),
                })

        if signals_holder:
            sig_data = signals_holder.get()
            old_regimes = dict(self.regime_map)
            for s in sig_data.get("signals", []):
                ticker = s["ticker"]
                self.signal_map[ticker] = s
                self.regime_map[ticker] = s.get("regime", "UNKNOWN")
                self.edge_history[ticker].append({
                    "ts": time.time(),
                    "edge": s.get("edge", 0),
                    "net_edge": s.get("net_edge", 0),
                    "confidence": s.get("confidence", 0),
                })
                if ticker in old_regimes and old_regimes[ticker] != s.get("regime"):
                    self.regime_transitions[ticker].append({
                        "ts": time.time(),
                        "from": old_regimes[ticker],
                        "to": s.get("regime"),
                    })

        if position_manager:
            self.portfolio_state = {
                "open_positions": position_manager.get_open_positions(),
                "summary": position_manager.get_summary(),
                "heat": position_manager.get_portfolio_heat(),
                "bankroll": position_manager.bankroll,
            }

        if feed:
            recent = feed.get_recent(20)
            self.alerts = [e for e in recent if e.get("event_type") in ("ERROR", "SIGNAL_CHANGE")]

    def get_price_volatility(self, ticker: str) -> float:
        """Compute recent price standard deviation from history."""
        hist = list(self.price_history.get(ticker, []))
        if len(hist) < 3:
            return 0.0
        prices = [h["price"] for h in hist[-20:] if h["price"] > 0]
        if len(prices) < 3:
            return 0.0
        mean = sum(prices) / len(prices)
        var = sum((p - mean) ** 2 for p in prices) / len(prices)
        return math.sqrt(var)

    def get_spread(self, ticker: str) -> float:
        """Get current bid-ask spread."""
        market = self.market_states.get(ticker, {})
        bid = market.get("yes_bid", 0)
        ask = market.get("yes_ask", 0)
        if bid > 0 and ask > 0:
            return ask - bid
        return 0.10  # default wide spread if no quotes

    def get_opportunity_universe(self) -> list:
        """Return all signals ranked by opportunity quality."""
        opportunities = []
        open_tickers = {p.get("ticker") for p in self.portfolio_state.get("open_positions", [])}

        for ticker, signal in self.signal_map.items():
            if ticker in open_tickers:
                continue
            if signal.get("direction") == "HOLD":
                continue

            net_edge = signal.get("net_edge", 0)
            if net_edge <= 0:
                continue

            regime = signal.get("regime", "UNKNOWN")
            regime_mult = {"CONVERGENCE": 1.0, "MEAN_REVERTING": 0.9, "TRENDING": 0.8,
                          "HIGH_VOLATILITY": 0.5, "STALE": 0.0}.get(regime, 0.3)

            # v2: Factor in fee ratio for quality scoring
            fee_impact = signal.get("fee_impact", signal.get("risk", {}).get("fee_impact", 0))
            fee_ratio = fee_impact / abs(signal.get("edge", 0.01)) if signal.get("edge") else 1
            fee_quality = max(0, 1 - fee_ratio)  # 0 = all fees, 1 = no fees

            quality = abs(net_edge) * signal.get("confidence", 0) * regime_mult * fee_quality

            # Edge stability
            edge_hist = list(self.edge_history.get(ticker, []))
            edge_stable = True
            edge_trend = 0
            if len(edge_hist) >= 3:
                recent_edges = [e["net_edge"] for e in edge_hist[-5:]]
                edge_trend = recent_edges[-1] - recent_edges[0]
                if edge_trend < -0.01:
                    edge_stable = False
                    quality *= 0.7

            # v2: Price volatility (is the market actually moving?)
            price_vol = self.get_price_volatility(ticker)
            spread = self.get_spread(ticker)

            opportunities.append({
                "ticker": ticker,
                "signal": signal,
                "quality": quality,
                "edge_stable": edge_stable,
                "edge_trend": edge_trend,
                "regime": regime,
                "regime_mult": regime_mult,
                "price_volatility": price_vol,
                "spread": spread,
                "fee_ratio": fee_ratio,
            })

        return sorted(opportunities, key=lambda x: x["quality"], reverse=True)


# ── Reasoning Engine ──────────────────────────────────────────────────────

class ReasoningEngine:
    """Questions every assumption before making a trade."""

    MAX_FEE_RATIO = 0.40  # v2: HARD reject if fees > 40% of edge
    MIN_PRICE_VOL = 0.003  # v2: minimum price std dev to consider market alive

    def __init__(self):
        # Paper trading thresholds — lower to generate RL training data
        # Raise these before going live: conviction=0.60, min_net_edge=0.03
        self.conviction_threshold = 0.45  # lowered from 0.55 for data generation
        self.max_positions = 8  # more slots for data generation
        self.max_heat = 0.45  # slightly more aggressive for paper
        self.min_net_edge = 0.015  # lowered from 0.02 — capture more trades

    def evaluate_opportunity(self, opp: dict, perception: MarketPerception) -> TradeThesis | None:
        """Evaluate an opportunity and build a thesis if warranted."""
        signal = opp["signal"]
        ticker = signal["ticker"]
        market = perception.market_states.get(ticker, {})

        price = signal.get("current_price", 0.5)
        fair_value = signal.get("fair_value", price)
        edge = signal.get("edge", 0)
        net_edge = signal.get("net_edge", 0)
        direction = signal.get("direction", "HOLD")
        confidence = signal.get("confidence", 0)
        regime = signal.get("regime", "UNKNOWN")

        checks = []
        risk_factors = []
        conviction = 0.5

        # 1. EDGE CHECK — must be real and reasonable
        if abs(net_edge) < self.min_net_edge:
            return None
        # Reject absurd edges (> 25c) — model artifact, not real alpha
        if abs(edge) > 0.25:
            return None
        checks.append(f"Net edge {net_edge:.3f} > min {self.min_net_edge}")
        conviction += min(abs(net_edge) * 2, 0.15)

        # 2. FEE CHECK — v2: HARD GATE at 40%
        fee_impact = signal.get("fee_impact", signal.get("risk", {}).get("fee_impact", 0))
        fee_ratio = fee_impact / abs(edge) if edge != 0 else 1
        if fee_ratio > self.MAX_FEE_RATIO:
            return None  # v2: hard reject, not soft penalty
        checks.append(f"Fee ratio {fee_ratio:.0%} < {self.MAX_FEE_RATIO:.0%} limit")

        # 3. PRICE MOVEMENT CHECK — v2: is this market actually alive?
        price_vol = opp.get("price_volatility", 0)
        if price_vol < self.MIN_PRICE_VOL and price_vol > 0:
            risk_factors.append(f"Dead market: price vol {price_vol:.4f} < {self.MIN_PRICE_VOL}")
            conviction -= 0.15  # heavy penalty for dead markets
        elif price_vol >= 0.01:
            checks.append(f"Market active: price vol {price_vol:.4f}")
            conviction += 0.05

        # 4. SPREAD CHECK — v2: is spread eating the edge?
        spread = opp.get("spread", 0)
        if spread > 0 and abs(edge) > 0:
            spread_ratio = spread / abs(edge)
            if spread_ratio > 0.5:
                risk_factors.append(f"Wide spread: {spread:.3f} = {spread_ratio:.0%} of edge")
                conviction -= 0.10

        # 5. REGIME CHECK
        regime_mult = opp.get("regime_mult", 0.5)
        if regime == "STALE":
            return None
        if regime_mult >= 0.8:
            checks.append(f"Regime {regime} supports trade")
            conviction += 0.05
        elif regime_mult < 0.5:
            risk_factors.append(f"Regime {regime} weak (mult={regime_mult})")
            conviction -= 0.05

        # 6. EDGE STABILITY
        if not opp.get("edge_stable", True):
            risk_factors.append(f"Edge decaying (trend={opp.get('edge_trend', 0):.4f})")
            conviction -= 0.10
        else:
            checks.append("Edge stable")
            conviction += 0.03

        # 7. CONTRARIAN CHECK
        contrarian_note = ""
        if price > 0.90:
            contrarian_note = f"Strong YES consensus at {price:.0%}"
            if direction == "BUY_YES":
                risk_factors.append("Buying >90% — tiny upside, large downside")
                conviction -= 0.10
            else:
                checks.append("Contrarian NO at >90%")
                conviction += 0.05
        elif price < 0.10:
            contrarian_note = f"Strong NO consensus at {price:.0%}"
            if direction == "BUY_NO":
                risk_factors.append("Buying NO at <10% — tiny upside")
                conviction -= 0.10
            else:
                checks.append("Contrarian YES at <10%")
                conviction += 0.05
        else:
            contrarian_note = f"Market at {price:.0%}"

        # 8. TIME DECAY
        hours_left = 999
        exp_time = market.get("expiration_time") or signal.get("expiration_time")
        if exp_time:
            try:
                exp = datetime.fromisoformat(str(exp_time).replace("Z", "+00:00"))
                hours_left = max(0, (exp - datetime.now(timezone.utc)).total_seconds() / 3600)
            except Exception:
                pass

        time_horizon = ""
        if hours_left < 2:
            time_horizon = "IMMINENT"
            conviction += 0.10
        elif hours_left < 24:
            time_horizon = f"SHORT ({hours_left:.0f}h)"
            conviction += 0.05
        elif hours_left < 168:
            time_horizon = f"MEDIUM ({hours_left/24:.0f}d)"
        else:
            time_horizon = f"LONG ({hours_left/24:.0f}d)"
            conviction -= 0.05

        # 9. PORTFOLIO CHECK
        heat = perception.portfolio_state.get("heat", 0)
        n_open = len(perception.portfolio_state.get("open_positions", []))
        if heat > self.max_heat:
            risk_factors.append(f"Heat {heat:.0%} > {self.max_heat:.0%}")
            conviction -= 0.15
        if n_open >= self.max_positions:
            risk_factors.append(f"Max {self.max_positions} positions reached")
            conviction -= 0.15

        # 10. SENTIMENT + PREDICTION
        sentiment_edge = signal.get("sentiment_edge", 0)
        if abs(sentiment_edge) > 0.02:
            aligns = (sentiment_edge > 0 and direction == "BUY_YES") or (sentiment_edge < 0 and direction == "BUY_NO")
            if aligns:
                checks.append(f"Sentiment aligns ({sentiment_edge:.3f})")
                conviction += 0.03
            else:
                risk_factors.append(f"Sentiment disagrees ({sentiment_edge:.3f})")
                conviction -= 0.03

        pred_dir = signal.get("price_prediction_1h", 0)
        if pred_dir != 0:
            agrees = (pred_dir > 0 and direction == "BUY_YES") or (pred_dir < 0 and direction == "BUY_NO")
            if agrees:
                checks.append("XGBoost agrees")
                conviction += 0.03
            else:
                risk_factors.append("XGBoost disagrees")
                conviction -= 0.03

        # 11. EXTERNAL DATA CHECK — does external model support the thesis?
        try:
            from data.external_feeds import feed_manager
            ext_result = feed_manager.get_probability_for_ticker(ticker, price, hours_left)
            if ext_result and not ext_result.get("stale", True):
                ext_prob = ext_result["probability"]
                # Compare external probability to market price
                if direction == "BUY_YES":
                    ext_edge = ext_prob - price
                else:
                    ext_edge = (1 - ext_prob) - (1 - price)  # = price - ext_prob

                if ext_edge > 0.02:
                    checks.append(f"External model supports: prob={ext_prob:.2f} vs market={price:.2f} (edge={ext_edge:+.3f})")
                    conviction += 0.10
                elif ext_edge < -0.02:
                    risk_factors.append(f"External model DISAGREES: prob={ext_prob:.2f} vs market={price:.2f} (edge={ext_edge:+.3f})")
                    conviction -= 0.15  # big penalty for disagreement
                else:
                    checks.append(f"External model neutral: prob={ext_prob:.2f} ~ market={price:.2f}")
            else:
                risk_factors.append("No external data model for this market")
                conviction -= 0.05
        except Exception as e:
            logger.debug("External data check failed for %s: %s", ticker, e)

        conviction = max(0, min(1, conviction))
        if conviction < self.conviction_threshold:
            return None

        # Build thesis
        edge_source = signal.get("strategy", "convergence")
        if hours_left < 48:
            edge_source = "convergence_near_expiry"

        thesis_text = self._generate_thesis(ticker, direction, edge, net_edge, regime,
                                            hours_left, market.get("title", ""), contrarian_note)

        invalidation = f"Exit if: edge < {self.min_net_edge:.3f}, regime changes, or stop-loss hit"

        return TradeThesis(
            ticker=ticker, direction=direction, edge_source=edge_source,
            thesis=thesis_text, confidence_reasons=checks, risk_factors=risk_factors,
            contrarian_check=contrarian_note, time_horizon=time_horizon,
            invalidation=invalidation, fair_value=fair_value, market_price=price,
            edge=edge, net_edge=net_edge, fee_impact=fee_impact,
            sentiment_edge=sentiment_edge, regime=regime,
            conviction=round(conviction, 3),
            hours_to_expiry=round(hours_left, 1),
            spread=round(spread, 4),
            price_volatility=round(price_vol, 4),
        )

    def _generate_thesis(self, ticker, direction, edge, net_edge, regime,
                         hours_left, title, contrarian_note) -> str:
        dir_word = "YES" if direction == "BUY_YES" else "NO"
        parts = [f"BUY {dir_word} on {ticker}"]
        if "FED" in ticker.upper():
            parts.append("Fed rate — FOMC expectations")
        elif "BTC" in ticker.upper():
            parts.append("Bitcoin price — crypto sentiment")
        elif "AAAG" in ticker.upper():
            parts.append("Gas price — supply/demand")
        else:
            parts.append(f"{title[:40]}")
        parts.append(f"Edge: {net_edge:+.3f} net")
        parts.append(f"Regime: {regime}")
        if hours_left < 48:
            parts.append(f"Expiry: {hours_left:.0f}h")
        parts.append(contrarian_note)
        return " | ".join(parts)


# ── Experience Memory ─────────────────────────────────────────────────────

@dataclass
class TradeExperience:
    ticker: str
    direction: str
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    contracts: int
    gross_pnl: float
    net_pnl: float
    fees: float
    hold_minutes: float
    exit_reason: str
    regime_at_entry: str
    edge_at_entry: float
    net_edge_at_entry: float
    confidence_at_entry: float
    conviction_at_entry: float
    sentiment_at_entry: float
    hours_to_expiry_at_entry: float
    heat_at_entry: float
    thesis: str
    risk_factors: list
    was_profitable: bool = False
    edge_realized: float = 0.0
    thesis_correct: bool = False
    was_flat: bool = False  # v2: market didn't move at all
    lesson: str = ""

    def compute_outcome(self):
        self.was_profitable = self.net_pnl > 0
        price_move = abs(self.exit_price - self.entry_price)
        self.was_flat = price_move < 0.005  # v2: less than half a cent move
        if self.edge_at_entry != 0:
            self.edge_realized = price_move / abs(self.edge_at_entry)
        self.thesis_correct = self.was_profitable and self.exit_reason in ("TAKE_PROFIT", "SETTLEMENT")

        if self.was_flat:
            self.lesson = f"FLAT MARKET: price didn't move ({price_move:.4f}). Pure fee loss. Avoid this state."
        elif self.was_profitable:
            if self.exit_reason == "TAKE_PROFIT":
                self.lesson = "Thesis confirmed — edge captured"
            elif self.exit_reason == "SETTLEMENT":
                self.lesson = "Held to settlement — convergence worked"
            else:
                self.lesson = f"Profitable via {self.exit_reason}"
        else:
            if self.exit_reason == "STOP_LOSS":
                self.lesson = f"Stop-loss hit — edge realized: {self.edge_realized:.0%}"
            elif self.exit_reason == "EDGE_DECAY":
                self.lesson = "Edge decayed — timing wrong"
            elif self.exit_reason == "REGIME_CHANGE":
                self.lesson = f"Regime changed from {self.regime_at_entry} — thesis invalidated"
            else:
                self.lesson = f"Lost via {self.exit_reason}"


# ── RL Learner ────────────────────────────────────────────────────────────

class RLLearner:
    """Tabular Q-learning with counterfactual skip tracking."""

    def __init__(self, save_path=None):
        self.save_path = save_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "models", "saved", "rl_policy.json"
        )
        self.q_table: dict = {}
        self.experience_buffer: deque = deque(maxlen=1000)
        self.learning_rate = 0.15  # v2: faster learning with limited data
        self.discount_factor = 0.95
        self.exploration_rate = 0.15
        self.min_exploration = 0.05
        self.decay_rate = 0.995
        self.n_updates = 0
        self._lock = threading.Lock()
        # v2: Track skipped opportunities for counterfactual learning
        self._skip_tracking: dict = {}  # state_key -> {ticker, price_at_skip, ts}
        self._load()

    def _discretize_state(self, experience: TradeExperience) -> str:
        """v2: Reduced to 3 dimensions for faster convergence."""
        regime = experience.regime_at_entry

        # Tighter edge buckets for prediction markets
        edge = abs(experience.net_edge_at_entry)
        if edge < 0.03:
            edge_b = "tiny"
        elif edge < 0.06:
            edge_b = "small"
        elif edge < 0.10:
            edge_b = "med"
        else:
            edge_b = "large"

        # Time bucket
        hours = experience.hours_to_expiry_at_entry
        time_b = "near" if hours < 24 else "mid" if hours < 168 else "far"

        return f"{regime}|{edge_b}|{time_b}"

    def _compute_reward(self, exp: TradeExperience) -> float:
        """v2: Heavy penalty for flat trades (fee drag without resolution)."""
        pnl = exp.net_pnl
        cost = exp.contracts * exp.entry_price
        if cost <= 0:
            cost = exp.contracts * (1 - exp.entry_price)  # BUY_NO
        pnl_pct = pnl / cost if cost > 0 else 0

        # v2: FLAT TRADE = worst outcome. You paid fees for nothing.
        if exp.was_flat:
            return -0.25  # strong negative signal — avoid this state

        # Asymmetric: losses hurt 1.5x more
        if pnl_pct < 0:
            reward = pnl_pct * 1.5
        else:
            reward = pnl_pct

        # Bonus for thesis-correct resolution
        if exp.thesis_correct:
            reward += 0.05

        # Time efficiency bonus
        hold_hours = exp.hold_minutes / 60
        if hold_hours > 0 and pnl > 0:
            reward += max(0, 1 - hold_hours / 24) * 0.05

        return round(reward, 4)

    def record_experience(self, exp: TradeExperience):
        exp.compute_outcome()
        with self._lock:
            self.experience_buffer.append(exp)
            self._update_q_value(exp)
            self.n_updates += 1
            if self.n_updates % 10 == 0:
                self.exploration_rate = max(self.min_exploration,
                                           self.exploration_rate * self.decay_rate)
            if self.n_updates % 10 == 0:
                self._save()
        logger.info("RL: %s pnl=$%.2f | %s | Q-states=%d",
                    exp.ticker, exp.net_pnl, exp.lesson, len(self.q_table))

    def _update_q_value(self, exp: TradeExperience):
        state = self._discretize_state(exp)
        reward = self._compute_reward(exp)
        if state not in self.q_table:
            self.q_table[state] = {"trade": 0.0, "skip": 0.0, "count": 0, "skip_count": 0}
        old_q = self.q_table[state]["trade"]
        self.q_table[state]["trade"] = old_q + self.learning_rate * (reward - old_q)
        self.q_table[state]["count"] += 1

    def record_skip(self, state_key: str, ticker: str, price: float):
        """v2: Track a skipped opportunity for counterfactual learning."""
        self._skip_tracking[state_key] = {
            "ticker": ticker, "price": price, "ts": time.time()
        }

    def update_skip_counterfactuals(self, perception):
        """v2: Check skipped trades — if market was flat, skipping was correct."""
        with self._lock:
            to_remove = []
            for state_key, info in self._skip_tracking.items():
                elapsed = time.time() - info["ts"]
                if elapsed < 1800:  # wait 30 min before evaluating
                    continue
                to_remove.append(state_key)

                ticker = info["ticker"]
                market = perception.market_states.get(ticker, {})
                current_price = market.get("price", 0)
                skip_price = info["price"]

                if current_price <= 0:
                    continue

                price_move = abs(current_price - skip_price)
                if state_key not in self.q_table:
                    self.q_table[state_key] = {"trade": 0.0, "skip": 0.0, "count": 0, "skip_count": 0}

                if price_move < 0.005:
                    # Market was flat — skipping was correct
                    old_q = self.q_table[state_key]["skip"]
                    self.q_table[state_key]["skip"] = old_q + self.learning_rate * (0.1 - old_q)
                    self.q_table[state_key]["skip_count"] += 1
                    logger.debug("RL counterfactual: %s flat after skip — Q(skip) updated", ticker)

            for key in to_remove:
                self._skip_tracking.pop(key, None)

    def should_trade(self, state_key: str) -> tuple:
        if state_key not in self.q_table:
            return True, 0.0, "No experience — default trade"

        q = self.q_table[state_key]
        trade_q = q["trade"]
        skip_q = q.get("skip", 0)
        count = q.get("count", 0)

        if random.random() < self.exploration_rate:
            return True, 0.0, f"Exploring (eps={self.exploration_rate:.2f})"

        # v2: Compare trade vs skip Q-values
        if trade_q > skip_q + 0.03:
            adjustment = min(trade_q * 0.5, 0.15)
            return True, adjustment, f"RL: trade Q={trade_q:.3f} > skip Q={skip_q:.3f} ({count} exp)"
        elif skip_q > trade_q + 0.03:
            return False, -0.10, f"RL: skip Q={skip_q:.3f} > trade Q={trade_q:.3f} — SKIP"
        else:
            return True, 0.0, f"RL neutral: trade={trade_q:.3f} skip={skip_q:.3f}"

    def get_performance_by_state(self) -> dict:
        return {k: {"q_trade": v["trade"], "q_skip": v.get("skip", 0),
                     "count": v["count"], "skip_count": v.get("skip_count", 0)}
                for k, v in self.q_table.items()}

    def get_lessons_learned(self) -> list:
        lessons = []
        for exp in list(self.experience_buffer)[-20:]:
            lessons.append({
                "ticker": exp.ticker, "direction": exp.direction,
                "pnl": round(exp.net_pnl, 2), "lesson": exp.lesson,
                "regime": exp.regime_at_entry, "conviction": exp.conviction_at_entry,
                "edge_realized": round(exp.edge_realized, 2),
                "thesis_correct": exp.thesis_correct, "was_flat": exp.was_flat,
            })
        return lessons

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
            with open(self.save_path, "w") as f:
                json.dump({"q_table": self.q_table, "n_updates": self.n_updates,
                           "exploration_rate": self.exploration_rate}, f, indent=2)
        except Exception as e:
            logger.warning("RL save failed: %s", e)

    def _load(self):
        try:
            with open(self.save_path) as f:
                data = json.load(f)
            self.q_table = data.get("q_table", {})
            self.n_updates = data.get("n_updates", 0)
            self.exploration_rate = data.get("exploration_rate", 0.15)
            logger.info("RL: Loaded %d Q-states, %d prior updates", len(self.q_table), self.n_updates)
        except (FileNotFoundError, json.JSONDecodeError):
            pass


# ── The QuantBrain Agent ──────────────────────────────────────────────────

class QuantBrain:
    """Autonomous trading agent. Perceive -> Reason -> Decide -> Execute -> Reflect -> Learn."""

    def __init__(self, execution_engine, position_manager, risk_model,
                 state, orderbooks, signals_holder, feed):
        self.execution_engine = execution_engine
        self.position_manager = position_manager
        self.risk_model = risk_model
        self.state = state
        self.orderbooks = orderbooks
        self.signals_holder = signals_holder
        self.feed = feed

        self.perception = MarketPerception()
        self.reasoning = ReasoningEngine()
        self.learner = RLLearner()

        self._pending_theses: dict = {}
        self._decision_log: deque = deque(maxlen=100)
        self._cycle_count = 0
        # v2: Per-ticker cooldown after flat exits
        self._ticker_cooldown: dict = {}  # ticker -> cooldown_until_ts

    def run_cycle(self) -> dict:
        self._cycle_count += 1
        cycle_start = time.time()

        # 1. PERCEIVE
        self.perception.update(
            self.state, self.signals_holder,
            self.position_manager, self.orderbooks, self.feed
        )

        # v2: Update skip counterfactuals
        self.learner.update_skip_counterfactuals(self.perception)

        # 2. REASON about opportunities
        opportunities = self.perception.get_opportunity_universe()
        theses = []
        skipped = []

        for opp in opportunities[:10]:
            ticker = opp["ticker"]

            # v2: Check per-ticker cooldown
            cooldown_until = self._ticker_cooldown.get(ticker, 0)
            if time.time() < cooldown_until:
                remaining = int(cooldown_until - time.time())
                skipped.append({"ticker": ticker, "reason": f"Cooldown ({remaining}s remaining)"})
                continue

            thesis = self.reasoning.evaluate_opportunity(opp, self.perception)
            if thesis is None:
                skipped.append({"ticker": ticker, "reason": "Failed pre-trade checklist"})
                continue

            # 3. CHECK RL POLICY
            state_key = self._make_rl_state(thesis)
            should_trade, rl_adjustment, rl_reason = self.learner.should_trade(state_key)

            thesis.conviction += rl_adjustment
            thesis.conviction = max(0, min(1, thesis.conviction))

            if not should_trade:
                skipped.append({"ticker": ticker, "reason": f"RL: {rl_reason}"})
                # v2: Track skip for counterfactual
                self.learner.record_skip(state_key, ticker, thesis.market_price)
                continue

            if thesis.conviction < self.reasoning.conviction_threshold:
                skipped.append({"ticker": ticker, "reason": f"Conv {thesis.conviction:.2f} < threshold"})
                self.learner.record_skip(state_key, ticker, thesis.market_price)
                continue

            theses.append(thesis)

        # 4. DECIDE — v2: rank ALL theses, take the BEST (not first that passes)
        entries = []
        if theses:
            # Sort by risk-adjusted quality: conviction * net_edge / fee_impact
            theses.sort(key=lambda t: t.conviction * abs(t.net_edge) / max(t.fee_impact, 0.001), reverse=True)

            for thesis in theses[:2]:  # max 2 entries per cycle
                signal = dict(self.perception.signal_map.get(thesis.ticker, {}))
                signal["_brain_thesis"] = thesis.to_dict()
                signal["_brain_conviction"] = thesis.conviction

                # Use parlay_arb strategy for parlay signals (hold to settlement)
                # Use regime-based strategy for everything else
                if signal.get("_signal_source") == "parlay_pricer" or signal.get("strategy") == "parlay_arb":
                    signal["strategy"] = "parlay_arb"
                else:
                    regime_to_strategy = {
                        "CONVERGENCE": "convergence", "MEAN_REVERTING": "mean_reversion",
                        "TRENDING": "momentum", "HIGH_VOLATILITY": "event_driven",
                    }
                    signal["strategy"] = regime_to_strategy.get(thesis.regime, "convergence")

                result = self.execution_engine.evaluate_entries([signal])
                if result:
                    entries.extend(result)
                    self._pending_theses[thesis.ticker] = thesis
                    self.feed.add(
                        FeedEventType.TRADE, ticker=thesis.ticker,
                        message=f"BRAIN ENTRY {thesis.direction} {thesis.ticker}: "
                                f"conv={thesis.conviction:.2f}, edge={thesis.net_edge:.4f}",
                    )
                else:
                    gate_result = self.execution_engine._check_entry_gates(signal)
                    logger.info("Brain REJECTED: %s -> %s", thesis.ticker, gate_result.get("reason", "?"))

        # 5. REFLECT
        self._reflect_on_closes()

        elapsed = time.time() - cycle_start
        decision = {
            "cycle": self._cycle_count,
            "ts": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round(elapsed * 1000),
            "opportunities_evaluated": len(opportunities[:10]),
            "theses_generated": len(theses),
            "entries_executed": len(entries),
            "skipped": len(skipped),
            "open_positions": len(self.perception.portfolio_state.get("open_positions", [])),
            "heat": self.perception.portfolio_state.get("heat", 0),
            "rl_exploration": self.learner.exploration_rate,
            "rl_total_experiences": len(self.learner.experience_buffer),
        }
        self._decision_log.appendleft(decision)

        if entries:
            logger.info("Brain cycle %d: %d entries, %d skipped, %.0fms",
                        self._cycle_count, len(entries), len(skipped), elapsed * 1000)

        return decision

    def _reflect_on_closes(self):
        closed = self.position_manager.get_closed_positions()
        for pos in closed[-10:]:
            ticker = pos.get("ticker", "")
            if ticker not in self._pending_theses:
                continue
            thesis = self._pending_theses.pop(ticker)

            exp = TradeExperience(
                ticker=ticker, direction=pos.get("direction", ""),
                entry_price=pos.get("entry_price", 0), exit_price=pos.get("exit_price", 0),
                entry_time=pos.get("entry_time", ""), exit_time=pos.get("exit_time", ""),
                contracts=pos.get("contracts", 0),
                gross_pnl=pos.get("realized_pnl", 0) + pos.get("fees_paid", 0),
                net_pnl=pos.get("realized_pnl", 0), fees=pos.get("fees_paid", 0),
                hold_minutes=pos.get("hold_time_minutes", 0),
                exit_reason=pos.get("exit_reason", ""),
                regime_at_entry=thesis.regime,
                edge_at_entry=thesis.edge,
                net_edge_at_entry=thesis.net_edge,
                confidence_at_entry=thesis.conviction,
                conviction_at_entry=thesis.conviction,
                sentiment_at_entry=thesis.sentiment_edge,
                hours_to_expiry_at_entry=thesis.hours_to_expiry,  # v2: actual value
                heat_at_entry=self.perception.portfolio_state.get("heat", 0),
                thesis=thesis.thesis, risk_factors=thesis.risk_factors,
            )

            self.learner.record_experience(exp)

            # v2: Cooldown after flat exit (30 min)
            if exp.was_flat:
                self._ticker_cooldown[ticker] = time.time() + 1800
                logger.info("Brain: %s COOLDOWN 30min (flat exit, fee drag only)", ticker)

    def _make_rl_state(self, thesis: TradeThesis) -> str:
        exp = TradeExperience(
            ticker=thesis.ticker, direction=thesis.direction,
            entry_price=thesis.market_price, exit_price=0,
            entry_time="", exit_time="", contracts=0,
            gross_pnl=0, net_pnl=0, fees=0, hold_minutes=0, exit_reason="",
            regime_at_entry=thesis.regime, edge_at_entry=thesis.edge,
            net_edge_at_entry=thesis.net_edge, confidence_at_entry=thesis.conviction,
            conviction_at_entry=thesis.conviction, sentiment_at_entry=thesis.sentiment_edge,
            hours_to_expiry_at_entry=thesis.hours_to_expiry,
            heat_at_entry=self.perception.portfolio_state.get("heat", 0),
            thesis=thesis.thesis, risk_factors=thesis.risk_factors,
        )
        return self.learner._discretize_state(exp)

    def get_status(self) -> dict:
        return {
            "cycle_count": self._cycle_count,
            "recent_decisions": list(self._decision_log)[:10],
            "pending_theses": {k: v.to_dict() for k, v in self._pending_theses.items()},
            "ticker_cooldowns": {k: int(v - time.time()) for k, v in self._ticker_cooldown.items() if v > time.time()},
            "rl_stats": {
                "total_experiences": len(self.learner.experience_buffer),
                "q_states": len(self.learner.q_table),
                "exploration_rate": round(self.learner.exploration_rate, 3),
                "n_updates": self.learner.n_updates,
                "pending_skips": len(self.learner._skip_tracking),
            },
            "rl_performance": self.learner.get_performance_by_state(),
            "lessons_learned": self.learner.get_lessons_learned(),
            "perception": {
                "tracked_markets": len(self.perception.market_states),
                "active_signals": len(self.perception.signal_map),
                "regime_distribution": dict(Counter(self.perception.regime_map.values()))
                    if self.perception.regime_map else {},
            },
        }
