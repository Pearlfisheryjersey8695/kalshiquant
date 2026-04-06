"""
Live ensemble signal generator.

Takes live features from compute_live_features() and scores them with
TRAINED model artifacts (no retraining). Produces the same signal JSON
format as models/ensemble.py but with live orderbook data flowing into
fair value estimates.

Model artifacts are fitted once at startup (or on 1h refit) and reused.
"""

import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger("kalshi.live_ensemble")

# Ensure project root in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models.fair_value import FairValueModel
from models.price_predictor import PricePredictor
from models.regime_detector import RegimeDetector
from models.risk_model import RiskModel, kalshi_fee_rt
from models.meta_model import MetaModel
from models.features import prepare_ml_data
from engine.strategies import select_strategies, get_strategy, STRATEGIES


class LiveModelManager:
    """
    Manages trained model artifacts for live inference.
    Models are fitted once from batch data, then reused for every 5-min cycle.
    The 1-hour refit cycle can call refit() to update models.
    """

    def __init__(self):
        self.fv_model: FairValueModel | None = None
        self.predictor: PricePredictor | None = None
        self.regime: RegimeDetector | None = None
        self.risk: RiskModel | None = None
        self.meta: MetaModel | None = None
        self.scored_map: dict = {}
        self.expiry_map: dict = {}
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit_from_batch(self, portfolio_value: int = 10000) -> bool:
        """
        Fit all models from batch data (clean_features.csv).
        Called once at startup and then on 1h refit.
        Returns True if successful.
        """
        t0 = time.time()
        data_dir = os.path.join(PROJECT_ROOT, "data")

        try:
            from models.features import load_features
            features = load_features()
        except Exception as e:
            logger.error("Cannot load batch features for model fitting: %s", e)
            return False

        # Load metadata
        try:
            scored = pd.read_csv(os.path.join(data_dir, "scored_markets.csv"))
            self.scored_map = scored.set_index("ticker").to_dict("index")
        except Exception:
            self.scored_map = {}

        try:
            tradeable = pd.read_csv(os.path.join(data_dir, "tradeable_markets.csv"))
            self.expiry_map = tradeable.set_index("ticker")["expiration_time"].to_dict()
        except Exception:
            self.expiry_map = {}

        # Temporal split for fair value (same as ensemble.py)
        features_sorted = features.sort_index()
        cal_end = int(len(features_sorted) * 0.70)
        fv_calibration = features_sorted.iloc[:cal_end]

        # Fit fair value
        self.fv_model = FairValueModel()
        self.fv_model.fit(fv_calibration)
        self.fv_model.set_scored_map(self.scored_map)

        # Fit predictor — use same training split as FV to avoid look-ahead bias
        self.predictor = PricePredictor()
        self.predictor.fit(fv_calibration)

        # Fit regime detector — use same training split as FV to avoid look-ahead bias
        self.regime = RegimeDetector()
        self.regime.fit(fv_calibration)

        # Fit risk model
        self.risk = RiskModel(portfolio_value=portfolio_value)
        self.risk.fit(features)

        # Fit meta-model
        self.meta = MetaModel()
        try:
            meta_eval = fv_calibration.iloc[int(len(fv_calibration) * 0.6):]
            if len(meta_eval) > 50:
                meta_fv = self.fv_model.predict(meta_eval)
                meta_fv_latest = meta_fv.groupby("ticker").last()
                meta_xgb = self.predictor.predict(meta_eval)
                meta_xgb_latest = meta_xgb.groupby("ticker").last() if len(meta_xgb) > 0 else pd.DataFrame()
                ml_meta = prepare_ml_data(meta_eval)
                if "future_return" in ml_meta.columns:
                    meta_returns = ml_meta.groupby("ticker")["future_return"].last()
                    meta_regimes = self.regime.get_latest_regimes(meta_eval)
                    common = meta_fv_latest.index.intersection(meta_returns.index)
                    xgb_dirs, xgb_confs, xgb_changes = [], [], []
                    fv_edges_arr, regime_arr, actual_arr = [], [], []
                    for t in common:
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
                        self.meta.fit(
                            np.array(xgb_dirs), np.array(xgb_confs), np.array(xgb_changes),
                            np.array(fv_edges_arr), np.array(regime_arr), np.array(actual_arr),
                        )
        except Exception as e:
            logger.warning("Meta-model fit failed: %s", e)

        self._fitted = True
        elapsed = time.time() - t0
        logger.info("Models fitted from batch data in %.1fs", elapsed)
        return True


