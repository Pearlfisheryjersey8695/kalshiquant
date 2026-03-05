"""STEP 1 DIAGNOSTIC: Why only 3 trades? Check every filter for all 26 markets."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
from models.features import load_features, prepare_ml_data, get_feature_matrix
from models.fair_value import FairValueModel
from models.price_predictor import PricePredictor
from models.regime_detector import RegimeDetector
from models.risk_model import RiskModel
from models.meta_model import MetaModel
from pipeline.sentiment import get_consensus_edge
from collections import defaultdict

features = load_features()
features_sorted = features.sort_index()
n = len(features_sorted)
split = int(n * 0.70)
train = features_sorted.iloc[:split]
test = features_sorted.iloc[split:]

print(f"Total rows: {n}, Train: {len(train)}, Test: {len(test)}")
print(f"Test period: {test.index.min()} to {test.index.max()}")
print(f"Test duration: {(test.index.max() - test.index.min()).total_seconds()/3600:.1f} hours")
print()

# Fit models
fv = FairValueModel()
fv.fit(train)

scored = pd.read_csv("data/scored_markets.csv")
scored_map = scored.set_index("ticker").to_dict("index")
fv.set_scored_map(scored_map)

# Warmup adaptive weights
late_train = train.iloc[int(len(train)*0.5):]
_ = fv.predict(late_train)

# Predict on test
fv_all = fv.predict(test)

regime = RegimeDetector()
regime.fit(train)
latest_regimes = regime.get_latest_regimes(features_sorted)

predictor = PricePredictor()
predictor.fit(features_sorted)
pred_results = predictor.predict(features_sorted)
if len(pred_results) > 0:
    latest_preds = pred_results.sort_index().groupby("ticker").last()
else:
    latest_preds = pd.DataFrame()

# Train meta-model
meta = MetaModel()
meta_eval = train.iloc[int(len(train)*0.6):]
if len(meta_eval) > 50:
    try:
        meta_fv = fv.predict(meta_eval)
        meta_fv_latest = meta_fv.groupby("ticker").last()
        meta_xgb = predictor.predict(meta_eval)
        meta_xgb_latest = meta_xgb.groupby("ticker").last() if len(meta_xgb) > 0 else pd.DataFrame()
        ml_meta = prepare_ml_data(meta_eval)
        if "future_return" in ml_meta.columns:
            meta_returns = ml_meta.groupby("ticker")["future_return"].last()
            meta_regimes = regime.get_latest_regimes(meta_eval)
            common = meta_fv_latest.index.intersection(meta_returns.index)
            xd, xc, xch, fe, ra, aa = [], [], [], [], [], []
            for t in common:
                fve = meta_fv_latest.loc[t, "edge"]
                if abs(fve) < 0.01: continue
                fe.append(fve)
                if t in meta_xgb_latest.index:
                    xd.append(int(meta_xgb_latest.loc[t, "predicted_direction"]))
                    xc.append(float(meta_xgb_latest.loc[t, "confidence"]))
                    xch.append(float(meta_xgb_latest.loc[t].get("predicted_change", 0)))
                else:
                    xd.append(0); xc.append(0.5); xch.append(0.0)
                ra.append(meta_regimes.get(t, "UNKNOWN"))
                aa.append(float(meta_returns.loc[t]))
            if len(fe) > 10:
                meta.fit(np.array(xd), np.array(xc), np.array(xch), np.array(fe), np.array(ra), np.array(aa))
    except Exception as e:
        print(f"Meta failed: {e}")

risk = RiskModel(portfolio_value=10000)
risk.fit(train)

FEE = 0.03
MIN_EDGE = max(0.02, FEE * 1.5)  # 0.045

print(f"FV weights after warmup: {fv.get_current_weights()}")
print(f"MIN_EDGE = {MIN_EDGE}")
print()

# ── Analyze ALL 26 tickers at their latest test bar ──
print("=" * 220)
print(f"{'Ticker':<50} {'Price':>5} {'FV':>5} {'Edge':>7} {'|E|>4.5c':>8} {'Regime':<16} {'RegK':>4} {'PrD':>3} {'PrC':>4} {'Meta':>5} {'MetK':>4} {'Dir':>7} {'BYpf':>4} {'Ctrs':>5} {'PszK':>4} {'CnsE':>6} {'KILL REASON'}")
print("-" * 220)

# Build BUY_YES prefix map
buy_yes_prefixes = set()
for t in test["ticker"].unique():
    ticker_fv = fv_all[fv_all["ticker"] == t]
    if len(ticker_fv) > 0:
        latest_edge = ticker_fv.iloc[-1]["edge"]
        if latest_edge > MIN_EDGE:
            parts = t.rsplit("-", 1)
            prefix = parts[0] if len(parts) > 1 else t
            buy_yes_prefixes.add(prefix)

regime_mult_map = {
    "MEAN_REVERTING": 1.0, "TRENDING": 0.9, "HIGH_VOLATILITY": 0.6,
    "CONVERGENCE": 0.8, "STALE": 0.0
}

for ticker in sorted(test["ticker"].unique()):
    ticker_fv = fv_all[fv_all["ticker"] == ticker].sort_values("timestamp")
    if len(ticker_fv) == 0:
        print(f"{ticker:<50} NO FV DATA")
        continue

    last = ticker_fv.iloc[-1]
    price = last["current_price"]
    fair_val = last["fair_value"]
    edge = last["edge"]

    mkt_regime = latest_regimes.get(ticker, "UNKNOWN")
    regime_kill = mkt_regime == "STALE"
    edge_pass = abs(edge) >= MIN_EDGE

    pred_dir = 0; pred_conf = 0.0; pred_change = 0.0
    if ticker in latest_preds.index:
        pred_dir = int(latest_preds.loc[ticker, "predicted_direction"])
        pred_conf = float(latest_preds.loc[ticker, "confidence"])
        pred_change = float(latest_preds.loc[ticker].get("predicted_change", 0))

    direction = "BUY_YES" if edge > 0 else "BUY_NO" if edge < 0 else "HOLD"

    buy_yes_skip = False
    if direction == "BUY_NO":
        parts = ticker.rsplit("-", 1)
        prefix = parts[0] if len(parts) > 1 else ticker
        if prefix in buy_yes_prefixes:
            buy_yes_skip = True

    regime_mult = regime_mult_map.get(mkt_regime, 0.5)
    meta_quality = meta.predict_trade_quality(pred_dir, pred_conf, pred_change, edge, mkt_regime)
    meta_kill = meta_quality < 0.40

    contracts = 0
    pos_sz_kill = False
    if edge_pass and not regime_kill and not meta_kill and not buy_yes_skip:
        fv_conf = min(abs(edge) / 0.10, 1.0)
        pred_agrees = (pred_dir > 0 and edge > 0) or (pred_dir < 0 and edge < 0)
        pred_bonus = pred_conf * 0.3 if pred_agrees else -pred_conf * 0.1
        confidence = float(np.clip(fv_conf * 0.5 + pred_bonus + regime_mult * 0.15 + 0.5 * 0.05, 0, 1))
        contracts, details = risk.position_size(
            ticker, edge, confidence, price,
            direction=direction,
            category=scored_map.get(ticker, {}).get("category", ""),
            volume_24h=int(scored_map.get(ticker, {}).get("volume", 0)),
        )
        if contracts <= 0:
            pos_sz_kill = True

    cons = get_consensus_edge(ticker, price)
    cons_edge = cons["consensus_edge"]

    reason = ">>> SIGNAL" if contracts > 0 else ""
    if regime_kill: reason = "REGIME=STALE"
    elif not edge_pass: reason = f"|edge|={abs(edge)*100:.1f}c < 4.5c"
    elif buy_yes_skip: reason = "BUY_YES_PREF"
    elif meta_kill: reason = f"META={meta_quality:.0%} < 40%"
    elif pos_sz_kill:
        # Diagnose WHY position_size returned 0
        if direction == "BUY_NO":
            cost = 1.0 - price
        else:
            cost = price
        fee_ratio = FEE / cost if cost > 0 else 999
        reason = f"POS_SIZE=0 (fee/cost={fee_ratio:.1%}, cost={cost:.2f})"

    print(f"{ticker:<50} {price:>5.2f} {fair_val:>5.2f} {edge:>+7.4f} {'Y' if edge_pass else 'N':>8} {mkt_regime:<16} {'K' if regime_kill else '.':>4} {pred_dir:>3} {pred_conf:>4.2f} {meta_quality:>5.2f} {'K' if meta_kill else '.':>4} {direction:>7} {'K' if buy_yes_skip else '.':>4} {contracts:>5} {'K' if pos_sz_kill else '.':>4} {cons_edge:>+6.2f} {reason}")

# ── Edge distribution across ALL test bars ──
print("\n\n=== EDGE DISTRIBUTION ACROSS ALL TEST BARS ===")
print(f"{'Ticker':<50} {'Bars':>5} {'|E|>4.5c':>8} {'|E|>3c':>6} {'|E|>2c':>6} {'MaxE':>6} {'MeanE':>6}")
print("-" * 100)
for ticker in sorted(test["ticker"].unique()):
    ticker_fv = fv_all[fv_all["ticker"] == ticker]
    total_bars = len(ticker_fv)
    gt45 = len(ticker_fv[ticker_fv["edge"].abs() >= 0.045])
    gt3 = len(ticker_fv[ticker_fv["edge"].abs() >= 0.03])
    gt2 = len(ticker_fv[ticker_fv["edge"].abs() >= 0.02])
    mx = ticker_fv["edge"].abs().max()
    mn = ticker_fv["edge"].abs().mean()
    print(f"{ticker:<50} {total_bars:>5} {gt45:>8} {gt3:>6} {gt2:>6} {mx*100:>5.1f}c {mn*100:>5.1f}c")

# ── XGBoost predictions for all tickers ──
print("\n\n=== XGBOOST PREDICTIONS (ALL TICKERS) ===")
print(f"{'Ticker':<50} {'Dir':>4} {'Conf':>5} {'PredChange':>10}")
print("-" * 80)
for ticker in sorted(test["ticker"].unique()):
    if ticker in latest_preds.index:
        pd_dir = int(latest_preds.loc[ticker, "predicted_direction"])
        pd_conf = float(latest_preds.loc[ticker, "confidence"])
        pd_change = float(latest_preds.loc[ticker].get("predicted_change", 0))
        print(f"{ticker:<50} {pd_dir:>4} {pd_conf:>5.2f} {pd_change*100:>9.2f}c")
    else:
        print(f"{ticker:<50}  N/A")

# ── Sentiment for all tickers ──
print("\n\n=== CONSENSUS EDGE (ALL TICKERS) ===")
for ticker in sorted(test["ticker"].unique()):
    last_price = fv_all[fv_all["ticker"] == ticker].iloc[-1]["current_price"]
    cons = get_consensus_edge(ticker, last_price)
    print(f"  {ticker:<50} price={last_price:.2f}  consensus_prob={cons['consensus_prob']:.2f}  edge={cons['consensus_edge']:+.4f}  src={cons['source']}")
