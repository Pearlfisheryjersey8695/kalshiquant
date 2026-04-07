"""
Step 2.5 -- Risk Model & Position Sizing
  1. Half-Kelly position sizing
  2. Portfolio limits (10% single, 25% category, 60% total, 40% reserve)
  3. Correlation-adjusted VaR
  4. Stop-loss / take-profit / time-based exits
  5. Hedging suggestions (correlated opposites)
  6. Win probability calibration (isotonic regression from backtest data)
"""

import json
import logging
import math
import os
import sqlite3

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from models.base import BaseModel, registry

logger = logging.getLogger(__name__)


def _default_calibration_path() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "models", "saved", "win_prob_calibration.json")


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class WinProbCalibrator:
    """Isotonic regression calibrator: confidence -> actual win probability.

    Uses sklearn IsotonicRegression (PAV algorithm) which is the gold-standard
    nonparametric monotonic fit. Trained on closed positions and backtest trades.
    Falls back to a conservative linear mapping if no calibration data is available.

    The fitted curve is stored as (x_grid, y_grid) so it can round-trip through JSON
    without pickling sklearn objects.
    """

    MIN_TRAINING_SAMPLES = 10
    MIN_PER_CLASS = 3  # need at least this many winners AND losers to trust the fit

    def __init__(self):
        self._x_grid: list[float] = []
        self._y_grid: list[float] = []
        self._n_train: int = 0
        self._is_fitted: bool = False

    # ── Training data sources ────────────────────────────────────
    @staticmethod
    def _confidence_from_edge(edge: float) -> float:
        """Reconstruct ensemble confidence from edge magnitude.
        Mirrors the formula used in the signal ensemble.
        """
        return min(abs(edge) / 0.10, 1.0) * 0.5 + 0.2

    @classmethod
    def _load_backtest_pairs(cls, backtest_path: str = None) -> list[tuple[float, float]]:
        if backtest_path is None:
            backtest_path = os.path.join(_project_root(), "signals", "backtest_results.json")
        try:
            with open(backtest_path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        pairs = []
        for t in data.get("trades", []):
            conf = cls._confidence_from_edge(t.get("edge_at_entry", 0))
            net = t.get("net_pnl", t.get("pnl", 0))
            pairs.append((conf, 1.0 if net > 0 else 0.0))
        return pairs

    @classmethod
    def _load_position_pairs(cls, db_path: str = None) -> list[tuple[float, float]]:
        if db_path is None:
            db_path = os.path.join(_project_root(), "data", "positions.db")
        if not os.path.exists(db_path):
            return []
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT confidence_at_entry, edge_at_entry, realized_pnl "
                "FROM positions WHERE status='closed'"
            )
            rows = cur.fetchall()
            conn.close()
        except sqlite3.Error as e:
            logger.warning("Could not read positions DB: %s", e)
            return []
        pairs = []
        for conf, edge, pnl in rows:
            # Prefer the recorded confidence; fall back to edge-derived
            if conf is None or conf <= 0:
                conf = cls._confidence_from_edge(edge or 0)
            pairs.append((float(conf), 1.0 if (pnl or 0) > 0 else 0.0))
        return pairs

    def fit_from_history(self) -> int:
        """Train on the union of backtest trades and closed live positions.

        Returns the number of training samples used (0 if too few to fit).
        """
        pairs = self._load_position_pairs() + self._load_backtest_pairs()
        if len(pairs) < self.MIN_TRAINING_SAMPLES:
            logger.info(
                "WinProbCalibrator: only %d samples (need %d) — keeping fallback",
                len(pairs), self.MIN_TRAINING_SAMPLES,
            )
            return 0

        n_wins = sum(1 for _, y in pairs if y > 0.5)
        n_losses = len(pairs) - n_wins
        if n_wins < self.MIN_PER_CLASS or n_losses < self.MIN_PER_CLASS:
            # Degenerate sample (e.g. all losers in 15-trade backtest) — refusing
            # to fit prevents the calibrator from clamping every prediction to its
            # y_min/y_max floor and blocking all future trades.
            logger.warning(
                "WinProbCalibrator: degenerate sample (wins=%d losses=%d, need >=%d each) — keeping fallback",
                n_wins, n_losses, self.MIN_PER_CLASS,
            )
            return 0

        x = np.array([p[0] for p in pairs], dtype=float)
        y = np.array([p[1] for p in pairs], dtype=float)

        # Clip the predicted probability range so the calibrator can never
        # output 0 or 1 (those would imply infinite Kelly bets).
        iso = IsotonicRegression(y_min=0.05, y_max=0.95, out_of_bounds="clip")
        iso.fit(x, y)

        # Materialise the curve on a fine grid so it survives JSON round-trip
        grid = np.linspace(0.0, 1.0, 51)
        self._x_grid = grid.tolist()
        self._y_grid = iso.predict(grid).tolist()
        self._n_train = len(pairs)
        self._is_fitted = True
        logger.info("WinProbCalibrator fitted on %d samples", self._n_train)
        return self._n_train

    # Backwards-compatible alias
    def fit_from_backtest(self, backtest_path: str = None):
        return self.fit_from_history()

    # ── Prediction ───────────────────────────────────────────────
    def calibrate(self, confidence: float) -> float:
        """Map raw confidence to calibrated win probability."""
        if not self._is_fitted or not self._x_grid:
            # Fallback: conservative linear mapping (max win_prob = 0.65)
            return 0.5 + max(0.0, min(1.0, confidence)) * 0.15
        c = max(0.0, min(1.0, confidence))
        return float(np.interp(c, self._x_grid, self._y_grid))

    # ── Persistence ──────────────────────────────────────────────
    def save(self, path: str = None):
        if path is None:
            path = _default_calibration_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "x_grid": self._x_grid,
                "y_grid": self._y_grid,
                "n_train": self._n_train,
                "is_fitted": self._is_fitted,
            }, f)

    def load(self, path: str = None):
        if path is None:
            path = _default_calibration_path()
        try:
            with open(path) as f:
                data = json.load(f)
            self._x_grid = list(data.get("x_grid", []))
            self._y_grid = list(data.get("y_grid", []))
            self._n_train = int(data.get("n_train", 0))
            self._is_fitted = bool(data.get("is_fitted", False))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def info(self) -> dict:
        return {
            "is_fitted": self._is_fitted,
            "n_train": self._n_train,
            "min_calibrated": min(self._y_grid) if self._y_grid else None,
            "max_calibrated": max(self._y_grid) if self._y_grid else None,
        }