def run_live_ensemble(
    live_features: pd.DataFrame,
    state,
    orderbooks,
    model_mgr: LiveModelManager,
    portfolio_value: int = 10000,
) -> dict:
    """
    Score live features with trained models. Returns signal envelope dict.

    Key difference from batch ensemble: orderbook signal in fair value is
    LIVE (non-zero), so fair value estimates incorporate real-time liquidity.
    """
    t0 = time.time()

    if not model_mgr.is_fitted:
        logger.warning("Models not fitted, cannot run live ensemble")
        return {}

    if live_features.empty:
        return {}

    fv_model = model_mgr.fv_model
    predictor = model_mgr.predictor
    regime = model_mgr.regime
    risk = model_mgr.risk
    meta = model_mgr.meta
    scored_map = model_mgr.scored_map
    expiry_map = model_mgr.expiry_map

    # ── Get regime + predictions from live features ────────────────────
    # The regime detector and predictor expect a multi-row DataFrame with
    # historical depth. For live, we have 1 row per ticker. Use the batch
    # data context for regime and prediction (they need history for HMM),
    # but override with live features where possible.
    try:
        latest_regimes = regime.get_latest_regimes(live_features)
        latest_regime_probs = regime.get_latest_regime_probs(live_features)
    except Exception as e:
        logger.warning("Regime detection on live features failed: %s, using fallback", e)
        latest_regimes = {}
        latest_regime_probs = {}
        for _, row in live_features.iterrows():
            t = row["ticker"]
            try:
                if hasattr(regime, '_classify_row_fallback'):
                    r = regime._classify_row_fallback(row)
                else:
                    r = "UNKNOWN"
                latest_regimes[t] = r
                if hasattr(regime, '_fallback_probs'):
                    latest_regime_probs[t] = regime._fallback_probs(r)
                else:
                    latest_regime_probs[t] = {}
            except Exception:
                latest_regimes[t] = "UNKNOWN"
                latest_regime_probs[t] = {}

    # For tickers with no regime or UNKNOWN, use heuristic fallback
    for ticker_name in live_features["ticker"].unique():
        if ticker_name not in latest_regimes or latest_regimes[ticker_name] == "UNKNOWN":
            try:
                row = live_features[live_features["ticker"] == ticker_name].iloc[-1]
                if hasattr(regime, '_classify_row_fallback'):
                    latest_regimes[ticker_name] = regime._classify_row_fallback(row)
                else:
                    # Simple heuristic: use volatility and momentum
                    vol = row.get("volatility_1h", 0)
                    mom = abs(row.get("momentum_1h", 0))
                    hours_left = row.get("time_to_expiry_hours", 999)

                    if vol < 0.005:
                        latest_regimes[ticker_name] = "STALE"
                    elif hours_left < 24:
                        latest_regimes[ticker_name] = "CONVERGENCE"
                    elif mom > 0.3:  # logit-space momentum
                        latest_regimes[ticker_name] = "TRENDING"
                    elif vol > 0.03:
                        latest_regimes[ticker_name] = "HIGH_VOLATILITY"
                    else:
                        latest_regimes[ticker_name] = "MEAN_REVERTING"
            except Exception:
                latest_regimes[ticker_name] = "CONVERGENCE"  # safe default

    try:
        pred_results = predictor.predict(live_features)
        if len(pred_results) > 0:
            latest_preds = pred_results.groupby("ticker").last()
        else:
            latest_preds = pd.DataFrame()
    except Exception as e:
        logger.warning("Predictor on live features failed: %s", e)
        latest_preds = pd.DataFrame()

    # ── Fair value with LIVE orderbook data ────────────────────────────
    # The fair value model's predict() reads orderbook_imbalance from the row.
    # In live mode, this is non-zero (from the real orderbook), unlike batch
    # where it was 0 for all historical rows.
    try:
        fv_all = fv_model.predict(live_features)
        pre_filter_edge = min(s.min_edge for s in STRATEGIES.values()) if STRATEGIES else 0.025
        fv_signals = fv_model.get_signals(live_features, min_edge=pre_filter_edge)
        fv_weights = fv_model.get_current_weights()
    except Exception as e:
        logger.error("Fair value prediction failed: %s", e)
        return {}

    # ── Build event prefix map for BUY_YES preference ──────────────────
    event_tickers = defaultdict(list)
    for _, fv_row in fv_signals.iterrows():
        t = fv_row["ticker"]
        parts = t.rsplit("-", 1)
        prefix = parts[0] if len(parts) > 1 else t
        event_tickers[prefix].append(fv_row.to_dict())

    # ── Combine into unified signals ──────────────────────────────────
    signals = []

    for _, fv_row in fv_signals.iterrows():
        ticker = fv_row["ticker"]

        # Use LIVE price from state store (freshest available)
        live_market = state.get_market(ticker)
        if live_market and live_market["price"] > 0:
            current_price = live_market["price"]
        else:
            current_price = fv_row["current_price"]

        fair_value = fv_row["fair_value"]
        edge = fair_value - current_price  # recompute with live price

        # Regime
        mkt_regime = latest_regimes.get(ticker, "UNKNOWN")
        regime_probs = latest_regime_probs.get(ticker, {})

        if mkt_regime == "STALE":
            continue

        # Price prediction (shared across strategies for this ticker)
        pred_dir, pred_conf, pred_change = 0, 0.0, 0.0
        if ticker in latest_preds.index:
            pred_dir = int(latest_preds.loc[ticker, "predicted_direction"])
            pred_conf = float(latest_preds.loc[ticker, "confidence"])
            if "predicted_change" in latest_preds.columns:
                pred_change = float(latest_preds.loc[ticker, "predicted_change"])

        # Direction
        if edge > 0:
            direction = "BUY_YES"
        elif edge < 0:
            direction = "BUY_NO"
        else:
            direction = "HOLD"

        # If multiple siblings in same event, keep the one with largest |edge|
        # instead of always preferring BUY_YES (which introduces directional bias)
        event_prefix = ticker.rsplit("-", 1)[0] if "-" in ticker else ticker
        siblings = event_tickers.get(event_prefix, [])
        if len(siblings) > 1:
            my_abs_edge = abs(edge)
            better_sibling = any(
                sib["ticker"] != ticker and abs(sib["edge"]) > my_abs_edge
                for sib in siblings
            )
            if better_sibling:
                continue

        # Get minutes_to_release from live features row
        live_row = live_features[live_features["ticker"] == ticker]
        minutes_to_release = 999.0
        if len(live_row) > 0 and "minutes_to_release" in live_row.columns:
            mtr = live_row.iloc[0].get("minutes_to_release", 999.0)
            if pd.notna(mtr):
                minutes_to_release = float(mtr)

        # Select applicable strategies for this market
        matched_strategies = select_strategies(mkt_regime, minutes_to_release)
        if not matched_strategies:
            continue

        # Combined confidence (shared across strategies)
        # Note: This confidence score is NOT a calibrated probability.
        # It is a composite heuristic that feeds into position sizing.
        # For production use, replace with isotonic regression on OOS backtest data.
        fv_conf = min(abs(edge) / 0.10, 1.0)
        pred_agrees = (pred_dir > 0 and edge > 0) or (pred_dir < 0 and edge < 0)
        pred_bonus = pred_conf * 0.3 if pred_agrees else -pred_conf * 0.1
        regime_mult = {
            "MEAN_REVERTING": 1.0, "TRENDING": 0.9, "HIGH_VOLATILITY": 0.6,
            "CONVERGENCE": 0.8, "STALE": 0.0,
        }.get(mkt_regime, 0.5)
        regime_certainty = max(regime_probs.values()) if regime_probs else 0.5
        confidence = float(np.clip(
            fv_conf * 0.4 + pred_bonus + regime_mult * 0.15 + regime_certainty * 0.05, 0, 1
        ))

        # Meta-model quality (shared)
        meta_quality = meta.predict_trade_quality(
            pred_dir, pred_conf, pred_change, edge, mkt_regime, regime_probs
        )

        scored_info = scored_map.get(ticker, {})

        # Sentiment (shared)
        try:
            from pipeline.sentiment import get_sentiment
            sentiment_data = get_sentiment(
                ticker, scored_info.get("title", ""),
                scored_info.get("category", ""), current_price,
            )
        except Exception:
            sentiment_data = {
                "consensus_edge": 0.0, "sentiment_edge": 0.0,
                "ai_prob": 0.0, "ai_edge": 0.0, "ai_reasoning": "",
                "consensus_prob": 0.0, "source": "",
            }

        # Emit one signal per matching strategy
        for strat_config in matched_strategies:
            # Strategy-specific edge gate
            if abs(edge) < strat_config.min_edge:
                continue

            # Strategy-specific meta gate
            if meta_quality < strat_config.meta_gate:
                continue

            # Strategy-specific entry checks
            if strat_config.name == "momentum" and not pred_agrees:
                continue
            if strat_config.name == "event_driven":
                if abs(sentiment_data.get("consensus_edge", 0)) < 0.02:
                    continue

            # Position sizing (uses base risk model, strategy adjusts Kelly)
            contracts, risk_details = risk.position_size(
                ticker, edge, confidence, current_price,
                direction=direction,
                category=scored_info.get("category", ""),
                volume_24h=int(scored_info.get("volume", 0)),
            )
            if contracts <= 0:
                continue
            contracts = min(contracts, strat_config.max_contracts)

            # Hedging
            hedges = risk.suggest_hedges(ticker, direction)

            # Reasons
            net_edge_val = risk_details.get("net_edge", abs(edge) - kalshi_fee_rt(current_price))
            reasons = [
                f"Fair value {fair_value:.2f} vs market {current_price:.2f} "
                f"(gross {edge:+.2f}, net {net_edge_val:+.4f})",
                f"Strategy: {strat_config.name}",
            ]
            if pred_agrees:
                reasons.append(f"ML predictor agrees ({pred_conf:.0%} confidence)")
            elif pred_dir != 0:
                reasons.append(f"ML predictor disagrees (direction={pred_dir})")
            reasons.append(f"Regime: {mkt_regime}")
            reasons.append(f"Meta-model quality: {meta_quality:.0%}")
            reasons.append("Source: LIVE features (WebSocket)")
            if abs(sentiment_data.get("consensus_edge", 0)) > 0.01:
                reasons.append(f"Consensus edge: {sentiment_data['consensus_edge']:+.2f}")

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
                "strategy": strat_config.name,
                "strategy_params": {
                    "stop_loss_pct": strat_config.stop_loss_pct,
                    "take_profit_ratio": strat_config.take_profit_ratio,
                    "kelly_fraction": strat_config.kelly_fraction,
                    "max_hold_hours": strat_config.max_hold_hours,
                },
                "minutes_to_release": round(minutes_to_release, 1),
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
                "decay_curve": [{"minutes": 0, "edge": round(edge, 4)}],
                "consensus_edge": round(sentiment_data.get("consensus_edge", 0), 4),
                "consensus_prob": round(sentiment_data.get("consensus_prob", 0), 4),
                "sentiment_edge": round(sentiment_data.get("sentiment_edge", 0), 4),
                "ai_prob": round(sentiment_data.get("ai_prob", 0), 4),
                "ai_edge": round(sentiment_data.get("ai_edge", 0), 4),
                "ai_reasoning": sentiment_data.get("ai_reasoning", sentiment_data.get("reasoning", "")),
                "regime_probs": {k: round(v, 4) for k, v in regime_probs.items()} if regime_probs else {},
                "fv_weights": fv_weights,
                "live": True,
            }
            signals.append(signal)

    # Filter by minimum liquidity, rank by signal quality only (not volume)
    signals = [s for s in signals if s.get("volume", 0) >= 50]
    for s in signals:
        s["_rank"] = abs(s.get("net_edge", s["edge"])) * s["confidence"]
    signals.sort(key=lambda s: s["_rank"], reverse=True)

    # Reserve at least 1 signal per active strategy in top 10
    seen_strategies = set()
    reserved = []
    remaining = []
    for s in signals:
        if s["strategy"] not in seen_strategies and len(reserved) < 10:
            reserved.append(s)
            seen_strategies.add(s["strategy"])
        else:
            remaining.append(s)
    top_signals = reserved
    for s in remaining:
        if len(top_signals) >= 10:
            break
        top_signals.append(s)
    # Re-sort by rank
    top_signals.sort(key=lambda s: s.get("_rank", 0), reverse=True)
    for s in top_signals:
        s.pop("_rank", None)
    # Clean _rank from remaining too
    for s in signals:
        s.pop("_rank", None)

    elapsed_ms = (time.time() - t0) * 1000

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_value": portfolio_value,
        "total_signals": len(signals),
        "signals": top_signals,
        "live": True,
    }

    logger.info(
        "Live ensemble: %d markets scored, %d signals generated, %.0fms elapsed",
        len(live_features), len(signals), elapsed_ms,
    )

    return result


