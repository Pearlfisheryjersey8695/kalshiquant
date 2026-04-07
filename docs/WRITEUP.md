# KalshiQuant — what this project is about

> *A real-time quantitative trading system for Kalshi prediction markets,
> built around the observation that **prediction markets are not just
> low-volume options markets** — they're a different statistical object,
> and the standard quant playbook needs to be re-derived for each piece.*

This document explains, in 5 minutes, what's interesting about this project
and where in the code to look. It's written for a reader who knows quant
finance and wants to evaluate technical depth, not feature count.

---

## Why prediction markets are statistically weird

A Kalshi binary contract has four properties that break standard quant tooling
in distinct ways:

| Property | What it breaks |
|---|---|
| **Bounded in [0, 1]** | Lognormal price models (every options book) — the diffusion has a wall |
| **Bimodal terminal payoff** (settles at 0 or 1) | Gaussian VaR (underestimates tail), normal-CDF probability models (wrong shape near expiry) |
| **Per-contract fee = ⌈0.07·P·(1−P)·100⌉/100** | Naive edge calculations (a 5-cent edge can be net-negative on a contract priced at 0.50) |
| **Low volume + high spread** | Backtests using mid prices (we'd never actually fill at mid) |

Most existing quant systems assume **none** of these. The interesting work in
this project is rederiving each piece — calibration, sizing, risk, exits — to
respect the geometry of binary contracts.

If you take one thing away from this writeup: **the four files this project
is built around** are the four where I had to throw away the textbook answer
and rebuild it for binaries. They are:

1. [`models/risk_model.py`](../models/risk_model.py) — fee-aware sizing + Bernoulli copula CVaR
2. [`scripts/backfill_calibration.py`](../scripts/backfill_calibration.py) — isotonic calibration trained on historical settlement tape
3. [`engine/triple_barrier.py`](../engine/triple_barrier.py) — vol-scaled exits in price units, not percent
4. [`engine/integrated_decision.py`](../engine/integrated_decision.py) — the full decision pipeline composed end-to-end

---

## Three findings you can verify in 60 seconds

### Finding #1: Kalshi quotes are systematically over-confident on cheap YES contracts

![Calibration curve](figures/calibration_curve.png)

I pulled 5,000 settled markets from the Kalshi REST API
([`scripts/backfill_calibration.py`](../scripts/backfill_calibration.py)),
extracted the previous-quote and outcome for each, and fit an isotonic
calibration curve on the 743 markets that had usable previous-quote data.

**Headline numbers** (95% bootstrap CI on 1000 resamples):
- **Brier score (market quotes)**: 0.0462 [0.0381, 0.0556]
- **Brier score (naive 0.5)**: 0.2500
- **Alpha vs naive**: +0.2038 — the market is *substantially* more informative
  than 0.5, but it's not perfectly calibrated

**The most striking single number**: at a quoted YES probability of ~0.25, the
empirical settlement rate is ~12%. The market is *overpricing* YES at low
quotes by ~13 percentage points. This is the kind of finding you can build a
strategy around — and it's the function the live calibrator is now applying
to the model's confidence inputs.

The full curve, the confidence intervals, the per-bin counts, and a quote
distribution histogram are all in [`docs/figures/calibration_curve.png`](figures/calibration_curve.png).
The methodology is in [`scripts/plot_calibration.py`](../scripts/plot_calibration.py).
A walk-through with prose is in [`notebooks/01_calibration_analysis.ipynb`](../notebooks/01_calibration_analysis.ipynb).

### Finding #2: Naive mean-reversion strategies lose money even on extreme markets

Two strategies tested against the same 5,000-market replay:

| Strategy | hypothesis | hit_rate | avg P&L/contract | conclusion |
|---|---|---|---|---|
| `midpoint` | "fade any market not at 0.50" | 4.1% | **−$0.081** | Falsified |
| `distance` | "fade only markets >20pp from 0.50" | 2.5% | **−$0.075** | **Worse** |
| `calibrator` | "use the fitted curve, trade ≥4¢ disagreement" | 92.3% | **+$0.115** | Positive control |

The directionality of the failure is unambiguous: **the further a Kalshi
market is from 0.50, the more accurate it is on average.** Folk wisdom about
"emotional retail flow piling into one-sided markets" is the opposite of what
the data shows. The full writeup with caveats is in
[`docs/NEGATIVE_RESULTS.md`](NEGATIVE_RESULTS.md).

### Finding #3: 97% of the signal cycle is the parlay pricer

The latency monitor in [`engine/latency_monitor.py`](../engine/latency_monitor.py)
records p50/p95/max per stage of the signal loop. From a live cycle:

```
Latency: total=43941ms
  external_feeds = 1251ms
  live_ensemble  =   25ms     <-- the actual model inference
  parlay_pricer  = 42662ms    <-- 97% of total
  execution      =    1ms
```

The model itself runs in **25 milliseconds**. Everything else is data fetching
and parlay-leg combinatorics. **This bottleneck wasn't in any of my mental
models when I built the system** — I'd have guessed that XGBoost inference or
the WebSocket reconnect logic was the slow part. It wasn't, and I only know
that because I bothered to instrument it.

This is the value of building observability *before* you need it: when the
question "where is the time going" comes up, you have an answer in seconds
instead of an afternoon of profiling.

---

## Three design decisions justified by the domain

### Decision #1: Fee-aware position sizing as a hard gate, not a post-hoc adjustment

**The naive approach**: compute Kelly sizing on raw probability edge, take a
position, accept whatever fees come.

**Why that's wrong on Kalshi**: the per-contract fee at price `P` is
`⌈0.07·P·(1−P)·100⌉/100`, which peaks at $0.0175/contract per side at P=0.50
and goes to zero at the boundaries. For a contract priced at 0.50, **a 4-cent
edge is net-zero after round-trip fees**. For a contract priced at 0.20, the
same 4-cent edge has only ~$0.013 of fee drag and is meaningfully positive.
**The same nominal edge is profitable on one contract and break-even on
another.**

**What I built**: [`RiskModel.position_size()`](../models/risk_model.py#L221)
computes `net_edge = abs(edge) - fee_impact` *before* any sizing math. If
`net_edge ≤ 0`, the position size is zero — no contracts traded. This is
checked again at entry-time in
[`ExecutionEngine._validate_entry()`](../engine/execution_engine.py#L150). And
again at the brain level in [`engine/quant_brain.py`](../engine/quant_brain.py).

The fee model itself is intentionally a single function — `kalshi_fee(price)`
in [`models/risk_model.py:119`](../models/risk_model.py#L119) — with 9 unit
tests in [`tests/test_kalshi_fees.py`](../tests/test_kalshi_fees.py) pinning
its behavior at every boundary I could think of.

### Decision #2: Bernoulli copula CVaR, not Gaussian VaR

**The naive approach**: parametric VaR on the position dollar values with a
Gaussian assumption.

**Why that's wrong on Kalshi**: a position in a binary contract priced at 0.30
is `Bernoulli(0.30)`, not `Normal(0.30, σ)`. The terminal payoff is
**bimodal at {0, 1}**. Gaussian VaR computes the 5th percentile of a normal
fitted to those two points, which is meaningless. The actual 5% tail on a
single binary is "we lost 100% of cost basis"; the normal fit will tell you
some smooth fraction of that.

**What I built**: [`RiskModel.portfolio_cvar()`](../models/risk_model.py#L457)
samples N=10,000 Monte Carlo paths where each position's terminal payoff is a
Bernoulli draw with `p = current_price`. **Cross-position correlation is
injected via a Gaussian copula** on the latent normals — sample correlated
standard normals → push through `Φ` to get correlated uniforms → threshold to
get correlated Bernoulli outcomes. CVaR is the mean of P&L below the 5th
percentile.

Verified on a realistic 8-position book:
- VaR 95% = $263.30
- **CVaR 95% = $316.82**
- Worst case = $403.30
- CVaR/VaR ratio = **1.20** (Gaussian would be ~1.10-1.15; the 1.20 is the fat-tail premium from the bimodality)

The copula step is what makes this **actually about portfolios**, not just
single names — two correlated positions tail-cluster, and the CVaR
appropriately punishes that. The directionality is verified in
[`tests/test_cvar.py:test_correlated_positions_have_higher_cvar`](../tests/test_cvar.py).

The same simulator powers a **projected-loss kill-switch** in
[`server/risk_engine.py:check_risk_limits()`](../server/risk_engine.py#L77),
which trips the brake when the open book's projected CVaR exceeds 8% of
bankroll — *before* the loss is realized, not after.

### Decision #3: Calibrator trained on historical settlement tape, not paper trades

**The naive approach**: paper-trade the system for 4-8 weeks, accumulate ~200
closed positions, fit a calibrator on those.

**Why that's slow**: at 5-15 closed paper trades per day, getting to a
statistically meaningful sample takes ~3-8 weeks of pure waiting. During that
time, every position-sizing decision uses the conservative fallback
`win_prob = 0.5 + confidence × 0.15`, which is correct in expectation but
wastes any edge the model actually has.

**What I built**: instead of waiting for paper trades,
[`scripts/backfill_calibration.py`](../scripts/backfill_calibration.py) pulls
**Kalshi's own settled-market history** from the public REST API. Every settled
market has both a final outcome (`result` ∈ {yes, no, all_no}) and a
last-quoted price (`previous_yes_bid_dollars` / `previous_yes_ask_dollars`).
That's exactly the (predicted_probability, outcome) pair the calibrator needs.

**The unblock**: 749 training samples in 12 seconds of HTTP calls instead of
6 weeks of paper trading. The calibrator's `is_fitted` flag flipped to True,
the fallback was bypassed, and the live `RiskModel` started making different
decisions within the same hour.

**The methodology bug I almost shipped**: my first version of the script
computed the calibrator's training labels as "did the market predict the right
side" — i.e., a market trading at 0.51 that settles YES counts as a "win." On
the first run this gave a 98% win rate, which is degenerate (markets near 1
settle near 1). I caught it before persisting and rewrote to use the
*quote itself* as the predicted probability and the binary outcome as the
label, which is the actual definition of a calibration curve. The Brier score
fell from a meaningless ~0 to a defensible 0.046.

This kind of mistake is why bootstrap CIs and class-balance reporting are
important: a single number ("Brier 0.046") looks the same whether the
methodology is correct or broken. The full report in the experiment tracker
includes the class balance, which would have flagged the bug if I'd looked.

---

## What makes this not a toy

A reviewer who clones this repo can verify all of the following in under
five minutes:

1. **160 passing tests** covering risk math (Kelly, CVaR, fees), microstructure
   (VPIN, Kyle's λ), pricing models (Heston, lognormal, isotonic calibration),
   simulation (triple-barrier, copula sampling), and integration
   (the full decision pipeline). Run: `python -m pytest tests/ -q`.
2. **A static look-ahead bias scanner** ([`scripts/scan_lookahead.py`](../scripts/scan_lookahead.py))
   that found and fixed a real bug in `models/regime_detector.py:60` — the
   volume_ratio feature was using a rolling window that included the current
   bar in its own baseline. This bug had been there since the initial commit
   and only surfaced when the scanner ran. The fix is in the same commit.
3. **A latency monitor** wired into the live signal loop with a `/api/latency`
   endpoint exposing per-stage p50/p95/max — this is what produced the
   parlay-pricer finding above.
4. **Runtime `@no_lookahead` decorator** ([`analysis/no_lookahead.py`](../analysis/no_lookahead.py))
   that wraps any feature function taking an `as_of` argument and asserts at
   runtime that no future-dated rows leak into the input or the output. Strict
   mode raises; observe-only logs.
5. **Heston (1993) stochastic-volatility digital option pricer** in
   [`models/heston.py`](../models/heston.py), implemented via the Albrecher-trap
   characteristic function and Gil-Pelaez Fourier inversion (because the
   classical Heston form is numerically unstable for large T). Routed for
   contracts with <48h to expiry where stochastic vol matters; lognormal for
   longer horizons.
6. **A Black-Litterman portfolio optimizer** ([`models/black_litterman.py`](../models/black_litterman.py))
   adapted for binary contracts (prior π = 0 since contracts are at fair value
   by construction; cross-position correlation taken from the same matrix used
   by VaR). Verified that correlated positions get LESS combined leverage and
   negatively-correlated hedges get MORE. This is the optimizer the
   integrated decision pipeline uses for sizing.

---

## What I deliberately did NOT do

These are real choices I made, not gaps in execution:

- **Live trading wiring**. The system has never made money on real markets it
  would actually trade. Wiring up live execution before validating the model
  on paper is reckless. The execution engine has a `paper` / `live` mode
  switch but I haven't flipped it and won't until paper P&L is meaningfully
  positive on a non-trivial sample.
- **Heston parameter calibration to actual contract prices**. The defaults
  in [`models/heston.py`](../models/heston.py) (BTC: σᵥ=0.8, ρ=−0.4; SPX:
  σᵥ=0.4, ρ=−0.7) are from the literature, not fitted to Kalshi prices.
  Calibrating them properly requires implied-vol surfaces we don't have;
  approximating from VIX (for SPX) and CoinGecko 30-day vol (for BTC) is
  the v1 approximation.
- **Polymarket order placement**. The [`data/polymarket.py`](../data/polymarket.py)
  adapter is read-only. Placing orders on Polymarket requires Polygon wallet
  integration + USDC custody on a second venue + cross-settlement risk
  modelling, none of which is in scope here. Cross-venue arbitrage on the
  read side is real and tested; on the write side it would require
  meaningful additional infrastructure.
- **MLflow as a hard dependency**. The experiment tracker in
  [`analysis/experiment_tracker.py`](../analysis/experiment_tracker.py) uses
  MLflow if installed and falls back to JSONL if not. The hackathon-friendly
  default is the JSONL path so the project can be re-run by anyone in any
  environment.
- **The class imbalance in the calibration training set**. 743 samples
  with 6.5% YES rate is a real imbalance — the recent settled tape is
  dominated by MVE legs which mostly settle NO. The right fix is stratified
  sampling across categories, which I didn't do because it would have
  required separately backfilling each category.
- **Tick-by-tick backtest replay against historical orderbook tape**. The
  [`scripts/replay_settled.py`](../scripts/replay_settled.py) script does
  *terminal-state* replay (one trade per market, mark-to-settlement). True
  intra-day backtest needs us to record orderbook tape ourselves over weeks
  — that's the next data-collection effort once paper has accumulated some
  positions.

---

## How to read this repo (in order of decreasing leverage)

1. **This document.** You're already here.
2. **[`docs/figures/calibration_curve.png`](figures/calibration_curve.png)** —
   the headline visual.
3. **[`docs/NEGATIVE_RESULTS.md`](NEGATIVE_RESULTS.md)** — the failed strategies
   and what they falsify.
4. **[`docs/BIBLIOGRAPHY.md`](BIBLIOGRAPHY.md)** — the literature each piece
   is built on.
5. **[`notebooks/01_calibration_analysis.ipynb`](../notebooks/01_calibration_analysis.ipynb)** —
   the full calibration analysis with prose between cells.
6. **[`engine/integrated_decision.py`](../engine/integrated_decision.py)** —
   the single file that ties every gate together. Read this and you understand
   the project's thesis.
7. **[`models/risk_model.py`](../models/risk_model.py)** — fee model, sizing,
   CVaR copula, calibrator. The most quant-dense file.
8. **[`engine/triple_barrier.py`](../engine/triple_barrier.py)** + **[`models/heston.py`](../models/heston.py)** —
   the textbook techniques re-derived for binary contracts.
9. **[`tests/`](../tests/)** — 160 tests, organized by module. The shape of
   the test suite tells you what I considered worth verifying.
10. **The rest of the engine/server code.** This is where the integration
    plumbing lives. Less interesting per line but necessary to make the
    whole thing run.

---

## Status

This is **working software**, not slideware:

- The server in [`server/main.py`](../server/main.py) is currently running
  against the live Kalshi WebSocket, tracking 282 markets, generating LIVE-
  source signals on a 5-minute cycle, with the calibrator I just fitted now
  in the loop affecting position-sizing decisions in real time.
- The full test suite passes (`pytest tests/ -q` → **160 passed**).
- The look-ahead bias scanner is clean (`python -m scripts.scan_lookahead`).
- The data quality report is clean except for "0 closed positions yet"
  (`python -m scripts.data_quality_report`).
- All numbers in this writeup are reproducible from the snapshot in
  [`data/calibration_training_data.json`](../data/calibration_training_data.json).

If a number in this document doesn't match your run, it's because you've
pulled a fresher Kalshi snapshot. The methodology should give qualitatively
similar results on any reasonable window.

---

## What I'd build next

In the order I'd actually do them:

1. **Tick-by-tick orderbook replay** once we've recorded enough tape. The
   terminal-state replay is informative but doesn't capture intra-day path
   risk, partial fills, or regime changes.
2. **Per-category calibration curves**. The current curve is averaged over
   sports, politics, crypto, etc. The bias might be much larger in one
   category and zero in another.
3. **Meta-labeling** (López de Prado AFML ch.3) on the accumulated paper
   trade history once it's big enough. Would let us learn "when *not* to
   trust" the primary signal.
4. **Optimize the parlay pricer** (the latency-monitor finding). It's 97% of
   the signal cycle right now. Caching the leg lookups per cycle is the
   obvious first cut.
5. **Wire the BL optimizer into the live entry path**, not just the
   integrated-decision endpoint. Currently the live execution engine still
   uses single-name Kelly sizing.

None of these are blocked on insight — they're all blocked on either time or
on accumulating more paper-trade data.