def kalshi_fee(price: float) -> float:
    """Kalshi taker fee per contract per side, in dollars.

    Formula: ceil(0.07 * P * (1-P) * 100) / 100
    Max fee is 1.75c/side at P=0.50. Round trip = 2 * this.

    Examples:
      P=0.50 -> 1.75c/side, 3.5c RT
      P=0.10 -> 0.63c/side, 1.26c RT
      P=0.05 -> 0.33c/side, 0.66c RT
      P=0.90 -> 0.63c/side, 1.26c RT
    """
    if price <= 0 or price >= 1:
        return 0.0
    return math.ceil(0.07 * price * (1 - price) * 100) / 100


def kalshi_fee_rt(entry_price: float, exit_price: float = None) -> float:
    """Kalshi round-trip fee per contract in dollars.
    If exit_price not provided, estimates exit at same price (conservative for ATM).
    """
    if exit_price is None:
        exit_price = entry_price
    return kalshi_fee(entry_price) + kalshi_fee(exit_price)


class RiskModel(BaseModel):
    name = "risk_model"

    # Portfolio limits
    MAX_SINGLE_PCT = 0.06       # 6% of portfolio per market (reduced from 10%)
    MAX_CATEGORY_PCT = 0.25     # 25% per category
    MAX_TOTAL_PCT = 0.60        # 60% total deployed
    RESERVE_PCT = 0.40          # 40% cash reserve
    STOP_LOSS_PCT = 0.15        # 15% stop-loss
    TAKE_PROFIT_RATIO = 2.0     # 2:1 reward/risk
    KELLY_FRACTION = 0.5        # Half-Kelly
    MAX_KELLY_CAP = 0.03        # Never bet more than 3% of bankroll per position
    # Conservative mapping: XGBoost predict_proba is uncalibrated,
    # so we shrink the confidence -> win_prob mapping.
    # 0.15 scale: max confidence=1.0 -> win_prob=0.65 (not 0.80)
    WIN_PROB_SCALE = 0.15

    def __init__(self, portfolio_value=10000):
        self.portfolio_value = portfolio_value
        self._correlations = {}
        self._var_estimates = {}
        self._calibrator = WinProbCalibrator()
        # Try to load saved calibration; if missing, attempt to fit from history
        self._calibrator.load()
        if not self._calibrator._is_fitted:
            try:
                if self._calibrator.fit_from_history() > 0:
                    self._calibrator.save()
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Calibrator auto-fit failed: %s", e)

    def fit(self, data: pd.DataFrame):
        """Compute cross-market correlations and volatility for VaR."""
        # Build return series per ticker
        returns = {}
        for ticker, grp in data.groupby("ticker"):
            r = grp["close"].pct_change().dropna()
            if len(r) > 5:
                returns[ticker] = r

                # Use non-overlapping windows to avoid autocorrelated vol estimates
                prices = grp["close"].values
                window = min(288, len(prices) - 1)  # 288 five-min bars = 1 day
                if len(prices) > window * 2:  # Need at least 2 non-overlapping windows
                    daily_rets = []
                    for start in range(0, len(prices) - window, window):
                        p0 = prices[start]
                        p1 = prices[start + window]
                        if p0 > 0:
                            daily_rets.append((p1 - p0) / p0)
                    daily_vol = np.std(daily_rets) if len(daily_rets) > 1 else r.std() * np.sqrt(window)
                else:
                    # Too few bars for non-overlapping daily windows
                    daily_vol = r.std() * np.sqrt(min(len(r), window))

                self._var_estimates[ticker] = {
                    "daily_vol": daily_vol,
                    "var_95": np.percentile(r, 5),
                    "var_99": np.percentile(r, 1),
                }

        # Cross-correlations
        if len(returns) > 1:
            ret_df = pd.DataFrame(returns)
            ret_df = ret_df.dropna(axis=1, how="all").ffill(limit=5)
            if ret_df.shape[1] > 1:
                corr = ret_df.corr()
                for i, t1 in enumerate(corr.columns):
                    for t2 in corr.columns[i+1:]:
                        self._correlations[(t1, t2)] = corr.loc[t1, t2]

    def kelly_size(self, win_prob, win_return, loss_return):
        """Half-Kelly with hard safety cap at MAX_KELLY_CAP."""
        if loss_return == 0 or win_return == 0:
            return 0
        b = win_return / abs(loss_return)
        p = win_prob
        q = 1 - p
        kelly = (b * p - q) / b
        sized = max(0, kelly * self.KELLY_FRACTION)
        return min(sized, self.MAX_KELLY_CAP)

    def position_size(self, ticker, edge, confidence, current_price, direction="BUY_YES", category="", volume_24h=0):
        """
        Calculate recommended position size in contracts.
        Fee-aware: rejects trades where gross edge < transaction costs.
        Volume-capped: never exceeds 5% of 24h volume.
        """
        if abs(edge) < 0.01 or confidence < 0.3:
            return 0, {}

        # ── Fee-aware net edge (dynamic Kalshi fee) ───────────────
        fee_impact = kalshi_fee_rt(current_price)
        net_edge = abs(edge) - fee_impact
        if net_edge <= 0:
            return 0, {}  # Gross edge doesn't cover fees

        # Kelly on actual binary contract payoffs with TP/SL targets
        if direction == "BUY_NO":
            cost = 1.0 - current_price
        else:
            cost = current_price

        # TP/SL price targets
        tp_target = abs(edge) * self.TAKE_PROFIT_RATIO  # price move for TP
        sl_target = cost * self.STOP_LOSS_PCT  # price move for SL

        # Win return = take-profit payout minus exit fee
        if direction == "BUY_YES":
            tp_exit_price = min(current_price + tp_target, 0.99)
        else:
            tp_exit_price = max(current_price - tp_target, 0.01)
        win_return = tp_target - kalshi_fee(tp_exit_price)
        # Loss return = stop-loss cost
        loss_return = sl_target

        if win_return <= 0 or loss_return <= 0:
            return 0, {}

        # Win probability: use calibrator (isotonic regression from backtest data)
        # Falls back to conservative linear mapping if no calibration data
        win_prob = self._calibrator.calibrate(confidence)

        kelly_frac = self.kelly_size(win_prob, win_return, loss_return)

        max_dollars = self.portfolio_value * self.MAX_SINGLE_PCT
        kelly_dollars = self.portfolio_value * kelly_frac
        size_dollars = min(kelly_dollars, max_dollars)

        # Cost per contract depends on trade direction
        if direction == "BUY_NO":
            cost_per_contract = 1.0 - current_price  # NO costs (1 - YES_price)
        else:
            cost_per_contract = current_price          # YES costs YES_price
        if cost_per_contract <= 0:
            return 0, {}

        # Fee-to-cost ratio guard: reject when fees exceed 20% of cost basis
        # Allows cheaper contracts (20c+) to trade. Examples:
        # BUY_NO YES=0.82: cost=18c, fee/cost=16.7% → OK
        # BUY_YES YES=0.04: cost=4c, fee/cost=75% → REJECTED
        # BUY_YES YES=0.20: cost=20c, fee/cost=15% → OK
        if fee_impact / cost_per_contract > 0.20:
            return 0, {}

        contracts = int(size_dollars / cost_per_contract)
        contracts = max(0, contracts)

        # ── Contract cap at 5% of 24h volume ─────────────────────
        if volume_24h > 0:
            max_volume_contracts = max(1, int(volume_24h * 0.05))
            contracts = min(contracts, max_volume_contracts)

        if contracts <= 0:
            return 0, {}

        # Stop-loss and take-profit depend on direction
        if direction == "BUY_NO":
            stop_price = min(current_price + (1 - current_price) * self.STOP_LOSS_PCT, 0.99)
            take_profit_price = max(current_price - abs(edge) * self.TAKE_PROFIT_RATIO, 0.01)
            true_max_loss = contracts * (1 - current_price)
            max_gain = contracts * current_price
        else:
            stop_price = current_price * (1 - self.STOP_LOSS_PCT)
            take_profit_price = min(current_price + abs(edge) * self.TAKE_PROFIT_RATIO, 0.99)
            true_max_loss = contracts * current_price
            max_gain = contracts * (1.0 - current_price)
        stop_loss_amount = contracts * abs(current_price - stop_price)

        details = {
            "kelly_fraction": round(kelly_frac, 4),
            "size_dollars": round(size_dollars, 2),
            "contracts": contracts,
            "stop_loss": round(stop_price, 4),
            "take_profit": round(take_profit_price, 4),
            "true_max_loss": round(true_max_loss, 2),
            "stop_loss_amount": round(stop_loss_amount, 2),
            "max_gain": round(max_gain, 2),
            "risk_reward": round(max_gain / true_max_loss, 2) if true_max_loss > 0 else 0,
            "net_edge": round(net_edge, 4),
            "fee_impact": round(fee_impact, 4),
            "total_fees": round(contracts * fee_impact, 2),
        }

        return contracts, details

    def portfolio_var(self, positions):
        """95% portfolio Value-at-Risk in dollars (positive = loss).

        Backed by the Monte Carlo Bernoulli simulator in portfolio_cvar() so the
        number is internally consistent with CVaR. Kept as a thin wrapper for
        existing callers (alerts, dashboard tiles).
        """
        return self.portfolio_cvar(positions).get("var_95", 0.0)

    # ── CVaR / Expected Shortfall ────────────────────────────────────────
    #
    # Why this exists:
    #   Parametric VaR with a Gaussian assumption is wrong for binary contracts —
    #   a Kalshi YES at 30c is Bernoulli(0.30), not Normal. The terminal payoff is
    #   bimodal (0 or 1), so Gaussian VaR underestimates tail loss. CVaR via Monte
    #   Carlo on the actual Bernoulli distribution captures the real tail.
    #
    # Method:
    #   1. For each position, model terminal payoff as Bernoulli(p = current_price).
    #   2. Inject correlation via a Gaussian copula on the latent normal variables
    #      (correlated u_i -> threshold at Phi^-1(p_i) -> correlated outcomes).
    #   3. Compute P&L per simulation, take the 5% tail mean.
    #
    # Output is conservative because Bernoulli at terminal is the *worst-case*
    # interpretation. For mid-life mark-to-market risk you'd add a vol-of-price
    # term, but for Kalshi's hold-to-expiry binaries this is the right primitive.

    CVAR_ALPHA = 0.05       # 95% CVaR
    CVAR_SIMS = 10_000      # Monte Carlo paths

    def portfolio_cvar(self, positions, n_sims: int = None, seed: int = None) -> dict:
        """Monte Carlo CVaR / Expected Shortfall for a portfolio of binary contracts.

        Returns a dict with var_95, cvar_95, worst_case, expected_pnl, n_sims.
        Compatible with the existing portfolio_var() consumer — that key is still
        present and now sourced from the simulation.
        """
        if not positions:
            return {
                "var_95": 0.0, "cvar_95": 0.0, "worst_case": 0.0,
                "expected_pnl": 0.0, "n_sims": 0,
            }

        n_sims = n_sims or self.CVAR_SIMS
        rng = np.random.default_rng(seed)

        # Build per-position arrays
        n = len(positions)
        contracts = np.array([p["contracts"] for p in positions], dtype=float)
        prices = np.array([p["current_price"] for p in positions], dtype=float)
        # Clip away from {0, 1} so the inverse normal is finite
        prices = np.clip(prices, 1e-4, 1 - 1e-4)

        # Direction: BUY_YES pays 1.0 if YES wins; BUY_NO pays 1.0 if NO wins.
        is_yes = np.array([
            p.get("direction", "BUY_YES") == "BUY_YES" for p in positions
        ], dtype=bool)
        # Cost basis (entry assumed at current_price for risk purposes)
        cost = np.where(is_yes, prices, 1 - prices) * contracts

        # Build correlation matrix on the latent Gaussian
        corr = np.eye(n)
        tickers = [p["ticker"] for p in positions]
        for i in range(n):
            for j in range(i + 1, n):
                key = (tickers[i], tickers[j])
                rev = (tickers[j], tickers[i])
                c = self._correlations.get(key, self._correlations.get(rev, 0.0))
                # Clip to keep matrix PSD-friendly
                c = max(-0.95, min(0.95, c))
                corr[i, j] = c
                corr[j, i] = c

        # Cholesky for correlated normals; fall back to identity if not PSD
        try:
            L = np.linalg.cholesky(corr + 1e-8 * np.eye(n))
        except np.linalg.LinAlgError:
            L = np.eye(n)

        # Sample correlated standard normals -> uniforms via Phi -> Bernoulli outcomes
        z = rng.standard_normal(size=(n_sims, n)) @ L.T
        # Phi(z): standard normal CDF
        from math import erf, sqrt as _sqrt
        u = 0.5 * (1.0 + np.vectorize(lambda x: erf(x / _sqrt(2.0)))(z))

        # YES wins iff u < p_yes; NO wins iff u >= p_yes (i.e. YES loses)
        yes_wins = u < prices  # shape (n_sims, n)
        # Payoff per position per sim: contracts * 1 if our side wins, 0 otherwise
        our_side_wins = np.where(is_yes[None, :], yes_wins, ~yes_wins)
        gross_payoff = our_side_wins.astype(float) * contracts  # (n_sims, n)

        # P&L per sim = total payoff - total cost basis
        pnl = gross_payoff.sum(axis=1) - cost.sum()

        # VaR at 5th percentile (loss is negative -> percentile of pnl)
        var_threshold = float(np.percentile(pnl, self.CVAR_ALPHA * 100))
        # CVaR = mean of P&L below VaR threshold
        tail_mask = pnl <= var_threshold
        cvar_value = float(pnl[tail_mask].mean()) if tail_mask.any() else var_threshold

        return {
            "var_95": round(-var_threshold, 2),       # report as positive loss
            "cvar_95": round(-cvar_value, 2),         # report as positive loss
            "worst_case": round(-float(pnl.min()), 2),
            "expected_pnl": round(float(pnl.mean()), 2),
            "n_sims": n_sims,
        }

    # ── Stress scenarios ──────────────────────────────────────────────────
    #
    # The 5 scenarios below are the ones that actually matter for a prediction
    # market book. Each takes the current portfolio and computes the P&L assuming
    # the scenario's "world" is realised — categorical positions get crushed,
    # uncorrelated positions are untouched.

    STRESS_SCENARIOS: dict[str, dict] = {
        "crypto_crash": {
            "description": "BTC -20% in 24h: all crypto YES positions worth 0, NO positions worth 1",
            "category_match": ("crypto", "btc", "eth", "kxbtc", "kxeth"),
            "yes_outcome": 0.0,  # BTC settled NO
        },
        "fed_surprise_hike": {
            "description": "FOMC surprise +50bps: all 'hold' or 'cut' YES positions worth 0",
            "category_match": ("fed", "fomc", "rate", "kxfed"),
            "yes_outcome": 0.0,
        },
        "spx_gap_down": {
            "description": "SPX -5% gap: all 'above' equity YES positions worth 0",
            "category_match": ("spx", "sp500", "kxinx", "kxspx", "equity"),
            "yes_outcome": 0.0,
        },
        "vol_spike": {
            "description": "VIX 2x: vol-sensitive positions mark to 50% of cost basis",
            "category_match": ("vix", "vol"),
            "yes_outcome": None,  # not settlement, mark-to-market only
            "mtm_haircut": 0.50,
        },
        "liquidity_shock": {
            "description": "Exit at 50% of mid: every open position takes 50% slippage on close",
            "category_match": None,  # applies to all positions
            "yes_outcome": None,
            "mtm_haircut": 0.50,
        },
    }

    def stress_test(self, positions) -> dict:
        """Run all 5 stress scenarios against the current book.

        Returns {scenario_name: {pnl, n_hit, description}}.
        """
        results = {}
        for name, spec in self.STRESS_SCENARIOS.items():
            pnl_total = 0.0
            n_hit = 0
            for pos in positions:
                ticker = pos.get("ticker", "").lower()
                contracts = pos["contracts"]
                price = pos["current_price"]
                direction = pos.get("direction", "BUY_YES")
                cost = contracts * (price if direction == "BUY_YES" else 1 - price)

                # Match position against scenario category
                if spec["category_match"] is not None:
                    if not any(tag in ticker for tag in spec["category_match"]):
                        continue
                n_hit += 1

                if spec["yes_outcome"] is not None:
                    # Settlement scenario: terminal payoff
                    yes_pays = spec["yes_outcome"] * contracts
                    if direction == "BUY_YES":
                        payoff = yes_pays
                    else:
                        payoff = (1 - spec["yes_outcome"]) * contracts
                    pnl_total += payoff - cost
                else:
                    # Mark-to-market haircut scenario
                    haircut = spec["mtm_haircut"]
                    pnl_total += -cost * haircut

            results[name] = {
                "description": spec["description"],
                "pnl": round(pnl_total, 2),
                "n_positions_hit": n_hit,
            }
        return results

    def suggest_hedges(self, ticker, direction):
        """Find correlated markets that could serve as hedges."""
        hedges = []
        for (t1, t2), corr in self._correlations.items():
            peer = None
            if t1 == ticker:
                peer = t2
            elif t2 == ticker:
                peer = t1
            if peer and abs(corr) > 0.5:
                # Opposite direction for positive correlation, same for negative
                hedge_dir = "BUY_NO" if (direction == "BUY_YES") == (corr > 0) else "BUY_YES"
                hedges.append({
                    "ticker": peer,
                    "direction": hedge_dir,
                    "correlation": round(corr, 3),
                })

        return sorted(hedges, key=lambda h: abs(h["correlation"]), reverse=True)[:3]

    @staticmethod
    def estimate_slippage(orderbook, contracts: int, direction: str) -> dict:
        """Estimate execution slippage from live orderbook.
        Returns slippage in [0,1] price space, avg fill, mid, and fill info.
        """
        if orderbook is None or not getattr(orderbook, "yes", None):
            return {"slippage": 0.0, "avg_fill": 0.0, "mid": 0.0, "filled": 0, "levels": 0}

        side = "buy_yes" if direction == "BUY_YES" else "buy_no"
        result = orderbook.walk_book(contracts, side)

        return {
            "slippage": round(result["slippage_cents"] / 100.0, 4),
            "avg_fill": round(result["avg_fill_cents"] / 100.0, 4),
            "mid": round(orderbook.get_mid_price_cents() / 100.0, 4),
            "filled": result["filled"],
            "levels": result.get("levels", 0),
        }

    def predict(self, data):
        """Not used directly -- use position_size() and portfolio_var()."""
        return {}


registry.register(RiskModel())
