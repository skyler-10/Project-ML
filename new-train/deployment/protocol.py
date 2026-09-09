from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import struct


HEADER = b"\xAA\x55"
TAIL = b"\x0D\x0A"
DATA_LENGTH = 36
CRC_LENGTH = 2
PACKET_LENGTH = DATA_LENGTH + CRC_LENGTH
PACKET_STRUCT = struct.Struct("<2sBHIIffHffBBB2s")


class PacketError(ValueError):
    pass


@dataclass(frozen=True)
class WaterQualityPacket:
    protocol_version: int
    cycle_id: int
    device_timestamp_ms: int
    temp_C: float
    pH: float
    tds_mgL: int
    ec_uScm: float
    do_mgL: float
    do_status: int
    ec_status: int
    ph_status: int
    raw_packet_hex: str

    def as_record(self) -> dict[str, int | float | str]:
        return asdict(self)


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def missing_cycle_count(previous_cycle_id: int | None, cycle_id: int) -> int:
    if previous_cycle_id is None:
        return 0
    difference = (cycle_id - previous_cycle_id) & 0xFFFFFFFF
    if difference == 0 or difference >= 0x80000000:
        return 0
    return difference - 1


def parse_packet(packet: bytes) -> WaterQualityPacket:
    if len(packet) != PACKET_LENGTH:
        raise PacketError(f"Expected {PACKET_LENGTH} bytes, got {len(packet)}")

    data = packet[:DATA_LENGTH]
    received_crc = int.from_bytes(packet[DATA_LENGTH:], "little")
    expected_crc = crc16_modbus(data)
    if received_crc != expected_crc:
        raise PacketError(
            f"CRC mismatch: received 0x{received_crc:04X}, expected 0x{expected_crc:04X}"
        )

    (
        header,
        version,
        declared_length,
        cycle_id,
        timestamp_ms,
        temp_c,
        ph,
        tds_mgl,
        ec_uscm,
        do_mgl,
        do_status,
        ec_status,
        ph_status,
        tail,
    ) = PACKET_STRUCT.unpack(data)

    if header != HEADER:
        raise PacketError("Invalid packet header")
    if declared_length != DATA_LENGTH:
        raise PacketError(
            f"Invalid declared length: {declared_length}; expected {DATA_LENGTH}"
        )
    if tail != TAIL:
        raise PacketError("Invalid packet tail")
    if not all(math.isfinite(value) for value in (temp_c, ph, ec_uscm, do_mgl)):
        raise PacketError("Measurement contains NaN or infinity")

    return WaterQualityPacket(
        protocol_version=version,
        cycle_id=cycle_id,
        device_timestamp_ms=timestamp_ms,
        temp_C=temp_c,
        pH=ph,
        tds_mgL=tds_mgl,
        ec_uScm=ec_uscm,
        do_mgL=do_mgl,
        do_status=do_status,
        ec_status=ec_status,
        ph_status=ph_status,
        raw_packet_hex=packet.hex(" ").upper(),
    )


class PacketStreamDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self.invalid_frames = 0

    def feed(self, chunk: bytes) -> list[WaterQualityPacket]:
        self._buffer.extend(chunk)
        decoded: list[WaterQualityPacket] = []

        while True:
            header_index = self._buffer.find(HEADER)
            if header_index < 0:
                self._buffer[:] = self._buffer[-1:]
                break
            if header_index:
                del self._buffer[:header_index]
            if len(self._buffer) < PACKET_LENGTH:
                break

            candidate = bytes(self._buffer[:PACKET_LENGTH])
            try:
                decoded.append(parse_packet(candidate))
            except PacketError:
                self.invalid_frames += 1
                del self._buffer[0]
            else:
                del self._buffer[:PACKET_LENGTH]

        return decoded