# Negative results

> *"In science, the only thing more valuable than a result is a result that
> contradicts what you expected."*

This document records what **didn't** work, with the same level of detail as
what did. Most quant systems hide their failed experiments. The failures are
where the actual learning is.

Each entry contains: the hypothesis being tested, the experimental setup, the
result with statistics, and the conclusion drawn from it.

---

## Setup

All experiments below run [`scripts/replay_settled.py`](../scripts/replay_settled.py)
against the same snapshot of Kalshi settled markets — 5,000 markets pulled via
the public REST API, of which ~750 had usable previous-quote data after the
filters in [`scripts/backfill_calibration.py`](../scripts/backfill_calibration.py).

Each strategy is a function `quote → BUY_YES | BUY_NO | SKIP`. For markets it
chose to trade, the simulated entry is at the previous-quote price and the exit
is settlement. P&L is computed per-contract and is fully fee-aware (Kalshi
round-trip fee = `ceil(0.07 * P * (1-P) * 100) / 100` per side).

| Strategy | Hypothesis under test |
|---|---|
| `midpoint` | "Markets priced away from 0.50 are over-confident and will mean-revert" |
| `distance` | "Specifically, markets *more than 20pp away from 0.50* are the most over-confident, so we should fade only the extremes" |
| `calibrator` | "Apply the isotonic calibration curve fitted on a different slice of the same tape — trade only when calibrated FV diverges from the live quote by ≥4¢" |

---

## Negative result #1: naive mean-reversion loses money

**Hypothesis**: Kalshi quotes drift away from fair value, so fading any market
not at 0.50 should be profitable on average after fees.

**Strategy**: `midpoint`
- If `quote > 0.55`: BUY_NO
- If `quote < 0.45`: BUY_YES
- Else SKIP

**Result**:

| metric | value |
|---|---|
| n_markets_seen | 5,000 |
| n_traded | 725 |
| n_winners | 30 |
| **hit_rate** | **4.1%** |
| **avg net P&L per contract** | **−$0.081** |
| total net P&L (per contract, summed over 725 trades) | −$58.82 |
| per-trade Sharpe (no annualization) | −0.43 |
| avg win | +$0.686 |
| avg loss | −$0.114 |

**Interpretation**: Of 725 trades, only 30 (4.1%) were winners. The strategy
fades quotes one direction and the market settles the *other* direction
overwhelmingly. The wins are large (+$0.69 each) — when a fade works, it pays
out — but they're rare enough that the much-more-frequent small losses
dominate the expected value.

**What this falsifies**: The folk hypothesis that prediction markets are
"emotional" and over-confident in either direction. They are, on average,
**closer to truth than 0.50** even when they look extreme. The cost of being
wrong about that is roughly 8 cents per contract.

---

## Negative result #2: fading only extremes is *worse*

**Hypothesis**: OK, fading every non-50 market is too aggressive. Maybe only
the *extreme* markets (>20pp from 0.50) are the over-confident ones. Restrict
the fade to those.

**Strategy**: `distance` (threshold = 0.20)
- If `quote > 0.70`: BUY_NO
- If `quote < 0.30`: BUY_YES
- Else SKIP

**Result**:

| metric | value |
|---|---|
| n_traded | 673 |
| **hit_rate** | **2.5%** |
| **avg net P&L per contract** | **−$0.075** |
| per-trade Sharpe | −0.48 |
| avg win | +$0.776 |
| avg loss | −$0.097 |

**Interpretation**: The hit rate **drops further** (4.1% → 2.5%) when we
restrict to extremes. This is the *opposite* of what the hypothesis predicted —
extreme markets are **even more accurate** than mildly one-sided markets. The
per-contract loss is similar to strategy #1 (slightly smaller because the
filter eliminated some near-50 noise), but the directionality is unambiguous:
**the further a Kalshi market is from 0.50, the more right it is on average.**

**What this falsifies**: A second folk hypothesis — that "extreme markets are
where the dumb retail flow piles in." The data says no. Extreme markets in this
sample are **better-calibrated** than middling markets, not worse. If anything
the inefficiency is in the **middle** (40-60c), not the tails.

