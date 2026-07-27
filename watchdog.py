#!/usr/bin/env python3
"""CC Switch Vision Bridge Watchdog (pythonw build).

Runs via pythonw.exe so Task Scheduler does not spawn a console window
every tick. The previous powershell.exe -WindowStyle Hidden build still
flashed a conhost on each run. Logic mirrors watchdog.ps1: if the bridge
port is listening, exit 0; otherwise kill a stale bridge pid, restart the
bridge scheduled task, and wait up to 30s for the port to come back.
"""
import logging
import os
import socket
import subprocess
import sys
import time

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CCSwitchVisionBridge")
LOG_PATH = os.path.join(APP_DIR, "watchdog.log")
PID_PATH = os.path.join(APP_DIR, "bridge.pid")
BRIDGE_PORT = 15722
BRIDGE_TASK = "CC Switch Vision Bridge"
STARTUP_TIMEOUT = 30  # seconds, matches watchdog.ps1 loop (60 * 0.5s)

# No console window for any child process (powershell/taskkill/schtasks).
CREATE_NO_WINDOW = 0x08000000

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("watchdog")


def port_listening(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            return True
    except OSError:
        return False


def run(cmd, **kw):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
        **kw,
    )


def kill_stale_bridge():
    if not os.path.exists(PID_PATH):
        return
    try:
        with open(PID_PATH) as f:
            bpid = int(f.read().strip())
    except (ValueError, OSError):
        return
    r = run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f'Get-CimInstance Win32_Process -Filter "ProcessId={bpid}" | '
            "Select-Object -ExpandProperty CommandLine",
        ]
    )
    if "cc_switch_vision_bridge" in (r.stdout or ""):
        run(["taskkill", "/F", "/PID", str(bpid)])
        log.info("killed stale bridge pid=%d", bpid)


def restart_bridge() -> bool:
    run(["schtasks", "/End", "/TN", BRIDGE_TASK])
    run(["schtasks", "/Run", "/TN", BRIDGE_TASK])
    for _ in range(STARTUP_TIMEOUT * 2):
        time.sleep(0.5)
        if port_listening(BRIDGE_PORT):
            return True
    return False


def main() -> int:
    if port_listening(BRIDGE_PORT):
        log.info("port %d ok, no action", BRIDGE_PORT)
        return 0
    log.warning("port %d not listening, restarting bridge", BRIDGE_PORT)
    kill_stale_bridge()
    if restart_bridge():
        log.info("bridge back up on port %d", BRIDGE_PORT)
        return 0
    log.error("bridge did not reopen port %d", BRIDGE_PORT)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log.exception("watchdog crashed: %s", e)
        sys.exit(2)
