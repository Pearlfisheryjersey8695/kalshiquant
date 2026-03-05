"""
Step 2.7 -- Meta-Model (Stacking)
Logistic regression trained on validation-set predictions from base models.
Learns WHEN to trust each model combination: if XGBoost says BUY but fair
value disagrees and regime is HIGH_VOLATILITY, the meta-model suppresses
the signal.

Target: was the trade net profitable after fees?
Now accepts regime probability vectors alongside single labels.
"""

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder
from collections import defaultdict

REGIME_NAMES = ["TRENDING", "MEAN_REVERTING", "HIGH_VOLATILITY", "CONVERGENCE", "STALE"]


class MetaModel:
    """Stacking meta-learner: learns when to trust base model combinations."""

    FEE_PER_CONTRACT_RT = 0.03

    def __init__(self):
        self.model = None
        self.regime_encoder = LabelEncoder()
        self._fitted = False
        self._regime_win_rates = {}

    def _build_features(self, xgb_dir, xgb_conf, xgb_change, fv_edge, regime,
                        regime_probs=None):
        """Build feature vector from inputs."""
        try:
            regime_val = self.regime_encoder.transform([regime])[0]
        except (ValueError, KeyError):
            regime_val = 0

        agrees = float(np.sign(xgb_dir) == np.sign(fv_edge))
        regime_wr = self._regime_win_rates.get(regime, 0.5)

        # Regime probability features (5 values)
        if regime_probs and isinstance(regime_probs, dict):
            rp = [regime_probs.get(r, 0.0) for r in REGIME_NAMES]
        else:
            # Single-label fallback: one-hot-ish
            rp = [0.9 if r == regime else 0.025 for r in REGIME_NAMES]

        regime_certainty = max(rp) if rp else 0.5

        return [
            xgb_dir, xgb_conf, xgb_change, fv_edge, abs(fv_edge),
            regime_val, agrees, regime_wr, regime_certainty,
        ] + rp

    def fit(self, xgb_directions, xgb_confidences, xgb_changes,
            fv_edges, regimes, actual_returns, regime_probs_list=None):
        """Train meta-model on validation set."""
        if len(xgb_directions) < 20:
            return

        try:
            self.regime_encoder.fit(list(set(regimes)) + REGIME_NAMES)
        except Exception:
            pass

        # Compute per-regime historical win rates
        signal_directions = np.sign(fv_edges)
        directionally_correct = np.sign(actual_returns) == signal_directions
        net_profitable = directionally_correct & (
            np.abs(actual_returns) > self.FEE_PER_CONTRACT_RT
        )

        regime_counts = defaultdict(lambda: {"total": 0, "wins": 0})
        for i, r in enumerate(regimes):
            regime_counts[r]["total"] += 1
            if net_profitable[i]:
                regime_counts[r]["wins"] += 1
        self._regime_win_rates = {
            r: stats["wins"] / stats["total"] if stats["total"] > 0 else 0.5
            for r, stats in regime_counts.items()
        }

        # Build feature matrix
        X = []
        for i in range(len(xgb_directions)):
            rp = regime_probs_list[i] if regime_probs_list else None
            features = self._build_features(
                xgb_directions[i], xgb_confidences[i], xgb_changes[i],
                fv_edges[i], regimes[i], rp,
            )
            X.append(features)
        X = np.array(X)
        y = net_profitable.astype(int)

        # Remove NaN/inf
        valid = np.isfinite(X).all(axis=1) & np.isfinite(y)
        X, y = X[valid], y[valid]

        if len(X) < 20 or len(np.unique(y)) < 2:
            return

        self.model = LogisticRegression(max_iter=1000, class_weight="balanced")
        self.model.fit(X, y)
        self._fitted = True

        acc = self.model.score(X, y)
        print(f"  MetaModel: accuracy={acc:.3f} on {len(X)} validation samples "
              f"(profitable rate: {y.mean():.1%})")

    def predict_trade_quality(self, xgb_dir, xgb_conf, xgb_change,
                               fv_edge, regime, regime_probs=None):
        """Returns probability that this trade will be net profitable."""
        if not self._fitted:
            return 0.5

        features = self._build_features(
            xgb_dir, xgb_conf, xgb_change, fv_edge, regime, regime_probs,
        )
        X = np.array([features])

        proba = self.model.predict_proba(X)[0]
        if len(proba) > 1:
            return float(proba[1])
        return float(proba[0])
