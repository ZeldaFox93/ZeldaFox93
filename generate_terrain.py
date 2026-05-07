#!/usr/bin/env python3
"""
Generate a SimCity 4 terrain file (.sc4) and preview PNG for Sims 2 import.

The .sc4 file contains three sections only:
  HMAP  – 64×64 uint16 heightmap (terrain shape, no structures)
  ROAD  – road segments + roundabout + bridge (infrastructure only)
  ZONE  – 64×64 uint8 zone-type grid (empty lot-slot designations only)

No buildings, no objects, no lot contents.
"""

import json
import math
import struct
import zlib
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "terrain_config.json"
TILES = 64  # SC4 terrain resolution


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Zone type constants (match terrain_config.json zone_types)
# ---------------------------------------------------------------------------
Z_EMPTY    = 0x00
Z_RES_LOW  = 0x01
Z_RES_MED  = 0x02
Z_ROAD     = 0x10
Z_BEACH    = 0x20
Z_FOREST   = 0x30


# ---------------------------------------------------------------------------
# Heightmap (HMAP section)  —  values in [0, 65535]
# ---------------------------------------------------------------------------

def build_heightmap(cfg: dict) -> list[list[int]]:
    hm   = cfg["terrain_shape"]["heightmap"]
    sea  = hm["sea_level"]
    bch  = hm["beach_level"]
    pln  = hm["plains_level"]
    peak = hm["hills_peak"]
    vdep = hm["valley_depth"]
    scale = 65535 // 100

    grid = [[pln * scale] * TILES for _ in range(TILES)]

    beach = cfg["terrain_shape"]["beach"]
    by0, by1 = beach["tile_y_start"], beach["tile_y_end"]
    for ty in range(by0, TILES):
        t = (ty - by0) / max(TILES - by0, 1)
        val = int((bch + (sea - bch) * t) * scale)
        for tx in range(TILES):
            grid[ty][tx] = val

    fh = cfg["terrain_shape"]["forest_hills"]
    fcx = fh["tile_x"] + fh["tile_w"] // 2
    fcy = fh["tile_y"] + fh["tile_h"] // 2
    for ty in range(fh["tile_y"], fh["tile_y"] + fh["tile_h"]):
        for tx in range(fh["tile_x"], fh["tile_x"] + fh["tile_w"]):
            dx = (tx - fcx) / (fh["tile_w"] / 2)
            dy = (ty - fcy) / (fh["tile_h"] / 2)
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 1.0:
                bump = int(peak * scale * 0.6 * (1 - dist))
                grid[ty][tx] = min(65535, grid[ty][tx] + bump)

    vl = cfg["terrain_shape"]["valley"]
    vcx = vl["tile_x"] + vl["tile_w"] // 2
    vcy = vl["tile_y"] + vl["tile_h"] // 2
    for ty in range(vl["tile_y"], vl["tile_y"] + vl["tile_h"]):
        for tx in range(vl["tile_x"], vl["tile_x"] + vl["tile_w"]):
            dx = (tx - vcx) / (vl["tile_w"] / 2)
            dy = (ty - vcy) / (vl["tile_h"] / 2)
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 1.0:
                dip = int(vdep * scale * (1 - dist))
                grid[ty][tx] = max(0, grid[ty][tx] - dip)

    return grid


def encode_hmap(hmap: list[list[int]]) -> bytes:
    """Pack the heightmap as big-endian uint16 row-major."""
    data = bytearray()
    for row in hmap:
        for v in row:
            data += struct.pack(">H", v)
    return bytes(data)


# ---------------------------------------------------------------------------
# Zone map (ZONE section)  —  empty lot-slot designations, no buildings
# ---------------------------------------------------------------------------

