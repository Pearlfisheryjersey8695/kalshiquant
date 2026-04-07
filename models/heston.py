"""Heston (1993) stochastic-volatility digital option pricer.

Why this matters
----------------
For near-expiry binary contracts (< 48h to settlement), the lognormal /
Black-Scholes constant-vol assumption breaks down hard. Two reasons:

1. **Vol clustering**: realized vol over the next few hours is itself uncertain.
   Black-Scholes treats σ as known. Heston treats σ² as a CIR-process random
   variable, which fattens the tails dramatically near expiry.

2. **Smile / skew**: BS assigns the same probability to "above 110" and
   "below 90" if both are equidistant from spot. Real prediction-market
   prices show clear skew, especially around event-driven contracts. Heston
   captures this via the correlation parameter ρ between spot and vol.

For our prediction market use case, the deliverable is:
    P(S_T > K)   given (S_0, K, T, v_0, kappa, theta, sigma_v, rho)

which is the *digital* (binary) option price under Heston.

Formulation
-----------
We use the Heston (1993) characteristic function and the Gil-Pelaez inversion
formula:

    P(S_T > K) = 1/2 + (1/π) * ∫_0^∞ Re[ e^(-i*u*ln(K)) * φ(u) / (i*u) ] du

where φ is the characteristic function of ln(S_T) under the risk-neutral
measure. This is the classical "P2" probability in Heston's original paper.

Implementation
--------------
Pure Python with numpy + scipy.integrate.quad for the Fourier inversion.
About 5-10ms per evaluation, easily fast enough for our 5-minute signal cycle.
For near-expiry contracts only — for T > 7 days the lognormal model is fine
and much cheaper.

Default parameters
------------------
We use sensible BTC/SPX defaults that match historical realized characteristics:
    kappa   = 2.0    (mean reversion speed)
    theta   = v_0    (long-run vol target = current vol)
    sigma_v = 0.5    (vol of vol)
    rho     = -0.5   (negative spot-vol correlation, "leverage effect")

These are reasonable starting points; calibration to actual contract prices
would refine them but isn't necessary for v1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.integrate import quad


@dataclass(frozen=True)
class HestonParams:
    v0: float        # current variance (vol²)
    kappa: float     # mean reversion speed
    theta: float     # long-run variance target
    sigma_v: float   # vol of vol
    rho: float       # spot-vol correlation


def _characteristic_function(
    u: complex,
    s0: float,
    T: float,
    r: float,
    p: HestonParams,
) -> complex:
    """Heston characteristic function for ln(S_T) — the "trap" form
    (Albrecher et al. 2007) which is numerically stable for large T."""
    x = math.log(s0)
    a = p.kappa * p.theta

    # Discriminant
    iu = 1j * u
    d = np.sqrt(
        (p.rho * p.sigma_v * iu - p.kappa) ** 2
        + (p.sigma_v ** 2) * (iu + u ** 2)
    )
    g = (p.kappa - p.rho * p.sigma_v * iu - d) / (
        p.kappa - p.rho * p.sigma_v * iu + d
    )

    # Numerically-stable C and D coefficients
    exp_dT = np.exp(-d * T)
    C = (r * iu * T) + (a / (p.sigma_v ** 2)) * (
        (p.kappa - p.rho * p.sigma_v * iu - d) * T
        - 2.0 * np.log((1.0 - g * exp_dT) / (1.0 - g))
    )
    D = (
        (p.kappa - p.rho * p.sigma_v * iu - d) / (p.sigma_v ** 2)
    ) * ((1.0 - exp_dT) / (1.0 - g * exp_dT))

    return np.exp(C + D * p.v0 + iu * x)


def heston_digital_prob(
    s0: float,
    K: float,
    T: float,
    p: HestonParams,
    r: float = 0.0,
    direction: str = "above",
) -> float:
    """P(S_T > K) under Heston dynamics, via Gil-Pelaez inversion.

    Parameters
    ----------
    s0 : current spot
    K  : strike
    T  : time to expiry in YEARS (use hours/8760 for hourly contracts)
    p  : HestonParams
    r  : risk-free rate (default 0)
    direction : "above" → P(S_T > K), "below" → P(S_T < K)
    """
    if s0 <= 0 or K <= 0 or T <= 0:
        return 0.5

    log_K = math.log(K)

    def integrand(u: float) -> float:
        if u < 1e-12:
            return 0.0
        cf = _characteristic_function(complex(u, 0), s0, T, r, p)
        val = np.exp(-1j * u * log_K) * cf / (1j * u)
        return float(np.real(val))

    # Integrate from 0 to a large upper bound. The CF decays fast enough that
    # 200 is more than enough for our typical parameter ranges.
    try:
        integral, _ = quad(integrand, 1e-8, 200.0, limit=120)
    except Exception:
        # If quadrature fails (very near-degenerate parameters), fall back to 0.5
        return 0.5

    prob_above = 0.5 + integral / math.pi
    prob_above = max(0.001, min(0.999, prob_above))
    return prob_above if direction == "above" else 1.0 - prob_above


def default_params_for_asset(annual_vol: float, asset: str = "default") -> HestonParams:
    """Sensible defaults given a single annual-vol input.

    Different asset classes have different empirical Heston parameters; the
    defaults here are calibrated from the literature for crypto vs equity.
    """
    v0 = annual_vol ** 2  # variance from vol
    if asset == "btc":
        return HestonParams(v0=v0, kappa=3.0, theta=v0, sigma_v=0.8, rho=-0.4)
    if asset == "spx":
        return HestonParams(v0=v0, kappa=2.0, theta=v0, sigma_v=0.4, rho=-0.7)
    return HestonParams(v0=v0, kappa=2.0, theta=v0, sigma_v=0.5, rho=-0.5)


def heston_or_lognormal(
    s0: float,
    K: float,
    hours_to_expiry: float,
    annual_vol: float,
    asset: str = "default",
    direction: str = "above",
    near_expiry_threshold_hours: float = 48.0,
) -> tuple[float, str]:
    """Auto-route: use Heston near expiry, lognormal for longer horizons.

    Returns (probability, model_used).
    """
    # Long horizon → lognormal is fine and cheap
    if hours_to_expiry >= near_expiry_threshold_hours:
        from data.external_feeds import _lognormal_prob
        return _lognormal_prob(s0, K, annual_vol, hours_to_expiry, direction), "lognormal"

    # Near expiry → Heston
    T_years = hours_to_expiry / (24 * 365)
    params = default_params_for_asset(annual_vol, asset)
    return heston_digital_prob(s0, K, T_years, params, direction=direction), "heston"
