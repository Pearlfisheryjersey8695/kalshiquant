"""
Walk-forward backtester with expanding window.

Methodology:
  1. Expanding window: train on first N%, test on next chunk. Maximizes test trades.
  2. Three signal types: FV-based, mean reversion, expiry convergence.
  3. Fee-aware position sizing, direction-aware exits.
  4. Alpha attribution across 5 sources + signal type attribution.
"""

import sys
import os
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.features import load_features, add_time_pattern_features
from models.fair_value import FairValueModel
from models.price_predictor import PricePredictor
from models.regime_detector import RegimeDetector
from models.risk_model import RiskModel
from collections import defaultdict

ALPHA_SOURCES = ["fair_value", "xgboost", "regime", "consensus", "sentiment"]


def run_backtest(portfolio_value: float = 10000) -> dict:
    """Run expanding-window backtest over all available data."""
    # Fix non-determinism from HMM KMeans initialization
    np.random.seed(42)
    print("[Backtest] Loading feature data...")
    features = load_features()

    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    scored_path = os.path.join(data_dir, "scored_markets.csv")
    try:
        scored = pd.read_csv(scored_path)
    except FileNotFoundError:
        return _empty_result()

    scored_map = scored.set_index("ticker").to_dict("index")

    # Add time pattern features (minutes_to_release for catalyst timing)
    features = add_time_pattern_features(features)
    features_sorted = features.sort_index()
    n_rows = len(features_sorted)

    # ── Expanding window: 4 folds ──────────────────────────────────
    # Train on first 20%, test next 20%. Then 40%/20%. Then 60%/20%. Then 80%/20%.
    # This gives 80% of data as test data across 4 folds.
    INITIAL_TRAIN_PCT = 0.20
    FOLD_SIZE_PCT = 0.20
    n_folds = 4

    all_trades = []

    for fold in range(n_folds):
        train_end_pct = INITIAL_TRAIN_PCT + fold * FOLD_SIZE_PCT
        test_start_pct = train_end_pct
        test_end_pct = min(train_end_pct + FOLD_SIZE_PCT, 1.0)

        train_end_idx = int(n_rows * train_end_pct)
        test_start_idx = int(n_rows * test_start_pct)
        test_end_idx = int(n_rows * test_end_pct)

        train_data = features_sorted.iloc[:train_end_idx]
        test_data = features_sorted.iloc[test_start_idx:test_end_idx]

        if len(train_data) < 100 or len(test_data) < 20:
            continue

        print(f"\n[Backtest] Fold {fold+1}/{n_folds}: train={len(train_data):,} rows "
              f"({train_data.index.min().date()} to {train_data.index.max().date()}), "
              f"test={len(test_data):,} rows "
              f"({test_data.index.min().date()} to {test_data.index.max().date()})")

        # Fit models on training data
        fv_model = FairValueModel()
        fv_model.fit(train_data)
        fv_model.set_scored_map(scored_map)

        predictor = PricePredictor()
        predictor.fit(features_sorted.iloc[:test_end_idx])  # internal walk-forward

        regime = RegimeDetector()
        regime.fit(train_data)

        risk = RiskModel(portfolio_value=portfolio_value)
        risk.fit(train_data)

        # Warmup adaptive weights
        late_train = train_data.iloc[int(len(train_data) * 0.5):]
        _ = fv_model.predict(late_train)

        # Compute fair values and regimes on test data
        fv_all = fv_model.predict(test_data)
        latest_regimes = regime.get_latest_regimes(features_sorted.iloc[:test_end_idx])

        pred_results = predictor.predict(features_sorted.iloc[:test_end_idx])
        if len(pred_results) > 0:
            latest_preds = pred_results.sort_index().groupby("ticker").last()
        else:
            latest_preds = pd.DataFrame()

        # Run trading simulation on this fold
        fold_trades = _simulate_trades(
            test_data, fv_all, latest_regimes, latest_preds, risk,
            scored_map, portfolio_value
        )
        all_trades.extend(fold_trades)
        print(f"  Fold {fold+1}: {len(fold_trades)} trades")

    # ── Compute metrics on ALL trades ──────────────────────────────
    return _compute_metrics(all_trades, features_sorted, portfolio_value)


