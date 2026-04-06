"""
Demo mode: generate synthetic signals from backtest trades when ensemble
produces zero live signals (all markets filtered by regime/meta gates).
Ensures the dashboard is never empty during hackathon demo.
"""

import json
import os
from datetime import datetime, timezone


def load_demo_signals(portfolio_value: float = 10000) -> dict:
    """Load backtest trades and convert the best ones into display signals."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bt_path = os.path.join(project_root, "signals", "backtest_results.json")

    try:
        with open(bt_path) as f:
            bt = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty()

    trades = bt.get("trades", [])
    if not trades:
        return _empty()

    # Pick trades with positive edge and interesting characteristics
    # Use a mix of winners and active positions
    demo_trades = []
    seen_tickers = set()
    for t in trades:
        base = t["ticker"].rsplit("-", 1)[0]
        if base in seen_tickers:
            continue
        seen_tickers.add(base)
        demo_trades.append(t)

    # Convert trades to signal format
    signals = []
    for t in demo_trades[:10]:
        ticker = t["ticker"]
        price = t["entry_price"]
        edge = t["edge_at_entry"]
        direction = t["direction"]

        # Compute fair value from edge
        fair_value = round(price + edge, 4)

        # Estimate confidence from edge magnitude
        confidence = min(abs(edge) / 0.10, 1.0) * 0.7

        # Risk details from the trade
        fee_rt = t.get("fee_per_contract_rt", 0.03)
        contracts = t["contracts"]

        signal = {
            "ticker": ticker,
            "title": _ticker_to_title(ticker),
            "category": _ticker_to_category(ticker),
            "current_price": price,
            "fair_value": fair_value,
            "edge": edge,
            "direction": direction,
            "confidence": round(confidence, 4),
            "strategy": "ENSEMBLE",
            "regime": t.get("regime", "UNKNOWN"),
            "regime_probs": {t.get("regime", "UNKNOWN"): 0.8, "MEAN_REVERTING": 0.2},
            "recommended_contracts": contracts,
            "price_prediction_1h": 1 if direction == "BUY_YES" else -1,
            "prediction_confidence": round(confidence * 0.8, 4),
            "meta_quality": round(confidence * 0.9, 4),
            "net_edge": round(abs(edge) - fee_rt, 4),
            "fee_impact": fee_rt,
            "risk": {
                "kelly_fraction": 0.01,
                "size_dollars": round(contracts * price, 2),
                "contracts": contracts,
                "stop_loss": round(max(price * 0.85, 0.01), 4),
                "take_profit": round(min(price + abs(edge) * 2, 0.99), 4),
                "true_max_loss": round(contracts * price, 2),
                "stop_loss_amount": round(contracts * price * 0.15, 2),
                "max_gain": round(contracts * (1 - price), 2),
                "risk_reward": round((1 - price) / price, 2) if price > 0 else 0,
                "net_edge": round(abs(edge) - fee_rt, 4),
                "fee_impact": fee_rt,
                "total_fees": round(contracts * fee_rt, 2),
            },
            "reasons": [
                f"Demo signal from backtest (originally {t['exit_reason']})",
                f"Edge: {edge:+.4f} ({t['signal_type']})",
                f"Regime: {t.get('regime', 'UNKNOWN')}",
                f"Historical P&L: ${t.get('net_pnl', 0):.2f} net",
            ],
            "hedge": None,
            "consensus_edge": 0,
            "consensus_prob": 0,
            "sentiment_edge": 0,
            "ai_prob": 0,
            "ai_edge": 0,
            "ai_reasoning": "",
            "fv_weights": {},
            "decay_curve": [],
            "volume": 1000,
            "open_interest": 500,
            "tradability_score": 0.5,
            "expiration_time": None,
        }
        signals.append(signal)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_value": portfolio_value,
        "total_signals": len(signals),
        "signals": signals,
        "demo_mode": True,
    }


def _ticker_to_title(ticker: str) -> str:
    if "KXFED" in ticker:
        return f"Fed Funds Rate {ticker.split('-')[-1] if '-' in ticker else ''}"
    if "KXBTCMAX" in ticker:
        return f"Bitcoin Monthly Max Price"
    if "KXBTCMIN" in ticker:
        return f"Bitcoin Monthly Min Price"
    if "KXAAAGASM" in ticker:
        return f"Average Gas Price"
    return ticker


def _ticker_to_category(ticker: str) -> str:
    if "KXFED" in ticker:
        return "Economics"
    if "KXBTC" in ticker:
        return "Crypto"
    if "KXAAAG" in ticker:
        return "Economics"
    return "Other"


def _empty() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_value": 10000,
        "total_signals": 0,
        "signals": [],
        "demo_mode": True,
    }
