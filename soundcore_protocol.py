"""
Soundcore BR/EDR RFCOMM protocol (reverse-engineered).

Ported faithfully from the open-source CoreSound project
(github.com/CriticalRange/CoreSound, GPL-3.0) which derived these
commands from btsnoop Bluetooth captures of the official Soundcore app.

Frame format (host -> device), RFCOMM channel 15:
    [08 EE 00 00 00] [cat:1] [type:1] [total_len:2 LE] [payload:N] [checksum:1]
    total_len = 10 + len(payload)   (counts 0x08 .. checksum inclusive)
    checksum  = sum(bytes from 0x08 through end of payload) & 0xFF

Device -> host responses carry header: 09 FF 00 00 01 ...

These commands are confirmed for the Q-series over-ear ANC headphones
(mode 0x06:0x81, EQ 0x03:0x87). The Soundcore Q20i (model code A3004,
over-ear) supports the 3 ambient modes and 22 EQ presets this module exposes.
"""

RFCOMM_CHANNEL = 15

# ---------------------------------------------------------------------------
# Frame building
# ---------------------------------------------------------------------------

def build_command(category: int, type_: int, payload: bytes = b"") -> bytes:
    p = bytes(payload)
    total_len = 10 + len(p)
    body = bytes([
        0x08, 0xEE, 0x00, 0x00, 0x00,
        category, type_,
        total_len & 0xFF, (total_len >> 8) & 0xFF,
    ]) + p
    checksum = sum(body) & 0xFF
    return body + bytes([checksum])


def build_device_info_query() -> bytes:
    return build_command(0x01, 0x01, b"")


# ---------------------------------------------------------------------------
# Ambient sound mode (ANC / Transparency / Normal)
# ---------------------------------------------------------------------------
# Mode mapping for this firmware family: 0x00=ANC, 0x01=Transparency, 0x02=Normal
MODES = {"normal": 0x02, "anc": 0x00, "transparent": 0x01, "transparency": 0x01}
MODES_REVERSE = {0x00: "anc", 0x01: "transparency", 0x02: "normal"}

# scene_byte = (anc_sub_mode << 4) | transparency_sub_mode
# High nibble = ANC sub-mode; 0x5 (Maximum) confirmed from btsnoop.
ANC_SCENES = {
    "level1": 0x1, "level2": 0x2, "level3": 0x3, "level4": 0x4, "level5": 0x5,
    "plane": 0x6, "car": 0x7,
}
# Low nibble = Transparency sub-mode
TRANSPARENCY_SCENES = {"fully": 0x0, "vocal": 0x1}


def build_mode_command(mode: str, anc_scene: str = "level5",
                       transparency_scene: str = "vocal",
                       wind_enabled: bool = False) -> bytes:
    mode_val = MODES.get(mode)
    if mode_val is None:
        raise ValueError(f"Unknown mode: {mode}")
    anc_val = ANC_SCENES.get(anc_scene, ANC_SCENES["level5"])
    trans_val = TRANSPARENCY_SCENES.get(transparency_scene, TRANSPARENCY_SCENES["vocal"])
    scene_byte = (anc_val << 4) | trans_val
    wind_byte = 0x01 if wind_enabled else 0x00
    return build_command(0x06, 0x81, bytes([mode_val, scene_byte, 0x01, 0x00, wind_byte, 0x00, 0x03]))


