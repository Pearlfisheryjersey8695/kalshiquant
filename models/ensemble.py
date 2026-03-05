"""
Step 2.6 -- Ensemble Signal Generator
Combines fair value, price predictor, regime detector, and risk model
into unified trade signals. Outputs top 10 signals to signals/latest_signals.json.
"""

import sys, os, json, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from models.base import registry
from models.features import load_features, prepare_ml_data
from models.fair_value import FairValueModel
from models.price_predictor import PricePredictor
from models.regime_detector import RegimeDetector
from models.risk_model import RiskModel
from models.meta_model import MetaModel
from collections import defaultdict

# Fee-aware minimum edge thresholds — regime-adaptive
FEE_PER_CONTRACT_RT = 0.03
MIN_EDGE = 0.03
MIN_EDGE_MEAN_REVERT = 0.025  # 2.5c — 67% WR justifies tighter threshold
MIN_EDGE_TRENDING = 0.08      # 8c — 50% WR = no edge, need overwhelming signal
MIN_EDGE_CONVERGENCE = 0.05   # 5c — prevents fee drag on cheap contracts
CONVERGENCE_REGIME_MAX_CONTRACTS = 500  # Cap in CONVERGENCE regime only


def run_ensemble(portfolio_value=10000):
    """Run the full model pipeline and generate signals."""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    signals_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "signals")
    os.makedirs(signals_dir, exist_ok=True)

    # ── Load data ────────────────────────────────────────────────────────
    print("[1/5] Loading feature data ...")
    features = load_features()

    scored_path = os.path.join(data_dir, "scored_markets.csv")
    try:
        scored = pd.read_csv(scored_path)
    except FileNotFoundError:
        print(f"  WARNING: {scored_path} not found. Run statistical_quality.py first.")
        print("  Returning empty signals.")
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
                "portfolio_value": portfolio_value, "total_signals": 0, "signals": []}
    scored_map = scored.set_index("ticker").to_dict("index")

    # Load tradeable_markets.csv for expiration_time (not in scored_markets)
    tradeable_path = os.path.join(data_dir, "tradeable_markets.csv")
    expiry_map = {}
    try:
        tradeable = pd.read_csv(tradeable_path)
        expiry_map = tradeable.set_index("ticker")["expiration_time"].to_dict()
    except (FileNotFoundError, KeyError):
        print("  WARNING: Could not load expiration times from tradeable_markets.csv")

    print(f"  {features['ticker'].nunique()} markets, {len(features):,} feature rows")

    # ── Temporal split for fair value calibration ─────────────────────────
    # Fair value base rates must be estimated from HISTORICAL data only,
    # then edges measured on RECENT data. Otherwise edge = f(mean_of_data) - price
    # is circular (in-sample).
    features_sorted = features.sort_index()
    n_rows = len(features_sorted)
    cal_end = int(n_rows * 0.70)
    fv_calibration = features_sorted.iloc[:cal_end]
    fv_evaluation = features_sorted.iloc[cal_end:]
    print(f"  Fair value split: {len(fv_calibration):,} calibration, {len(fv_evaluation):,} evaluation rows")

    # ── Fit models ───────────────────────────────────────────────────────
    print("[2/5] Fitting models ...")

    fv_model = FairValueModel()
    fv_model.fit(fv_calibration)  # base rates from historical period only
    fv_model.set_scored_map(scored_map)
    print("  Fair value model fitted (out-of-sample)")

    predictor = PricePredictor()
    predictor.fit(features)  # has its own internal walk-forward split

    regime = RegimeDetector()
    regime.fit(features)  # regime classification is per-row, no circularity
    print("  Regime detector fitted")

    risk = RiskModel(portfolio_value=portfolio_value)
    risk.fit(features)  # risk estimation uses all data (conservative)
    print("  Risk model fitted")

    # ── Generate signals ─────────────────────────────────────────────────
    print("[3/5] Generating fair value signals ...")
    fv_all = fv_model.predict(fv_evaluation)
    # Use lowest threshold to get all candidates — regime filtering happens later
    fv_signals = fv_model.get_signals(fv_evaluation, min_edge=MIN_EDGE_MEAN_REVERT)
    fv_weights = fv_model.get_current_weights()
    print(f"  {len(fv_signals)} markets with |edge| > {MIN_EDGE_MEAN_REVERT*100:.1f}c (pre-regime filter)")
    print(f"  FV weights: {fv_weights}")

    print("[4/5] Running price predictor + regime ...")
    latest_regimes = regime.get_latest_regimes(features)
    latest_regime_probs = regime.get_latest_regime_probs(features)

    pred_results = predictor.predict(features)

    # Get latest prediction per ticker
    if len(pred_results) > 0:
        latest_preds = pred_results.sort_index().groupby("ticker").last()
    else:
        latest_preds = pd.DataFrame()

    # ── Train meta-model (stacking) on late calibration data ──────────────
    meta = MetaModel()
    meta_eval = fv_calibration.iloc[int(len(fv_calibration) * 0.6):]
    if len(meta_eval) > 50:
        try:
            meta_fv = fv_model.predict(meta_eval)
            meta_fv_latest = meta_fv.groupby("ticker").last()
            meta_xgb = predictor.predict(meta_eval)
            meta_xgb_latest = meta_xgb.groupby("ticker").last() if len(meta_xgb) > 0 else pd.DataFrame()
            ml_meta = prepare_ml_data(meta_eval)
            if "future_return" in ml_meta.columns:
                meta_returns = ml_meta.groupby("ticker")["future_return"].last()
                meta_regimes = regime.get_latest_regimes(meta_eval)
                common_tickers = meta_fv_latest.index.intersection(meta_returns.index)
                xgb_dirs, xgb_confs, xgb_changes = [], [], []
                fv_edges_arr, regime_arr, actual_arr = [], [], []
                for t in common_tickers:
                    fv_edge = meta_fv_latest.loc[t, "edge"]
                    if abs(fv_edge) < 0.01:
                        continue
                    fv_edges_arr.append(fv_edge)
                    if t in meta_xgb_latest.index:
                        xgb_dirs.append(int(meta_xgb_latest.loc[t, "predicted_direction"]))
                        xgb_confs.append(float(meta_xgb_latest.loc[t, "confidence"]))
                        xgb_changes.append(float(meta_xgb_latest.loc[t].get("predicted_change", 0)))
                    else:
                        xgb_dirs.append(0)
                        xgb_confs.append(0.5)
                        xgb_changes.append(0.0)
                    regime_arr.append(meta_regimes.get(t, "UNKNOWN"))
                    actual_arr.append(float(meta_returns.loc[t]))
                if len(fv_edges_arr) > 10:
                    meta.fit(
                        np.array(xgb_dirs), np.array(xgb_confs), np.array(xgb_changes),
                        np.array(fv_edges_arr), np.array(regime_arr), np.array(actual_arr)
                    )
        except Exception as e:
            print(f"  MetaModel training skipped: {e}")

    # ── Build event prefix map for BUY_YES preference ─────────────────────
    event_tickers = defaultdict(list)
    for _, fv_row in fv_signals.iterrows():
        t = fv_row["ticker"]
        parts = t.rsplit("-", 1)
        prefix = parts[0] if len(parts) > 1 else t
        event_tickers[prefix].append(fv_row.to_dict())

    # ── Combine into unified signals ─────────────────────────────────────
    print("[5/5] Building ensemble signals ...")
    signals = []

    for _, fv_row in fv_signals.iterrows():
        ticker = fv_row["ticker"]
        current_price = fv_row["current_price"]
        fair_value = fv_row["fair_value"]
        edge = fv_row["edge"]

        # Regime
        mkt_regime = latest_regimes.get(ticker, "UNKNOWN")
        regime_probs = latest_regime_probs.get(ticker, {})

        # Skip stale markets
        if mkt_regime == "STALE":
            continue

        # Regime-adaptive minimum edge
        effective_min = MIN_EDGE
        if mkt_regime == "MEAN_REVERTING":
            effective_min = MIN_EDGE_MEAN_REVERT
        elif mkt_regime == "TRENDING":
            effective_min = MIN_EDGE_TRENDING
        elif mkt_regime == "CONVERGENCE":
            effective_min = MIN_EDGE_CONVERGENCE
        if abs(edge) < effective_min:
            continue

        # Price prediction
        pred_dir = 0
        pred_conf = 0.0
        pred_change = 0.0
        if ticker in latest_preds.index:
            pred_dir = int(latest_preds.loc[ticker, "predicted_direction"])
            pred_conf = float(latest_preds.loc[ticker, "confidence"])
            if "predicted_change" in latest_preds.columns:
                pred_change = float(latest_preds.loc[ticker, "predicted_change"])

        # Determine direction
        if edge > 0:
            direction = "BUY_YES"
        elif edge < 0:
            direction = "BUY_NO"
        else:
            direction = "HOLD"

        # Fix 4: Prefer BUY_YES over BUY_NO when equivalent
        if direction == "BUY_NO":
            parts = ticker.rsplit("-", 1)
            prefix = parts[0] if len(parts) > 1 else ticker
            siblings = event_tickers.get(prefix, [])
            has_buy_yes = any(
                sib["ticker"] != ticker and sib["edge"] > MIN_EDGE
                for sib in siblings
            )
            if has_buy_yes:
                continue  # Skip BUY_NO, equivalent BUY_YES exists in same event

        # Combined confidence
        fv_conf = min(abs(edge) / 0.10, 1.0)

        # Predictor agreement bonus
        pred_agrees = (pred_dir > 0 and edge > 0) or (pred_dir < 0 and edge < 0)
        pred_bonus = pred_conf * 0.3 if pred_agrees else -pred_conf * 0.1

        # Regime suitability
        regime_mult = {
            "MEAN_REVERTING": 1.0,
            "TRENDING": 0.9,
            "HIGH_VOLATILITY": 0.6,
            "CONVERGENCE": 0.8,
            "STALE": 0.0,
        }.get(mkt_regime, 0.5)

        # Regime certainty bonus from HMM
        regime_certainty = max(regime_probs.values()) if regime_probs else 0.5
        confidence = np.clip(fv_conf * 0.5 + pred_bonus + regime_mult * 0.15 + regime_certainty * 0.05, 0, 1)

        # Meta-model gate: regime-adaptive threshold
        meta_quality = meta.predict_trade_quality(
            pred_dir, pred_conf, pred_change, edge, mkt_regime, regime_probs
        )
        meta_gate = 0.30
        if mkt_regime == "MEAN_REVERTING":
            meta_gate = 0.20  # Let more signals through — 67% WR regime
        elif mkt_regime == "TRENDING":
            meta_gate = 0.40  # Stricter — 50% WR regime
        if meta_quality < meta_gate:
            continue

        scored_info = scored_map.get(ticker, {})

        # Sentiment
        try:
            from pipeline.sentiment import get_sentiment
            sentiment_data = get_sentiment(
                ticker, scored_info.get("title", ""),
                scored_info.get("category", ""), current_price,
            )
        except Exception:
            sentiment_data = {"consensus_edge": 0.0, "sentiment_edge": 0.0,
                              "ai_prob": 0.0, "ai_edge": 0.0, "ai_reasoning": "",
                              "consensus_prob": 0.0, "source": ""}

        # Position sizing (fee-aware, volume-capped)
        contracts, risk_details = risk.position_size(
            ticker, edge, confidence, current_price,
            direction=direction,
            category=scored_info.get("category", ""),
            volume_24h=int(scored_info.get("volume", 0)),
        )

        # Skip if position_size rejected (edge < fees)
        if contracts <= 0:
            continue

        # Cap in CONVERGENCE regime only — prevents fee drag
        if mkt_regime == "CONVERGENCE":
            contracts = min(contracts, CONVERGENCE_REGIME_MAX_CONTRACTS)

        # Hedging
        hedges = risk.suggest_hedges(ticker, direction)

        # Build reasons
        reasons = []
        net_edge_val = risk_details.get("net_edge", abs(edge) - FEE_PER_CONTRACT_RT)
        reasons.append(f"Fair value {fair_value:.2f} vs market {current_price:.2f} "
                       f"(gross {edge:+.2f}, net {net_edge_val:+.4f})")
        if pred_agrees:
            reasons.append(f"ML predictor agrees ({pred_conf:.0%} confidence)")
        elif pred_dir != 0:
            reasons.append(f"ML predictor disagrees (direction={pred_dir})")
        reasons.append(f"Regime: {mkt_regime}")
        reasons.append(f"Meta-model quality: {meta_quality:.0%}")
        if abs(sentiment_data.get("consensus_edge", 0)) > 0.01:
            reasons.append(f"Consensus edge: {sentiment_data['consensus_edge']:+.2f} ({sentiment_data.get('source', '')})")

        signal = {
            "ticker": ticker,
            "title": scored_info.get("title", ""),
            "category": scored_info.get("category", ""),
            "current_price": round(current_price, 4),
            "fair_value": round(fair_value, 4),
            "edge": round(edge, 4),
            "net_edge": round(risk_details.get("net_edge", 0), 4),
            "fee_impact": round(risk_details.get("fee_impact", 0), 4),
            "direction": direction,
            "confidence": round(confidence, 4),
            "meta_quality": round(meta_quality, 4),
            "regime": mkt_regime,
            "strategy": _strategy_for_regime(mkt_regime),
            "price_prediction_1h": pred_dir,
            "predicted_change": round(pred_change, 4),
            "prediction_confidence": round(pred_conf, 4),
            "recommended_contracts": contracts,
            "risk": risk_details,
            "hedge": hedges[0] if hedges else None,
            "reasons": reasons,
            "volume": int(scored_info.get("volume", 0)),
            "open_interest": int(scored_info.get("open_interest", 0)),
            "tradability_score": scored_info.get("tradability_score", 0),
            "expiration_time": expiry_map.get(ticker, None),
            "decay_curve": _compute_decay_curve(ticker, fv_all, edge),
            "consensus_edge": round(sentiment_data.get("consensus_edge", 0), 4),
            "consensus_prob": round(sentiment_data.get("consensus_prob", 0), 4),
            "sentiment_edge": round(sentiment_data.get("sentiment_edge", 0), 4),
            "ai_prob": round(sentiment_data.get("ai_prob", 0), 4),
            "ai_edge": round(sentiment_data.get("ai_edge", 0), 4),
            "ai_reasoning": sentiment_data.get("ai_reasoning", sentiment_data.get("reasoning", "")),
            "regime_probs": {k: round(v, 4) for k, v in regime_probs.items()} if regime_probs else {},
            "fv_weights": fv_weights,
        }
        signals.append(signal)

    # Sort by |edge| * confidence * liquidity proxy
    for s in signals:
        s["_rank_score"] = abs(s["edge"]) * s["confidence"] * min(s["volume"] / 10000, 1.0)

    signals.sort(key=lambda s: s["_rank_score"], reverse=True)

    # Remove internal ranking field
    for s in signals:
        del s["_rank_score"]

    # Top 10
    top_signals = signals[:10]

    # Save
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_value": portfolio_value,
        "total_signals": len(signals),
        "signals": top_signals,
    }

    output_path = os.path.join(signals_dir, "latest_signals.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    # Also save all signals
    all_path = os.path.join(signals_dir, "all_signals.json")
    with open(all_path, "w") as f:
        json.dump({"signals": signals}, f, indent=2, default=str)

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  ENSEMBLE SIGNAL RESULTS")
    print("=" * 80)
    print(f"  Total signals: {len(signals)}")
    print(f"  Top {len(top_signals)} by |edge| * confidence * liquidity:")
    print()
    print(f"  {'Ticker':<45} {'Dir':<8} {'Edge':>6} {'Conf':>5} {'Regime':<16} {'Contracts':>9}")
    print("  " + "-" * 95)
    for s in top_signals:
        print(f"  {s['ticker']:<45} {s['direction']:<8} {s['edge']:>+6.2f} {s['confidence']:>5.1%} "
              f"{s['regime']:<16} {s['recommended_contracts']:>9}")

    # VaR calculation
    positions = [
        {"ticker": s["ticker"], "contracts": s["recommended_contracts"],
         "current_price": s["current_price"]}
        for s in top_signals if s["recommended_contracts"] > 0
    ]
    var_95 = risk.portfolio_var(positions)
    print(f"\n  Portfolio VaR (95%): ${var_95:,.2f}")
    print(f"\n  Output: {output_path}")
    print("=" * 80)

    return output


def _compute_decay_curve(ticker, fv_all, current_edge):
    """Compute how edge has evolved: edge at t-60m, t-30m, t-15m, t-5m, t=now.
    Uses historical fair value computations to show alpha decay/persistence.
    """
    ticker_data = fv_all[fv_all["ticker"] == ticker].sort_values("timestamp")
    if len(ticker_data) < 2:
        return [{"minutes": 0, "edge": round(current_edge, 4)}]

    curve = [{"minutes": 0, "edge": round(current_edge, 4)}]

    # Look back at historical bars: 1, 3, 6, 12 bars at 5min each
    offsets = [(1, -5), (3, -15), (6, -30), (12, -60)]
    n = len(ticker_data)
    for bars_back, minutes in offsets:
        idx = n - 1 - bars_back
        if idx >= 0:
            hist_edge = float(ticker_data.iloc[idx]["edge"])
            curve.append({"minutes": minutes, "edge": round(hist_edge, 4)})

    curve.sort(key=lambda p: p["minutes"])
    return curve


def _strategy_for_regime(regime):
    return {
        "TRENDING": "momentum",
        "MEAN_REVERTING": "contrarian",
        "HIGH_VOLATILITY": "reduced_size",
        "CONVERGENCE": "time_decay",
        "STALE": "no_trade",
    }.get(regime, "default")


if __name__ == "__main__":
    run_ensemble()
