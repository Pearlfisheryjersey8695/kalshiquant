"""
Step 2.3 -- Short-Term Price Predictor
XGBoost classifier: predict price direction over next 1h.
Target: +1 (up > 2%), -1 (down > 2%), 0 (flat).
Walk-forward cross-validation.
"""

import numpy as np
import pandas as pd
import pickle, os, json
from models.base import BaseModel, registry
from models.features import prepare_ml_data, get_feature_matrix


class PricePredictor(BaseModel):
    name = "price_predictor"

    def __init__(self):
        self.model = None
        self.regressor = None        # Fix 5e: regression on cents change
        self.calibrated_model = None # Fix 5d: Platt-scaled classifier
        self.feature_names = []
        self.metrics = {}
        self._fitted = False

    def fit(self, data: pd.DataFrame):
        """Train XGBoost on prepared feature data with walk-forward CV.
        Filters to non-STALE regimes only — STALE markets teach the model
        to predict flat, which destroys its ability to detect real moves.
        """
        try:
            from xgboost import XGBClassifier
        except ImportError:
            from sklearn.ensemble import GradientBoostingClassifier as XGBClassifier

        # Filter out STALE/CONVERGENCE markets for training — they're either
        # flat or deterministic, teaching the model to predict 0 (no move)
        if "volatility_1h" in data.columns:
            mean_vol = data.groupby("ticker")["volatility_1h"].mean()
            active_tickers = mean_vol[mean_vol > mean_vol.quantile(0.25)].index
            data_filtered = data[data["ticker"].isin(active_tickers)]
            if len(data_filtered) > 200:
                print(f"  PricePredictor: Training on {len(active_tickers)}/{data['ticker'].nunique()} "
                      f"active tickers ({len(data_filtered)}/{len(data)} rows)")
                data = data_filtered

        ml_data = prepare_ml_data(data)
        # Sort globally by timestamp so the train/val/test split is temporal,
        # not cross-ticker. Without this, data is concatenated ticker-by-ticker
        # and the split would assign entire tickers to train vs test.
        ml_data = ml_data.sort_index()
        X, y, feature_names = get_feature_matrix(ml_data)
        self.feature_names = feature_names

        if len(X) < 50 or y is None:
            print(f"  PricePredictor: insufficient data ({len(X)} rows)")
            return

        # Drop rows where target is NaN
        valid = y.notna()
        X, y = X[valid], y[valid].astype(int)
        # XGBoost needs 0-indexed classes: map -1->0, 0->1, 1->2
        self._label_map = {-1: 0, 0: 1, 1: 2}
        self._label_unmap = {0: -1, 1: 0, 2: 1}
        y = y.map(self._label_map)

        if len(X) < 50:
            print(f"  PricePredictor: insufficient valid rows ({len(X)})")
            return

        # True walk-forward split: earliest 70% -> train, next 15% -> val, latest 15% -> test
        n = len(X)
        train_end = int(n * 0.70)
        val_end = int(n * 0.85)

        X_train, y_train = X.iloc[:train_end], y.iloc[:train_end]
        X_val, y_val = X.iloc[train_end:val_end], y.iloc[train_end:val_end]
        X_test, y_test = X.iloc[val_end:], y.iloc[val_end:]

        # Train
        try:
            self.model = XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                use_label_encoder=False,
                eval_metric="mlogloss",
                verbosity=0,
            )
            self.model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )
        except TypeError:
            # Fallback for sklearn GradientBoosting
            self.model = XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
            )
            self.model.fit(X_train, y_train)

        # Evaluate
        from sklearn.metrics import accuracy_score, classification_report

        train_acc = accuracy_score(y_train, self.model.predict(X_train))
        test_acc = accuracy_score(y_test, self.model.predict(X_test))
        test_pred = self.model.predict(X_test)

        self.metrics = {
            "train_accuracy": round(train_acc, 4),
            "test_accuracy": round(test_acc, 4),
            "train_size": len(X_train),
            "test_size": len(X_test),
            "class_distribution": dict(y.value_counts()),
            "feature_count": len(self.feature_names),
        }

        self._fitted = True
        print(f"  PricePredictor: train_acc={train_acc:.3f} test_acc={test_acc:.3f} "
              f"({len(X_train)} train, {len(X_test)} test)")

        # ── Fix 5d: Platt scaling on validation set ───────────────────
        try:
            from sklearn.calibration import CalibratedClassifierCV
            self.calibrated_model = CalibratedClassifierCV(
                self.model, cv="prefit", method="sigmoid"
            )
            self.calibrated_model.fit(X_val, y_val)
            print("  PricePredictor: Platt scaling applied")
        except Exception as e:
            print(f"  PricePredictor: Platt scaling failed: {e}")
            self.calibrated_model = None

        # ── Fix 5e: Train regressor on actual price change ────────────
        try:
            ml_data_full = prepare_ml_data(data)
            ml_data_full = ml_data_full.sort_index()
            if "future_return" in ml_data_full.columns:
                # Reset index to match get_feature_matrix output (integer index)
                ml_data_reset_reg = ml_data_full.reset_index(drop=False)
                X_reg, _, reg_features = get_feature_matrix(ml_data_full)
                y_reg = ml_data_reset_reg.loc[X_reg.index, "future_return"]
                valid_reg = y_reg.notna()
                X_reg, y_reg = X_reg[valid_reg], y_reg[valid_reg]

                n_reg = len(X_reg)
                reg_train_end = int(n_reg * 0.70)

                if n_reg > 100:
                    try:
                        from xgboost import XGBRegressor
                    except ImportError:
                        from sklearn.ensemble import GradientBoostingRegressor as XGBRegressor

                    self.regressor = XGBRegressor(
                        n_estimators=200, max_depth=4,
                        learning_rate=0.05, subsample=0.8,
                        colsample_bytree=0.8, verbosity=0,
                    )
                    try:
                        self.regressor.fit(
                            X_reg.iloc[:reg_train_end], y_reg.iloc[:reg_train_end],
                            eval_set=[(X_reg.iloc[reg_train_end:], y_reg.iloc[reg_train_end:])],
                            verbose=False,
                        )
                    except TypeError:
                        self.regressor.fit(
                            X_reg.iloc[:reg_train_end], y_reg.iloc[:reg_train_end],
                        )

                    from sklearn.metrics import mean_absolute_error
                    test_pred = self.regressor.predict(X_reg.iloc[reg_train_end:])
                    reg_mae = mean_absolute_error(y_reg.iloc[reg_train_end:], test_pred)
                    print(f"  PricePredictor (regression): MAE={reg_mae:.4f} cents")
                    self.metrics["regression_mae"] = round(float(reg_mae), 4)
        except Exception as e:
            print(f"  PricePredictor regression failed: {e}")
            self.regressor = None

    def predict(self, data: pd.DataFrame) -> pd.DataFrame:
        """Predict direction for each row. Returns DataFrame with predictions."""
        if not self._fitted:
            # Return neutral predictions
            return pd.DataFrame({
                "ticker": data["ticker"] if "ticker" in data.columns else "",
                "predicted_direction": 0,
                "confidence": 0.0,
            }, index=data.index)

        ml_data = prepare_ml_data(data, add_targets=False)
        ml_data_reset = ml_data.reset_index(drop=False)
        X, _, _ = get_feature_matrix(ml_data)

        # Align features
        missing = [c for c in self.feature_names if c not in X.columns]
        for c in missing:
            X[c] = 0
        X = X[self.feature_names]
        X = X.dropna()

        if len(X) == 0:
            return pd.DataFrame()

        raw_preds = self.model.predict(X)
        # Unmap back: 0->-1, 1->0, 2->1
        preds = np.array([self._label_unmap.get(int(p), 0) for p in raw_preds])

        # Use Platt-scaled probabilities if available (Fix 5d)
        if self.calibrated_model is not None:
            try:
                proba = self.calibrated_model.predict_proba(X)
                confidence = np.max(proba, axis=1)
            except Exception:
                proba = self.model.predict_proba(X) if hasattr(self.model, "predict_proba") else None
                confidence = np.max(proba, axis=1) if proba is not None else np.ones(len(preds)) * 0.5
        elif hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(X)
            confidence = np.max(proba, axis=1)
        else:
            confidence = np.ones(len(preds)) * 0.5

        # Regression: predicted price change in cents (Fix 5e)
        predicted_change = np.zeros(len(preds))
        if self.regressor is not None:
            try:
                predicted_change = self.regressor.predict(X)
            except Exception:
                pass

        result = pd.DataFrame({
            "ticker": ml_data_reset.loc[X.index, "ticker"].values,
            "predicted_direction": preds,
            "confidence": np.round(confidence, 4),
            "predicted_change": np.round(predicted_change, 4),
        }, index=X.index)

        return result

    def save(self, directory="models/saved"):
        os.makedirs(directory, exist_ok=True)
        if self.model is not None:
            with open(os.path.join(directory, "price_predictor.pkl"), "wb") as f:
                pickle.dump(self.model, f)
        with open(os.path.join(directory, "price_predictor_meta.json"), "w") as f:
            json.dump({
                "metrics": self.metrics,
                "features": self.feature_names,
            }, f, indent=2, default=str)
        return directory


registry.register(PricePredictor())