# ---------------------------------------------------------------------------
# EQ — 22 captured preset curves + custom 8-band
# ---------------------------------------------------------------------------
# 114-byte EQ curve payloads captured from btsnoop logs, indexed by preset id.
EQ_CURVES = {
    0: "0000000078787878787878787800787878787878787878000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c780078007800780078007800780078007800000078007800780078007800780078007800780000000000",
    1: "01000000a0828c8ca0a0a08c7800a0828c8ca0a0a08c78000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3ca00082008c008c00a000a000a0008c0078000000a00082008c008c00a000a000a0008c00780000000000",
    2: "02000000a0968278787878787800a09682787878787878000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3ca000960082007800780078007800780078000000a0009600820078007800780078007800780000000000",
    3: "03000000505a6e78787878787800505a6e787878787878000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c50005a006e00780078007800780078007800000050005a006e0078007800780078007800780000000000",
    4: "0400000096966464788c96a0780096966464788c96a078000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c960096006400640078008c009600a00078000000960096006400640078008c009600a000780000000000",
    5: "050000005a8ca0a0968c786478005a8ca0a0968c786478000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c5a008c00a000a00096008c0078006400780000005a008c00a000a00096008c0078006400780000000000",
    6: "060000008c5a6e828c8c825a78008c5a6e828c8c825a78000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c8c005a006e0082008c008c0082005a00780000008c005a006e0082008c008c0082005a00780000000000",
    7: "070000008c8296968c64504678008c8296968c64504678000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c8c008200960096008c00640050004600780000008c008200960096008c00640050004600780000000000",
    8: "08000000968c648c828c96967800968c648c828c969678000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c96008c0064008c0082008c00960096007800000096008c0064008c0082008c0096009600780000000000",
    9: "0900000064646e7878786464780064646e787878646478000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c640064006e007800780078006400640078000000640064006e0078007800780064006400780000000000",
    10: "0a0000008c966e6e8c6e8c9678008c966e6e8c6e8c9678000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c8c0096006e006e008c006e008c009600780000008c0096006e006e008c006e008c009600780000000000",
    11: "0b0000008c8c6464788c96a078008c8c6464788c96a078000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c8c008c006400640078008c009600a000780000008c008c006400640078008c009600a000780000000000",
    12: "0c00000078786464647896aa780078786464647896aa78000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c7800780064006400640078009600aa00780000007800780064006400640078009600aa00780000000000",
    13: "0d0000006e8ca09678648c8278006e8ca09678648c8278000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c6e008c00a0009600780064008c008200780000006e008c00a0009600780064008c008200780000000000",
    14: "0e0000007896968ca0aa96a078007896968ca0aa96a078000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c7800960096008c00a000aa009600a000780000007800960096008c00a000aa009600a000780000000000",
    15: "0f0000006e829696826e645a78006e829696826e645a78000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c6e0082009600960082006e0064005a00780000006e0082009600960082006e0064005a00780000000000",
    16: "10000000b48c64648c9696a07800b48c64648c9696a078000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3cb4008c00640064008c0096009600a00078000000b4008c00640064008c0096009600a000780000000000",
    17: "11000000968c6e6e8296a0aa7800968c6e6e8296a0aa78000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c96008c006e006e0082009600a000aa007800000096008c006e006e0082009600a000aa00780000000000",
    18: "12000000a0968278645a50507800a0968278645a505078000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3ca00096008200780064005a005000500078000000a00096008200780064005a0050005000780000000000",
    19: "130000005a64828c8c82785a78005a64828c8c82785a78000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c5a00640082008c008c00820078005a00780000005a00640082008c008c00820078005a00780000000000",
    20: "140000006464646e828c8ca078006464646e828c8ca078000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c6400640064006e0082008c008c00a000780000006400640064006e0082008c008c00a000780000000000",
    21: "15000000787878645a50503c7800787878645a50503c78000001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c78007800780064005a00500050003c007800000078007800780064005a00500050003c00780000000000",
}

# Captured Dolby Audio EQ packet (preset id 0x0000FEFE)
DOLBY_EQ_HEX = "fefe0000646464738e9fa1af7878646464738e9fa1af78780001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c64006400640073008e009f00a100af007800780064006400640073008e009f00a100af00780078000000"

# Fixed firmware block (bytes 24-71) identical across all captured EQ packets
_CEQ_FIXED = bytes.fromhex(
    "0001007b7776788c9592963c3c7b7776788c9592963c3c69fa0964017b7776788c9592963c3c7b7776788c9592963c3c"
)  # 48 bytes


def build_eq_command(preset_id: int) -> bytes:
    curve = EQ_CURVES.get(preset_id)
    if curve:
        return build_command(0x03, 0x87, bytes.fromhex(curve))
    payload = bytearray(114)
    payload[0] = preset_id & 0xFF
    return build_command(0x03, 0x87, bytes(payload))


