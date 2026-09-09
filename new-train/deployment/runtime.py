from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
import sqlite3
from threading import Lock

import joblib
import pandas as pd

from protocol import WaterQualityPacket


MODEL_PATH = Path(__file__).resolve().parent / "water_quality_rf_5_features.joblib"
MAX_DISPLAY_ROWS = 500
HISTORY_COLUMNS = [
    "session_id",
    "sample_id",
    "computer_time",
    "cycle_id",
    "device_timestamp_ms",
    "ec_uScm",
    "tds_mgL",
    "temp_C",
    "pH",
    "do_mgL",
    "do_status",
    "ec_status",
    "ph_status",
    "predicted_level",
    "confidence",
]


class WaterQualityPredictor:
    def __init__(self, model_path: Path = MODEL_PATH) -> None:
        artifact = joblib.load(model_path)
        self.model = artifact["model"]
        self.label_encoder = artifact["label_encoder"]
        self.feature_columns = artifact["feature_columns"]

    def predict(self, packet: WaterQualityPacket) -> tuple[str, float]:
        values = packet.as_record()
        features = pd.DataFrame(
            [[values[column] for column in self.feature_columns]],
            columns=self.feature_columns,
        )
        encoded_level = self.model.predict(features)
        probabilities = self.model.predict_proba(features)[0]
        level = self.label_encoder.inverse_transform(encoded_level)[0]
        return str(level), float(probabilities.max())


def build_record(
    packet: WaterQualityPacket, level: str, confidence: float
) -> dict[str, int | float | str]:
    return {
        "computer_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "cycle_id": packet.cycle_id,
        "device_timestamp_ms": packet.device_timestamp_ms,
        "ec_uScm": round(packet.ec_uScm, 2),
        "tds_mgL": packet.tds_mgL,
        "temp_C": round(packet.temp_C, 2),
        "pH": round(packet.pH, 2),
        "do_mgL": round(packet.do_mgL, 2),
        "do_status": packet.do_status,
        "ec_status": packet.ec_status,
        "ph_status": packet.ph_status,
        "predicted_level": level,
        "confidence": round(confidence, 6),
    }


class SQLiteRecordStore:
    def __init__(self, file_path: Path, serial_port: str, baudrate: int) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._closed = False
        self.connection = sqlite3.connect(self.file_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        self.session_id = self._start_session(serial_port, baudrate)

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                serial_port TEXT,
                baudrate INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                sample_id INTEGER NOT NULL,
                computer_time TEXT NOT NULL,
                cycle_id INTEGER,
                device_timestamp_ms INTEGER,
                ec_uScm REAL NOT NULL,
                tds_mgL INTEGER NOT NULL,
                temp_C REAL NOT NULL,
                pH REAL NOT NULL,
                do_mgL REAL NOT NULL,
                do_status INTEGER,
                ec_status INTEGER,
                ph_status INTEGER,
                predicted_level TEXT NOT NULL,
                confidence REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                UNIQUE (session_id, sample_id)
            );

            CREATE INDEX IF NOT EXISTS idx_measurements_session_sample
            ON measurements(session_id, sample_id DESC);

            CREATE INDEX IF NOT EXISTS idx_measurements_time
            ON measurements(computer_time DESC);

            CREATE INDEX IF NOT EXISTS idx_measurements_level
            ON measurements(predicted_level);
            """
        )
        self.connection.commit()

    def _start_session(self, serial_port: str, baudrate: int) -> int:
        started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        cursor = self.connection.execute(
            "INSERT INTO sessions (started_at, serial_port, baudrate) VALUES (?, ?, ?)",
            (started_at, serial_port, baudrate),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_connection(self, serial_port: str, baudrate: int) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "UPDATE sessions SET serial_port = ?, baudrate = ? WHERE id = ?",
                (serial_port, baudrate, self.session_id),
            )

    def append(self, record: dict[str, object]) -> dict[str, object]:
        with self._lock, self.connection:
            row = self.connection.execute(
                "SELECT COALESCE(MAX(sample_id), 0) + 1 FROM measurements WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            sample_id = int(row[0])
            values = {
                "session_id": self.session_id,
                "sample_id": sample_id,
                **{column: record.get(column) for column in HISTORY_COLUMNS[2:]},
            }
            self.connection.execute(
                """
                INSERT INTO measurements (
                    session_id, sample_id, computer_time, cycle_id,
                    device_timestamp_ms, ec_uScm, tds_mgL, temp_C, pH,
                    do_mgL, do_status, ec_status, ph_status,
                    predicted_level, confidence
                ) VALUES (
                    :session_id, :sample_id, :computer_time, :cycle_id,
                    :device_timestamp_ms, :ec_uScm, :tds_mgL, :temp_C, :pH,
                    :do_mgL, :do_status, :ec_status, :ph_status,
                    :predicted_level, :confidence
                )
                """,
                values,
            )
        return values

    def read(self, limit: int | None = None) -> list[dict[str, object]]:
        return self.query_history(session_id=self.session_id, limit=limit)

    def list_sessions(self) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT
                sessions.id,
                sessions.started_at,
                sessions.ended_at,
                sessions.serial_port,
                sessions.baudrate,
                COUNT(measurements.id) AS record_count
            FROM sessions
            LEFT JOIN measurements ON measurements.session_id = sessions.id
            GROUP BY sessions.id
            ORDER BY sessions.id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def query_history(
        self,
        session_id: int | None = None,
        start_date: str = "",
        end_date: str = "",
        level: str = "",
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        conditions = []
        parameters: list[object] = []
        if session_id is not None:
            conditions.append("session_id = ?")
            parameters.append(session_id)
        if start_date:
            conditions.append("substr(computer_time, 1, 10) >= ?")
            parameters.append(start_date)
        if end_date:
            conditions.append("substr(computer_time, 1, 10) <= ?")
            parameters.append(end_date)
        if level:
            conditions.append("predicted_level = ?")
            parameters.append(level)

        sql = f"SELECT {', '.join(HISTORY_COLUMNS)} FROM measurements"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        rows = self.connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def export_csv(file_path: Path, rows: list[dict[str, object]]) -> int:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=HISTORY_COLUMNS)
            writer.writeheader()
            writer.writerows(
                {column: row.get(column) for column in HISTORY_COLUMNS} for row in rows
            )
        return len(rows)

    def close(self) -> None:
        if self._closed:
            return
        ended_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock, self.connection:
            self.connection.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (ended_at, self.session_id),
            )
        self.connection.close()
        self._closed = True