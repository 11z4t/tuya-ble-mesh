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
KEYS_FILE = Path("/tmp/mesh_keys.json")  # nosec B108
SEQ_FILE = Path("/tmp/mesh_seq_tracker.json")  # nosec B108

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
            check=True,
            capture_output=True,
            timeout=5,
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
            check=True,
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        _LOGGER.error("Failed to enable WiFi: %s", exc)
        return False

    # Wait for DHCP
    for attempt in range(20):
        time.sleep(1)
        result = subprocess.run(
            ["ip", "addr", "show", WLAN_IFACE],
            capture_output=True,
            text=True,
            timeout=5,
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


def _restart_bluetooth() -> bool:
    """Restart bluetooth stack. Returns True on success."""
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "bluetooth"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        time.sleep(2)
        subprocess.run(
            ["sudo", "hciconfig", "hci0", "up"],
            check=True,
            capture_output=True,
            timeout=5,
        )
        time.sleep(1)
        return True
    except Exception as exc:
        _LOGGER.error("Bluetooth restart failed: %s", exc)
        return False


def _execute_sig_mesh(action: str, mac: str, target: str) -> dict:
    """Execute a SIG Mesh command via mesh_proxy_cmd.py subprocess."""
    script = str(Path(__file__).parent / "mesh_proxy_cmd.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "lib")

    try:
        result = subprocess.run(
            [
                sys.executable,
                script,
                action,
                "--mac",
                mac,
                "--target",
                target,
                "--wait",
                "5",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path(__file__).parent.parent),
            env=env,
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
        _LOGGER.info("SIG Mesh command exit code: %d", exit_code)
    except subprocess.TimeoutExpired:
        stdout, stderr, exit_code = "", "BLE command timed out", -1
    except Exception as exc:
        stdout, stderr, exit_code = "", str(exc), -1

    status = None
    for line in stdout.split("\n"):
        if "OnOff Status:" in line:
            status = "ON" if "ON" in line else "OFF"
        elif "AppKey Status: Success" in line:
            status = "appkey_ok"
        elif "SETUP COMPLETE" in line:
            status = "setup_ok"

    return {
        "success": exit_code == 0,
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout[-500:] if stdout else "",
        "stderr": stderr[-200:] if stderr else "",
    }


def _execute_telink(action: str, mac: str, params: dict) -> dict:
    """Execute a Telink proprietary mesh command via MeshDevice.

    Runs in a subprocess to avoid asyncio conflicts with the daemon loop.
    """
    script_code = f"""
import asyncio, sys, json
sys.path.insert(0, "{Path(__file__).resolve().parent.parent / "lib"}")
from tuya_ble_mesh.device import MeshDevice

async def run():
    dev = MeshDevice("{mac}", b"out_of_mesh", b"123456", mesh_id=0)
    result = {{"success": False}}
    try:
        await dev.connect(timeout=15.0, max_retries=3)
        result["firmware"] = dev.firmware_version
        action = "{action}"
        if action == "on":
            await dev.send_power(True)
            result["status"] = "ON"
        elif action == "off":
            await dev.send_power(False)
            result["status"] = "OFF"
        elif action == "brightness":
            level = {params.get("level", 100)}
            await dev.send_brightness(level)
            result["status"] = f"brightness_{{level}}"
        elif action == "color_temp":
            temp = {params.get("temp", 128)}
            await dev.send_color_temp(temp)
            result["status"] = f"color_temp_{{temp}}"
        elif action == "color":
            r, g, b = {params.get("r", 255)}, {params.get("g", 255)}, {params.get("b", 255)}
            await dev.send_color(r, g, b)
            result["status"] = f"color_{{r}}_{{g}}_{{b}}"
        elif action == "light_mode":
            mode = {params.get("mode", 0)}
            await dev.send_light_mode(mode)
            result["status"] = f"mode_{{mode}}"
        elif action == "color_brightness":
            level = {params.get("level", 255)}
            await dev.send_color_brightness(level)
            result["status"] = f"color_brightness_{{level}}"
        else:
            result["error"] = f"Unknown telink action: {{action}}"
            print(json.dumps(result))
            await dev.disconnect()
            return
        result["success"] = True
        await asyncio.sleep(0.5)
        await dev.disconnect()
    except Exception as e:
        result["error"] = f"{{type(e).__name__}}: {{e}}"
    print(json.dumps(result))

asyncio.run(run())
"""
    env = os.environ.copy()
    try:
        result = subprocess.run(
            [sys.executable, "-c", script_code],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr
        if result.returncode == 0 and stdout:
            for line in stdout.split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    return json.loads(line)
        return {
            "success": False,
            "error": f"Subprocess failed (exit {result.returncode})",
            "stderr": stderr[-200:] if stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Telink command timed out"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def execute_ble_command(cmd: dict) -> dict:
    """Execute a BLE mesh command with WiFi temporarily disabled."""
    action = cmd.get("action", "")
    target = cmd.get("target", "00B0")
    mac = cmd.get("mac", "DC:23:4F:10:52:C4")
    device_type = cmd.get("device_type", "sig_mesh")

    _LOGGER.info(
        "Executing BLE command: type=%s action=%s target=%s mac=%s",
        device_type,
        action,
        target,
        mac,
    )

    sig_actions = ("on", "off", "status", "setup", "composition")
    telink_actions = (
        "on",
        "off",
        "brightness",
        "color_temp",
        "color",
        "light_mode",
        "color_brightness",
    )

    if device_type == "telink" and action not in telink_actions:
        return {"success": False, "error": f"Unknown telink action: {action}"}
    if device_type == "sig_mesh" and action not in sig_actions:
        return {"success": False, "error": f"Unknown sig_mesh action: {action}"}

    # Phase 1: Disable WiFi
    if not wifi_down():
        return {"success": False, "error": "Failed to disable WiFi"}

    time.sleep(1)

    # Phase 2: Restart bluetooth
    if not _restart_bluetooth():
        wifi_up()
        return {"success": False, "error": "Bluetooth restart failed"}

    # Phase 3: Run command
    if device_type == "telink":
        result = _execute_telink(action, mac, cmd.get("params", {}))
    else:
        result = _execute_sig_mesh(action, mac, target)

    # Phase 4: Re-enable WiFi
    wifi_up()

    result.update(
        {
            "action": action,
            "target": target,
            "device_type": device_type,
            "mac": mac,
            "timestamp": time.time(),
        }
    )
    return result


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
        _LOGGER.info(
            "Result written: success=%s status=%s", result["success"], result.get("status")
        )
    except Exception as exc:
        _LOGGER.error("Failed to write result: %s", exc)


def _http_response(status: str, body: str) -> str:
    """Build a minimal HTTP/1.1 response."""
    return (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n{body}"
    )


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
            body = json.dumps({"status": "ok", "timestamp": time.time()})
            response = _http_response("200 OK", body)
            writer.write(response.encode())

        elif method == "GET" and path == "/result":
            if LAST_RESULT.exists():
                body = LAST_RESULT.read_text()
            else:
                body = json.dumps({"error": "No result available"})
            response = _http_response("200 OK", body)
            writer.write(response.encode())

        elif method == "POST" and path == "/command":
            body_start = request_str.find("\r\n\r\n")
            if body_start >= 0:
                body_str = request_str[body_start + 4 :]
                try:
                    cmd = json.loads(body_str)
                    PENDING_CMD.write_text(json.dumps(cmd))
                    resp_body = json.dumps({"queued": True})
                    response = _http_response("202 Accepted", resp_body)
                except json.JSONDecodeError:
                    resp_body = json.dumps({"error": "Invalid JSON"})
                    response = _http_response("400 Bad Request", resp_body)
            else:
                resp_body = json.dumps({"error": "No body"})
                response = _http_response("400 Bad Request", resp_body)
            writer.write(response.encode())

        else:
            resp_body = json.dumps({"error": "Not found"})
            response = _http_response("404 Not Found", resp_body)
            writer.write(response.encode())

        await writer.drain()
    except Exception as exc:
        _LOGGER.debug("HTTP handler error: %s", exc)
    finally:
        writer.close()


async def run_http_server() -> None:
    """Run the HTTP server for remote command submission."""
    server = await asyncio.start_server(http_handler, "0.0.0.0", HTTP_PORT)  # nosec B104
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
            keys.get("mac"),
            keys.get("unicast"),
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
