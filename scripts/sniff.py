#!/usr/bin/env python3
"""Passive BLE sniffer using Adafruit nRF51822 via serial (SLIP protocol).

Uses the Nordic nRF Sniffer firmware protocol to capture BLE advertising
packets. This is a serial-based sniffer, NOT an HCI adapter.

Hardware: Adafruit Bluefruit LE Sniffer (nRF51822 + CP210x UART)
Protocol: Nordic nRF Sniffer v2, SLIP-encoded, 460800 baud
Device:   /dev/ttyUSB0 (auto-detected)
"""

import argparse
import asyncio
import logging
import pathlib
import struct
import sys
from datetime import datetime

import serial

_LOGGER = logging.getLogger(__name__)

# --- SLIP constants ---
SLIP_END = 0xC0
SLIP_ESC = 0xDB
SLIP_ESC_END = 0xDC
SLIP_ESC_ESC = 0xDD

# --- nRF Sniffer packet types ---
REQ_FOLLOW = 0x00
EVENT_FOLLOW = 0x01
EVENT_CONNECT = 0x05
EVENT_DEVICE = 0x06
REQ_SCAN_CONT = 0x07
EVENT_DISCONNECT = 0x09
SET_TEMPORARY_KEY = 0x0C
PING_REQ = 0x0D
PING_RESP = 0x0E
SET_ADV_CHANNEL_HOP_SEQ = 0x17

SNIFFER_BAUDRATE = 460800
PROTOCOL_VERSION = 1


# --- Custom exceptions ---


class SnifferError(Exception):
    """Base exception for sniffer operations."""


class SnifferNotFoundError(SnifferError):
    """No sniffer device found on serial ports."""


class SnifferProtocolError(SnifferError):
    """Unexpected data from sniffer firmware."""


class SnifferTimeoutError(SnifferError):
    """Sniffer did not respond in time."""


# --- SLIP encoding/decoding ---


def slip_encode(data: bytes) -> bytes:
    """SLIP-encode a data frame."""
    encoded = bytearray([SLIP_END])
    for byte in data:
        if byte == SLIP_END:
            encoded.extend([SLIP_ESC, SLIP_ESC_END])
        elif byte == SLIP_ESC:
            encoded.extend([SLIP_ESC, SLIP_ESC_ESC])
        else:
            encoded.append(byte)
    encoded.append(SLIP_END)
    return bytes(encoded)


def slip_decode(data: bytes) -> bytes:
    """SLIP-decode a data frame."""
    decoded = bytearray()
    i = 0
    while i < len(data):
        if data[i] == SLIP_ESC:
            i += 1
            if i >= len(data):
                break
            if data[i] == SLIP_ESC_END:
                decoded.append(SLIP_END)
            elif data[i] == SLIP_ESC_ESC:
                decoded.append(SLIP_ESC)
            else:
                decoded.append(data[i])
        elif data[i] != SLIP_END:
            decoded.append(data[i])
        i += 1
    return bytes(decoded)


# --- Packet construction ---


def build_packet(packet_type: int, payload: bytes = b"") -> bytes:
    """Build an nRF Sniffer protocol packet."""
    header_len = 6
    payload_len = len(payload)
    # header: header_len(1) + payload_len(1) + protocol_version(1) +
    #         packet_counter(2, LE) + packet_type(1)
    header = struct.pack(
        "<BBBHB",
        header_len,
        payload_len,
        PROTOCOL_VERSION,
        0,  # packet counter (host→sniffer, can be 0)
        packet_type,
    )
    return header + payload


def parse_packet_header(data: bytes) -> tuple[int, int, int, int, bytes] | None:
    """Parse nRF Sniffer packet header.

    Returns (header_len, payload_len, proto_ver, packet_type, payload)
    or None if too short.
    """
    if len(data) < 6:
        return None
    header_len, payload_len, proto_ver, _pkt_counter, pkt_type = struct.unpack("<BBBHB", data[:6])
    payload = data[header_len : header_len + payload_len]
    return header_len, payload_len, proto_ver, pkt_type, payload


