import csv
from pathlib import Path
import tempfile
import unittest

from protocol import crc16_modbus, parse_packet
from runtime import (
    SQLiteRecordStore,
    WaterQualityPredictor,
    build_record,
)


DATA = bytes.fromhex(
    "AA 55 02 24 00 01 00 00 00 40 E2 01 00 00 00 C8 41 "
    "9A 99 01 41 5E 01 00 20 02 44 00 00 D8 40 00 00 00 0D 0A"
)


class RuntimeTests(unittest.TestCase):
    @staticmethod
    def _record(
        computer_time: str = "2026-09-07T09:00:00+08:00",
        level: str = "Caution",
    ) -> dict[str, object]:
        return {
            "computer_time": computer_time,
            "cycle_id": 1,
            "device_timestamp_ms": 123456,
            "ec_uScm": 520.5,
            "tds_mgL": 350,
            "temp_C": 25.0,
            "pH": 8.1,
            "do_mgL": 6.75,
            "do_status": 0,
            "ec_status": 0,
            "ph_status": 0,
            "predicted_level": level,
            "confidence": 0.9,
        }

    def test_sqlite_keeps_history_and_isolates_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "water_quality.db"
            first_store = SQLiteRecordStore(database_path, "COM3", 115200)
            first_session_id = first_store.session_id
            first_record = first_store.append(self._record())
            first_store.close()

            second_store = SQLiteRecordStore(database_path, "COM4", 9600)
            second_record = second_store.append(
                self._record("2026-09-08T10:00:00+08:00", "Normal")
            )
            current_rows = second_store.read()
            historical_rows = second_store.query_history(
                session_id=first_session_id
            )
            sessions = second_store.list_sessions()
            second_store.close()

        self.assertEqual(first_record["sample_id"], 1)
        self.assertEqual(second_record["sample_id"], 1)
        self.assertEqual(len(current_rows), 1)
        self.assertEqual(current_rows[0]["predicted_level"], "Normal")
        self.assertEqual(len(historical_rows), 1)
        self.assertEqual(historical_rows[0]["predicted_level"], "Caution")
        self.assertEqual(len(sessions), 2)

    def test_sqlite_history_filters_and_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SQLiteRecordStore(root / "water_quality.db", "COM3", 115200)
            store.append(self._record("2026-09-07T09:00:00+08:00", "Caution"))
            store.append(self._record("2026-09-08T09:00:00+08:00", "Normal"))

            rows = store.query_history(
                start_date="2026-09-08",
                end_date="2026-09-08",
                level="Normal",
            )
            export_path = root / "exports" / "history.csv"
            exported_count = store.export_csv(export_path, rows)
            store.close()

            with export_path.open(encoding="utf-8-sig", newline="") as file:
                exported_rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["predicted_level"], "Normal")
        self.assertEqual(exported_count, 1)
        self.assertEqual(exported_rows[0]["predicted_level"], "Normal")

    def test_packet_to_prediction(self) -> None:
        packet = parse_packet(DATA + crc16_modbus(DATA).to_bytes(2, "little"))
        predictor = WaterQualityPredictor()
        level, confidence = predictor.predict(packet)

        self.assertIn(level, {"Normal", "Caution", "Warning", "Severe"})
        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)


if __name__ == "__main__":
    unittest.main()