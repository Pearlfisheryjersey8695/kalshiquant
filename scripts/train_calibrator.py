"""Train the WinProbCalibrator on closed positions + backtest history.

Run periodically (daily/weekly) so position sizing stays in sync with realised
hit rates. Output is a JSON curve at models/saved/win_prob_calibration.json.

Usage:
    python -m scripts.train_calibrator
"""

import logging
import sys

from analysis.experiment_tracker import track
from models.risk_model import WinProbCalibrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    with track("calibrator_train", tags={"trigger": "manual"}) as run:
        cal = WinProbCalibrator()
        n = cal.fit_from_history()
        run.log_metric("n_train_samples", n)
        if n == 0:
            run.set_tag("status", "skipped_too_few_samples")
            print("Not enough history yet — calibrator left in fallback mode.")
            return 1
        cal.save()
        info = cal.info()
        run.log_metric("min_calibrated", info["min_calibrated"])
        run.log_metric("max_calibrated", info["max_calibrated"])
        run.log_artifact("models/saved/win_prob_calibration.json")
        run.set_tag("status", "ok")

        print(f"Trained on {n} samples")
        print(f"  calibrated range: [{info['min_calibrated']:.3f}, {info['max_calibrated']:.3f}]")
        print(f"  saved -> models/saved/win_prob_calibration.json")
        # Spot-check the curve at a few confidence levels
        print("\nCalibration curve:")
        for c in [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
            calibrated = cal.calibrate(c)
            run.log_metric(f"curve_at_{int(c*100)}", calibrated)
            print(f"  conf={c:.2f} -> win_prob={calibrated:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
