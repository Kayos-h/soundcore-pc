# Soundcore for PC

A desktop control panel for **Soundcore / Anker** Bluetooth headphones on
Windows — a PC version of the official Soundcore mobile app. Built on the
**reverse-engineered** RFCOMM control protocol and **tested live against the
Soundcore Q20i (model A3004)**.

![modes](docs/_placeholder)  <!-- optional screenshot -->

## What it can do

- Find paired Bluetooth audio devices and connect to your headphones.
- Show **battery level** and connection status.
- Switch **ambient sound mode**: Noise Cancelling / Transparency / Normal,
  with **ANC strength** levels 1–5 and **wind-noise reduction**.
- Apply any of the **22 built-in EQ presets** (Soundcore Signature, Bass
  Booster, Rock, Pop, …), a **Dolby** curve, or a **custom 8-band EQ**.

These are the same commands the official app sends — not placebo UI.

## How it works (the honest version)

Soundcore headphones are controlled over a **Bluetooth BR/EDR RFCOMM** channel
using Anker's proprietary, encrypted-ish binary protocol. Anker never published
it; the community reverse-engineered parts of it from `btsnoop` captures.

This app ports the protocol from the open-source
[CoreSound](https://github.com/CriticalRange/CoreSound) project (GPL-3.0) into a
focused Python app:

- `soundcore_protocol.py` — frame builder/parser. Frame layout:
  `[08 EE 00 00 00][cat][type][len:2LE][payload][checksum]`. Contains the mode
  command (`06:81`), the 22 captured EQ preset curves + custom EQ (`03:87`), and
  battery/mode/device-info parsers.
- `bluetooth_transport.py` — Windows RFCOMM via the **WinRT** API
  (`BluetoothDevice` → `RfcommServices` → `StreamSocket`). On the Q20i the
  control channel is a **vendor-specific service** whose UUID ends in the model
  code: `0cf12d31-fac3-4553-bd80-d6832e7b3004`.
- `soundcore_app.py` — the customtkinter GUI.

### Why WinRT and not pybluez

Python 3.14's stdlib `socket` doesn't expose `AF_BTH` on Windows, and
`pybluez2` has no prebuilt wheel for 3.14 (it needs a C compiler). The
`winrt-*` packages ship prebuilt `cp314` wheels and talk to the same OS
Bluetooth stack — zero compilation.

## Requirements

- Windows 10/11
- Python 3.11+ (developed on 3.14)
- The headphones **paired in Windows Bluetooth settings** first
- If multipoint is on, it helps to disconnect the phone so the PC owns the
  control channel

## Install & run

```powershell
cd "C:\Users\Nitro\Desktop\soundcore"
pip install -r requirements.txt
python soundcore_app.py
```

Or just double-click **`Run Soundcore.bat`**.

## Device support

Confirmed on the **Q20i (A3004)**. The mode and EQ command formats are shared
across the Q-series over-ear ANC headphones, so other Q-series models are very
likely to work; earbud families may differ in battery offsets. If a model
connects but a feature misbehaves, the per-device quirk lives in
`soundcore_protocol.py`.

## Credits

Protocol derived from **CoreSound** by CriticalRange (GPL-3.0). This project
inherits GPL-3.0.
