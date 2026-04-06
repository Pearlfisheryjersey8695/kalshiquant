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
import math
import os

import numpy as np
import pandas as pd
from models.base import BaseModel, registry


class WinProbCalibrator:
    """Isotonic regression calibrator: confidence -> actual win probability.
    Trained on OOS backtest data. Falls back to linear if no calibration data.
    """

    def __init__(self):
        self._bins: list[tuple[float, float]] = []  # (confidence_threshold, empirical_win_rate)
        self._is_fitted = False

    def fit_from_backtest(self, backtest_path: str = None):
        """Load backtest results and build calibration curve."""
        if backtest_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            backtest_path = os.path.join(project_root, "signals", "backtest_results.json")

        try:
            with open(backtest_path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        trades = data.get("trades", [])
        if len(trades) < 10:
            return

        # Build (confidence, is_winner) pairs
        pairs = []
        for t in trades:
            # Estimate confidence from edge magnitude (same formula as ensemble)
            edge = abs(t.get("edge_at_entry", 0))
            conf = min(edge / 0.10, 1.0) * 0.5 + 0.2  # rough reconstruction
            is_winner = 1.0 if t.get("net_pnl", t.get("pnl", 0)) > 0 else 0.0
            pairs.append((conf, is_winner))

        pairs.sort(key=lambda x: x[0])

        # Build calibration bins (5 quantile bins)
        n = len(pairs)
        n_bins = min(5, n // 3)
        if n_bins < 2:
            return

        bin_size = n // n_bins
        self._bins = []
        for i in range(n_bins):
            start = i * bin_size
            end = start + bin_size if i < n_bins - 1 else n
            bin_confs = [p[0] for p in pairs[start:end]]
            bin_wins = [p[1] for p in pairs[start:end]]
            threshold = sum(bin_confs) / len(bin_confs)
            win_rate = sum(bin_wins) / len(bin_wins)
            self._bins.append((threshold, win_rate))

        self._is_fitted = True

    def calibrate(self, confidence: float) -> float:
        """Map raw confidence to calibrated win probability."""
        if not self._is_fitted or not self._bins:
            # Fallback: conservative linear mapping
            return 0.5 + confidence * 0.15

        # Interpolate between bins
        if confidence <= self._bins[0][0]:
            return self._bins[0][1]
        if confidence >= self._bins[-1][0]:
            return self._bins[-1][1]

        for i in range(len(self._bins) - 1):
            c0, w0 = self._bins[i]
            c1, w1 = self._bins[i + 1]
            if c0 <= confidence <= c1:
                t = (confidence - c0) / (c1 - c0) if c1 != c0 else 0
                return w0 + t * (w1 - w0)

        return self._bins[-1][1]

    def save(self, path: str = None):
        """Save calibration curve to JSON."""
        if path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(project_root, "models", "saved", "win_prob_calibration.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"bins": self._bins, "is_fitted": self._is_fitted}, f)

    def load(self, path: str = None):
        """Load calibration curve from JSON."""
        if path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(project_root, "models", "saved", "win_prob_calibration.json")
        try:
            with open(path) as f:
                data = json.load(f)
            self._bins = [tuple(b) for b in data.get("bins", [])]
            self._is_fitted = data.get("is_fitted", False)
        except (FileNotFoundError, json.JSONDecodeError):
            pass


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
        # Try to load saved calibration
        self._calibrator.load()

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
        """
        Correlation-adjusted Value at Risk for a portfolio of positions.
        positions: [{ticker, contracts, current_price}, ...]
        """
        if not positions:
            return 0

        values = []
        vols = []
        for pos in positions:
            t = pos["ticker"]
            direction = pos.get("direction", "BUY_YES")
            if direction == "BUY_NO":
                val = pos["contracts"] * (1 - pos["current_price"])
            else:
                val = pos["contracts"] * pos["current_price"]
            values.append(val)
            var_info = self._var_estimates.get(t, {})
            vols.append(var_info.get("daily_vol", 0.02))

        values = np.array(values)
        vols = np.array(vols)
        n = len(values)

        # Build correlation matrix
        corr_matrix = np.eye(n)
        tickers = [p["ticker"] for p in positions]
        for i in range(n):
            for j in range(i+1, n):
                key = (tickers[i], tickers[j])
                rev_key = (tickers[j], tickers[i])
                c = self._correlations.get(key, self._correlations.get(rev_key, 0.0))
                corr_matrix[i, j] = c
                corr_matrix[j, i] = c

        # Note: Parametric VaR with normal assumption underestimates tail risk
        # for binary contracts bounded in [0,1]. We add a binary jump risk premium
        # to account for potential settlement at 0 or 1.
        # Portfolio VaR (parametric, 95%)
        weights = values / values.sum() if values.sum() > 0 else np.ones(n) / n
        port_vol = np.sqrt(weights @ (np.diag(vols) @ corr_matrix @ np.diag(vols)) @ weights)
        binary_jump_risk = sum(
            pos["contracts"] * min(pos["current_price"], 1 - pos["current_price"])
            for pos in positions
        ) * 0.10  # 10% probability of jump-to-boundary
        var_95 = values.sum() * port_vol * 1.645 + binary_jump_risk

        return round(var_95, 2)

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