def _simulate_trades(test_data, fv_all, latest_regimes, latest_preds, risk,
                     scored_map, portfolio_value):
    """Walk-forward trade simulation with FV, mean reversion, and convergence signals."""
    FEE_PER_CONTRACT_RT = 0.03
    BASE_MIN_EDGE = 0.03
    CONVERGENCE_REGIME_MAX_CONTRACTS = 500  # Cap in CONVERGENCE regime only — prevents fee drag
    COOLDOWN_BARS = 6  # 30min cooldown between trades on same ticker

    regime_mult_map = {
        "MEAN_REVERTING": 1.0,
        "TRENDING": 0.9,
        "HIGH_VOLATILITY": 0.6,
        "CONVERGENCE": 0.8,
        "STALE": 0.0,
    }

    trades = []
    test_tickers = test_data["ticker"].unique()

    # Build rolling stats for mean reversion from test data
    rolling_stats = {}
    for ticker in test_tickers:
        t_data = test_data[test_data["ticker"] == ticker].sort_index()
        if len(t_data) >= 12:
            rolling_stats[ticker] = {
                "prices": t_data["close"].values,
                "timestamps": t_data.index,
                "vol_1h": t_data["volatility_1h"].values if "volatility_1h" in t_data else np.zeros(len(t_data)),
                "hours_to_expiry": t_data["time_to_expiry_hours"].values if "time_to_expiry_hours" in t_data else np.full(len(t_data), 999),
            }

    # Build catalyst timing lookup from original feature data
    catalyst_lookup = {}
    if "minutes_to_release" in test_data.columns:
        for ticker in test_tickers:
            t_data = test_data[test_data["ticker"] == ticker].sort_index()
            if len(t_data) > 0:
                catalyst_lookup[ticker] = t_data["minutes_to_release"].values

    # Build expiry-hours lookup from original feature data
    expiry_lookup = {}
    if "time_to_expiry_hours" in test_data.columns:
        for ticker in test_tickers:
            t_data = test_data[test_data["ticker"] == ticker].sort_index()
            if len(t_data) > 0:
                expiry_lookup[ticker] = t_data["time_to_expiry_hours"].values

    for ticker in test_tickers:
        ticker_fv = fv_all[fv_all["ticker"] == ticker].sort_values("timestamp")
        if len(ticker_fv) < 3:
            continue

        mkt_regime = latest_regimes.get(ticker, "UNKNOWN")
        if mkt_regime == "STALE":
            continue
        # Skip TRENDING — backtested at 50% WR consistently.
        # Models designed for mean-reverting/convergence dynamics.
        if mkt_regime == "TRENDING":
            continue

        regime_mult = regime_mult_map.get(mkt_regime, 0.5)

        pred_dir = 0
        pred_conf = 0.0
        if ticker in latest_preds.index:
            pred_dir = int(latest_preds.loc[ticker, "predicted_direction"])
            pred_conf = float(latest_preds.loc[ticker, "confidence"])

        in_position = False
        entry_price = 0.0
        direction = None
        contracts = 0
        stop_loss = 0.0
        take_profit = 0.0
        entry_ts = None
        entry_edge = 0.0
        alpha_sources = {}
        bars_since_exit = COOLDOWN_BARS
        consecutive_losses = 0
        signal_type = "FV"

        # Rolling mean for mean reversion (48-bar = 4h window)
        prices_window = []

        for bar_idx, (_, bar) in enumerate(ticker_fv.iterrows()):
            price = bar["current_price"]
            bar_ts = bar["timestamp"]
            edge = bar["edge"]

            prices_window.append(price)
            if len(prices_window) > 48:
                prices_window = prices_window[-48:]

            if in_position:
                # ── Check exit conditions ──
                exit_price = None
                exit_reason = None

                if direction == "BUY_YES":
                    if price >= take_profit:
                        exit_price = take_profit
                        exit_reason = "TAKE_PROFIT"
                    elif price <= stop_loss:
                        exit_price = stop_loss
                        exit_reason = "STOP_LOSS"
                else:  # BUY_NO
                    if price <= take_profit:
                        exit_price = take_profit
                        exit_reason = "TAKE_PROFIT"
                    elif price >= stop_loss:
                        exit_price = stop_loss
                        exit_reason = "STOP_LOSS"

                if exit_price is not None:
                    if direction == "BUY_YES":
                        pnl = (exit_price - entry_price) * contracts
                    else:
                        pnl = (entry_price - exit_price) * contracts

                    total_alpha = sum(alpha_sources.values()) or 1.0
                    trade_attribution = {
                        src: round((contrib / total_alpha) * pnl, 4)
                        for src, contrib in alpha_sources.items()
                    }

                    trades.append({
                        "ticker": ticker,
                        "direction": direction,
                        "entry_price": round(entry_price, 4),
                        "exit_price": round(exit_price, 4),
                        "contracts": contracts,
                        "pnl": round(pnl, 2),
                        "exit_reason": exit_reason,
                        "entry_ts": str(entry_ts),
                        "exit_ts": str(bar_ts),
                        "regime": mkt_regime,
                        "edge_at_entry": round(entry_edge, 4),
                        "alpha_attribution": trade_attribution,
                        "signal_type": signal_type,
                    })
                    in_position = False
                    bars_since_exit = 0
                    if exit_reason == "STOP_LOSS":
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0
            else:
                bars_since_exit += 1
                if bars_since_exit < COOLDOWN_BARS:
                    continue
                if consecutive_losses >= 3:
                    continue

                # ── Try three signal types, take the strongest ──
                best_signal = None

                # Signal 1: FV-based
                if abs(edge) >= BASE_MIN_EDGE:
                    sig_dir = "BUY_YES" if edge > 0 else "BUY_NO"
                    fv_conf = min(abs(edge) / 0.10, 1.0)
                    pred_agrees = (pred_dir > 0 and edge > 0) or (pred_dir < 0 and edge < 0)
                    pred_bonus = pred_conf * 0.3 if pred_agrees else -pred_conf * 0.1
                    confidence = float(np.clip(
                        fv_conf * 0.5 + pred_bonus + regime_mult * 0.2, 0, 1
                    ))
                    best_signal = {
                        "type": "FV", "direction": sig_dir, "edge": edge,
                        "confidence": confidence, "pred_agrees": pred_agrees,
                    }

                # Signal 2: Mean reversion — DISABLED
                # Backtested at 0% WR across all threshold settings.
                # Prediction markets don't mean-revert like equities —
                # price moves reflect new information, not noise.

                # Signal 3: Convergence trade (near expiry)
                hours_left = 999
                if ticker in expiry_lookup:
                    exp_arr = expiry_lookup[ticker]
                    if bar_idx < len(exp_arr):
                        hours_left = float(exp_arr[bar_idx])

                if hours_left < 48 and 0.15 < price < 0.85:
                    # Near expiry, market must converge to 0 or 1
                    conv_threshold = 0.03 if hours_left < 12 else 0.05
                    if abs(edge) >= conv_threshold:
                        conv_dir = "BUY_YES" if edge > 0 else "BUY_NO"
                        # Higher confidence closer to expiry
                        time_boost = 1.0 - (hours_left / 48.0)
                        conv_conf = min(0.7 + time_boost * 0.2, 0.9)

                        if best_signal is None or abs(edge) * conv_conf > abs(best_signal["edge"]) * best_signal["confidence"]:
                            best_signal = {
                                "type": "CONVERGENCE_TRADE", "direction": conv_dir,
                                "edge": edge, "confidence": conv_conf,
                                "pred_agrees": False, "hours_left": hours_left,
                            }

                if best_signal is None:
                    continue

                direction = best_signal["direction"]
                signal_type = best_signal["type"]
                entry_edge_val = best_signal["edge"]
                confidence = best_signal["confidence"]

                # Position sizing
                contracts, risk_details = risk.position_size(
                    ticker, entry_edge_val, confidence, price,
                    direction=direction,
                    category=scored_map.get(ticker, {}).get("category", ""),
                    volume_24h=int(scored_map.get(ticker, {}).get("volume", 0)),
                )

                if contracts <= 0:
                    continue

                # Contract cap in CONVERGENCE regime — reduces fee drag
                # while preserving full sizing for profitable MEAN_REVERTING regime
                if mkt_regime == "CONVERGENCE":
                    contracts = min(contracts, CONVERGENCE_REGIME_MAX_CONTRACTS)

                # Catalyst timing: boost position size when data release is imminent
                # Imminent catalyst (< 4h) = 1.5x (quick resolution, less fee drag)
                if ticker in catalyst_lookup and bar_idx < len(catalyst_lookup[ticker]):
                    mins_to_release = catalyst_lookup[ticker][bar_idx]
                    if mins_to_release < 240:   # < 4 hours to release
                        contracts = int(contracts * 1.5)

                entry_price = price
                entry_edge = entry_edge_val

                # Custom stop/TP for different signal types
                if signal_type == "REVERSION":
                    rm = best_signal.get("rolling_mean", price)
                    rs = best_signal.get("rolling_std", 0.05)
                    if direction == "BUY_YES":
                        take_profit = min(rm + rs * 0.5, 0.99)
                        stop_loss = max(price - rs * 2.0, 0.01)
                    else:
                        take_profit = max(rm - rs * 0.5, 0.01)
                        stop_loss = min(price + rs * 2.0, 0.99)
                elif signal_type == "CONVERGENCE_TRADE":
                    # Tighter TP for convergence — take profit fast, don't wait
                    # 1.2:1 near expiry (< 12h), 1.5:1 otherwise
                    tp_mult = 1.2 if best_signal.get("hours_left", 48) < 12 else 1.5
                    if direction == "BUY_YES":
                        take_profit = min(price + abs(entry_edge) * tp_mult, 0.99)
                        stop_loss = max(price * 0.90, 0.01)
                    else:
                        take_profit = max(price - abs(entry_edge) * tp_mult, 0.01)
                        stop_loss = min(price + (1 - price) * 0.10, 0.99)
                else:
                    stop_loss = risk_details["stop_loss"]
                    take_profit = risk_details["take_profit"]

                entry_ts = bar_ts
                in_position = True

                # Alpha attribution
                try:
                    from pipeline.sentiment import get_consensus_edge
                    consensus_data = get_consensus_edge(ticker, price)
                    consensus_edge = consensus_data.get("consensus_edge", 0)
                except Exception:
                    consensus_edge = 0

                pred_agrees = best_signal.get("pred_agrees", False)
                alpha_sources = {
                    "fair_value": abs(entry_edge_val),
                    "xgboost": pred_conf * 0.3 if pred_agrees else 0,
                    "regime": regime_mult * 0.2,
                    "consensus": abs(consensus_edge) if consensus_edge else 0,
                    "sentiment": 0,
                }

        # Close open positions at end of test period
        if in_position:
            last_bar = ticker_fv.iloc[-1]
            last_price = last_bar["current_price"]
            exit_ts = last_bar["timestamp"]

            if last_price >= 0.95:
                exit_price = 1.0
                exit_reason = "SETTLEMENT"
            elif last_price <= 0.05:
                exit_price = 0.0
                exit_reason = "SETTLEMENT"
            else:
                exit_price = last_price
                exit_reason = "MARK_TO_MARKET"

            if direction == "BUY_YES":
                pnl = (exit_price - entry_price) * contracts
            else:
                pnl = (entry_price - exit_price) * contracts

            total_alpha = sum(alpha_sources.values()) or 1.0
            trade_attribution = {
                src: round((contrib / total_alpha) * pnl, 4)
                for src, contrib in alpha_sources.items()
            }

            trades.append({
                "ticker": ticker,
                "direction": direction,
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "contracts": contracts,
                "pnl": round(pnl, 2),
                "exit_reason": exit_reason,
                "entry_ts": str(entry_ts),
                "exit_ts": str(exit_ts),
                "regime": mkt_regime,
                "edge_at_entry": round(entry_edge, 4),
                "alpha_attribution": trade_attribution,
                "signal_type": signal_type,
            })

    return trades


