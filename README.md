# Multi-Transport-Tester

**Multi-Transport-Tester** is a desktop app for **quickly testing TCP, UDP, Redis, and Serial communication from a single UI**.
It is built with Tkinter and provides connection controls, send/receive testing, framing options, periodic sending, and log search/navigation.

## Features

- Multi-transport support: `TCP`, `UDP`, `Redis Pub/Sub`, `Serial`
- Shared message framing:
  - Delimiter mode (`LF`, `CRLF`, `CUSTOM HEX`)
  - Fixed-length mode (`strict`, `pad`, `truncate`)
- Manual send, periodic jobs, and heartbeat
- Real-time log viewer:
  - Search with `Prev/Next` navigation
  - Auto-scroll toggle
  - Wrap, copy, save, clear
  - Highlighting for important logs (`error`, `connect/disconnect`, `ui/setting`)
- Status metrics:
  - RX/TX bytes, frames, speed, and last RX/TX time
- Settings save/load (`settings.json`)
- Windows one-file executable build (PyInstaller)

## Requirements

- Python 3.12
- Tkinter (GUI)

```text
redis>=5.0,<6.0
pyserial>=3.5,<4.0
python-osc>=1.8,<2.0
```

- PyInstaller (for packaging)

## Project Structure

```text
MultiTransportTester/
├─ app.py                         # Main UI entry point
├─ engine.py                      # Background asyncio network engine
├─ ui_widgets.py                  # Shared UI widgets
├─ ui/
│  └─ transports/
│     ├─ tcp.py                   # TCP transport UI
│     ├─ udp.py                   # UDP transport UI
│     ├─ redis.py                 # Redis transport UI
│     ├─ serial.py                # Serial transport UI
│     └─ base.py                  # Shared transport UI types/callbacks
├─ MultiTransportTester.spec      # PyInstaller one-file spec
├─ requirements.txt               # Runtime dependencies
├─ tests/                         # Local test scripts (gitignored by default)
└─ settings.json                  # Runtime user settings (gitignored by default)
```

## Getting Started (Development)

PowerShell:

```powershell
cd D:\hong\PythonTest\MultiTransportTester
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
.\venv\Scripts\python app.py
```

## Quick UI Guide

### 1) Top Bar

- `Transport`: select TCP / UDP / REDIS / SERIAL
- `Theme`: select light / dark
- `START`: start selected transport
- `STOP`: stop all active transport tasks
- `APPLY`: apply current settings to the running engine

### 2) Log Panel

- Search: type in `Search` to highlight matches
- Navigation: `Prev`, `Next`
- Utilities: `Copy`, `Save`, `Wrap`, `Clear`, `Auto-Scroll`

### 3) Shared Settings

- Message Framing
  - `delimiter` / `fixed`
  - delimiter type + custom hex
  - fixed-length policy (`strict` / `pad` / `truncate`)
- Manual Send
  - Send UTF-8 or HEX payloads manually
- Timer Jobs
  - `sendTimer_1`, `sendTimer_2`, `sendTimer_3`, `heartbeat`
  - Set interval, payload, HEX mode, and send immediately

## Keyboard Shortcuts

- `Ctrl + F`: focus log search box
- `Enter` (in search): next result
- `Shift + Enter` (in search): previous result
- `F3`: next result
- `Shift + F3`: previous result
- `Ctrl + L`: clear log
- `Ctrl + Shift + S`: save log to file
- `Ctrl + Shift + C`: copy selected log (or all log text)

## Build (Windows EXE)

### One-file Build (Recommended)

```powershell
.\venv\Scripts\pip install pyinstaller
.\venv\Scripts\python -m PyInstaller --onefile --windowed --name MultiTransportTester app.py --noconfirm --clean
```

Result:

- `dist\MultiTransportTester.exe`

### Build from Spec

```powershell
.\venv\Scripts\python -m PyInstaller MultiTransportTester.spec --noconfirm
```

## Configuration File (`settings.json`)

UI state is saved during runtime and restored on next launch.

- Transport type and each transport's settings
- Framing options
- Manual send/job/heartbeat settings
- Theme and log-wrap UI options

The file is created in the project root.

## Troubleshooting

- Redis `connect failed`
  - Check whether Redis is running and verify host/port/db/password.

- Serial `open failed`
  - Check COM port name, access permission, and device connection state.

## License

This project is licensed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