def build_dolby_command() -> bytes:
    return build_command(0x03, 0x87, bytes.fromhex(DOLBY_EQ_HEX))


def build_custom_eq_command(bands) -> bytes:
    """bands: iterable of 8 byte values (0x00..0xFF, 0x78 = 0 dB neutral)."""
    bands = list(bands)[:8]
    bands += [0x78] * (8 - len(bands))
    buf = bytearray(114)
    buf[0] = 0xFF  # custom marker
    for i in range(8):
        buf[4 + i] = bands[i]
    buf[12] = 0x78
    for i in range(8):
        buf[14 + i] = bands[i]
    buf[22] = 0x78
    buf[24:24 + len(_CEQ_FIXED)] = _CEQ_FIXED
    for i in range(8):
        buf[72 + i * 2] = bands[i]
        buf[73 + i * 2] = 0x00
    buf[88] = 0x78
    for i in range(8):
        buf[92 + i * 2] = bands[i]
        buf[93 + i * 2] = 0x00
    buf[108] = 0x78
    return build_command(0x03, 0x87, bytes(buf))


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_soundcore_frames(buf: bytes):
    """Return (frames, remaining). frames = list of (cat, type, payload)."""
    frames = []
    i = 0
    n = len(buf)
    while i < n:
        if i + 10 > n:
            break
        if not (buf[i] == 0x09 and buf[i + 1] == 0xFF and buf[i + 2] == 0x00
                and buf[i + 3] == 0x00 and buf[i + 4] == 0x01):
            i += 1
            continue
        total_len = buf[i + 7] | (buf[i + 8] << 8)
        if total_len < 10:
            i += 1
            continue
        if i + total_len > n:
            break
        frames.append((buf[i + 5], buf[i + 6], buf[i + 9:i + 9 + total_len - 10]))
        i += total_len
    return frames, buf[i:]


def _normalize_battery_value(v):
    if 0 <= v <= 100:
        return v
    return None


def extract_battery(payload: bytes):
    if not payload or len(payload) < 3:
        return None
    if len(payload) >= 43:
        trip = {
            "left": _normalize_battery_value(payload[40]),
            "right": _normalize_battery_value(payload[41]),
            "case": _normalize_battery_value(payload[42]),
        }
        valid = sum(1 for v in trip.values() if v is not None)
        plausible = sum(1 for v in trip.values() if v is not None and v >= 5)
        if valid >= 2 and plausible >= 1:
            return trip
    markers = [(0x01, 0xFE, 0xFE), (0x02, 0x02, 0x00), (0x01, 0xFF, 0xFF), (0x00, 0xFE, 0xFE)]
    for i in range(0, len(payload) - 5):
        for m in markers:
            if payload[i] == m[0] and payload[i + 1] == m[1] and payload[i + 2] == m[2]:
                trip = {
                    "left": _normalize_battery_value(payload[i + 3]),
                    "right": _normalize_battery_value(payload[i + 4]),
                    "case": _normalize_battery_value(payload[i + 5]),
                }
                valid = sum(1 for v in trip.values() if v is not None)
                plausible = sum(1 for v in trip.values() if v is not None and v >= 5)
                if valid >= 2 and plausible >= 1:
                    return trip
    return None


def extract_single_battery(payload: bytes):
    """One-piece headphones (Q20i/A3004) report ONE battery value, encoded as a
    0-5 level in the high nibble of a status byte that follows the EQ-curve
    terminator '78 78'. Observed: '78 78 02 51 ..' where 0x51 -> level 5 -> ~90-100%.

    Returns {'single': percent} or None. Level->percent map is approximate
    because the device only exposes 5 bars, matching what Windows shows.
    """
    level_pct = {0: 5, 1: 20, 2: 40, 3: 60, 4: 80, 5: 95}
    for i in range(len(payload) - 3):
        if payload[i] == 0x78 and payload[i + 1] == 0x78:
            status = payload[i + 3]            # byte after the 1-byte gap
            level = (status >> 4) & 0x0F
            if level in level_pct:
                return {"single": level_pct[level], "level": level}
    return None


