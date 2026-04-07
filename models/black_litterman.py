"""Black-Litterman portfolio optimizer adapted for binary contracts.

Why Black-Litterman
-------------------
The current sizing logic is "per-position Kelly with a 6% cap and a heat
limit." That's a *single-name* sizing rule with a hand-coded portfolio
constraint bolted on. It has two well-known failure modes:

  1. **No view-weighting.** Two signals at edge=0.05 are sized the same
     even if one comes from a model we trust 90% and one from 30%.
  2. **No correlation accounting at sizing time.** We have a corr matrix
     for the post-hoc VaR calc, but we DON'T use it when deciding how
     much to put on each position. So three highly-correlated positions
     all get full Kelly and the book ends up concentrated.

Black-Litterman (Goldman 1990; Idzorek 2002) solves both: it takes a market
prior (here: the Kalshi-implied probabilities), a set of model "views" with
explicit confidence, and produces an optimal weight vector that respects the
covariance structure.

Adaptation for binaries
-----------------------
The classical BL formula:

    posterior_returns = [(τΣ)⁻¹ + Pᵀ Ω⁻¹ P]⁻¹ [(τΣ)⁻¹ π + Pᵀ Ω⁻¹ Q]

For binary contracts in [0,1]:
  - π (prior expected return) = 0 if priced at fair value (which the market is, by definition).
  - "Returns" become "expected P&L per dollar invested" = (model_prob - market_prob).
  - Ω (view confidence) = diagonal matrix from model confidence scores.
  - Σ (covariance) = computed from the cross-market price correlation we
    already track for VaR.

The optimization step then maximizes a quadratic utility:

    w* = (λ Σ_post)⁻¹ μ_post

with a leverage constraint Σ|w| ≤ L (default L = 0.6, matches MAX_TOTAL_PCT).

This is a meaningfully better sizing than per-position Kelly because:
  - Two correlated positions get LESS combined weight than two independent ones
  - Low-confidence views get shrunk toward the prior
  - Negative-correlation hedges get bonus weight

What we DON'T do
----------------
- Reverse-optimization for the prior. With binaries the prior is just zero.
- Tau calibration. We use τ=0.05, the canonical value from Idzorek.
- Inequality constraints other than total leverage. Per-name caps still
  come from RiskModel.MAX_SINGLE_PCT.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class BLView:
    """One model view: 'I think contract i has expected P&L `q` with confidence `c`'."""
    ticker: str
    expected_pnl: float    # in units of dollars per dollar invested (e.g. 0.04 = 4c edge)
    confidence: float      # in [0, 1]; higher = stronger view


@dataclass
class BLResult:
    weights: dict[str, float]      # ticker -> portfolio weight (signed)
    posterior_mu: dict[str, float] # ticker -> posterior expected return
    leverage: float                # Σ|w|
    n_views: int


class BlackLittermanOptimizer:
    """Pure-numpy BL optimizer with explicit leverage constraint.

    Default parameters
    ------------------
    tau         : 0.05  (Idzorek, "scalar uncertainty in the prior")
    risk_aversion: 2.5  (typical for institutional sizing)
    max_leverage: 0.60  (matches RiskModel.MAX_TOTAL_PCT)
    """

    DEFAULT_TAU = 0.05
    DEFAULT_RISK_AVERSION = 2.5
    DEFAULT_MAX_LEVERAGE = 0.60

    def __init__(
        self,
        tau: float = DEFAULT_TAU,
        risk_aversion: float = DEFAULT_RISK_AVERSION,
        max_leverage: float = DEFAULT_MAX_LEVERAGE,
    ):
        self.tau = tau
        self.risk_aversion = risk_aversion
        self.max_leverage = max_leverage

    def optimize(
        self,
        views: list[BLView],
        cov_matrix: np.ndarray | None = None,
    ) -> BLResult:
        """Run BL optimization on a list of views.

        Parameters
        ----------
        views : list[BLView]
            One per active signal. Order is preserved.
        cov_matrix : np.ndarray | None
            n×n PSD matrix in the same order as `views`. If None, we use
            an identity matrix scaled by 0.04² (the typical 4c sigma of a
            mid-priced binary), i.e. assume independence.

        Returns
        -------
        BLResult with weights summing in absolute value to <= max_leverage.
        """
        n = len(views)
        if n == 0:
            return BLResult(weights={}, posterior_mu={}, leverage=0.0, n_views=0)

        tickers = [v.ticker for v in views]
        Q = np.array([v.expected_pnl for v in views], dtype=float)
        # Confidence -> view variance (Ω). Higher confidence -> smaller variance.
        # Map [0,1] confidence to variance via: omega_i = (1 - c)² * tau * sigma²
        # so a confidence of 1 produces an effectively-certain view (zero variance).
        confidences = np.clip(np.array([v.confidence for v in views]), 0.05, 0.99)
        omega_diag = (1.0 - confidences) ** 2

        # Default Σ: diagonal with 4c sigma per name
        if cov_matrix is None:
            sigma = 0.04
            cov_matrix = np.eye(n) * (sigma ** 2)
        else:
            cov_matrix = np.asarray(cov_matrix, dtype=float)
            if cov_matrix.shape != (n, n):
                raise ValueError(
                    f"cov_matrix must be {n}x{n} to match the number of views"
                )

        # Add a small diagonal jitter for invertibility
        cov_matrix = cov_matrix + 1e-8 * np.eye(n)

        # Identity P (each view targets exactly one asset)
        P = np.eye(n)

        # ── Posterior mean ─────────────────────────────────────────
        # mu_post = ((τΣ)⁻¹ + Pᵀ Ω⁻¹ P)⁻¹ ((τΣ)⁻¹ π + Pᵀ Ω⁻¹ Q)
        # With π = 0 (binary fair-value prior is zero excess return):
        tau_sigma_inv = np.linalg.inv(self.tau * cov_matrix)
        omega_inv = np.diag(1.0 / np.maximum(omega_diag * self.tau, 1e-8))
        precision = tau_sigma_inv + P.T @ omega_inv @ P
        posterior_mu = np.linalg.solve(precision, P.T @ omega_inv @ Q)

        # Posterior covariance
        sigma_post = cov_matrix + np.linalg.inv(precision)

        # ── Optimal weights (mean-variance) ────────────────────────
        # w* = (λ Σ_post)⁻¹ μ_post
        try:
            raw_weights = np.linalg.solve(self.risk_aversion * sigma_post, posterior_mu)
        except np.linalg.LinAlgError:
            raw_weights = np.zeros(n)

        # ── Apply leverage constraint ──────────────────────────────
        gross = np.sum(np.abs(raw_weights))
        if gross > self.max_leverage and gross > 0:
            raw_weights = raw_weights * (self.max_leverage / gross)

        weights = {tickers[i]: float(raw_weights[i]) for i in range(n)}
        mu_dict = {tickers[i]: float(posterior_mu[i]) for i in range(n)}

        return BLResult(
            weights=weights,
            posterior_mu=mu_dict,
            leverage=float(np.sum(np.abs(raw_weights))),
            n_views=n,
        )

    def views_from_signals(self, signals: list[dict]) -> list[BLView]:
        """Convert ensemble signals into BL views.

        Each signal dict is expected to have at least:
          - ticker
          - net_edge (post-fee edge in price units)
          - confidence
          - direction ("BUY_YES" or "BUY_NO")
        """
        views = []
        for s in signals:
            edge = s.get("net_edge", s.get("edge", 0))
            # Sign based on direction so the optimizer knows long vs short
            if s.get("direction") == "BUY_NO":
                edge = -edge
            views.append(BLView(
                ticker=s["ticker"],
                expected_pnl=float(edge),
                confidence=float(s.get("confidence", 0.5)),
            ))
        return views
