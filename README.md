# ios-inspect-windows

Debug websites in **Safari on iPhone** and in **WKWebView** straight from **Windows**, no Mac required.
You get the familiar Chrome DevTools: Console, Network, Elements, Sources.

```
iPhone (Safari)  ──USB──▶  ios-inspect  ──▶  Chrome / Edge DevTools on Windows
```

> Apple ships the iOS Web Inspector only inside Safari on macOS. `ios-inspect` uses
> [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) to reach the Web Inspector
> service on the iPhone over USB and shows it in Chrome or Edge.
>
> Using Linux? See [ios-inspect-linux](https://github.com/raisulkhairi/ios-inspect-linux).

## Features

- **Console**: see `console.log` output and errors from the iPhone, run JavaScript in the page.
- **Network**: requests, responses, headers, status codes, timing.
- **Elements**: inspect the DOM and edit CSS live.
- **Sources**: browse JS files and set breakpoints.
- **One command**: waits for the iPhone, starts the bridge, and opens the browser for you.
- **Auto-reconnect**: if the cable comes loose, just plug it back in.
- **`ios-inspect doctor`**: checks your setup and tells you what to fix.

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10 or 11 | |
| Python 3.9+ | From [python.org](https://www.python.org/downloads/windows/) or the Microsoft Store |
| **Apple Devices** app (or iTunes) | Installs the iPhone USB driver and *Apple Mobile Device Service* |
| Chrome, Edge or Brave | Edge is built into Windows, so it works out of the box |
| iPhone + data cable | Charge-only cables will not work |
| Internet access | The DevTools UI is downloaded when it opens |

## Installation

In PowerShell:

```powershell
# 1. Install "Apple Devices" from the Microsoft Store (or iTunes from apple.com)

# 2. Install ios-inspect with pipx (recommended)
py -m pip install --user pipx
py -m pipx ensurepath          # then open a new terminal
pipx install git+https://github.com/raisulkhairi/ios-inspect-windows.git
```

Or in a virtualenv:

```powershell
py -m venv $HOME\.venvs\ios-inspect
& $HOME\.venvs\ios-inspect\Scripts\pip install git+https://github.com/raisulkhairi/ios-inspect-windows.git
& $HOME\.venvs\ios-inspect\Scripts\ios-inspect doctor
```

## iPhone setup (one time)

1. Go to **Settings → Apps → Safari → Advanced** and turn on **Web Inspector**.
   (iOS 17 and earlier: **Settings → Safari → Advanced → Web Inspector**.)
2. Plug the iPhone into your PC, unlock it, and tap **Trust** when *"Trust This Computer?"* appears.
   The iPhone should now show up in the Apple Devices app.

## Usage

```powershell
ios-inspect
```

Then:

1. Open the website you want to debug in **Safari on the iPhone**.
2. Your browser opens `http://127.0.0.1:9222/` automatically and lists the inspectable pages.
3. **Click the page title** (the blue link). DevTools opens.
4. When you are done, press `Ctrl+C` in the terminal.

> **Tip:** in the **Network** tab, reload the page on the iPhone after DevTools opens
> so that every request from the start is captured.

### Commands

| Command | What it does |
|---|---|
| `ios-inspect` | Start the Web Inspector (same as `ios-inspect start`) |
| `ios-inspect doctor` | Check Apple Mobile Device Service, the iPhone, Web Inspector, and the browser |
| `ios-inspect pages` | List Safari tabs that can be inspected |
| `ios-inspect devices` | List connected iPhones |

### `start` options

`start` is the default command, so these also work without it, e.g. `ios-inspect --no-browser`.

| Option | What it does |
|---|---|
| `--port 9333` | Use another port (default `9222`) |
| `--no-browser` | Do not open the browser automatically |
| `--no-reconnect` | Exit when the iPhone disconnects |
| `--pause-new-targets` | Pause every new JSContext on its first statement |
| `-v`, `--verbose` | Show bridge logs (for troubleshooting) |
| `--udid <UDID>` | Pick a specific iPhone when several are connected (put it before the subcommand) |
| `--browser <name or path>` | Use a specific browser, e.g. `--browser msedge` or a full path to the `.exe` |

### Debugging apps (WKWebView)

WKWebView pages inside an iOS app can be inspected when the app enables:

```swift
webView.isInspectable = true   // iOS 16.4+
```

### Using it from VS Code

Run `ios-inspect --no-browser`, then add this to `.vscode/launch.json`:

```json
{
  "type": "chrome",
  "request": "attach",
  "name": "iPhone Safari",
  "address": "127.0.0.1",
  "port": 9222,
  "urlFilter": "*",
  "webRoot": "${workspaceFolder}"
}
```

## Troubleshooting

Run `ios-inspect doctor` first. Common problems:

| Symptom | Fix |
|---|---|
| `Apple Mobile Device Service running` fails | Install **Apple Devices** (or iTunes) and restart. In `services.msc`, make sure *Apple Mobile Device Service* is running. |
| Stuck on `Waiting for an iPhone...` | Re-plug or try another USB port/cable, unlock the iPhone, tap **Trust**. Check that it appears in the Apple Devices app. |
| Stuck on `Bridge is still starting...` | Unlock the iPhone and keep Safari open. If it persists, stop and rerun with `--verbose`. |
| The page list is empty | Make sure Web Inspector is on, a website is open in Safari, and the iPhone is unlocked, then refresh the list. |
| `iPhone disconnected` | The cable came loose. Plug it back in; `ios-inspect` reconnects on its own. |
| DevTools shows a blank page | Check your internet connection (the DevTools UI is downloaded when it opens). |
| Console freezes under heavy logging | Open DevTools from `http://127.0.0.1:9222/`, not from `chrome://inspect` / `edge://inspect`. |
| Page marked *held elsewhere* | The page is being debugged somewhere else (Safari on a Mac, or a second `ios-inspect`). Close that debugger first. |
| `Port 9222 is already in use` | Another `ios-inspect` (or a browser with remote debugging) holds the port. Stop it, or run `ios-inspect --port 9333`. |
| `ios-inspect` is not recognized | Open a new terminal after `pipx ensurepath`, or call it through the venv path shown above. |

## Limitations

- The iOS Simulator is not supported (it needs Xcode/macOS).
- USB only; debugging over Wi-Fi has not been tested.
- Apple may change the Web Inspector protocol in new iOS releases. If something breaks,
  try updating pymobiledevice3: `pipx runpip ios-inspect-windows install -U pymobiledevice3`.

## How it works

`ios-inspect` itself is only a launcher. The heavy lifting (talking to the iPhone and translating
the protocol) is done by [pymobiledevice3](https://github.com/doronz88/pymobiledevice3).

```
┌─ iPhone ───────────────┐     ┌─ Windows PC ──────────────────────────────────────┐
│ Safari / WKWebView     │     │                                                   │
│        │               │     │  Apple Mobile     pymobiledevice3      Chrome /   │
│ webinspectord ◀────────┼─USB─┼─▶ Device     ──▶  webinspector cdp ◀──▶ Edge       │
│ (Web Inspector service)│     │    Service        • translates         DevTools   │
└────────────────────────┘     │    (:27015)       • serves :9222                  │
                               │        ▲                                          │
                               │  ios-inspect: starts, watches, opens the          │
                               │               browser, reconnects                 │
                               └───────────────────────────────────────────────────┘
```

On Windows, Apple Mobile Device Service plays the role that `usbmuxd` plays on Linux: it owns the
USB link to the iPhone and listens on `127.0.0.1:27015`, where pymobiledevice3 connects.
`pymobiledevice3 webinspector cdp` translates between the **WebKit Inspector Protocol** (what the
iPhone speaks) and the **Chrome DevTools Protocol** (what Chrome and Edge DevTools speak), and
serves it on `127.0.0.1:9222`.

## License

[GPL-3.0-or-later](LICENSE). This project uses
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (GPL-3.0).