def extract_mode(payload: bytes):
    if len(payload) < 5:
        return None
    scene_byte = payload[1]
    anc_val = scene_byte >> 4
    trans_val = scene_byte & 0x0F
    anc_scene = next((k for k, v in ANC_SCENES.items() if v == anc_val), "level5")
    trans_scene = next((k for k, v in TRANSPARENCY_SCENES.items() if v == trans_val), "vocal")
    return {
        "mode": MODES_REVERSE.get(payload[0], "normal"),
        "anc_scene": anc_scene,
        "trans_scene": trans_scene,
        "wind_enabled": payload[4] == 0x01,
    }


def extract_mode_from_info(payload: bytes):
    """Some Q-series headphones (incl. Q20i/A3004) report ambient mode inside the
    cat=01 type=01 device-info frame rather than a standalone 06:01 frame.

    Observed Q20i info tail (ANC active):
        .. 78 78 00 <MODE> 01 00 00 00 01 00 01 01 03 04 03 ..
    where <MODE> byte uses the same 0x00=ANC / 0x01=Transparency / 0x02=Normal
    mapping as the mode command. We locate the '78 78 00' EQ terminator and read
    the mode byte that follows.
    """
    for i in range(len(payload) - 4):
        if payload[i] == 0x78 and payload[i + 1] == 0x78 and payload[i + 2] == 0x00:
            mode_byte = payload[i + 3]
            if mode_byte in MODES_REVERSE:
                return {"mode": MODES_REVERSE[mode_byte]}
    return None


def extract_device_info(payload: bytes):
    if not payload or len(payload) < 37:
        return None
    for start in range(0, len(payload) - 30):
        chunk = payload[start:start + 31]
        if any(b < 0x20 or b >= 0x7F for b in chunk):
            continue
        s = chunk.decode("ascii", errors="ignore")
        if len(s) < 31 or not (s[0:2].isdigit() and s[2] == "."):
            continue
        model = s[10:14]
        mac = s[14:26]
        try:
            int(model, 16)
            int(mac, 16)
        except ValueError:
            continue
        return {
            "firmware_left": s[0:5],
            "firmware_right": s[5:10],
            "model_num": model.lower(),
            "mac": ":".join(mac[i:i + 2] for i in range(0, 12, 2)).upper(),
            "hardware_rev": s[26:31],
        }
    return None


# Human-readable EQ preset names (match the official app's catalogue order).
EQ_PRESET_NAMES = {
    0: "Soundcore Signature",
    1: "Acoustic",
    2: "Bass Booster",
    3: "Bass Reducer",
    4: "Classical",
    5: "Dance",
    6: "Deep",
    7: "Electronic",
    8: "Flat",
    9: "Hip-Hop",
    10: "Jazz",
    11: "Latin",
    12: "Lounge",
    13: "Piano",
    14: "Pop",
    15: "R&B",
    16: "Rock",
    17: "Small Speakers",
    18: "Spoken Word",
    19: "Treble Booster",
    20: "Treble Reducer",
    21: "Podcast",
}


if __name__ == "__main__":
    # Self-test: verify checksum/length math against the documented frame format.
    cmd = build_mode_command("anc", "level5")
    print("ANC mode cmd:", cmd.hex())
    assert cmd[0:5] == bytes([0x08, 0xEE, 0x00, 0x00, 0x00])
    assert cmd[5] == 0x06 and cmd[6] == 0x81
    assert cmd[7] == (len(cmd) & 0xFF)
    assert cmd[-1] == (sum(cmd[:-1]) & 0xFF)
    eq = build_eq_command(2)  # Bass Booster
    print("EQ Bass Booster cmd len:", len(eq), "checksum ok:", eq[-1] == (sum(eq[:-1]) & 0xFF))
    info = build_device_info_query()
    print("Info query:", info.hex())
    print("All frame assertions passed.")