# --- Serial sniffer reader ---


def detect_sniffer_port() -> str:
    """Auto-detect the first available serial sniffer port."""
    for pattern in ("ttyUSB*", "ttyACM*"):
        ports = sorted(pathlib.Path("/dev").glob(pattern))
        if ports:
            return str(ports[0])
    raise SnifferNotFoundError("No serial sniffer found. Check /dev/ttyUSB* or /dev/ttyACM*")


class SnifferReader:
    """Reads and decodes SLIP frames from the nRF Sniffer serial port."""

    def __init__(self, port: str, baudrate: int = SNIFFER_BAUDRATE) -> None:
        self._port = port
        self._baudrate = baudrate
        self._serial: serial.Serial | None = None
        self._running = False
        self._packet_count = 0

    async def open(self) -> None:
        """Open the serial port."""
        loop = asyncio.get_running_loop()
        self._serial = await loop.run_in_executor(
            None,
            lambda: serial.Serial(
                self._port,
                baudrate=self._baudrate,
                timeout=0.1,
            ),
        )
        _LOGGER.info("Opened sniffer on %s at %d baud", self._port, self._baudrate)

    async def close(self) -> None:
        """Close the serial port."""
        if self._serial and self._serial.is_open:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._serial.close)
            _LOGGER.info("Closed sniffer port")

    async def _write(self, data: bytes) -> None:
        """Write data to serial port."""
        if not self._serial:
            raise SnifferError("Serial port not open")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._serial.write, data)

    async def _read(self, size: int = 1024) -> bytes:
        """Read data from serial port."""
        if not self._serial:
            raise SnifferError("Serial port not open")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._serial.read, size)

    async def send_command(self, packet_type: int, payload: bytes = b"") -> None:
        """Send a SLIP-encoded command to the sniffer."""
        packet = build_packet(packet_type, payload)
        frame = slip_encode(packet)
        await self._write(frame)

    async def ping(self, timeout: float = 2.0) -> bool:
        """Send PING and wait for PONG. Returns True if sniffer responds."""
        await self.send_command(PING_REQ)

        deadline = asyncio.get_event_loop().time() + timeout
        buf = bytearray()

        while asyncio.get_event_loop().time() < deadline:
            chunk = await self._read(256)
            if chunk:
                buf.extend(chunk)
                # Look for a complete SLIP frame
                while SLIP_END in buf:
                    idx = buf.index(SLIP_END)
                    frame_data = bytes(buf[:idx])
                    buf = buf[idx + 1 :]
                    if not frame_data:
                        continue
                    decoded = slip_decode(frame_data)
                    parsed = parse_packet_header(decoded)
                    if parsed and parsed[3] == PING_RESP:
                        return True
            else:
                await asyncio.sleep(0.05)

        return False

    async def start_scanning(self) -> None:
        """Send REQ_SCAN_CONT to start continuous scanning."""
        await self.send_command(REQ_SCAN_CONT)
        _LOGGER.info("Started continuous BLE scanning")

    async def read_packets(
        self,
        duration: float = 0,
        output_file: str | None = None,
    ) -> None:
        """Read and display sniffed BLE packets.

        Args:
            duration: Scan duration in seconds (0 = indefinite).
            output_file: Optional file path for raw packet logging.
        """
        self._running = True
        seen_addresses: dict[str, int] = {}
        buf = bytearray()
        start_time = asyncio.get_event_loop().time()
        out_fh = None

        if output_file:
            out_fh = open(output_file, "wb")  # noqa: SIM115
            _LOGGER.info("Logging raw packets to %s", output_file)

        try:
            while self._running:
                if duration > 0:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= duration:
                        break

                chunk = await self._read(4096)
                if not chunk:
                    await asyncio.sleep(0.01)
                    continue

                buf.extend(chunk)

                # Process complete SLIP frames
                while SLIP_END in buf:
                    idx = buf.index(SLIP_END)
                    frame_data = bytes(buf[:idx])
                    buf = buf[idx + 1 :]

                    if not frame_data:
                        continue

                    decoded = slip_decode(frame_data)
                    if out_fh:
                        out_fh.write(decoded + b"\n")

                    parsed = parse_packet_header(decoded)
                    if not parsed:
                        continue

                    _, _, _, pkt_type, payload = parsed
                    self._packet_count += 1

                    if pkt_type == EVENT_DEVICE and len(payload) >= 10:
                        self._handle_device_event(payload, seen_addresses)
                    elif pkt_type == PING_RESP:
                        _LOGGER.debug("Ping response received")
                    else:
                        _LOGGER.debug(
                            "Packet type=0x%02X len=%d",
                            pkt_type,
                            len(payload),
                        )

        finally:
            if out_fh:
                out_fh.close()
            print(f"\n  Total packets: {self._packet_count}")
            print(f"  Unique addresses: {len(seen_addresses)}")

    def _handle_device_event(
        self,
        payload: bytes,
        seen: dict[str, int],
    ) -> None:
        """Parse and display an EVENT_DEVICE packet."""
        if len(payload) < 10:
            return

        _flags = payload[0]  # reserved for future filtering
        channel = payload[1]
        # RSSI is signed byte
        rssi = struct.unpack("b", payload[2:3])[0]
        addr_len = payload[3]

        if addr_len != 6 or len(payload) < 4 + addr_len:
            return

        addr_bytes = payload[4 : 4 + addr_len]
        address = ":".join(f"{b:02X}" for b in reversed(addr_bytes))

        adv_data = payload[4 + addr_len :]

        count = seen.get(address, 0) + 1
        seen[address] = count

        # Only print first sighting and every 10th after
        if count == 1 or count % 50 == 0:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(
                f"  [{ts}] ch={channel:2d} rssi={rssi:4d} "
                f"addr={address} "
                f"adv[{len(adv_data)}]={adv_data[:20].hex()}"
                f"{'...' if len(adv_data) > 20 else ''}"
                f"  (seen {count}x)"
            )

    def stop(self) -> None:
        """Signal the reader to stop."""
        self._running = False


