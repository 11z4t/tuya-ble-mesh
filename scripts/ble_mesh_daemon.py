#!/usr/bin/env python3
"""BLE Mesh Bridge Daemon — mediates WiFi/BLE coexistence on RPi.

Watches a command file for pending mesh operations. When a command
arrives, temporarily disables WiFi, executes the BLE operation,
re-enables WiFi, and writes the result.

Designed to run as a systemd service on RPi where CYW43455 WiFi/BLE
coexistence prevents simultaneous WiFi and BLE connections.

Commands are submitted via JSON files:
  /var/lib/tuya_ble_mesh/pending_cmd.json
Results are written to:
  /var/lib/tuya_ble_mesh/last_result.json

The daemon also serves a minimal HTTP API on port 8099 (when WiFi is up)
for submitting commands from remote systems (e.g., HA on VM 900).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

_LOGGER = logging.getLogger("ble_mesh_daemon")

# Paths
STATE_DIR = Path("/var/lib/tuya_ble_mesh")
PENDING_CMD = STATE_DIR / "pending_cmd.json"
LAST_RESULT = STATE_DIR / "last_result.json"
KEYS_FILE = Path("/tmp/mesh_keys.json")
SEQ_FILE = Path("/tmp/mesh_seq_tracker.json")

# WiFi interface
WLAN_IFACE = "wlan0"

# Daemon config
POLL_INTERVAL = 1.0  # seconds between checking for pending commands
HTTP_PORT = 8099
MAX_BLE_RETRIES = 3
BLE_TIMEOUT = 20.0


def ensure_state_dir() -> None:
    """Create state directory if it doesn't exist."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def wifi_down() -> bool:
    """Disable WiFi interface. Returns True on success."""
    try:
        subprocess.run(
            ["sudo", "ip", "link", "set", WLAN_IFACE, "down"],
            check=True, capture_output=True, timeout=5,
        )
        _LOGGER.info("WiFi DOWN")
        return True
    except Exception as exc:
        _LOGGER.error("Failed to disable WiFi: %s", exc)
        return False


def wifi_up() -> bool:
    """Re-enable WiFi interface. Returns True when IP acquired."""
    try:
        subprocess.run(
            ["sudo", "ip", "link", "set", WLAN_IFACE, "up"],
            check=True, capture_output=True, timeout=5,
        )
    except Exception as exc:
        _LOGGER.error("Failed to enable WiFi: %s", exc)
        return False

    # Wait for DHCP
    for attempt in range(20):
        time.sleep(1)
        result = subprocess.run(
            ["ip", "addr", "show", WLAN_IFACE],
            capture_output=True, text=True, timeout=5,
        )
        if "inet " in result.stdout:
            _LOGGER.info("WiFi UP (got IP on attempt %d)", attempt + 1)
            return True

    _LOGGER.error("WiFi UP but no IP after 20s")
    return True


def load_keys() -> dict | None:
    """Load mesh keys from JSON file."""
    if not KEYS_FILE.exists():
        _LOGGER.error("Keys file not found: %s", KEYS_FILE)
        return None
    return json.loads(KEYS_FILE.read_text())


def execute_ble_command(cmd: dict) -> dict:
    """Execute a BLE mesh command with WiFi temporarily disabled.

    This runs in a subprocess since we can't use asyncio across WiFi toggle.
    """
    action = cmd.get("action", "")
    target = cmd.get("target", "00B0")
    mac = cmd.get("mac", "DC:23:4F:10:52:C4")

    _LOGGER.info("Executing BLE command: action=%s target=%s", action, target)

    if action not in ("on", "off", "status", "setup"):
        return {"success": False, "error": f"Unknown action: {action}"}

    # Phase 1: Disable WiFi
    if not wifi_down():
        return {"success": False, "error": "Failed to disable WiFi"}

    time.sleep(1)

    # Phase 2: Restart bluetooth
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "bluetooth"],
            check=True, capture_output=True, timeout=10,
        )
        time.sleep(2)
        subprocess.run(
            ["sudo", "hciconfig", "hci0", "up"],
            check=True, capture_output=True, timeout=5,
        )
        time.sleep(1)
    except Exception as exc:
        _LOGGER.error("Bluetooth restart failed: %s", exc)
        wifi_up()
        return {"success": False, "error": f"Bluetooth restart failed: {exc}"}

    # Phase 3: Run mesh command
    script = str(Path(__file__).parent / "mesh_proxy_cmd.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "lib")

    try:
        result = subprocess.run(
            [
                sys.executable, script,
                action,
                "--mac", mac,
                "--target", target,
                "--wait", "5",
            ],
            capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).parent.parent),
            env=env,
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
        _LOGGER.info("BLE command exit code: %d", exit_code)
    except subprocess.TimeoutExpired:
        stdout = ""
        stderr = "BLE command timed out"
        exit_code = -1
    except Exception as exc:
        stdout = ""
        stderr = str(exc)
        exit_code = -1

    # Phase 4: Re-enable WiFi
    wifi_up()

    # Parse result
    success = exit_code == 0
    status = None
    for line in stdout.split("\n"):
        if "OnOff Status:" in line:
            status = "ON" if "ON" in line else "OFF"
        elif "AppKey Status: Success" in line:
            status = "appkey_ok"
        elif "SETUP COMPLETE" in line:
            status = "setup_ok"

    return {
        "success": success,
        "action": action,
        "target": target,
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout[-500:] if stdout else "",
        "stderr": stderr[-200:] if stderr else "",
        "timestamp": time.time(),
    }


