"""ios-inspect: debug Safari / WKWebView on an iPhone from Windows.

Thin launcher around `pymobiledevice3 webinspector cdp`: waits for the device,
starts the bridge, opens the DevTools landing page in a Chromium-based browser,
and restarts the bridge when the cable is unplugged and plugged back in.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from importlib.metadata import PackageNotFoundError, version

# Default install folders, relative to %ProgramFiles%, %ProgramFiles(x86)% and %LocalAppData%:
# Windows installers do not put browsers on PATH. Edge ships with Windows, so it comes last as
# the fallback that is almost always there.
BROWSER_PATHS = (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ("Chromium", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"),
)
BROWSER_NAMES = ("chrome", "brave", "chromium", "msedge")

# Apple Mobile Device Service (installed with Apple Devices or iTunes) is the Windows
# counterpart of usbmuxd; pymobiledevice3 talks to it on this port.
USBMUX_ADDRESS = ("127.0.0.1", 27015)

# Without its own process group the bridge gets the console's Ctrl+C too, exits on its own, and
# the restart loop would mistake that for a crash. We stop it ourselves instead.
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

# Device names and page titles are UTF-8; the console code page often is not.
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def pmd3(*args: str) -> list[str]:
    return [sys.executable, "-m", "pymobiledevice3", *args]


def run_pmd3(*args: str, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        pmd3(*args), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=CHILD_ENV,
    )


def list_devices() -> list[dict]:
    try:
        return json.loads(run_pmd3("usbmux", "list", timeout=20).stdout or "[]")
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def pick_device(udid: str | None) -> dict | None:
    for dev in list_devices():
        if udid is None or dev.get("UniqueDeviceID") == udid:
            return dev
    return None


def wait_for_device(udid: str | None) -> dict:
    dev = pick_device(udid)
    if dev:
        return dev
    print("Waiting for an iPhone to be plugged in and unlocked...", flush=True)
    while True:
        time.sleep(2)
        dev = pick_device(udid)
        if dev:
            return dev


def get_json(url: str, timeout: float = 3) -> object | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (OSError, ValueError):
        return None


def find_browser(explicit: str | None) -> str | None:
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        return shutil.which(explicit)
    for name in BROWSER_NAMES:
        if shutil.which(name):
            return shutil.which(name)
    roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
    for parts in BROWSER_PATHS:
        for root in roots:
            path = os.path.join(root, *parts) if root else ""
            if path and os.path.isfile(path):
                return path
    return None


def open_browser(browser: str | None, url: str) -> None:
    path = find_browser(browser)
    if not path:
        print(f"No Chromium-based browser found. Open it manually in Chrome or Edge: {url}")
        return
    subprocess.Popen(
        [path, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=NEW_GROUP,
    )


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def describe(dev: dict) -> str:
    return (
        f"{dev.get('DeviceName', 'iPhone')} ({dev.get('ProductType', '?')}, "
        f"iOS {dev.get('ProductVersion', '?')}, {dev.get('ConnectionType', '?')})"
    )


def wait_child(proc: subprocess.Popen) -> None:
    # Popen.wait() blocks Ctrl+C on Windows; short timed waits let the signal handler run.
    while proc.poll() is None:
        try:
            proc.wait(0.5)
        except subprocess.TimeoutExpired:
            pass


def cmd_start(args: argparse.Namespace) -> int:
    base = f"http://{args.host}:{args.port}"
    opened = args.no_browser
    proc: subprocess.Popen | None = None

    def stop(*_: object) -> None:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("\nStopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)

    while True:
        # Checked before every launch: otherwise the bridge fails to bind and the old
        # server answering on the port would be reported as ready.
        if port_open(args.host, args.port):
            print(f"Port {args.port} is already in use (another ios-inspect, or Chrome with "
                  f"remote debugging?). Stop it, or pick another port: ios-inspect --port {args.port + 111}",
                  file=sys.stderr, flush=True)
            return 1

        dev = wait_for_device(args.udid)
        print(f"Connected: {describe(dev)}", flush=True)

        cmd = pmd3(
            "webinspector", "cdp", "--host", args.host, "--port", str(args.port),
            "--udid", dev["UniqueDeviceID"],
        )
        if args.pause_new_targets:
            cmd.append("--pause-new-targets")
        log = None if args.verbose else subprocess.DEVNULL
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, env=CHILD_ENV, creationflags=NEW_GROUP)

        started = time.time()
        warned = False
        ready = False
        while proc.poll() is None:
            if get_json(f"{base}/json/version"):
                ready = True
                break
            if not warned and time.time() - started > 30:
                print("Bridge is still starting... (unlock the iPhone; run with --verbose "
                      "if this persists)", flush=True)
                warned = True
            time.sleep(0.5)

        if ready and proc.poll() is None:
            # The bridge serves /json before it has fetched the page list from the device.
            list_deadline = time.time() + 5
            pages = get_json(f"{base}/json") or []
            while not pages and time.time() < list_deadline and proc.poll() is None:
                time.sleep(0.5)
                pages = get_json(f"{base}/json") or []
            print(f"Web Inspector ready: {base}/  ({len(pages)} inspectable page{'' if len(pages) == 1 else 's'})")
            if not pages:
                print("  No pages yet. Open a website in Safari on the iPhone, then refresh the list.")
            if not opened:
                open_browser(args.browser, f"{base}/")
                opened = True
            print("Click a page title to open DevTools. Press Ctrl+C to stop.", flush=True)
            wait_child(proc)

        code = proc.returncode
        if args.no_reconnect:
            return code or 1
        if pick_device(args.udid) and code not in (0, None):
            print(f"Bridge exited (code {code}). Run with --verbose for details.")
            print("Retrying in 5 seconds...", flush=True)
            time.sleep(5)
        else:
            print("iPhone disconnected. Plug it back in to continue.", flush=True)


def check(ok: bool, label: str, hint: str = "") -> bool:
    print(f"  [{'OK' if ok else 'X '}] {label}")
    if not ok and hint:
        print(f"        -> {hint}")
    return ok


def cmd_doctor(args: argparse.Namespace) -> int:
    print("ios-inspect doctor")
    try:
        pmd3_version = version("pymobiledevice3")
    except PackageNotFoundError:
        pmd3_version = None
    results = [
        check(bool(pmd3_version), f"pymobiledevice3 {pmd3_version or 'not installed'}",
              "pip install pymobiledevice3"),
        check(port_open(*USBMUX_ADDRESS), "Apple Mobile Device Service running",
              "Install \"Apple Devices\" from the Microsoft Store (or iTunes), then restart. "
              "Check services.msc for \"Apple Mobile Device Service\"."),
    ]
    dev = pick_device(args.udid)
    results.append(check(bool(dev), f"iPhone detected: {describe(dev)}" if dev else "iPhone detected",
                         "Plug in a data cable, unlock the iPhone, tap Trust. "
                         "It should appear in the Apple Devices app."))
    if dev:
        tabs = run_pmd3("webinspector", "opened-tabs", "--udid", dev["UniqueDeviceID"], "-t", "5", timeout=40)
        n = tabs.stdout.count("TYPE:")
        results.append(check(
            tabs.returncode == 0 and n > 0, f"Web Inspector responding ({n} page{'' if n == 1 else 's'})",
            "iOS 18+: Settings > Apps > Safari > Advanced > Web Inspector = ON, "
            "then open a website in Safari.",
        ))
    browser = find_browser(args.browser)
    results.append(check(bool(browser), f"Browser: {browser or 'not found'}",
                         "Install Google Chrome, or use Microsoft Edge."))
    print("All good." if all(results) else "Some checks failed (see the X marks).")
    return 0 if all(results) else 1


def cmd_pages(args: argparse.Namespace) -> int:
    dev = pick_device(args.udid)
    if not dev:
        print("No iPhone detected. Run `ios-inspect doctor`.")
        return 1
    return subprocess.call(
        pmd3("webinspector", "opened-tabs", "--udid", dev["UniqueDeviceID"], "-t", "5"), env=CHILD_ENV
    )


def cmd_devices(args: argparse.Namespace) -> int:
    devices = list_devices()
    if not devices:
        print("No iPhone detected.")
        return 1
    for dev in devices:
        print(f"{dev.get('UniqueDeviceID')}  {describe(dev)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        prog="ios-inspect",
        description="Debug Safari / WKWebView on an iPhone from Windows (console, network, elements).",
    )
    try:
        own_version = version("ios-inspect-windows")
    except PackageNotFoundError:
        own_version = "dev"
    parser.add_argument("--version", action="version", version=f"%(prog)s {own_version}")
    parser.add_argument("--udid", help="Pick a specific iPhone when several are connected")
    parser.add_argument("--browser", help="Browser name or path to its .exe (default: Chrome, Brave or Edge)")
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="Start the Web Inspector (default)")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=9222)
    start.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    start.add_argument("--no-reconnect", action="store_true",
                       help="Exit when the iPhone disconnects instead of waiting for it")
    start.add_argument("--pause-new-targets", action="store_true",
                       help="Pause every new JSContext on its first statement")
    start.add_argument("-v", "--verbose", action="store_true", help="Show bridge logs")

    sub.add_parser("doctor", help="Check the setup: Apple Mobile Device Service, iPhone, Web Inspector, browser")
    sub.add_parser("pages", help="List inspectable Safari tabs / WebViews")
    sub.add_parser("devices", help="List connected iPhones")

    argv = list(argv if argv is not None else sys.argv[1:])
    # `start` is the default subcommand, so `ios-inspect --no-browser` means `ios-inspect start --no-browser`.
    i = 0
    while i < len(argv):
        if argv[i] in ("--udid", "--browser"):
            i += 2
        elif argv[i].startswith(("--udid=", "--browser=")):
            i += 1
        else:
            break
    if i >= len(argv) or argv[i] not in (*sub.choices, "-h", "--help", "--version"):
        argv.insert(min(i, len(argv)), "start")

    args = parser.parse_args(argv)
    if args.command == "start":
        return cmd_start(args)
    return {"doctor": cmd_doctor, "pages": cmd_pages, "devices": cmd_devices}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
