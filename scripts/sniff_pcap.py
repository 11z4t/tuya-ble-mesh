#!/usr/bin/env python3
"""nRF51822 BLE Sniffer → PCAP capture.

Captures BLE traffic from Adafruit nRF51822 sniffer and writes PCAP
format (DLT 157 = LINKTYPE_NORDIC_BLE) for analysis with tshark/Wireshark.

Usage:
  python sniff_pcap.py                       # scan + follow target
  tshark -r /tmp/ble_capture.pcap -V         # analyze

Nordic SLIP encoding: START=0xAB, END=0xBC, ESC=0xCD
"""

import os
import struct
import time

import serial

# Target device
TARGET_MAC = "DC:23:4D:21:43:A5"
SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 460800
PCAP_FILE = "/tmp/ble_capture.pcap"
CAPTURE_SECONDS = 120

# Nordic SLIP constants (NOT standard SLIP!)
SLIP_START = 0xAB
SLIP_END = 0xBC
SLIP_ESC = 0xCD
SLIP_ESC_START = 0xAC  # SLIP_START + 1
SLIP_ESC_END = 0xBD  # SLIP_END + 1
SLIP_ESC_ESC = 0xCE  # SLIP_ESC + 1

# nRF Sniffer packet types
REQ_FOLLOW = 0x00
RESP_FOLLOW = 0x01
EVENT_DEVICE = 0x02
EVENT_CONNECT = 0x05
EVENT_PACKET = 0x06
REQ_SCAN_CONT = 0x07
EVENT_DISCONNECT = 0x09
PING_REQ = 0x0D
PING_RESP = 0x0E
GO_IDLE = 0xFE

# Sniffer header
HEADER_LENGTH = 6
PROTOVER = 1

# PCAP constants
PCAP_MAGIC = 0xA1B2C3D4
PCAP_VERSION_MAJOR = 2
PCAP_VERSION_MINOR = 4
LINKTYPE_NORDIC_BLE = 157
BOARD_ID = 0


def slip_encode(data):
    """Encode data using Nordic SLIP protocol."""
    out = bytearray([SLIP_START])
    for b in data:
        if b == SLIP_START:
            out.extend([SLIP_ESC, SLIP_ESC_START])
        elif b == SLIP_END:
            out.extend([SLIP_ESC, SLIP_ESC_END])
        elif b == SLIP_ESC:
            out.extend([SLIP_ESC, SLIP_ESC_ESC])
        else:
            out.append(b)
    out.append(SLIP_END)
    return bytes(out)


def slip_decode_frame(ser, timeout=5.0):
    """Read one SLIP frame from serial. Returns decoded bytes or None."""
    # Wait for SLIP_START
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        if b[0] == SLIP_START:
            break
    else:
        return None

    # Read until SLIP_END
    buf = bytearray()
    deadline = time.time() + timeout
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        if b[0] == SLIP_END:
            return bytes(buf)
        elif b[0] == SLIP_ESC:
            b2 = ser.read(1)
            if not b2:
                continue
            if b2[0] == SLIP_ESC_START:
                buf.append(SLIP_START)
            elif b2[0] == SLIP_ESC_END:
                buf.append(SLIP_END)
            elif b2[0] == SLIP_ESC_ESC:
                buf.append(SLIP_ESC)
            else:
                buf.append(b2[0])
        else:
            buf.append(b[0])
    return None


def build_sniffer_packet(pkt_id, payload=b"", counter=0):
    """Build a sniffer protocol packet."""
    hdr = struct.pack("<BBBHB", HEADER_LENGTH, len(payload), PROTOVER, counter, pkt_id)
    return hdr + payload


def write_pcap_header(f):
    """Write PCAP global header."""
    f.write(
        struct.pack(
            "<IHHIIII",
            PCAP_MAGIC,
            PCAP_VERSION_MAJOR,
            PCAP_VERSION_MINOR,
            0,
            0,
            0xFFFF,
            LINKTYPE_NORDIC_BLE,
        )
    )


def write_pcap_packet(f, data):
    """Write one PCAP packet record. Data = board_id + raw sniffer packet."""
    ts = time.time()
    ts_sec = int(ts)
    ts_usec = int((ts - ts_sec) * 1000000)
    pkt_data = bytes([BOARD_ID]) + data
    f.write(struct.pack("<IIII", ts_sec, ts_usec, len(pkt_data), len(pkt_data)))
    f.write(pkt_data)
    f.flush()


