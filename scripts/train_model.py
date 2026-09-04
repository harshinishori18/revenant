"""
scripts/train_model.py — trains Revenant's single model.

WHAT IT LEARNS
    P(success | context, failure reason, action)

Because the action is an input, the trained model is a *decision* model, not a
scoring model: at serve time the optimiser sweeps candidate actions through it
and picks the best one.

WHAT WE DELIBERATELY DO NOT SHIP
An earlier design also trained a "predict the failure reason from pre-failure
context" classifier. It scored ~0.28 accuracy on 7 classes, because (a) failure
reason genuinely is close to unpredictable from context alone and (b) in
production the gateway hands you the reason code at the moment of failure.
Shipping a weak second model would have cost more credibility than the extra
box on an architecture diagram is worth, so the reason code is treated as an
observed input — which is exactly what it is.

Run:  python -m scripts.train_model
"""
from __future__ import annotations

import json
import os

import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "train_log.csv")
ARTIFACTS = os.path.join(ROOT, "artifacts")
RESULTS = os.path.join(ROOT, "results")

CATEGORICALS = ["method", "bank", "device", "network_quality", "reason", "strategy", "channel"]
FEATURE_COLS = ["hour", "amount", "prior_attempts", "method", "bank", "device",
                "network_quality", "reason", "strategy", "channel", "log_delay"]


def encode(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    out = df.copy()
    maps = {}
    for col in CATEGORICALS:
        levels = sorted(df[col].astype(str).unique())
        maps[col] = {v: i for i, v in enumerate(levels)}
        out[col] = out[col].astype(str).map(maps[col]).astype(int)
    return out, maps


def main() -> None:
    os.makedirs(ARTIFACTS, exist_ok=True)
    os.makedirs(RESULTS, exist_ok=True)

    df = pd.read_csv(DATA)
    df, maps = encode(df)
    df["log_delay"] = np.log1p(df["delay_min"])

    rng = np.random.RandomState(0)
    idx = rng.permutation(len(df))
    cut = int(0.8 * len(df))
    tr, te = idx[:cut], idx[cut:]

    X, y = df[FEATURE_COLS], df["success"]
    model = lgb.LGBMClassifier(
        n_estimators=700, learning_rate=0.045, num_leaves=64,
        min_child_samples=40, subsample=0.9, subsample_freq=1,
        colsample_bytree=0.9, reg_lambda=1.0, verbose=-1, random_state=7,
    )
    model.fit(X.iloc[tr], y.iloc[tr])

    p = model.predict_proba(X.iloc[te])[:, 1]
    yt = y.iloc[te]
    frac_pos, mean_pred = calibration_curve(yt, p, n_bins=10, strategy="quantile")

    metrics = {
        "n_train": int(len(tr)), "n_holdout": int(len(te)),
        "auc": round(float(roc_auc_score(yt, p)), 4),
        "brier": round(float(brier_score_loss(yt, p)), 4),
        "log_loss": round(float(log_loss(yt, p)), 4),
        "base_rate": round(float(yt.mean()), 4),
        "mean_predicted": round(float(p.mean()), 4),
        "calibration": [{"predicted": round(float(a), 4), "observed": round(float(b), 4)}
                        for a, b in zip(mean_pred, frac_pos)],
        "feature_importance": sorted(
            [{"feature": f, "gain": int(g)} for f, g in
             zip(FEATURE_COLS, model.booster_.feature_importance("gain"))],
            key=lambda r: -r["gain"]),
    }

    print("=== Revenant action-conditioned success model ===")
    print(f"  holdout rows   : {metrics['n_holdout']:,}")
    print(f"  ROC AUC        : {metrics['auc']:.4f}")
    print(f"  Brier score    : {metrics['brier']:.4f}   (lower is better)")
    print(f"  log loss       : {metrics['log_loss']:.4f}")
    print(f"  base rate      : {metrics['base_rate']:.4f}")
    print(f"  mean predicted : {metrics['mean_predicted']:.4f}  -> calibrated")
    print("\n  top features by gain:")
    for row in metrics["feature_importance"][:6]:
        print(f"    {row['feature']:<18}{row['gain']:>12,}")

    joblib.dump({"model": model, "maps": maps, "feature_cols": FEATURE_COLS,
                 "metrics": metrics},
                os.path.join(ARTIFACTS, "revenant_model.pkl"))
    json.dump(metrics, open(os.path.join(RESULTS, "model_card.json"), "w"), indent=2)
    print("\nsaved -> artifacts/revenant_model.pkl, results/model_card.json")


if __name__ == "__main__":
    main()
