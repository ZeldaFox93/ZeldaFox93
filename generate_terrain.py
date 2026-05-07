#!/usr/bin/env python3
"""Generate a SimCity 4 terrain preview PNG (400x300) for Sims 2 import."""

import json
import math
import os
import struct
import zlib
from pathlib import Path


CONFIG_PATH = Path(__file__).parent / "terrain_config.json"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Heightmap generation
# ---------------------------------------------------------------------------

def make_heightmap(cfg: dict, w: int, h: int) -> list[list[float]]:
    """Return a 2-D list of height values in [0, 1]."""
    hm_cfg = cfg["heightmap"]
    sea    = hm_cfg["sea_level"]
    plains = hm_cfg["plains_level"]
    peak   = hm_cfg["hills_peak"]
    valley_d = hm_cfg["valley_depth"]

    grid = [[plains / 100.0] * w for _ in range(h)]

    # Beach strip at the bottom
    beach_zone = next(z for z in cfg["zones"]["beach"])
    by0 = int(beach_zone["y"] / 1024 * h)
    for y in range(by0, h):
        t = (y - by0) / max(h - by0, 1)
        for x in range(w):
            grid[y][x] = (sea + (hm_cfg["beach_level"] - sea) * (1 - t)) / 100.0

    # Forest hills (top-right)
    for zone in cfg["zones"]["forest"]:
        zx = int(zone["x"] / 1024 * w)
        zy = int(zone["y"] / 1024 * h)
        zw = int(zone["width"] / 1024 * w)
        zh = int(zone["height"] / 1024 * h)
        cx, cy = zx + zw // 2, zy + zh // 2
        for y in range(max(0, zy), min(h, zy + zh)):
            for x in range(max(0, zx), min(w, zx + zw)):
                dx = (x - cx) / (zw / 2)
                dy = (y - cy) / (zh / 2)
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 1.0:
                    bump = peak / 100.0 * (1 - dist)
                    grid[y][x] = max(grid[y][x], plains / 100.0 + bump * 0.6)

    # Valley (centre-bottom of inland area)
    for zone in cfg["zones"]["valley"]:
        zx = int(zone["x"] / 1024 * w)
        zy = int(zone["y"] / 1024 * h)
        zw = int(zone["width"] / 1024 * w)
        zh = int(zone["height"] / 1024 * h)
        cx, cy = zx + zw // 2, zy + zh // 2
        for y in range(max(0, zy), min(h, zy + zh)):
            for x in range(max(0, zx), min(w, zx + zw)):
                dx = (x - cx) / (zw / 2)
                dy = (y - cy) / (zh / 2)
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 1.0:
                    dip = valley_d / 100.0 * (1 - dist)
                    grid[y][x] = max(0.0, grid[y][x] - dip)

    return grid


# ---------------------------------------------------------------------------
# Colour mapping
# ---------------------------------------------------------------------------

def height_to_rgb(h: float) -> tuple[int, int, int]:
    """Map normalised height to an RGB terrain colour."""
    if h < 0.13:   return (65,  105, 225)   # deep water
    if h < 0.15:   return (135, 170, 210)   # shallow water
    if h < 0.17:   return (240, 220, 160)   # beach/sand
    if h < 0.35:   return (120, 170,  90)   # lowland grass
    if h < 0.55:   return ( 80, 130,  60)   # midland grass
    if h < 0.75:   return ( 90, 100,  70)   # high ground
    return                (160, 150, 140)   # rocky peak


def draw_road(pixels: list[list[tuple]], x0: int, y0: int,
              x1: int, y1: int, pw: int, h: int, w: int) -> None:
    """Bresenham line into pixels grid (scaled coords)."""
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for i in range(steps + 1):
        t  = i / steps
        px = int(x0 + (x1 - x0) * t)
        py = int(y0 + (y1 - y0) * t)
        for dy in range(-pw, pw + 1):
            for dx in range(-pw, pw + 1):
                nx, ny = px + dx, py + dy
                if 0 <= nx < w and 0 <= ny < h:
                    pixels[ny][nx] = (60, 60, 60)