def mac_to_list(mac_str):
    """Convert 'AA:BB:CC:DD:EE:FF' to [0xAA, 0xBB, ...]."""
    return [int(x, 16) for x in mac_str.split(":")]


def main():
    print(f"\n{'=' * 60}")
    print("  nRF51822 BLE Sniffer → PCAP")
    print(f"  Target: {TARGET_MAC}")
    print(f"  Port:   {SERIAL_PORT}")
    print(f"  Output: {PCAP_FILE}")
    print(f"  Duration: {CAPTURE_SECONDS}s")
    print(f"{'=' * 60}\n")

    ser = serial.Serial(SERIAL_PORT, baudrate=BAUDRATE, timeout=0.1, rtscts=True)
    counter = [0]

    def send(pkt_id, payload=b""):
        pkt = build_sniffer_packet(pkt_id, payload, counter[0])
        counter[0] += 1
        frame = slip_encode(pkt)
        ser.write(frame)

    # Ping
    print("  Pinging sniffer...", end=" ", flush=True)
    send(PING_REQ)
    time.sleep(1)
    resp = slip_decode_frame(ser, timeout=2.0)
    if resp and len(resp) >= 6 and resp[5] == PING_RESP:
        fw_ver = resp[6] | (resp[7] << 8) if len(resp) >= 8 else 0
        print(f"OK (fw={fw_ver})")
    else:
        print("no response (continuing)")

    # Start scanning
    print("  Starting scan...", flush=True)
    send(REQ_SCAN_CONT)
    time.sleep(2)
    # Drain scan responses
    while True:
        resp = slip_decode_frame(ser, timeout=0.5)
        if resp is None:
            break

    # Follow target
    mac_bytes = mac_to_list(TARGET_MAC)
    # REQ_FOLLOW payload: 6 bytes addr + 1 byte followOnlyAdvertisements
    follow_payload = bytes([*mac_bytes, 0])
    print(f"  Following {TARGET_MAC}...", flush=True)
    send(REQ_FOLLOW, follow_payload)
    time.sleep(0.5)

    # Open PCAP file
    with open(PCAP_FILE, "wb") as pcap:
        write_pcap_header(pcap)
        print("\n  CAPTURING — para med appen och styr lampan!")
        print("  Ctrl+C to stop.\n")

        pkt_count = 0
        data_count = 0
        connect_count = 0
        start = time.time()

        try:
            while time.time() - start < CAPTURE_SECONDS:
                frame = slip_decode_frame(ser, timeout=1.0)
                if frame is None:
                    continue
                if len(frame) < 6:
                    continue

                # Parse sniffer header
                frame[0]
                pay_len = frame[1]
                frame[2]
                pkt_id = frame[5]

                pkt_count += 1

                # Write ALL packets to PCAP
                write_pcap_packet(pcap, frame)

                # Print summary
                if pkt_id == EVENT_CONNECT:
                    connect_count += 1
                    print(f"  [{time.time() - start:6.1f}s] CONNECTION #{connect_count}")
                elif pkt_id == EVENT_DISCONNECT:
                    print(f"  [{time.time() - start:6.1f}s] DISCONNECT")
                elif pkt_id == EVENT_PACKET:
                    data_count += 1
                    if data_count <= 10 or data_count % 50 == 0:
                        print(
                            f"  [{time.time() - start:6.1f}s] DATA pkt #{data_count} ({pay_len}B)"
                        )
                elif pkt_id == EVENT_DEVICE:
                    pass  # advertising, ignore
                elif pkt_count <= 5:
                    print(f"  [{time.time() - start:6.1f}s] type=0x{pkt_id:02X} ({pay_len}B)")

        except KeyboardInterrupt:
            print("\n  Stopped.")

    elapsed = time.time() - start
    ser.close()
    pcap_size = os.path.getsize(PCAP_FILE)
    print(f"\n  Captured {pkt_count} packets ({data_count} data, {connect_count} connections)")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  PCAP: {PCAP_FILE} ({pcap_size} bytes)")
    print("\n  Analyze:")
    print(f"    tshark -r {PCAP_FILE} -V -Y 'btatt'")
    print(f"    tshark -r {PCAP_FILE} -Y 'btatt.opcode == 0x12' -V")


if __name__ == "__main__":
    main()
