# Bibliography

The literature each piece of this project is built on, with one-line notes
on where each reference fits and which file in the repo it informs.

The selection bias is intentional: these are the papers I actually read while
building, not a comprehensive survey. If a paper is on this list, the project
would be different without it.

---

## Calibration & probability scoring

**Brier, G.W. (1950).** *Verification of forecasts expressed in terms of
probability.* Monthly Weather Review 78(1).
> The original Brier score paper. The metric we use to measure both market
> calibration and the model's edge against it. Used in
> [`analysis/calibration_tracker.py`](../analysis/calibration_tracker.py)
> and [`scripts/backfill_calibration.py`](../scripts/backfill_calibration.py).

**Niculescu-Mizil, A. & Caruana, R. (2005).** *Predicting good probabilities
with supervised learning.* ICML.
> The paper that established isotonic regression as the standard non-parametric
> calibration method for ML classifiers, and the reliability-diagram
> visualization we reproduced in
> [`docs/figures/calibration_curve.png`](figures/calibration_curve.png).

**Platt, J. (1999).** *Probabilistic outputs for support vector machines and
comparisons to regularized likelihood methods.*
> The other classical calibration paper (Platt scaling). We chose isotonic over
> Platt because Platt assumes a sigmoid-shaped miscalibration which the Kalshi
> data plainly doesn't satisfy (the curve is flat in the middle, see plot).

**Guo, C., Pleiss, G., Sun, Y. & Weinberger, K.Q. (2017).** *On calibration of
modern neural networks.* ICML.
> Showed that modern deep nets are systematically over-confident — same failure
> mode we observe on Kalshi. The temperature-scaling fix from this paper is
> the simplest version of what isotonic regression does non-parametrically.

---

## Prediction market efficiency & calibration

**Wolfers, J. & Zitzewitz, E. (2004).** *Prediction markets.* Journal of
Economic Perspectives 18(2).
> The classic survey establishing that prediction markets aggregate information
> efficiently in expectation. Sets the prior we test against in
> [`docs/NEGATIVE_RESULTS.md`](NEGATIVE_RESULTS.md): "are these markets
> calibrated?"

**Manski, C.F. (2006).** *Interpreting the predictions of prediction markets.*
Economics Letters 91(3).
> Important caveat to the above: prediction market prices are not in general
> the same as expected probabilities, even under risk-neutrality. Justifies
> our use of empirical settlement frequency rather than assuming the market
> price IS the probability.

**Page, L. & Clemen, R.T. (2013).** *Do prediction markets produce well-
calibrated probability forecasts?* Economic Journal 123(568).
> Gives the empirical answer for political prediction markets: yes for
> high-volume contracts, no for low-volume / extreme-price contracts. This is
> the result our calibration curve replicates for Kalshi specifically.

---

## Risk modelling