def process_pending() -> None:
    """Check for and process pending commands."""
    if not PENDING_CMD.exists():
        return

    try:
        cmd = json.loads(PENDING_CMD.read_text())
    except Exception as exc:
        _LOGGER.error("Failed to read pending command: %s", exc)
        PENDING_CMD.unlink(missing_ok=True)
        return

    # Remove pending file immediately to prevent re-processing
    PENDING_CMD.unlink(missing_ok=True)

    _LOGGER.info("Processing command: %s", cmd.get("action", "?"))

    result = execute_ble_command(cmd)

    # Write result
    try:
        LAST_RESULT.write_text(json.dumps(result, indent=2))
        _LOGGER.info("Result written: success=%s status=%s", result["success"], result.get("status"))
    except Exception as exc:
        _LOGGER.error("Failed to write result: %s", exc)


async def http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle incoming HTTP request for command submission."""
    try:
        request = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        request_str = request.decode("utf-8", errors="replace")

        # Parse HTTP request
        lines = request_str.split("\r\n")
        method_line = lines[0] if lines else ""
        parts = method_line.split(" ")
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else "/"

        if method == "GET" and path == "/health":
            # Health check
            body = json.dumps({"status": "ok", "timestamp": time.time()})
            response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}"
            writer.write(response.encode())

        elif method == "GET" and path == "/result":
            # Get last result
            if LAST_RESULT.exists():
                body = LAST_RESULT.read_text()
            else:
                body = json.dumps({"error": "No result available"})
            response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}"
            writer.write(response.encode())

        elif method == "POST" and path == "/command":
            # Submit command
            body_start = request_str.find("\r\n\r\n")
            if body_start >= 0:
                body_str = request_str[body_start + 4:]
                try:
                    cmd = json.loads(body_str)
                    PENDING_CMD.write_text(json.dumps(cmd))
                    resp_body = json.dumps({"queued": True})
                    response = f"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\nContent-Length: {len(resp_body)}\r\n\r\n{resp_body}"
                except json.JSONDecodeError:
                    resp_body = json.dumps({"error": "Invalid JSON"})
                    response = f"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: {len(resp_body)}\r\n\r\n{resp_body}"
            else:
                resp_body = json.dumps({"error": "No body"})
                response = f"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: {len(resp_body)}\r\n\r\n{resp_body}"
            writer.write(response.encode())

        else:
            resp_body = json.dumps({"error": "Not found"})
            response = f"HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\nContent-Length: {len(resp_body)}\r\n\r\n{resp_body}"
            writer.write(response.encode())

        await writer.drain()
    except Exception as exc:
        _LOGGER.debug("HTTP handler error: %s", exc)
    finally:
        writer.close()


async def run_http_server() -> None:
    """Run the HTTP server for remote command submission."""
    server = await asyncio.start_server(http_handler, "0.0.0.0", HTTP_PORT)
    _LOGGER.info("HTTP server listening on port %d", HTTP_PORT)
    async with server:
        await server.serve_forever()


async def run_command_watcher() -> None:
    """Watch for pending commands and process them."""
    _LOGGER.info("Command watcher started")
    while True:
        try:
            # Run in thread pool since BLE operations are blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, process_pending)
        except Exception:
            _LOGGER.error("Command processing error", exc_info=True)
        await asyncio.sleep(POLL_INTERVAL)


async def main() -> None:
    """Run daemon: HTTP server + command file watcher."""
    ensure_state_dir()

    keys = load_keys()
    if keys:
        _LOGGER.info(
            "Keys loaded: MAC=%s unicast=%s",
            keys.get("mac"), keys.get("unicast"),
        )
    else:
        _LOGGER.warning("No keys found — provisioning needed first")

    _LOGGER.info("BLE Mesh Bridge Daemon starting...")

    await asyncio.gather(
        run_http_server(),
        run_command_watcher(),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _LOGGER.info("Daemon stopped")
