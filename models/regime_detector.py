"""
Step 2.4 -- Regime Detection (HMM + rule-based fallback)
Classifies each market into one of 5 regimes:
  1. TRENDING       - momentum strategy
  2. MEAN_REVERTING - contrarian strategy
  3. HIGH_VOLATILITY - reduce size, widen stops
  4. CONVERGENCE    - time decay strategy (near expiry)
  5. STALE          - DO NOT TRADE

Primary: GaussianHMM (5-state, 4D observations) per ticker
Fallback: rule-based thresholds when HMM can't fit (< MIN_OBS rows or convergence failure)
"""

import logging

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from models.base import BaseModel, registry

logger = logging.getLogger("kalshi.regime")

REGIME_NAMES = ["TRENDING", "MEAN_REVERTING", "HIGH_VOLATILITY", "CONVERGENCE", "STALE"]


class RegimeDetector(BaseModel):
    name = "regime_detector"

    N_STATES = 5
    MIN_OBS = 50  # need at least 50 data points for HMM

    # Rule-based fallback thresholds
    CONVERGENCE_HOURS = 48
    STALE_MULT = 0.15
    STALE_VOL_FLOOR = 0.001
    HIGH_VOL_MULT = 2.5
    TREND_MOMENTUM_MULT = 1.5

    def __init__(self):
        self._hmm_models: dict[str, GaussianHMM] = {}
        self._hmm_fitted: set[str] = set()
        self._state_mappings: dict[str, dict[int, str]] = {}  # ticker -> {state_idx: regime_name}
        self._fallback_stats: dict[str, dict] = {}

    # ── Observation matrix ────────────────────────────────────────────────

    def _build_observations(self, grp: pd.DataFrame) -> np.ndarray:
        """Build 4D observation matrix: [log_return, realized_vol, volume_ratio, spread]."""
        close = grp["close"].values
        vol = grp["volatility_1h"].values if "volatility_1h" in grp.columns else np.zeros(len(grp))
        volume = grp["volume_1h"].values if "volume_1h" in grp.columns else np.ones(len(grp))

        # 1. Log returns
        log_returns = np.diff(np.log(np.clip(close, 0.001, 0.999)), prepend=0)

        # 2. Realized volatility
        realized_vol = np.clip(vol, 0, 1)

        # 3. Volume ratio (current / rolling mean of PRIOR bars only)
        # .shift(1) prevents the current bar from contributing to its own baseline.
        vol_mean = pd.Series(volume).rolling(12, min_periods=1).mean().shift(1).fillna(0).values
        # Avoid divide-by-zero noise on the first bar (where shift(1) leaves a zero):
        # use np.divide with where= to skip degenerate denominators entirely.
        volume_ratio = np.divide(
            volume, vol_mean,
            out=np.ones_like(volume, dtype=float),
            where=vol_mean > 0,
        )
        volume_ratio = np.clip(volume_ratio, 0, 10)

        # 4. Spread: abs(zscore) as distance-from-mean proxy
        zscore = grp["zscore"].values if "zscore" in grp.columns else np.zeros(len(grp))
        spread_pct = np.clip(np.abs(zscore), 0, 5)

        obs = np.column_stack([log_returns, realized_vol, volume_ratio, spread_pct])
        return np.nan_to_num(obs, nan=0.0, posinf=5.0, neginf=-5.0)

    # ── HMM state labeling ────────────────────────────────────────────────

    def _label_hmm_states(self, hmm: GaussianHMM) -> dict[int, str]:
        """Map HMM state indices to regime names based on emission means.
        means columns: [log_return, vol, volume_ratio, spread]
        """
        means = hmm.means_  # (n_states, 4)
        n = hmm.n_components
        used: set[int] = set()
        mapping: dict[int, str] = {}

        # HIGH_VOLATILITY: highest realized vol (col 1)
        vol_order = np.argsort(means[:, 1])
        hv_idx = int(vol_order[-1])
        mapping[hv_idx] = "HIGH_VOLATILITY"
        used.add(hv_idx)

        # STALE: lowest realized vol
        for idx in vol_order:
            idx = int(idx)
            if idx not in used:
                mapping[idx] = "STALE"
                used.add(idx)
                break

        # TRENDING: highest absolute log_return mean
        remaining = [i for i in range(n) if i not in used]
        if remaining:
            trend_idx = max(remaining, key=lambda i: abs(means[i, 0]))
            mapping[trend_idx] = "TRENDING"
            used.add(trend_idx)

        # CONVERGENCE: highest spread among remaining
        remaining = [i for i in range(n) if i not in used]
        if remaining:
            conv_idx = max(remaining, key=lambda i: means[i, 3])
            mapping[conv_idx] = "CONVERGENCE"
            used.add(conv_idx)

        # Everything else -> MEAN_REVERTING
        for i in range(n):
            if i not in used:
                mapping[i] = "MEAN_REVERTING"

        return mapping

    # ── Fit ────────────────────────────────────────────────────────────────

    def fit(self, data: pd.DataFrame):
        """Fit per-ticker HMM models. Rule-based fallback for sparse tickers."""
        for ticker, grp in data.groupby("ticker"):
            # Always compute fallback stats
            vol_series = grp["volatility_1h"] if "volatility_1h" in grp.columns else pd.Series([0])
            mom_series = grp["momentum_1h"].abs() if "momentum_1h" in grp.columns else pd.Series([0])
            self._fallback_stats[ticker] = {
                "mean_vol": vol_series.mean(),
                "mean_momentum": mom_series.mean(),
            }

            if len(grp) < self.MIN_OBS:
                continue

            obs = self._build_observations(grp)
            try:
                hmm = GaussianHMM(
                    n_components=self.N_STATES,
                    covariance_type="diag",
                    n_iter=100,
                    random_state=42,
                    init_params="stmc",
                )
                hmm.fit(obs)
                self._hmm_models[ticker] = hmm
                self._hmm_fitted.add(ticker)
                self._state_mappings[ticker] = self._label_hmm_states(hmm)
            except Exception as e:
                logger.warning("HMM fit failed for %s: %s", ticker, e)

        logger.info(
            "HMM fitted for %d/%d tickers",
            len(self._hmm_fitted),
            len(data["ticker"].unique()),
        )

    # ── Rule-based fallback ───────────────────────────────────────────────

    def _classify_row_fallback(self, row) -> str:
        """Original rule-based classification."""
        vol = abs(row.get("volatility_1h", 0))
        momentum = abs(row.get("momentum_1h", 0))
        hours = row.get("time_to_expiry_hours", 1000)
        price = row.get("close", 0.5)
        ticker = row.get("ticker", "")

        stats = self._fallback_stats.get(ticker, {})
        mean_vol = stats.get("mean_vol", 0.02)
        mean_mom = stats.get("mean_momentum", 0.005)

        stale_thresh = max(mean_vol * self.STALE_MULT, self.STALE_VOL_FLOOR)
        high_vol_thresh = max(mean_vol * self.HIGH_VOL_MULT, mean_vol + 0.01)
        trend_mom_thresh = max(mean_mom * self.TREND_MOMENTUM_MULT, 0.005)

        if hours < self.CONVERGENCE_HOURS and (price > 0.8 or price < 0.2):
            return "CONVERGENCE"
        if vol < stale_thresh and momentum < stale_thresh:
            return "STALE"
        if vol > high_vol_thresh:
            return "HIGH_VOLATILITY"
        zscore = abs(row.get("zscore", 0))
        if momentum > trend_mom_thresh and zscore > 1.0:
            return "TRENDING"
        return "MEAN_REVERTING"

    def _fallback_probs(self, regime: str) -> dict[str, float]:
        """Generate probability dict for rule-based fallback (high confidence in label)."""
        probs = {r: 0.02 for r in REGIME_NAMES}
        probs[regime] = 0.92
        return probs

    # ── Predict ────────────────────────────────────────────────────────────

    def predict(self, data: pd.DataFrame) -> pd.DataFrame:
        """Assign regime + probability distribution to each row."""
        results = []
        for ticker, grp in data.groupby("ticker"):
            if ticker in self._hmm_fitted:
                try:
                    obs = self._build_observations(grp)
                    hmm = self._hmm_models[ticker]
                    mapping = self._state_mappings[ticker]

                    _, state_seq = hmm.decode(obs, algorithm="viterbi")
                    posteriors = hmm.predict_proba(obs)

                    for i, (idx, row) in enumerate(grp.iterrows()):
                        state_idx = state_seq[i]
                        regime = mapping[state_idx]

                        # Override: near-expiry always CONVERGENCE
                        hours = row.get("time_to_expiry_hours", 1000)
                        price = row.get("close", 0.5)
                        if hours < self.CONVERGENCE_HOURS and (price > 0.8 or price < 0.2):
                            regime = "CONVERGENCE"

                        # Build probability dict (aggregate duplicate regime mappings)
                        probs: dict[str, float] = {r: 0.0 for r in REGIME_NAMES}
                        for si in range(hmm.n_components):
                            regime_name = mapping[si]
                            probs[regime_name] += float(posteriors[i, si])

                        results.append({
                            "timestamp": idx,
                            "ticker": ticker,
                            "regime": regime,
                            "regime_probs": probs,
                            "hmm": True,
                        })
                    continue
                except Exception as e:
                    logger.warning("HMM predict failed for %s: %s, fallback", ticker, e)

            # Fallback: rule-based
            for idx, row in grp.iterrows():
                regime = self._classify_row_fallback(row)
                results.append({
                    "timestamp": idx,
                    "ticker": ticker,
                    "regime": regime,
                    "regime_probs": self._fallback_probs(regime),
                    "hmm": False,
                })

        return pd.DataFrame(results)

    def get_latest_regimes(self, data: pd.DataFrame) -> dict[str, str]:
        """Return {ticker: regime} for latest snapshot."""
        regimes = self.predict(data)
        latest = regimes.sort_values("timestamp").groupby("ticker").last()
        return latest["regime"].to_dict()

    def get_latest_regime_probs(self, data: pd.DataFrame) -> dict[str, dict]:
        """Return {ticker: {regime: prob}} for latest snapshot."""
        regimes = self.predict(data)
        latest = regimes.sort_values("timestamp").groupby("ticker").last()
        return latest["regime_probs"].to_dict()

    def detect_regime_changes(self, data: pd.DataFrame) -> list[dict]:
        """Detect regime changes and uncertainty for feed events."""
        regimes = self.predict(data)
        events = []
        for ticker, grp in regimes.groupby("ticker"):
            sorted_grp = grp.sort_values("timestamp")
            if len(sorted_grp) < 2:
                continue
            prev = sorted_grp.iloc[-2]
            curr = sorted_grp.iloc[-1]

            # Regime change
            if prev["regime"] != curr["regime"]:
                events.append({
                    "type": "REGIME_CHANGE",
                    "ticker": ticker,
                    "from": prev["regime"],
                    "to": curr["regime"],
                    "probs": curr["regime_probs"],
                })

            # Regime uncertainty: top prob < 50%
            probs = curr["regime_probs"]
            top_prob = max(probs.values()) if probs else 1.0
            if top_prob < 0.50:
                events.append({
                    "type": "REGIME_UNCERTAINTY",
                    "ticker": ticker,
                    "regime": curr["regime"],
                    "top_prob": round(top_prob, 3),
                    "probs": probs,
                })

        return events


registry.register(RegimeDetector())
