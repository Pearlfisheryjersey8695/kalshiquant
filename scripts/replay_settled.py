"""Backtest replay against Kalshi's settled-market tape.

What this does
--------------
We don't have historical orderbook tape (would need to record it ourselves
over weeks). But we DO have the full settled-market list from Kalshi's REST
API, complete with each market's last observed quote (`previous_yes_bid/ask`)
and final outcome (`result`).

That's enough to do a *terminal-state* backtest:

  For each settled market with a non-trivial last quote:
    1. Compute what our strategy would have decided at that quote
       (BUY_YES / BUY_NO / SKIP) using only the FAIR-VALUE input we'd
       have had at quote time.
    2. If we'd have entered, mark the position to settlement (binary 0 or 1).
    3. Subtract entry/exit fees.
    4. Aggregate P&L across all such "trades."

This is different from a tick replay because we only have ONE simulated trade
per market (no intra-life MtM, no early exits). But it gives us:
  - **Hit rate** of the strategy's directional calls
  - **Per-trade expected P&L** including fees
  - **Sharpe-like reward/risk** from the trade distribution
  - **A reality check** on whether the live model ever produces +EV in
    settled markets, BEFORE we risk capital

The alternative — pure paper replay — takes weeks and is what the live
server is doing in the background. This is the complementary "use what we
already have" check.

Usage:
    python -m scripts.replay_settled --max-pages 25
    python -m scripts.replay_settled --max-pages 25 --strategy fair_value_only
    python -m scripts.replay_settled --max-pages 25 --json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from statistics import mean, stdev

from analysis.experiment_tracker import track
from app.kalshi_client import KalshiClient
from models.risk_model import kalshi_fee_rt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("replay")


@dataclass
class ReplayTrade:
    ticker: str
    direction: str          # "BUY_YES" or "BUY_NO"
    entry_price: float      # in [0, 1]
    outcome: float          # 0 or 1
    pnl_per_contract: float
    fees: float
    net_pnl: float


@dataclass
class ReplayResult:
    n_markets_seen: int = 0
    n_skipped_no_quote: int = 0
    n_skipped_no_outcome: int = 0
    n_skipped_strategy_pass: int = 0
    n_traded: int = 0
    trades: list[ReplayTrade] = field(default_factory=list)

    @property
    def n_winners(self) -> int:
        return sum(1 for t in self.trades if t.net_pnl > 0)

    @property
    def hit_rate(self) -> float:
        return self.n_winners / self.n_traded if self.n_traded else 0.0

    @property
    def total_net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    @property
    def avg_net_pnl(self) -> float:
        return self.total_net_pnl / self.n_traded if self.n_traded else 0.0

    def to_dict(self) -> dict:
        return {
            "n_markets_seen": self.n_markets_seen,
            "n_skipped_no_quote": self.n_skipped_no_quote,
            "n_skipped_no_outcome": self.n_skipped_no_outcome,
            "n_skipped_strategy_pass": self.n_skipped_strategy_pass,
            "n_traded": self.n_traded,
            "n_winners": self.n_winners,
            "hit_rate": round(self.hit_rate, 4),
            "total_net_pnl_per_contract": round(self.total_net_pnl, 4),
            "avg_net_pnl_per_contract": round(self.avg_net_pnl, 4),
        }


# ── Parsing helpers (mirror backfill_calibration.py) ────────────────────
def _to_float(val) -> float:
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _settled_outcome(market: dict) -> float | None:
    result = (market.get("result") or "").lower()
    if result == "yes":
        return 1.0
    if result in ("no", "all_no"):
        return 0.0
    return None


def _last_quote(market: dict) -> float | None:
    bid = _to_float(market.get("previous_yes_bid_dollars"))
    ask = _to_float(market.get("previous_yes_ask_dollars"))
    if 0 < bid < 1 and 0 < ask < 1 and ask >= bid:
        return (bid + ask) / 2.0
    prev = _to_float(market.get("previous_price_dollars"))
    if 0 < prev < 1:
        return prev
    last = _to_float(market.get("last_price_dollars"))
    if 0 < last < 1:
        return last
    return None


# ── Strategy "decisions" ────────────────────────────────────────────────
# Each strategy is a function (last_quote, fair_value) -> "BUY_YES" / "BUY_NO" / None
#
# For replay we need a *fair value* signal — the thing the strategy compares
# the quote against. We have a few options:
#
#   1. fair_value_only:    skip this market (no FV input). Only useful as a
#                          baseline showing total skip rate.
#   2. midpoint:           assume FV = 0.50. Trades any market that's not at
#                          50c. Reveals whether one-sided markets reliably
#                          mean-revert (they don't).
#   3. distance_threshold: trade only when |quote - 0.50| > threshold. Tests
#                          whether extreme markets are over/underconfident.
#   4. realised_calibrator: USE THE FITTED CALIBRATOR as the fair value.
#                          This is the most interesting one — it tests whether
#                          the calibrator's correction adds alpha.

def strat_midpoint(quote: float) -> str | None:
    if quote > 0.55:
        return "BUY_NO"
    if quote < 0.45:
        return "BUY_YES"
    return None


def strat_distance(quote: float, threshold: float = 0.20) -> str | None:
    """Only trade extreme markets where the quote is far from 0.50.

    The hypothesis: extreme markets reflect *real* information and should be
    trusted (no fade), while moderately one-sided markets are noise and should
    be faded. We test the OPPOSITE — fade extremes — to see if it loses money.
    """
    if quote > 0.50 + threshold:
        return "BUY_NO"
    if quote < 0.50 - threshold:
        return "BUY_YES"
    return None


def strat_calibrator(quote: float, calibrator) -> str | None:
    """Use the fitted calibrator to predict actual hit rate.

    If calibrator says "markets at 0.30 actually settle YES 8% of the time"
    and we see a market at 0.30, that's a 22-point edge to BUY_NO. Trade
    when |edge| > 5c (after fees).
    """
    predicted_p_yes = calibrator.calibrate(quote)
    edge = predicted_p_yes - quote
    if abs(edge) < 0.05:
        return None
    return "BUY_YES" if edge > 0 else "BUY_NO"


# ── Replay ───────────────────────────────────────────────────────────────
def replay(
    client: KalshiClient,
    max_pages: int,
    strategy: str = "calibrator",
    min_volume: float = 0.0,
) -> ReplayResult:
    logger.info("Pulling settled markets (max %d pages)...", max_pages)
    settled = client.get_settled_markets(max_pages=max_pages)
    logger.info("Got %d settled markets", len(settled))

    result = ReplayResult(n_markets_seen=len(settled))

    cal = None
    if strategy == "calibrator":
        from models.risk_model import WinProbCalibrator
        cal = WinProbCalibrator()
        cal.load()
        if not cal._is_fitted:
            logger.warning("Calibrator not fitted — falling back to midpoint strategy")
            strategy = "midpoint"

    for m in settled:
        outcome = _settled_outcome(m)
        if outcome is None:
            result.n_skipped_no_outcome += 1
            continue
        quote = _last_quote(m)
        if quote is None:
            result.n_skipped_no_quote += 1
            continue
        vol = _to_float(m.get("volume_fp") or m.get("volume_24h_fp") or 0)
        if vol < min_volume:
            continue

        # Strategy decision
        if strategy == "midpoint":
            decision = strat_midpoint(quote)
        elif strategy == "distance":
            decision = strat_distance(quote)
        elif strategy == "calibrator":
            decision = strat_calibrator(quote, cal)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        if decision is None:
            result.n_skipped_strategy_pass += 1
            continue

        # Compute trade P&L on a single contract
        if decision == "BUY_YES":
            cost = quote
            payoff = outcome  # 1 if YES wins, 0 if NO
        else:  # BUY_NO
            cost = 1.0 - quote
            payoff = 1.0 - outcome
        gross = payoff - cost
        fees = kalshi_fee_rt(quote)
        net = gross - fees

        result.trades.append(ReplayTrade(
            ticker=m.get("ticker", ""),
            direction=decision,
            entry_price=quote,
            outcome=outcome,
            pnl_per_contract=gross,
            fees=fees,
            net_pnl=net,
        ))
        result.n_traded += 1

    return result


def _trade_sharpe(trades: list[ReplayTrade]) -> float:
    if len(trades) < 2:
        return 0.0
    pnls = [t.net_pnl for t in trades]
    mu = mean(pnls)
    sigma = stdev(pnls)
    if sigma == 0:
        return 0.0
    # Per-trade Sharpe (no annualization)
    return mu / sigma


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=25)
    parser.add_argument("--min-volume", type=float, default=0.0)
    parser.add_argument(
        "--strategy", choices=["midpoint", "distance", "calibrator"],
        default="calibrator",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with track(
        "replay_settled",
        params={
            "max_pages": args.max_pages,
            "min_volume": args.min_volume,
            "strategy": args.strategy,
        },
    ) as run:
        client = KalshiClient()
        result = replay(client, args.max_pages, args.strategy, args.min_volume)

        sharpe = _trade_sharpe(result.trades)
        run.log_metric("n_traded", result.n_traded)
        run.log_metric("hit_rate", result.hit_rate)
        run.log_metric("avg_net_pnl_per_contract", result.avg_net_pnl)
        run.log_metric("trade_sharpe", sharpe)
        run.set_tag("strategy", args.strategy)

        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            d = result.to_dict()
            print("=" * 60)
            print(f"  Replay [{args.strategy}] — {result.n_markets_seen} settled markets")
            print("=" * 60)
            for k, v in d.items():
                print(f"  {k:30s}  {v}")
            print(f"  {'trade_sharpe':30s}  {sharpe:.4f}")
            print("-" * 60)
            if result.trades:
                wins = [t for t in result.trades if t.net_pnl > 0]
                losses = [t for t in result.trades if t.net_pnl <= 0]
                print(f"  avg win  : {mean(t.net_pnl for t in wins):+.4f}/contract  (n={len(wins)})" if wins else "  no winners")
                print(f"  avg loss : {mean(t.net_pnl for t in losses):+.4f}/contract  (n={len(losses)})" if losses else "  no losers")
                # Sample first 5 trades
                print("\n  Sample trades:")
                for t in result.trades[:5]:
                    print(f"    {t.ticker[:40]:40s}  {t.direction}  entry={t.entry_price:.2f}  outcome={int(t.outcome)}  net={t.net_pnl:+.4f}")
            print("=" * 60)

    # Exit code: 0 if positive avg P&L, 1 if not (informational, not strict)
    return 0 if result.avg_net_pnl >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
