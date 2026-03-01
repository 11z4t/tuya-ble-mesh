# Setup Status — Malmbergs BT Lab

Generated: 2026-03-01

## Hardware

| Component | Status | Details |
|-----------|--------|---------|
| Bluetooth (hci0) | OK | UP RUNNING, BD Address: 2C:CF:67:3A:3B:43 |
| BLE Sniffer | OK | /dev/ttyUSB0, Silicon Labs CP210x UART Bridge (Adafruit nRF51822) |
| Shelly Plug S | OK | 192.168.1.50, Gen1 (SHPLG-S), auth:false, fw v1.14.0 |

## Software

| Component | Status | Details |
|-----------|--------|---------|
| Python venv | OK | ~/malmbergs-ble, Python 3.13.5 |
| bleak | OK | BLE library installed |
| pyserial | OK | Serial sniffer communication |
| aiohttp | OK | Shelly HTTP control |
| tshark | OK | /usr/bin/tshark v4.4.13 |
| Git | OK | Branch: main |

## Infrastructure

| Component | Status | Details |
|-----------|--------|---------|
| 1Password CLI | OK | v2.32.1 installed |
| OP_SERVICE_ACCOUNT_TOKEN | NOT SET | Manual setup required (see TODO.md) |
| 1Password vault | UNKNOWN | Cannot verify without token |
| NAS (/mnt/solutions) | NOT MOUNTED | //192.168.5.220/z-solutions, CIFS/SMB 3.0, sec=none |

## Action Items

1. Set `OP_SERVICE_ACCOUNT_TOKEN` in tmux session (see TODO.md)
2. Configure NAS automount for /mnt/solutions
3. Verify 1Password vault and items after token is set
