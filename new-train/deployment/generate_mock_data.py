from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


RANDOM_STATE = 42
ROWS_PER_CLASS = 25
BASE_DIR = Path(__file__).resolve().parents[1]
DEPLOYMENT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = DEPLOYMENT_DIR / "mock_data.csv"


def generate() -> pd.DataFrame:
    rng = np.random.default_rng(RANDOM_STATE)
    source = pd.read_csv(BASE_DIR / "new-water-data-cleaned.csv")
    artifact = joblib.load(DEPLOYMENT_DIR / "water_quality_rf_5_features.joblib")
    feature_columns = artifact["feature_columns"]

    sampled = (
        source.groupby("class", group_keys=False)
        .sample(n=ROWS_PER_CLASS, random_state=RANDOM_STATE)
        .reset_index(drop=True)
    )
    simulated = sampled[feature_columns].copy()
    simulated["ec_uScm"] *= rng.normal(1.0, 0.01, len(simulated))
    simulated["tds_mgL"] *= rng.normal(1.0, 0.01, len(simulated))
    simulated["temp_C"] += rng.normal(0.0, 0.10, len(simulated))
    simulated["pH"] += rng.normal(0.0, 0.02, len(simulated))
    simulated["do_mgL"] += rng.normal(0.0, 0.05, len(simulated))

    simulated["ec_uScm"] = simulated["ec_uScm"].clip(lower=0).round(2)
    simulated["tds_mgL"] = simulated["tds_mgL"].clip(0, 65535).round().astype(int)
    simulated["temp_C"] = simulated["temp_C"].round(2)
    simulated["pH"] = simulated["pH"].clip(0, 14).round(2)
    simulated["do_mgL"] = simulated["do_mgL"].clip(lower=0).round(2)

    encoded_levels = artifact["model"].predict(simulated[feature_columns])
    probabilities = artifact["model"].predict_proba(simulated[feature_columns])
    start_time = datetime(2026, 9, 7, 9, 0, 0)

    result = simulated.copy()
    result.insert(0, "sample_id", np.arange(1, len(result) + 1))
    result.insert(
        1,
        "computer_time",
        [(start_time + timedelta(seconds=index)).isoformat() for index in range(len(result))],
    )
    result["predicted_level"] = artifact["label_encoder"].inverse_transform(
        encoded_levels
    )
    result["confidence"] = probabilities.max(axis=1).round(6)
    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    return result


if __name__ == "__main__":
    data = generate()
    print(f"Generated {len(data)} rows: {OUTPUT_PATH}")
    print(data["predicted_level"].value_counts().to_string())