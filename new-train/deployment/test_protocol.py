import unittest

from protocol import (
    PacketError,
    PacketStreamDecoder,
    crc16_modbus,
    missing_cycle_count,
    parse_packet,
)


DATA = bytes.fromhex(
    "AA 55 02 24 00 01 00 00 00 40 E2 01 00 00 00 C8 41 "
    "9A 99 01 41 5E 01 00 20 02 44 00 00 D8 40 00 00 00 0D 0A"
)
PACKET = DATA + bytes.fromhex("71 BE")


class ProtocolTests(unittest.TestCase):
    def test_crc_matches_example(self) -> None:
        self.assertEqual(crc16_modbus(DATA), 0xBE71)

    def test_parse_example_packet(self) -> None:
        result = parse_packet(PACKET)

        self.assertEqual(result.cycle_id, 1)
        self.assertEqual(result.device_timestamp_ms, 123456)
        self.assertAlmostEqual(result.temp_C, 25.0)
        self.assertAlmostEqual(result.pH, 8.1, places=5)
        self.assertEqual(result.tds_mgL, 350)
        self.assertAlmostEqual(result.ec_uScm, 520.5)
        self.assertAlmostEqual(result.do_mgL, 6.75)

    def test_rejects_bad_crc(self) -> None:
        damaged = PACKET[:-1] + bytes([PACKET[-1] ^ 0xFF])
        with self.assertRaises(PacketError):
            parse_packet(damaged)

    def test_stream_decoder_handles_partial_and_noisy_input(self) -> None:
        decoder = PacketStreamDecoder()
        self.assertEqual(decoder.feed(b"noise" + PACKET[:12]), [])
        decoded = decoder.feed(PACKET[12:] + PACKET)

        self.assertEqual([item.cycle_id for item in decoded], [1, 1])
        self.assertEqual(decoder.invalid_frames, 0)

    def test_missing_cycle_count_handles_gaps_and_counter_wrap(self) -> None:
        self.assertEqual(missing_cycle_count(None, 10), 0)
        self.assertEqual(missing_cycle_count(10, 11), 0)
        self.assertEqual(missing_cycle_count(10, 14), 3)
        self.assertEqual(missing_cycle_count(0xFFFFFFFE, 1), 2)
        self.assertEqual(missing_cycle_count(100, 1), 0)


if __name__ == "__main__":
    unittest.main()