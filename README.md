# Android Phone Farm (macOS)

A **local**, operator-friendly control system for **multiple Android phones** connected via **USB hubs/switches**.

This project is designed to keep things **simple and reliable**:
- No microcontroller required (yet)
- No custom firmware
- ADB-based device control
- Works on macOS with zsh

It ships three interfaces that all talk to the same backend:

- **GUI (mouse-first)**: select phones by click / shift-click / drag-select, then use big obvious buttons.
- **TUI (keyboard-first)**: fast ops dashboard in your terminal.
- **CLI (zsh)**: scriptable commands (start/stop/status/select/run presets).

## Important note on use
This tool is built for legitimate device-lab operations such as:
- opening apps for manual review
- launching URLs for content/creative preview
- capturing screenshots/short recordings as evidence
- keeping devices organized and recoverable

Do not use automation to violate platform policies, spam, or create inauthentic engagement.

## What you need

### Hardware
- A Mac (Apple Silicon recommended)
- Powered USB hubs (and good cables)
- Android phones with USB debugging enabled

### Software (macOS)
Install the external tools:

```bash
brew install android-platform-tools
brew install scrcpy
```

## Install (Python)

```bash
cd android-phone-farm
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -e "./[gui]"
```

If you don't want the GUI, install without extras:

```bash
pip install -e .
```

## Start the system (easy mode)

Start backend:

```bash
./bin/farm start
```

Open the GUI:

```bash
./bin/farm gui
```

Or open the TUI:

```bash
./bin/farm tui
```

## The GUI workflow (super obvious)

1. Plug in phones and confirm they appear in ADB.
2. In the GUI, **drag-select** or **shift-click** to select a batch.
3. Click a big action button:
   - **Start Instagram** (opens Instagram on selected phones)
   - **Start TikTok**
   - **Start X (Twitter)**
   - **Start YouTube**
   - **Open Website** (opens a URL)
   - **See Screens** (launches scrcpy windows)
4. If a phone looks stuck, use:
   - **Restart Phones** (ADB reboot)
   - **Restart ADB (Mac)**
   - **Stop Everything**

## Troubleshooting

### Phones not showing up
- Use a known-good data cable (many cables are charge-only).
- On the phone, enable Developer Options and USB debugging.
- Accept the USB debugging authorization prompt.
- Run:
  ```bash
  adb devices -l
  ```

### Devices show "unauthorized"
- Unlock phone screen.
- Replug cable.
- Accept the prompt.

### Everything looks offline
- Restart ADB:
  ```bash
  ./bin/farm usb restart-adb
  ```

## Limitations (no microcontroller)
Without a microcontroller or a programmable per-port hub, the system cannot reliably power-cycle an individual phone port.

This project implements best-effort recovery:
1) restart ADB server
2) reboot device via ADB (if responsive)
3) optional hub power cycle via a user-supplied command (advanced)

## Config
Edit `config/farm.yaml` to set:
- host/port
- adb path
- optional hub power cycle command
- friendly device names and tags

## Run without the wrapper
You can run backend directly:

```bash
source .venv/bin/activate
python -m backend
```

Backend API:
- http://127.0.0.1:8765/api/v1/health
- http://127.0.0.1:8765/api/v1/fleet

# android-phone-farm