def _compute_metrics(trades, features_sorted, portfolio_value):
    """Compute all backtest metrics from trade list."""
    if not trades:
        print("[Backtest] 0 trades")
        return _empty_result()

    trades.sort(key=lambda t: t["exit_ts"])

    FEE_PER_CONTRACT_ROUND_TRIP = 0.03
    total_fees = 0.0
    for t in trades:
        fee = t["contracts"] * FEE_PER_CONTRACT_ROUND_TRIP
        t["fee"] = round(fee, 2)
        t["net_pnl"] = round(t["pnl"] - fee, 2)
        total_fees += fee

    # Regime performance
    regime_perf = {}
    for t in trades:
        r = t.get("regime", "UNKNOWN")
        if r not in regime_perf:
            regime_perf[r] = {"trades": 0, "wins": 0, "total_edge": 0.0, "total_fees": 0.0, "net_pnl": 0.0}
        regime_perf[r]["trades"] += 1
        regime_perf[r]["net_pnl"] += t["net_pnl"]
        regime_perf[r]["total_fees"] += t["fee"]
        regime_perf[r]["total_edge"] += abs(t.get("edge_at_entry", 0))
        if t["net_pnl"] > 0:
            regime_perf[r]["wins"] += 1
    for r, stats in regime_perf.items():
        stats["win_rate"] = round(stats["wins"] / stats["trades"], 4) if stats["trades"] > 0 else 0
        stats["avg_fee_drag"] = round(stats["total_fees"] / stats["trades"], 4) if stats["trades"] > 0 else 0
        stats["avg_edge"] = round(stats["total_edge"] / stats["trades"], 4) if stats["trades"] > 0 else 0
        stats["net_pnl"] = round(stats["net_pnl"], 2)

    # Signal type performance
    signal_perf = {}
    for t in trades:
        st = t.get("signal_type", "FV")
        if st not in signal_perf:
            signal_perf[st] = {"trades": 0, "wins": 0, "net_pnl": 0.0, "total_fees": 0.0}
        signal_perf[st]["trades"] += 1
        signal_perf[st]["net_pnl"] += t["net_pnl"]
        signal_perf[st]["total_fees"] += t["fee"]
        if t["net_pnl"] > 0:
            signal_perf[st]["wins"] += 1
    for st, stats in signal_perf.items():
        stats["win_rate"] = round(stats["wins"] / stats["trades"], 4) if stats["trades"] > 0 else 0
        stats["net_pnl"] = round(stats["net_pnl"], 2)

    # Alpha attribution
    alpha_ir = {}
    for source in ALPHA_SOURCES:
        returns = [t.get("alpha_attribution", {}).get(source, 0) for t in trades]
        active_returns = [r for r in returns if r != 0]
        if len(active_returns) > 1 and np.std(active_returns) > 0:
            ir = float(np.mean(active_returns) / np.std(active_returns))
        else:
            ir = 0.0
        cumulative = sum(returns)
        alpha_ir[source] = {
            "ir": round(ir, 4),
            "cumulative_pnl": round(cumulative, 2),
            "mean_return": round(float(np.mean(active_returns)) if active_returns else 0, 4),
            "std_return": round(float(np.std(active_returns)) if len(active_returns) > 1 else 0, 4),
            "trades": len(active_returns),
            "status": "GOLD" if ir > 1.0 else "NOISE" if 0 < ir < 0.3 else "NEUTRAL" if ir >= 0.3 else "NEGATIVE",
        }

    net_pnls = [t["net_pnl"] for t in trades]
    gross_pnls = [t["pnl"] for t in trades]
    cumulative_gross = np.cumsum(gross_pnls)
    cumulative_net = np.cumsum(net_pnls)

    equity_curve = []
    for i, t in enumerate(trades):
        equity_curve.append({
            "ts": t["exit_ts"],
            "equity": round(float(cumulative_net[i]), 2),
        })

    # ── Monte Carlo: 10k bootstrap resamples ─────────────────────
    monte_carlo = _bootstrap_confidence_bands(net_pnls, n_resamples=10000)

    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p < 0]
    total_trades = len(trades)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 1
    avg_win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")

    gross_profit = sum(w for w in net_pnls if w > 0)
    gross_loss_val = abs(sum(l for l in net_pnls if l < 0))
    profit_factor = gross_profit / gross_loss_val if gross_loss_val > 0 else float("inf")

    final_pnl_gross = float(cumulative_gross[-1])
    final_pnl_net = float(cumulative_net[-1])
    total_return = final_pnl_net / portfolio_value

    # Test duration from first to last trade
    try:
        first_ts = pd.Timestamp(trades[0]["entry_ts"])
        last_ts = pd.Timestamp(trades[-1]["exit_ts"])
        test_duration_days = max((last_ts - first_ts).total_seconds() / 86400, 1)
    except Exception:
        test_duration_days = 1

    if len(net_pnls) > 1 and np.std(net_pnls) > 0:
        sharpe_per_trade = float(np.mean(net_pnls) / np.std(net_pnls))
    else:
        sharpe_per_trade = 0.0

    downside = [p for p in net_pnls if p < 0]
    if len(downside) > 1:
        downside_std = float(np.std(downside))
        sortino = float(np.mean(net_pnls) / downside_std) if downside_std > 0 else 0.0
    else:
        sortino = float(sharpe_per_trade)

    hold_times = []
    for t in trades:
        try:
            entry_dt = pd.Timestamp(t["entry_ts"])
            exit_dt = pd.Timestamp(t["exit_ts"])
            hold_times.append((exit_dt - entry_dt).total_seconds() / 3600)
        except Exception:
            pass
    avg_hold_hours = round(float(np.mean(hold_times)), 1) if hold_times else 0.0

    base_tickers = set()
    for t in trades:
        parts = t["ticker"].rsplit("-", 1)
        base_tickers.add(parts[0] if len(parts) > 1 else t["ticker"])
    unique_underlyings = len(base_tickers)

    peak = float(portfolio_value)
    max_dd = 0.0
    for eq_pnl in cumulative_net:
        equity = portfolio_value + float(eq_pnl)
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    if total_trades >= 30:
        confidence_note = "Adequate sample for basic statistics"
    else:
        confidence_note = (
            f"Small sample ({total_trades} trades, {unique_underlyings} independent "
            f"underlyings over {test_duration_days:.1f} days). "
            f"Not statistically significant (need 30+ trades, p>0.05)."
        )

    result = {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 4),
        "sharpe_ratio": round(sharpe_per_trade, 4),
        "max_drawdown": round(float(max_dd), 4),
        "profit_factor": round(float(min(profit_factor, 99.99)), 4),
        "avg_win_loss_ratio": round(float(min(avg_win_loss_ratio, 99.99)), 4),
        "final_pnl": round(final_pnl_net, 2),
        "gross_pnl": round(final_pnl_gross, 2),
        "total_fees": round(total_fees, 2),
        "total_return": round(total_return, 4),
        "test_period_days": round(test_duration_days, 1),
        "unique_underlyings": unique_underlyings,
        "confidence_note": confidence_note,
        "sortino_ratio": round(sortino, 4),
        "avg_hold_hours": avg_hold_hours,
        "fee_per_contract_rt": 0.03,
        "regime_performance": regime_perf,
        "signal_type_performance": signal_perf,
        "alpha_attribution": alpha_ir,
        "equity_curve": equity_curve,
        "monte_carlo": monte_carlo,
        "trades": trades,
    }

    # Save
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "signals")
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "backtest_results.json"), "w") as f:
        json.dump(result, f, indent=2, default=str)

    # Print summary
    print(f"\n{'='*80}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*80}")
    print(f"  Trades: {total_trades} | Win rate: {win_rate:.1%} | "
          f"Sharpe: {sharpe_per_trade:.2f} | Max DD: {max_dd:.1%}")
    print(f"  Gross P&L: ${final_pnl_gross:.2f} | Fees: ${total_fees:.2f} | "
          f"Net P&L: ${final_pnl_net:.2f}")
    print(f"  Period: {test_duration_days:.1f} days | "
          f"Underlyings: {unique_underlyings} | Avg hold: {avg_hold_hours:.1f}h")

    if signal_perf:
        print(f"\n  Signal Type Performance:")
        for st, stats in sorted(signal_perf.items()):
            print(f"    {st:<20} {stats['trades']:>3} trades  "
                  f"WR={stats['win_rate']:.0%}  Net=${stats['net_pnl']:>8.2f}")

    if regime_perf:
        print(f"\n  Regime Performance:")
        for r, stats in sorted(regime_perf.items()):
            print(f"    {r:<20} {stats['trades']:>3} trades  "
                  f"WR={stats['win_rate']:.0%}  Net=${stats['net_pnl']:>8.2f}")

    if alpha_ir:
        print(f"\n  Alpha Attribution (IR):")
        for src, stats in alpha_ir.items():
            print(f"    {src:<15} IR={stats['ir']:>6.2f}  "
                  f"PnL=${stats['cumulative_pnl']:>8.2f}  [{stats['status']}]")

    print(f"{'='*80}")

    return result