async def run_sniffer(
    port: str,
    duration: float = 0,
    output_file: str | None = None,
) -> None:
    """Main sniffer flow."""
    print(f"\n{'=' * 60}")
    print("  Tuya BLE Mesh Lab — Passive BLE Sniffer")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Port: {port}")
    print(f"  Duration: {'indefinite' if duration == 0 else f'{duration}s'}")
    print(f"{'=' * 60}\n")

    reader = SnifferReader(port)
    await reader.open()

    try:
        # Try PING to verify sniffer firmware
        print("  Pinging sniffer...", end=" ", flush=True)
        if await reader.ping(timeout=2.0):
            print("OK (nRF Sniffer firmware detected)")
        else:
            print("no response (reading raw frames)")

        # Start scanning
        await reader.start_scanning()
        print("  Scanning for BLE advertisements...\n")

        await reader.read_packets(duration=duration, output_file=output_file)

    except KeyboardInterrupt:
        print("\n  Stopped by user.")
    finally:
        await reader.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Passive BLE sniffer via Adafruit nRF51822 (serial)",
    )
    parser.add_argument(
        "-p",
        "--port",
        default=None,
        help="Serial port (default: auto-detect /dev/ttyUSB*)",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=0,
        help="Scan duration in seconds (default: 0 = indefinite)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output file for raw packet logging",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    port = args.port
    if port is None:
        try:
            port = detect_sniffer_port()
        except SnifferNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    try:
        asyncio.run(run_sniffer(port, args.duration, args.output))
    except SnifferError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