def build_zone_map(cfg: dict, hmap: list[list[int]]) -> list[list[int]]:
    zmap = [[Z_EMPTY] * TILES for _ in range(TILES)]

    beach = cfg["terrain_shape"]["beach"]
    for ty in range(beach["tile_y_start"], TILES):
        for tx in range(TILES):
            zmap[ty][tx] = Z_BEACH

    fh = cfg["terrain_shape"]["forest_hills"]
    for ty in range(fh["tile_y"], fh["tile_y"] + fh["tile_h"]):
        for tx in range(fh["tile_x"], fh["tile_x"] + fh["tile_w"]):
            zmap[ty][tx] = Z_FOREST

    for slot in cfg["lot_slots"].get("residential_low", []):
        for ty in range(slot["tile_y"], slot["tile_y"] + slot["tile_w"]):
            for tx in range(slot["tile_x"], slot["tile_x"] + slot["tile_w"]):
                if 0 <= ty < TILES and 0 <= tx < TILES:
                    zmap[ty][tx] = Z_RES_LOW

    for slot in cfg["lot_slots"].get("residential_med", []):
        for ty in range(slot["tile_y"], slot["tile_y"] + slot["tile_h"]):
            for tx in range(slot["tile_x"], slot["tile_x"] + slot["tile_w"]):
                if 0 <= ty < TILES and 0 <= tx < TILES:
                    zmap[ty][tx] = Z_RES_MED

    return zmap


def encode_zone(zmap: list[list[int]]) -> bytes:
    """Pack zone map as one uint8 per tile, row-major."""
    return bytes(v for row in zmap for v in row)


# ---------------------------------------------------------------------------
# Road network (ROAD section)
# ---------------------------------------------------------------------------

ROAD_TYPE_CODES = {"highway": 0x01, "avenue": 0x02, "street": 0x03}

def encode_roads(cfg: dict) -> bytes:
    roads = cfg["roads"]
    buf = bytearray()

    for seg in roads["segments"]:
        code = ROAD_TYPE_CODES.get(seg["type"], 0x02)
        buf += struct.pack(">BBhhhh",
                           0x01,          # record type: segment
                           code,
                           seg["from"][0], seg["from"][1],
                           seg["to"][0],   seg["to"][1])

    ra = roads["roundabout"]
    buf += struct.pack(">BBhhB",
                       0x02,              # record type: roundabout
                       ra["lanes"],
                       ra["center_tile"][0], ra["center_tile"][1],
                       ra["radius_tiles"])

    br = roads["bridge"]
    buf += struct.pack(">BBhhhh",
                       0x03,              # record type: bridge
                       0x01,             # arch type
                       br["from"][0], br["from"][1],
                       br["to"][0],   br["to"][1])

    return bytes(buf)


# ---------------------------------------------------------------------------
# SC4 file writer  —  sectioned binary format
# ---------------------------------------------------------------------------

def _section(tag: bytes, data: bytes) -> bytes:
    assert len(tag) == 4
    compressed = zlib.compress(data, 6)
    return (tag
            + struct.pack(">II", len(data), len(compressed))
            + compressed)


def write_sc4(path: str, cfg: dict,
              hmap: list[list[int]],
              zmap: list[list[int]]) -> None:
    name = cfg["terrain_name"].encode("utf-8")
    header = (
        b"SC4T"
        + struct.pack(">HH", 1, 0)               # version 1.0
        + struct.pack(">B", TILES)               # grid size
        + struct.pack(">I", len(name)) + name
    )

    body = (
        _section(b"HMAP", encode_hmap(hmap))
        + _section(b"ROAD", encode_roads(cfg))
        + _section(b"ZONE", encode_zone(zmap))
    )

    with open(path, "wb") as f:
        f.write(header + body)


# ---------------------------------------------------------------------------
# Preview PNG  (stdlib only — no Pillow needed)
# ---------------------------------------------------------------------------

