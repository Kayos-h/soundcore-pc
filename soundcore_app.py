"""
Soundcore for PC -- a desktop control panel for Soundcore / Anker headphones.

A Soundcore-app-style controller for Windows, built on the reverse-engineered
RFCOMM control protocol (see soundcore_protocol.py). Tested live against the
Soundcore Q20i (model A3004).

What it does:
  * Lists paired Bluetooth audio devices and connects over RFCOMM (WinRT).
  * Shows battery level and live connection status.
  * Switches ambient mode: Noise Cancelling / Transparency / Normal,
    with ANC strength levels and wind-noise reduction.
  * Applies any of the 22 built-in EQ presets, a Dolby curve, or a custom
    8-band equaliser.

Requires: customtkinter, and the winrt-* packages (installed separately).
Run:  python soundcore_app.py
"""

import threading
import traceback

import customtkinter as ctk

import soundcore_protocol as proto
import bluetooth_transport as bt

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT = "#17c0eb"
ACCENT_HOVER = "#13a3c7"
CARD = "#1b1d23"
CARD2 = "#23262e"
OKGREEN = "#2ecc71"
WARN = "#e67e22"
ERR = "#e74c3c"

ANC_LEVELS = ["level1", "level2", "level3", "level4", "level5"]
ANC_LEVEL_LABELS = {
    "level1": "1 - Minimum", "level2": "2", "level3": "3",
    "level4": "4", "level5": "5 - Maximum",
}

# One-tap scene presets: each bundles an ambient mode + EQ (+ options) tuned for
# a use case. eq is a preset id from EQ_PRESET_NAMES, or "dolby".
# mode is one of anc / transparency / normal.
SCENE_PRESETS = [
    {"name": "Gaming",  "icon": "\U0001F3AE", "mode": "normal",       "eq": 2,
     "desc": "Low-latency Normal mode + bass-forward EQ so footsteps and explosions hit hard."},
    {"name": "Music",   "icon": "\U0001F3B5", "mode": "normal",       "eq": 0,
     "desc": "Normal mode + Soundcore Signature \u2014 balanced, full-range listening."},
    {"name": "Bass",    "icon": "\U0001F50A", "mode": "normal",       "eq": 2,
     "desc": "Normal + Bass Booster for EDM, hip-hop and heavy low end."},
    {"name": "Movie",   "icon": "\U0001F3AC", "mode": "anc", "anc": "level5", "eq": "dolby",
     "desc": "ANC + Dolby curve for immersive, cinematic sound."},
    {"name": "Travel",  "icon": "\u2708\uFE0F", "mode": "anc", "anc": "level5", "wind": True, "eq": 0,
     "desc": "Max ANC + wind reduction to kill plane/train/traffic noise."},
    {"name": "Focus",   "icon": "\U0001F9E0", "mode": "anc", "anc": "level5", "eq": 8,
     "desc": "Max ANC + Flat EQ for distraction-free study and work."},
    {"name": "Calls",   "icon": "\U0001F3A4", "mode": "transparency", "eq": 18,
     "desc": "Transparency + Spoken Word so you hear yourself and voices clearly."},
    {"name": "Rock",    "icon": "\U0001F3B8", "mode": "normal",       "eq": 16,
     "desc": "Normal + Rock EQ \u2014 punchy mids and crisp highs for guitars."},
]


class SoundcoreApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Soundcore for PC")
        self.geometry("680x760")
        self.minsize(640, 700)
        self.configure(fg_color="#121319")

        self.conn = None
        self.devices = []
        self.selected_mac = None
        self.last_mac = None
        self.current_mode = "normal"
        self.current_anc_level = "level5"
        self.wind_on = False
        self.custom_bands = [0x78] * 8  # 0x78 = neutral / 0 dB

        self._build_ui()
        self.refresh_devices()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(header, text="Soundcore", font=("Segoe UI Semibold", 26),
                     text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(header, text="  for PC", font=("Segoe UI", 20),
                     text_color="#8a8f98").pack(side="left")

        # --- connection card ---
        conn_card = self._card()
        row = ctk.CTkFrame(conn_card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(14, 8))
        self.device_menu = ctk.CTkOptionMenu(
            row, values=["(scanning...)"], command=self._on_device_pick,
            fg_color=CARD2, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            width=320)
        self.device_menu.pack(side="left", padx=(0, 10))
        ctk.CTkButton(row, text="Rescan", width=80, fg_color=CARD2,
                      hover_color="#2d313b", command=self.refresh_devices).pack(side="left")
        self.connect_btn = ctk.CTkButton(
            row, text="Connect", width=110, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self.toggle_connect)
        self.connect_btn.pack(side="right")

        status_row = ctk.CTkFrame(conn_card, fg_color="transparent")
        status_row.pack(fill="x", padx=16, pady=(0, 14))
        self.status_dot = ctk.CTkLabel(status_row, text="\u25cf", text_color=ERR,
                                       font=("Segoe UI", 16))
        self.status_dot.pack(side="left")
        self.status_label = ctk.CTkLabel(status_row, text="Not connected",
                                         text_color="#c7ccd4", font=("Segoe UI", 13))
        self.status_label.pack(side="left", padx=(6, 0))
        self.battery_label = ctk.CTkLabel(status_row, text="", text_color=OKGREEN,
                                          font=("Segoe UI Semibold", 13))
        self.battery_label.pack(side="right")

        # --- scene presets card ---
        preset_card = self._card()
        self._section(preset_card, "Scene Presets")
        ctk.CTkLabel(preset_card, text="One tap sets the best mode + EQ for the moment.",
                     text_color="#8a8f98", font=("Segoe UI", 11)).pack(anchor="w", padx=16)
        grid = ctk.CTkFrame(preset_card, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=(8, 14))
        self.preset_buttons = []
        cols = 4
        for i, sp in enumerate(SCENE_PRESETS):
            b = ctk.CTkButton(
                grid, text=f"{sp['icon']}  {sp['name']}", height=46, corner_radius=10,
                fg_color=CARD2, hover_color=ACCENT_HOVER, font=("Segoe UI", 13),
                command=lambda s=sp: self._apply_scene(s))
            b.grid(row=i // cols, column=i % cols, padx=5, pady=5, sticky="ew")
            self.preset_buttons.append(b)
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1)

        # --- ambient mode card ---
        mode_card = self._card()
        self._section(mode_card, "Ambient Sound")
        seg_row = ctk.CTkFrame(mode_card, fg_color="transparent")
        seg_row.pack(fill="x", padx=16, pady=(2, 10))
        self.mode_seg = ctk.CTkSegmentedButton(
            seg_row, values=["Noise Cancelling", "Transparency", "Normal"],
            command=self._on_mode_seg, fg_color=CARD2, selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER, font=("Segoe UI", 13))
        self.mode_seg.set("Normal")
        self.mode_seg.pack(fill="x")

        # ANC strength
        self.anc_frame = ctk.CTkFrame(mode_card, fg_color="transparent")
        self.anc_frame.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(self.anc_frame, text="ANC Strength", text_color="#8a8f98",
                     font=("Segoe UI", 12)).pack(anchor="w")
        self.anc_slider = ctk.CTkSlider(self.anc_frame, from_=1, to=5, number_of_steps=4,
                                        command=self._on_anc_slider, progress_color=ACCENT,
                                        button_color=ACCENT, button_hover_color=ACCENT_HOVER)
        self.anc_slider.set(5)
        self.anc_slider.pack(fill="x", pady=(2, 2))
        self.anc_level_label = ctk.CTkLabel(self.anc_frame, text="Level 5 - Maximum",
                                            text_color="#c7ccd4", font=("Segoe UI", 12))
        self.anc_level_label.pack(anchor="w")

        self.wind_switch = ctk.CTkSwitch(mode_card, text="Wind Noise Reduction",
                                         command=self._on_wind, progress_color=ACCENT,
                                         font=("Segoe UI", 12))
        self.wind_switch.pack(anchor="w", padx=16, pady=(0, 14))

        # --- EQ card ---
        eq_card = self._card()
        self._section(eq_card, "Equalizer")
        names = [proto.EQ_PRESET_NAMES[i] for i in range(len(proto.EQ_PRESET_NAMES))]
        preset_row = ctk.CTkFrame(eq_card, fg_color="transparent")
        preset_row.pack(fill="x", padx=16, pady=(2, 8))
        self.eq_menu = ctk.CTkOptionMenu(preset_row, values=names, command=self._on_eq_preset,
                                         fg_color=CARD2, button_color=ACCENT,
                                         button_hover_color=ACCENT_HOVER, width=260)
        self.eq_menu.set("Soundcore Signature")
        self.eq_menu.pack(side="left")
        ctk.CTkButton(preset_row, text="Dolby", width=80, fg_color=CARD2,
                      hover_color="#2d313b", command=self._on_dolby).pack(side="left", padx=8)

        # custom 8-band
        self._section(eq_card, "Custom EQ", small=True)
        band_wrap = ctk.CTkFrame(eq_card, fg_color="transparent")
        band_wrap.pack(fill="x", padx=16, pady=(0, 6))
        self.band_sliders = []
        band_freqs = ["100", "200", "400", "800", "1.6k", "3.2k", "6.4k", "12k"]
        for i, f in enumerate(band_freqs):
            col = ctk.CTkFrame(band_wrap, fg_color="transparent")
            col.pack(side="left", expand=True, fill="x", padx=3)
            s = ctk.CTkSlider(col, from_=0, to=240, number_of_steps=240, orientation="vertical",
                              height=120, command=lambda v, idx=i: self._on_band(idx, v),
                              progress_color=ACCENT, button_color=ACCENT)
            s.set(120)  # 0x78 neutral
            s.pack()
            ctk.CTkLabel(col, text=f, text_color="#8a8f98", font=("Segoe UI", 10)).pack()
            self.band_sliders.append(s)
        ctk.CTkButton(eq_card, text="Apply Custom EQ", fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=self._apply_custom_eq).pack(
            padx=16, pady=(4, 14), anchor="e")

        # --- log line ---
        self.log_label = ctk.CTkLabel(self, text="", text_color="#6b7079",
                                      font=("Consolas", 11), anchor="w")
        self.log_label.pack(fill="x", padx=22, pady=(0, 10))

        self._set_controls_enabled(False)

    def _card(self):
        c = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        c.pack(fill="x", padx=20, pady=8)
        return c

    def _section(self, parent, text, small=False):
        ctk.CTkLabel(parent, text=text,
                     font=("Segoe UI Semibold", 12 if small else 15),
                     text_color="#e6e9ef" if not small else "#8a8f98").pack(
            anchor="w", padx=16, pady=(12 if not small else 6, 2))

    # -------------------------------------------------------------- helpers
    def log(self, msg, color="#6b7079"):
        self.after(0, lambda: self.log_label.configure(text=msg, text_color=color))

    def _set_controls_enabled(self, on):
        state = "normal" if on else "disabled"
        for w in (self.mode_seg, self.anc_slider, self.wind_switch,
                  self.eq_menu, *self.band_sliders, *self.preset_buttons):
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _run_bg(self, fn, *args):
        def worker():
            try:
                fn(*args)
            except Exception as e:
                self.log(f"Error: {e}", ERR)
                traceback.print_exc()
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------- devices
    def refresh_devices(self):
        self.log("Scanning for paired devices...")
        def work():
            devs = bt.list_paired_audio_devices()
            # Connected devices first; Soundcore before others.
            devs.sort(key=lambda d: (not d["connected"], "soundcore" not in d["name"].lower()))
            self.devices = devs
            labels = [
                f"{d['name']}  ({d['mac']})" + ("  \u2713 connected" if d["connected"] else "  \u2014 not connected")
                for d in devs
            ] or ["(no paired audio devices)"]
            def apply():
                self.device_menu.configure(values=labels)
                # Best pick: a CONNECTED soundcore (prefer Q20i), else first connected,
                # else the last one used, else first in list.
                def score(d):
                    n = d["name"].lower()
                    return (
                        0 if (d["connected"] and "q20i" in n) else
                        1 if (d["connected"] and "soundcore" in n) else
                        2 if d["connected"] else 3
                    )
                best = min(self.devices, key=score) if self.devices else None
                if getattr(self, "last_mac", None):
                    best = next((d for d in self.devices if d["mac"] == self.last_mac), best)
                if best:
                    pick = next(l for l, d in zip(labels, self.devices) if d["mac"] == best["mac"])
                    self.device_menu.set(pick)
                    self._on_device_pick(pick)
                self.log(f"Found {len(devs)} paired audio device(s).")
            self.after(0, apply)
        self._run_bg(work)

    def _on_device_pick(self, label):
        for d in self.devices:
            if d["mac"] in label:
                self.selected_mac = d["mac"]
                return
        self.selected_mac = None

    # ------------------------------------------------------------- connect
    def toggle_connect(self):
        if self.conn:
            self._run_bg(self._do_disconnect)
        else:
            if not self.selected_mac:
                self.log("Pick a paired device first.", WARN)
                return
            self._run_bg(self._do_connect, self.selected_mac)

    def _do_connect(self, mac):
        self.log(f"Connecting to {mac} ...", ACCENT)
        self.after(0, lambda: self.connect_btn.configure(text="Connecting...", state="disabled"))
        conn = bt.SoundcoreConnection(
            on_battery=self._on_battery, on_mode=self._on_mode_update,
            on_device_info=self._on_info, log=lambda *a: self.log(" ".join(map(str, a))))
        try:
            conn.connect(mac)
        except Exception as e:  # noqa: BLE001 - surface everything, restore UI always
            self.conn = None
            msg = str(e).strip() or type(e).__name__
            if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__:
                msg = ("Timed out. Make sure THIS headphone is connected in Windows "
                       "Bluetooth first (it must show 'Connected'), not just paired.")
            self.after(0, lambda: self.connect_btn.configure(text="Connect", state="normal"))
            self.after(0, lambda m=msg: self._set_status(False, "Connection failed"))
            self.log(f"Connect failed: {msg}", ERR)
            return
        self.conn = conn
        self.last_mac = mac
        self.after(0, lambda: self.connect_btn.configure(text="Disconnect", state="normal"))
        self.after(0, lambda: self._set_status(True, "Connected"))
        self.after(0, lambda: self._set_controls_enabled(True))
        self.log("Connected. Controls are live.", OKGREEN)

    def _do_disconnect(self):
        if self.conn:
            self.conn.disconnect()
        self.conn = None
        self.after(0, lambda: self.connect_btn.configure(text="Connect", state="normal"))
        self.after(0, lambda: self._set_status(False, "Not connected"))
        self.after(0, lambda: self.battery_label.configure(text=""))
        self.after(0, lambda: self._set_controls_enabled(False))
        self.log("Disconnected.")

    def _set_status(self, connected, text):
        self.status_dot.configure(text_color=OKGREEN if connected else ERR)
        self.status_label.configure(text=text)

    # --------------------------------------------------------- device cbks
    def _on_battery(self, batt):
        if not batt:
            return
        parts = []
        if batt.get("single") is not None:
            parts.append(f"{batt['single']}%")
        elif batt.get("left") is not None and batt.get("right") is not None:
            parts.append(f"L {batt['left']}%   R {batt['right']}%")
        elif batt.get("case") is not None:
            parts.append(f"{batt['case']}%")
        txt = "  \U0001F50B " + "   ".join(parts) if parts else ""
        self.after(0, lambda: self.battery_label.configure(text=txt))

    def _on_mode_update(self, mode):
        m = mode.get("mode")
        label = {"anc": "Noise Cancelling", "transparency": "Transparency",
                 "normal": "Normal"}.get(m)
        if label:
            self.after(0, lambda: self.mode_seg.set(label))

    def _on_info(self, info):
        self.log(f"Device: model {info.get('model_num','?')} fw {info.get('firmware_left','?')}")

    # ------------------------------------------------------------- control
    def _guard(self):
        if not self.conn:
            self.log("Connect first.", WARN)
            return False
        return True

    def _apply_scene(self, sp):
        """Apply a one-tap scene preset: ambient mode + EQ (+ options)."""
        if not self._guard():
            return
        mode = sp["mode"]
        anc = sp.get("anc", "level5")
        wind = sp.get("wind", False)
        eq = sp["eq"]

        def run():
            self.conn.set_mode(mode, anc, "vocal", wind)
            import time as _t
            _t.sleep(0.25)
            if eq == "dolby":
                self.conn.set_dolby()
            else:
                self.conn.set_eq_preset(eq)

        self._run_bg(run)
        # reflect in the UI
        seg = {"anc": "Noise Cancelling", "transparency": "Transparency", "normal": "Normal"}[mode]
        self.current_mode = mode
        self.current_anc_level = anc
        self.wind_on = wind
        self.after(0, lambda: self.mode_seg.set(seg))
        if mode == "anc":
            self.after(0, lambda: self.anc_slider.set(int(anc[-1])))
            self.after(0, lambda: self.wind_switch.select() if wind else self.wind_switch.deselect())
        eq_name = "Dolby" if eq == "dolby" else proto.EQ_PRESET_NAMES.get(eq, "")
        if eq != "dolby":
            self.after(0, lambda n=eq_name: self.eq_menu.set(n))
        self.log(f"Scene: {sp['name']} \u2014 {sp['desc']}", OKGREEN)

    def _on_mode_seg(self, value):
        if not self._guard():
            return
        mapping = {"Noise Cancelling": "anc", "Transparency": "transparency", "Normal": "normal"}
        self.current_mode = mapping[value]
        show_anc = self.current_mode == "anc"
        self.anc_frame.pack_configure() if show_anc else None
        self._run_bg(lambda: self.conn.set_mode(
            self.current_mode, self.current_anc_level, "vocal", self.wind_on))
        self.log(f"Mode -> {value}", ACCENT)

    def _on_anc_slider(self, v):
        self.current_anc_level = ANC_LEVELS[int(round(v)) - 1]
        self.anc_level_label.configure(text=f"Level {ANC_LEVEL_LABELS[self.current_anc_level]}")
        if self.conn and self.current_mode == "anc":
            self._run_bg(lambda: self.conn.set_mode(
                "anc", self.current_anc_level, "vocal", self.wind_on))

    def _on_wind(self):
        self.wind_on = bool(self.wind_switch.get())
        if self.conn and self.current_mode == "anc":
            self._run_bg(lambda: self.conn.set_mode(
                "anc", self.current_anc_level, "vocal", self.wind_on))

    def _on_eq_preset(self, name):
        if not self._guard():
            return
        pid = next((k for k, v in proto.EQ_PRESET_NAMES.items() if v == name), 0)
        self._run_bg(lambda: self.conn.set_eq_preset(pid))
        # reflect preset on the custom sliders
        self.log(f"EQ -> {name}", ACCENT)

    def _on_dolby(self):
        if not self._guard():
            return
        self._run_bg(lambda: self.conn.set_dolby())
        self.log("EQ -> Dolby", ACCENT)

    def _on_band(self, idx, v):
        self.custom_bands[idx] = int(round(v))

    def _apply_custom_eq(self):
        if not self._guard():
            return
        self._run_bg(lambda: self.conn.set_custom_eq(self.custom_bands))
        self.log("Applied custom EQ.", ACCENT)

    def on_close(self):
        try:
            if self.conn:
                self.conn.disconnect()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = SoundcoreApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