def draw_roundabout(pixels: list[list[tuple]], cx: int, cy: int,
                    r: int, w: int, h: int) -> None:
    for angle_deg in range(0, 360):
        a = math.radians(angle_deg)
        for ri in range(r - 2, r + 3):
            px = int(cx + ri * math.cos(a))
            py = int(cy + ri * math.sin(a))
            if 0 <= px < w and 0 <= py < h:
                pixels[py][px] = (60, 60, 60)


def draw_bridge(pixels: list[list[tuple]], sx: int, sy: int,
                ex: int, ey: int, w: int, h: int) -> None:
    steps = max(abs(ex - sx), abs(ey - sy), 1)
    for i in range(steps + 1):
        t  = i / steps
        px = int(sx + (ex - sx) * t)
        py = int(sy + (ey - sy) * t)
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                nx, ny = px + dx, py + dy
                if 0 <= nx < w and 0 <= ny < h:
                    pixels[ny][nx] = (100, 80, 60)


# ---------------------------------------------------------------------------
# PNG encoder (stdlib only)
# ---------------------------------------------------------------------------

def _png_chunk(name: bytes, data: bytes) -> bytes:
    c   = zlib.crc32(name + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", c)


def write_png(path: str, pixels: list[list[tuple]], w: int, h: int) -> None:
    raw = b""
    for row in pixels:
        raw += b"\x00"  # filter type None
        for r, g, b in row:
            raw += bytes([r, g, b])

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw, 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", idat))
        f.write(_png_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_preview(cfg: dict) -> str:
    pw = cfg["preview"]["width"]
    ph = cfg["preview"]["height"]

    hmap = make_heightmap(cfg, pw, ph)
    pixels: list[list[tuple]] = [
        [height_to_rgb(hmap[y][x]) for x in range(pw)]
        for y in range(ph)
    ]

    # Scale road coords from 1024-space to preview-space
    def sx(v): return int(v / 1024 * pw)
    def sy(v): return int(v / 1024 * ph)

    for road in cfg["roads"]["main_roads"]:
        draw_road(pixels,
                  sx(road["from"][0]), sy(road["from"][1]),
                  sx(road["to"][0]),   sy(road["to"][1]),
                  1, ph, pw)

    ra = cfg["roads"]["roundabout"]
    draw_roundabout(pixels,
                    sx(ra["center_x"]), sy(ra["center_y"]),
                    max(2, int(ra["radius"] / 1024 * pw)),
                    pw, ph)

    br = cfg["bridge"]
    draw_bridge(pixels,
                sx(br["start"][0]), sy(br["start"][1]),
                sx(br["end"][0]),   sy(br["end"][1]),
                pw, ph)

    out = Path(__file__).parent / "terrains" / "previews" / cfg["preview"]["filename"]
    write_png(str(out), pixels, pw, ph)
    return str(out)


def write_sc4_stub(cfg: dict) -> str:
    """Write a minimal .sc4 placeholder (real SC4 files need SimCity 4)."""
    out = Path(__file__).parent / cfg["sc4_output"]
    out.parent.mkdir(parents=True, exist_ok=True)
    header = b"SC4T"                                    # 4-byte magic
    name   = cfg["terrain_name"].encode("utf-8")
    payload = (
        header
        + struct.pack(">I", len(name))
        + name
        + struct.pack(">II",
                      cfg["dimensions"]["width"],
                      cfg["dimensions"]["height"])
        + b"\x00" * 32                                  # reserved
    )
    with open(out, "wb") as f:
        f.write(payload)
    return str(out)


def main() -> None:
    cfg = load_config()

    preview_path = generate_preview(cfg)
    print(f"[OK] Preview PNG  -> {preview_path}")

    sc4_path = write_sc4_stub(cfg)
    print(f"[OK] SC4 stub     -> {sc4_path}")

    print()
    print("Next step – copy to Sims 2:")
    target = Path.home() / cfg["sims2_target_dir"]
    print(f"  cp {sc4_path}    '{target}/'")
    print(f"  cp {preview_path} '{target}/'")


if __name__ == "__main__":
    main()