def _png_chunk(name: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(name + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)


def write_png(path: str, pixels: list[list[tuple]], w: int, h: int) -> None:
    raw = bytearray()
    for row in pixels:
        raw += b"\x00"
        for r, g, b in row:
            raw += bytes([r, g, b])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(_png_chunk(b"IEND", b""))


def zone_to_rgb(z: int, h_norm: float) -> tuple[int, int, int]:
    if z == Z_BEACH:   return (240, 220, 160)
    if z == Z_FOREST:  return ( 40, 100,  40)
    if z == Z_RES_LOW: return (160, 210, 130)   # pale green = empty residential slot
    if z == Z_RES_MED: return (120, 180, 100)
    # undeveloped — shade by height
    if h_norm < 0.13:  return ( 65, 105, 225)
    if h_norm < 0.15:  return (135, 170, 210)
    if h_norm < 0.35:  return (100, 150,  70)
    if h_norm < 0.60:  return ( 80, 120,  55)
    return                    (130, 120, 100)


def _line_tiles(x0, y0, x1, y1):
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for i in range(steps + 1):
        t = i / steps
        yield int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t)


def generate_preview(cfg: dict,
                     hmap: list[list[int]],
                     zmap: list[list[int]]) -> str:
    pw = cfg["preview"]["width"]
    ph = cfg["preview"]["height"]

    def tx(tile_x): return int(tile_x / TILES * pw)
    def ty(tile_y): return int(tile_y / TILES * ph)

    def set_px(pixels, px, py, colour, half=1):
        for dy in range(-half, half + 1):
            for dx in range(-half, half + 1):
                nx, ny = px + dx, py + dy
                if 0 <= nx < pw and 0 <= ny < ph:
                    pixels[ny][nx] = colour

    pixels = [
        [zone_to_rgb(zmap[int(y / ph * TILES)][int(x / pw * TILES)],
                     hmap[int(y / ph * TILES)][int(x / pw * TILES)] / 65535)
         for x in range(pw)]
        for y in range(ph)
    ]

    road_colour   = (50, 50, 50)
    bridge_colour = (120, 90, 60)

    for seg in cfg["roads"]["segments"]:
        for ttx, tty in _line_tiles(*seg["from"], *seg["to"]):
            set_px(pixels, tx(ttx), ty(tty), road_colour)

    ra = cfg["roads"]["roundabout"]
    cx, cy, r = ra["center_tile"][0], ra["center_tile"][1], ra["radius_tiles"]
    for deg in range(0, 360, 2):
        a  = math.radians(deg)
        rx = int(cx + r * math.cos(a))
        ry = int(cy + r * math.sin(a))
        set_px(pixels, tx(rx), ty(ry), road_colour, 0)

    br = cfg["roads"]["bridge"]
    for ttx, tty in _line_tiles(*br["from"], *br["to"]):
        set_px(pixels, tx(ttx), ty(tty), bridge_colour, 0)

    out = Path(__file__).parent / "terrains" / "previews" / cfg["preview"]["filename"]
    write_png(str(out), pixels, pw, ph)
    return str(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg  = load_config()
    hmap = build_heightmap(cfg)
    zmap = build_zone_map(cfg, hmap)

    sc4_path = Path(__file__).parent / cfg["sc4_output"]
    sc4_path.parent.mkdir(parents=True, exist_ok=True)
    write_sc4(str(sc4_path), cfg, hmap, zmap)
    print(f"[OK] SC4 terrain  -> {sc4_path}")
    print(f"     Sections: HMAP ({TILES}x{TILES} uint16) | ROAD | ZONE ({TILES}x{TILES} uint8)")
    print(f"     No buildings encoded — lot slots are empty zone designations only.")

    preview_path = generate_preview(cfg, hmap, zmap)
    print(f"[OK] Preview PNG  -> {preview_path}")

    print()
    target = Path.home() / cfg["sims2_target_dir"]
    print("To install, copy both files to Sims 2:")
    print(f"  {sc4_path}")
    print(f"  {preview_path}")
    print(f"  -> '{target}/'")


if __name__ == "__main__":
    main()
