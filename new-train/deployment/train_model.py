from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder


RANDOM_STATE = 42
FEATURE_COLUMNS = ["ec_uScm", "tds_mgL", "temp_C", "pH", "do_mgL"]
BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = Path(__file__).resolve().parent / "water_quality_rf_5_features.joblib"
METRICS_PATH = Path(__file__).resolve().parent / "model_metrics.json"


def train() -> dict[str, object]:
    data = pd.read_csv(BASE_DIR / "new-water-data-cleaned.csv")
    features = data[FEATURE_COLUMNS]
    label_encoder = LabelEncoder()
    labels = label_encoder.fit_transform(data["class"])

    train_features, test_features, train_labels, test_labels = train_test_split(
        features,
        labels,
        test_size=0.40,
        random_state=RANDOM_STATE,
        stratify=labels,
    )
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=300,
                    random_state=RANDOM_STATE,
                    class_weight="balanced",
                    n_jobs=-1,
                ),
            ),
        ]
    )
    validation = StratifiedKFold(
        n_splits=5, shuffle=True, random_state=RANDOM_STATE
    )
    scores = cross_validate(
        model,
        train_features,
        train_labels,
        cv=validation,
        scoring=["accuracy", "f1_weighted", "f1_macro"],
        n_jobs=-1,
    )

    model.fit(train_features, train_labels)
    predictions = model.predict(test_features)
    metrics = {
        "features": FEATURE_COLUMNS,
        "train_rows": len(train_features),
        "test_rows": len(test_features),
        "cv_accuracy_mean": float(scores["test_accuracy"].mean()),
        "cv_accuracy_std": float(scores["test_accuracy"].std()),
        "cv_f1_weighted_mean": float(scores["test_f1_weighted"].mean()),
        "cv_f1_macro_mean": float(scores["test_f1_macro"].mean()),
        "test_accuracy": float(accuracy_score(test_labels, predictions)),
        "test_f1_weighted": float(
            f1_score(test_labels, predictions, average="weighted")
        ),
    }

    model.fit(features, labels)
    artifact = {
        "model": model,
        "label_encoder": label_encoder,
        "feature_columns": FEATURE_COLUMNS,
        "metrics": metrics,
    }
    joblib.dump(artifact, MODEL_PATH)
    METRICS_PATH.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metrics


if __name__ == "__main__":
    result = train()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Model saved to: {MODEL_PATH}")