**Rockafellar, R.T. & Uryasev, S. (2000).** *Optimization of conditional
value-at-risk.* Journal of Risk 2(3).
> The CVaR (Expected Shortfall) paper that established it as a coherent risk
> measure (subadditive — diversification reduces it, unlike VaR).
> [`models/risk_model.py:portfolio_cvar()`](../models/risk_model.py#L457)
> implements this for binary contracts.

**Embrechts, P., Lindskog, F. & McNeil, A. (2003).** *Modelling dependence
with copulas and applications to risk management.* in *Handbook of Heavy
Tailed Distributions in Finance*.
> The reference for using a Gaussian copula on the latent normals to inject
> correlation into Bernoulli outcomes — exactly what
> [`portfolio_cvar()`](../models/risk_model.py#L457) does for the binary
> portfolio.

**Acerbi, C. & Tasche, D. (2002).** *On the coherence of expected shortfall.*
Journal of Banking & Finance 26(7).
> The proof that ES (= CVaR) is a coherent risk measure where VaR is not.
> Justifies why we report both but USE CVaR for the kill-switch in
> [`server/risk_engine.py`](../server/risk_engine.py).

---

## Position sizing

**Kelly, J.L. (1956).** *A new interpretation of information rate.* Bell
System Technical Journal.
> The Kelly criterion. Half-Kelly is implemented in
> [`models/risk_model.py:kelly_size()`](../models/risk_model.py#L210), with the
> hard `MAX_KELLY_CAP = 0.03` cap because full Kelly on uncertain edges is
> notorious for blowing up.

**Black, F. & Litterman, R. (1990).** *Asset allocation: combining investor
views with market equilibrium.* Goldman Sachs Fixed Income Research.
> The original Black-Litterman paper. Combines a market prior with model views
> via Bayesian updating, then mean-variance optimizes.
> [`models/black_litterman.py`](../models/black_litterman.py) implements this
> with the prior π = 0 (since binary contracts at fair value have zero excess
> return by construction).

**Idzorek, T. (2002).** *A step-by-step guide to the Black-Litterman model.*
> The reference for the τ = 0.05 default and the view-confidence → omega
> mapping. We use both directly.

---

## Microstructure

**Easley, D., López de Prado, M. & O'Hara, M. (2012).** *Flow toxicity and
liquidity in a high-frequency world.* Review of Financial Studies 25(5).
> The VPIN paper. Bulk volume classification + volume buckets → "is the flow
> toxic right now?" Implemented in
> [`analysis/order_flow.py:compute_vpin()`](../analysis/order_flow.py#L92) and
> wired as an entry-time gate in [`engine/execution_engine.py`](../engine/execution_engine.py).

**Kyle, A.S. (1985).** *Continuous auctions and insider trading.*
Econometrica 53(6).
> Kyle's λ — the OLS slope of price changes on signed volume. Measures price
> impact per unit traded. The textbook microstructure metric for "how much
> does flow move this market." Implemented in
> [`analysis/order_flow.py:compute_kyle_lambda()`](../analysis/order_flow.py#L156).

---

## Backtesting & ML for finance

**López de Prado, M. (2018).** *Advances in Financial Machine Learning.* Wiley.
> Three chapters of this book are directly used:
> - **Ch. 3 (Triple-Barrier Method)** → [`engine/triple_barrier.py`](../engine/triple_barrier.py)
> - **Ch. 7 (Cross-validation under serial correlation)** → motivates the
>   look-ahead scanner in [`scripts/scan_lookahead.py`](../scripts/scan_lookahead.py)
> - **Ch. 4 (Sample weighting)** → motivates the per-sample-weight extension
>   I'd add if the calibrator had more variance to weight by

**Bouchaud, J.P., Bonart, J., Donier, J. & Gould, M. (2018).** *Trades,
quotes and prices: financial markets under the microscope.* CUP.
> The standard reference for the price-impact / cost-of-trading literature.
> Justifies why fee-aware sizing in
> [`models/risk_model.py:position_size()`](../models/risk_model.py#L221) is a
> hard gate, not a post-hoc adjustment.

**Hull, J. (2017).** *Options, Futures, and Other Derivatives* (10th ed).
> Reference text for the Black-Scholes / lognormal probability framework that
> [`data/external_feeds.py:_lognormal_prob()`](../data/external_feeds.py) uses
> as the long-horizon model.

---

## Stochastic volatility

**Heston, S.L. (1993).** *A closed-form solution for options with stochastic
volatility with applications to bond and currency options.* Review of Financial
Studies 6(2).
> The Heston model paper. Closed-form characteristic function for the joint
> dynamics of price and variance under a CIR variance process. Implemented in
> [`models/heston.py`](../models/heston.py) for near-expiry binary digital
> pricing.

**Albrecher, H., Mayer, P., Schoutens, W. & Tistaert, J. (2007).** *The little
Heston trap.* Wilmott Magazine.
> Critical paper for any practical Heston implementation: the original
> characteristic function form is numerically unstable for large T due to
> branch-cut issues. The "trap" form (which we use) avoids this with a
> reformulation of the discriminant. Without this paper, our Heston pricer
> would silently produce wrong numbers for contracts more than a few days out.

**Gil-Pelaez, J. (1951).** *Note on the inversion theorem.* Biometrika 38.
> The Fourier inversion formula:  
> P(X > k) = 1/2 + (1/π) ∫₀^∞ Re[e^(−i u k) φ(u) / (i u)] du.  
> The single line of integration code in
> [`models/heston.py:heston_digital_prob()`](../models/heston.py#L98) IS this
> formula.

---

## Software & infrastructure references

**Beazley, D. (2009).** *Generators: the final frontier.* PyCon talk.
> The reference for the contextmanager pattern used in
> [`engine/latency_monitor.py:LatencyMonitor.stage()`](../engine/latency_monitor.py).

**FastAPI documentation.**
> The async server in [`server/main.py`](../server/main.py) is built on
> FastAPI. Synchronous model calls are dispatched to a thread executor via
> `loop.run_in_executor` so they don't block the event loop — a pattern
> that's standard but often missed.

---

## What's NOT cited (and why)

A complete bibliography of "things I might have used" is twice as long. The
list above is "things I actually opened a file because of." Notable absences:

- **Hidden Markov Models for regime detection.** [`models/regime_detector.py`](../models/regime_detector.py)
  uses `hmmlearn` but the model is simple enough that no specific paper informs
  it beyond Rabiner's 1989 tutorial.
- **XGBoost.** Cited in the obvious place (Chen & Guestrin 2016), but the way
  we use it in [`models/predictor.py`](../models/predictor.py) is vanilla
  classification — no novel methodology.
- **Reinforcement learning for the Quant Brain agent.** The tabular Q-learning
  in [`engine/quant_brain.py`](../engine/quant_brain.py) is from Sutton &
  Barto's textbook, not a specific paper. The discretization scheme is ad-hoc
  and wouldn't merit a citation.