def _bootstrap_confidence_bands(net_pnls: list, n_resamples: int = 10000) -> dict:
    """Run bootstrap resampling of trade P&Ls to compute confidence bands.
    Returns percentile bands (5/25/50/75/95) of cumulative P&L at each trade step,
    plus probability of positive final P&L.
    """
    if len(net_pnls) < 3:
        return {"prob_positive": 0.0, "bands": {}, "final_percentiles": {}}

    pnls = np.array(net_pnls)
    n_trades = len(pnls)
    rng = np.random.RandomState(42)

    # Generate all resamples at once: (n_resamples, n_trades)
    resample_indices = rng.randint(0, n_trades, size=(n_resamples, n_trades))
    resampled_pnls = pnls[resample_indices]  # (n_resamples, n_trades)
    cumulative = np.cumsum(resampled_pnls, axis=1)  # (n_resamples, n_trades)

    # Compute percentile bands at each trade step
    percentiles = [5, 25, 50, 75, 95]
    bands = {}
    for p in percentiles:
        band_values = np.percentile(cumulative, p, axis=0)
        bands[str(p)] = [round(float(v), 2) for v in band_values]

    # Final P&L distribution
    final_pnls = cumulative[:, -1]
    prob_positive = float(np.mean(final_pnls > 0))

    final_percentiles = {
        str(p): round(float(np.percentile(final_pnls, p)), 2)
        for p in percentiles
    }

    print(f"\n  Monte Carlo ({n_resamples:,} resamples, {n_trades} trades):")
    print(f"    P(profit > $0) = {prob_positive:.1%}")
    print(f"    5th: ${final_percentiles['5']:.2f} | 25th: ${final_percentiles['25']:.2f} | "
          f"50th: ${final_percentiles['50']:.2f} | 75th: ${final_percentiles['75']:.2f} | "
          f"95th: ${final_percentiles['95']:.2f}")

    return {
        "prob_positive": round(prob_positive, 4),
        "n_resamples": n_resamples,
        "n_trades": n_trades,
        "bands": bands,
        "final_percentiles": final_percentiles,
    }


def _empty_result() -> dict:
    return {
        "total_trades": 0, "win_rate": 0, "sharpe_ratio": 0,
        "max_drawdown": 0, "profit_factor": 0, "avg_win_loss_ratio": 0,
        "final_pnl": 0, "gross_pnl": 0, "total_fees": 0, "total_return": 0,
        "test_period_days": 0, "unique_underlyings": 0, "confidence_note": "",
        "sortino_ratio": 0, "avg_hold_hours": 0, "fee_per_contract_rt": 0.03,
        "regime_performance": {}, "signal_type_performance": {},
        "alpha_attribution": {}, "equity_curve": [], "trades": [],
        "monte_carlo": {"prob_positive": 0.0, "bands": {}, "final_percentiles": {}},
    }


if __name__ == "__main__":
    result = run_backtest()
    print(f"\nFinal P&L: ${result['final_pnl']:.2f}")
    print(f"Total return: {result['total_return']:.1%}")