---

## Positive control: the calibrator strategy

To rule out "the replay framework is broken", run a strategy that's *expected*
to work: use the isotonic calibration curve fitted on the same tape and trade
only when the curve disagrees with the live quote by ≥4¢.

**Strategy**: `calibrator`

**Result**:

| metric | value |
|---|---|
| n_traded | 91 |
| **hit_rate** | **92.3%** |
| **avg net P&L per contract** | **+$0.115** |
| per-trade Sharpe | +0.39 |
| avg win | +$0.192 |
| avg loss | −$0.809 |

**Interpretation**: Positive expected value, positive Sharpe, ~92% hit rate.
**Caveat**: most of the wins are BUY_NO on cheap MVE-leg markets near
settlement. A meaningful portion of this "alpha" is structural decay
harvesting — markets near 0 settle near 0 — which is a real edge but not the
*kind* of edge we set out to find.

**What this confirms**: The replay framework correctly distinguishes profitable
from unprofitable strategies. The midpoint and distance failures are not
artifacts of bad measurement — they're real.

---

## Cross-strategy summary

| Strategy | n_trades | hit_rate | avg P&L/contract | per-trade Sharpe | conclusion |
|---|---|---|---|---|---|
| `midpoint` | 725 | 4.1% | **−$0.081** | −0.43 | Falsifies "fade non-50 markets" |
| `distance` | 673 | 2.5% | **−$0.075** | −0.48 | Falsifies "fade only extremes" |
| `calibrator` | 91 | 92.3% | **+$0.115** | +0.39 | Confirms framework works |

---

## Implications for the live system

These negative results directly informed three design decisions:

1. **`engine/strategies.py:mean_reversion`** uses `min_edge=0.025` and
   `take_profit_ratio=1.5`. Both numbers are tighter than they would be without
   these results — generic mean reversion would size aggressively on any market
   off 0.50, and the data says that's a money-loser.
2. **The calibrator gate in [`engine/integrated_decision.py`](../engine/integrated_decision.py)
   requires ≥4¢ of *calibrated* edge** before considering a trade. This is the
   threshold below which the replay can't distinguish edge from noise.
3. **The brain's persistence requirement** (`MIN_SIGNAL_PERSISTENCE` in
   `engine/execution_engine.py`) requires a signal to survive multiple cycles
   before triggering an entry. The midpoint/distance failures suggest that
   transient one-sided quotes are NOT reliable signals; only persistent ones
   should be acted on.

---

## What I'd test next (if I had more time)

The settled-market replay only captures the *terminal* P&L of a one-shot
entry. It doesn't capture the path. Three follow-up experiments worth running:

1. **Time-of-day stratification**: are mid-session quotes more or less
   informative than end-of-session quotes? The distance strategy might work in
   one regime and not the other.
2. **Category stratification**: are sports markets calibrated differently from
   political markets? The current calibration curve is averaged over all
   categories; per-category curves might reveal that the failure mode is
   actually category-specific.
3. **Edge-decay timing**: how long does a one-sided quote *persist* before
   reverting (or settling)? If the edge is real but decays in <5 minutes, our
   5-minute signal cycle is too slow to capture it.

The right way to do (1) and (3) is to record our own intra-day orderbook tape
for a few weeks and re-run replay against it. The right way to do (2) is just
to add a `--category` filter to the existing replay script.

---

## How to reproduce

```bash
# Pull the same settled-market snapshot used here
python -m scripts.backfill_calibration --max-pages 25

# Run each strategy
python -m scripts.replay_settled --max-pages 25 --strategy midpoint
python -m scripts.replay_settled --max-pages 25 --strategy distance
python -m scripts.replay_settled --max-pages 25 --strategy calibrator

# Each run logs to data/experiments.jsonl with full params + metrics for
# later inspection. The experiment tracker is in analysis/experiment_tracker.py.
```

Numbers above are from a snapshot pulled on **2026-04-07**. Re-running on a
later date will use a different settled-market window and may produce
different (but qualitatively similar) numbers.
