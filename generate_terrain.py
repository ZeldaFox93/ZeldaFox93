#!/usr/bin/env python3
"""
Generate a SimCity 4 terrain file (.sc4) and preview PNG for Sims 2 import.

The .sc4 file contains three sections only:
  HMAP  – 64×64 uint16 heightmap  (terrain shape, no structures)
  ROAD  – road segments + roundabout + bridge
  ZONE  – 64×64 uint8 zone-type grid (empty lot-slot designations only)

Lot positions are saved to lot_placements.json for reference.
No buildings, no objects, no lot contents are stored.
"""

import json
import math
import struct
import zlib
from pathlib import Path

ROOT        = Path(__file__).parent
CONFIG_PATH = ROOT / "terrain_config.json"
CATALOG_PATH= ROOT / "lots_catalog.json"
PLACEMENTS_PATH = ROOT / "lot_placements.json"
TILES = 64                   # lot grid (64×64 lots, zone map, road coords)
TERRAIN_VERTS = TILES + 1   # 65 — SC4 small city: one vertex per lot corner (16m/vertex)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)

def load_catalog() -> dict:
    with open(CATALOG_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Zone type codes
# ---------------------------------------------------------------------------
Z_EMPTY    = 0x00
Z_RES      = 0x01
Z_COMM     = 0x03
Z_SVC      = 0x04
Z_SPORT    = 0x05
Z_EDU      = 0x06
Z_URBAN    = 0x07
Z_SPECIAL  = 0x08
Z_BEACH_F  = 0x09
Z_FOREST_F = 0x0A
Z_ROAD     = 0x10
Z_BRIDGE   = 0x11
Z_BEACH    = 0x20
Z_FOREST   = 0x30

CATEGORY_CODE = {
    "urban_building": Z_URBAN,
    "commercial":     Z_COMM,
    "service":        Z_SVC,
    "sports":         Z_SPORT,
    "education":      Z_EDU,
    "residential":    Z_RES,
    "special":        Z_SPECIAL,
    "beach":          Z_BEACH_F,
    "forest":         Z_FOREST_F,
    "bridge":         Z_BRIDGE,
}


# ---------------------------------------------------------------------------
# Lot catalog expansion
# ---------------------------------------------------------------------------

def expand_catalog(catalog: dict) -> list[dict]:
    """Expand count-based entries (MAISON×200) into individual lot records."""
    lots = []
    for entry in catalog["lots"]:
        count = entry.get("count", 1)
        for i in range(1, count + 1):
            lot = dict(entry)
            lot.pop("count", None)
            if count > 1:
                lot["id"] = f"{entry['id']}-{i:03d}"
            lots.append(lot)
    return lots


# ---------------------------------------------------------------------------
# Lot placement
# ---------------------------------------------------------------------------

def _can_place(occupied: set, tx: int, ty: int, w: int, h: int) -> bool:
    if tx + w > TILES or ty + h > TILES:
        return False
    return all((tx + dx, ty + dy) not in occupied
               for dy in range(h) for dx in range(w))


def _mark(occupied: set, tx: int, ty: int, w: int, h: int) -> None:
    for dy in range(h):
        for dx in range(w):
            occupied.add((tx + dx, ty + dy))


def place_urban_buildings(ring_cfg: dict, urban_lots: list[dict],
                          occupied: set) -> list[dict]:
    placed = []
    positions = ring_cfg["positions"]
    for lot, (tx, ty) in zip(urban_lots, positions):
        w, h = lot["w"], lot["h"]
        if _can_place(occupied, tx, ty, w, h):
            _mark(occupied, tx, ty, w, h)
            placed.append({**lot, "tx": tx, "ty": ty})
        else:
            print(f"  [WARN] {lot['id']} blocked at ({tx},{ty}), skipping")
    return placed


def pack_zone(lots: list[dict], zone: dict, occupied: set) -> list[dict]:
    """Left-to-right, top-to-bottom greedy packer within a rectangle.
    Cursor persists between lots so the zone fills sequentially."""
    placed = []
    x, y   = zone["x1"], zone["y1"]
    x2, y2 = zone["x2"], zone["y2"]

    for lot in lots:
        w, h = lot["w"], lot["h"]
        placed_this = False
        # Scan forward from current cursor position
        scan_x, scan_y = x, y
        while scan_y + h <= y2:
            if scan_x + w > x2:
                scan_x  = zone["x1"]
                scan_y += h
                continue
            if _can_place(occupied, scan_x, scan_y, w, h):
                _mark(occupied, scan_x, scan_y, w, h)
                placed.append({**lot, "tx": scan_x, "ty": scan_y})
                x, y = scan_x + w, scan_y
                placed_this = True
                break
            scan_x += 1

        if not placed_this:
            print(f"  [WARN] No space for {lot['id']} in zone {zone}")

    return placed


def place_all_lots(cfg: dict, all_lots: list[dict]) -> list[dict]:
    occupied: set = set()
    placed_all: list[dict] = []

    by_cat: dict[str, list] = {}
    for lot in all_lots:
        by_cat.setdefault(lot["category"], []).append(lot)

    # 1. Urban buildings — ring placement
    ring_placed = place_urban_buildings(
        cfg["urban_ring"],
        by_cat.get("urban_building", []),
        occupied,
    )
    placed_all.extend(ring_placed)
    print(f"  urban_building : {len(ring_placed)} lots placed")

    # 2. All single-zone categories
    single_zones = ["commercial", "service", "education", "sports",
                    "special", "beach", "forest"]
    for cat in single_zones:
        zone_key = cat
        if zone_key not in cfg["placement_zones"]:
            continue
        zone = cfg["placement_zones"][zone_key]
        lots = by_cat.get(cat, [])
        p = pack_zone(lots, zone, occupied)
        placed_all.extend(p)
        print(f"  {cat:<16}: {len(p)}/{len(lots)} lots placed")

    # 3. Bridge — midpoint of the bridge road segment
    bridge_lots = by_cat.get("bridge", [])
    br = cfg["roads"]["bridge"]
    mid_x = (br["from"][0] + br["to"][0]) // 2
    mid_y = (br["from"][1] + br["to"][1]) // 2
    for lot in bridge_lots:
        placed_this = False
        for tx, ty in [(mid_x, mid_y), (mid_x - 1, mid_y),
                       (mid_x, mid_y + 1), (mid_x - 1, mid_y + 1)]:
            if _can_place(occupied, tx, ty, lot["w"], lot["h"]):
                _mark(occupied, tx, ty, lot["w"], lot["h"])
                placed_all.append({**lot, "tx": tx, "ty": ty})
                print(f"  bridge         : {lot['id']} at ({tx},{ty})")
                placed_this = True
                break
        if not placed_this:
            print(f"  [WARN] PONT could not be placed near bridge midpoint ({mid_x},{mid_y})")

    # 4. Residential — multiple zones, fill up to count
    res_zones = cfg["placement_zones"]["residential"]
    res_lots  = list(by_cat.get("residential", []))  # 200 houses
    total_placed = 0
    for zone in res_zones:
        if not res_lots:
            break
        p = pack_zone(res_lots, zone, occupied)
        placed_all.extend(p)
        placed_ids = {lot["id"] for lot in p}
        res_lots = [l for l in res_lots if l["id"] not in placed_ids]
        total_placed += len(p)
    print(f"  residential    : {total_placed}/200 lots placed")

    return placed_all


# ---------------------------------------------------------------------------
# Heightmap (HMAP section)
# ---------------------------------------------------------------------------

def build_heightmap(cfg: dict) -> list[list[int]]:
    hm    = cfg["terrain_shape"]["heightmap"]
    sea   = hm["sea_level"]
    bch   = hm["beach_level"]
    pln   = hm["plains_level"]
    peak  = hm["hills_peak"]
    vdep  = hm["valley_depth"]
    scale = 65535 // 100

    grid = [[pln * scale] * TILES for _ in range(TILES)]

    beach = cfg["terrain_shape"]["beach"]
    by0 = beach["tile_y_start"]
    for ty in range(by0, TILES):
        t   = (ty - by0) / max(TILES - by0, 1)
        val = int((bch + (sea - bch) * t) * scale)
        for tx in range(TILES):
            grid[ty][tx] = val

    fh  = cfg["terrain_shape"]["forest_hills"]
    fcx = fh["tile_x"] + fh["tile_w"] // 2
    fcy = fh["tile_y"] + fh["tile_h"] // 2
    for ty in range(fh["tile_y"], fh["tile_y"] + fh["tile_h"]):
        for tx in range(fh["tile_x"], fh["tile_x"] + fh["tile_w"]):
            if 0 <= ty < TILES and 0 <= tx < TILES:
                dx   = (tx - fcx) / (fh["tile_w"] / 2)
                dy   = (ty - fcy) / (fh["tile_h"] / 2)
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 1.0:
                    bump = int(peak * scale * 0.6 * (1 - dist))
                    grid[ty][tx] = min(65535, grid[ty][tx] + bump)

    vl  = cfg["terrain_shape"]["valley"]
    vcx = vl["tile_x"] + vl["tile_w"] // 2
    vcy = vl["tile_y"] + vl["tile_h"] // 2
    for ty in range(vl["tile_y"], vl["tile_y"] + vl["tile_h"]):
        for tx in range(vl["tile_x"], vl["tile_x"] + vl["tile_w"]):
            if 0 <= ty < TILES and 0 <= tx < TILES:
                dx   = (tx - vcx) / (vl["tile_w"] / 2)
                dy   = (ty - vcy) / (vl["tile_h"] / 2)
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 1.0:
                    dip = int(vdep * scale * (1 - dist))
                    grid[ty][tx] = max(0, grid[ty][tx] - dip)

    return grid


def _bilinear_upsample(hmap: list[list[int]],
                       target: int) -> list[list[float]]:
    """
    Upsample a TILES×TILES uint16 heightmap to target×target float metres
    using bilinear interpolation.

    SC4 small city = 64 lots × 16 m = 1024 m.
    At 4 m/vertex: 1024/4 + 1 = 257 vertices → TERRAIN_VERTS = 257.
    Each source lot covers 4 target vertices (16m / 4m = 4).
    """
    src = TILES
    out = []
    for vy in range(target):
        row = []
        for vx in range(target):
            # Map vertex to fractional lot coordinate
            lx = vx / (target - 1) * (src - 1)
            ly = vy / (target - 1) * (src - 1)
            x0, y0 = int(lx), int(ly)
            x1 = min(x0 + 1, src - 1)
            y1 = min(y0 + 1, src - 1)
            fx, fy = lx - x0, ly - y0
            v = (hmap[y0][x0] * (1 - fx) * (1 - fy)
                 + hmap[y0][x1] * fx       * (1 - fy)
                 + hmap[y1][x0] * (1 - fx) * fy
                 + hmap[y1][x1] * fx       * fy)
            row.append(v / 65535.0 * 250.0)   # uint16 → metres
        out.append(row)
    return out


def encode_hmap(hmap: list[list[int]]) -> bytes:
    """Heights as float32 metres, little-endian, row-major (preview / internal use)."""
    data = bytearray()
    for row in hmap:
        for v in row:
            data += struct.pack("<f", v / 65535.0 * 250.0)
    return bytes(data)


# ---------------------------------------------------------------------------
# Zone map (ZONE section)
# ---------------------------------------------------------------------------

def build_zone_map(cfg: dict, placed_lots: list[dict]) -> list[list[int]]:
    zmap = [[Z_EMPTY] * TILES for _ in range(TILES)]

    beach = cfg["terrain_shape"]["beach"]
    for ty in range(beach["tile_y_start"], TILES):
        for tx in range(TILES):
            zmap[ty][tx] = Z_BEACH

    fh = cfg["terrain_shape"]["forest_hills"]
    for ty in range(fh["tile_y"], fh["tile_y"] + fh["tile_h"]):
        for tx in range(fh["tile_x"], fh["tile_x"] + fh["tile_w"]):
            if 0 <= ty < TILES and 0 <= tx < TILES:
                zmap[ty][tx] = Z_FOREST

    for lot in placed_lots:
        code = CATEGORY_CODE.get(lot["category"], Z_EMPTY)
        for dy in range(lot["h"]):
            for dx in range(lot["w"]):
                ty, tx = lot["ty"] + dy, lot["tx"] + dx
                if 0 <= ty < TILES and 0 <= tx < TILES:
                    zmap[ty][tx] = code

    return zmap


def encode_zone(zmap: list[list[int]]) -> bytes:
    """Zone type codes, one uint8 per tile, row-major."""
    return bytes(v for row in zmap for v in row)


# ---------------------------------------------------------------------------
# Road network (ROAD section)
# ---------------------------------------------------------------------------

ROAD_TYPE_CODES = {"highway": 0x01, "avenue": 0x02, "street": 0x03}

def encode_roads(cfg: dict) -> bytes:
    """Road network descriptor, little-endian."""
    roads = cfg["roads"]
    buf   = bytearray()

    for seg in roads["segments"]:
        code = ROAD_TYPE_CODES.get(seg["type"], 0x02)
        buf += struct.pack("<BBhhhh",
                           0x01, code,
                           seg["from"][0], seg["from"][1],
                           seg["to"][0],   seg["to"][1])

    ra = roads["roundabout"]
    buf += struct.pack("<BBhhB",
                       0x02, ra["lanes"],
                       ra["center_tile"][0], ra["center_tile"][1],
                       ra["radius_tiles"])

    br = roads["bridge"]
    buf += struct.pack("<BBhhhh",
                       0x03, 0x01,
                       br["from"][0], br["from"][1],
                       br["to"][0],   br["to"][1])

    return bytes(buf)


# ---------------------------------------------------------------------------
# DBPF 1.0 writer  (Maxis Database Packed File — the authentic SC4 format)
# ---------------------------------------------------------------------------
#
# Header layout (96 bytes, all fields little-endian):
#   0x00  4  magic "DBPF"
#   0x04  4  major version = 1
#   0x08  4  minor version = 0
#   0x0C  12 unknown/reserved (zeros)
#   0x18  4  date created  (Unix timestamp)
#   0x1C  4  date modified (Unix timestamp)
#   0x20  4  index major version = 7
#   0x24  4  index entry count
#   0x28  4  index offset (from file start)
#   0x2C  4  index size in bytes
#   0x30  4  hole count = 0
#   0x34  4  hole offset = 0
#   0x38  4  hole size   = 0
#   0x3C  4  unknown = 0
#   0x40  32 reserved zeros
#
# Index entry (20 bytes each):
#   type_id   group_id   instance_id   offset   size  (all uint32 LE)
#
# Sub-file type IDs (SC4 terrain, from community reverse-engineering):
#   0x2026960B  SC4_CITY_HEADER  — city name + grid size
#   0x29244C6B  SC4_TERRAIN_MAP  — float32 height values in metres
#   0x6534284A  SC4_ROAD_NET     — road segment descriptors
#   0x49B9E60A  SC4_ZONE_MAP     — zone-type byte grid
#
# Group 0xA9D3BABE is the default SC4 terrain group.
# ---------------------------------------------------------------------------

_DBPF_MAGIC   = b"DBPF"
_DBPF_MAJOR   = 1
_DBPF_MINOR   = 0
_DBPF_IDX_VER = 7

# RegionViewSubfile TGI (SC4Parser-documented, CA027EDB/CA027EE1/00000000)
REGION_VIEW_TYPE  = 0xCA027EDB
REGION_VIEW_GROUP = 0xCA027EE1
REGION_VIEW_INST  = 0x00000000

# TerrainMapSubfile TGI — 0x29244C6B is the TGI Sims 2 actually reads
# (confirmed: 257×257 at this TGI → Large City; 65×65 → Small City)
SC4_TERRAIN_MAP = 0x29244C6B
SC4_GROUP       = 0xA9D3BABE

SC4_ROAD_NET  = 0x6534284A
SC4_ZONE_MAP  = 0x49B9E60A


def _dbpf_header(n: int, idx_off: int, idx_sz: int, ts: int) -> bytes:
    h = (_DBPF_MAGIC
         + struct.pack("<II", _DBPF_MAJOR, _DBPF_MINOR)
         + b"\x00" * 12
         + struct.pack("<II", ts, ts)
         + struct.pack("<IIII", _DBPF_IDX_VER, n, idx_off, idx_sz)
         + struct.pack("<III", 0, 0, 0)
         + struct.pack("<I",   0)
         + b"\x00" * 32)
    assert len(h) == 96, f"DBPF header must be 96 bytes, got {len(h)}"
    return h


def _dbpf_entry(type_id: int, group_id: int, inst_id: int,
                offset: int, size: int) -> bytes:
    return struct.pack("<IIIII", type_id, group_id, inst_id, offset, size)


def _region_view_subfile(cfg: dict) -> bytes:
    """
    RegionViewSubfile (TGI CA027EDB/CA027EE1/00000000).

    This is what Sims 2 reads first to determine city size.
    CitySizeX/Y are stored as raw × 64: small city → raw = 1  (1 × 64 = 64 lots).
    Parse order matches SC4Parser RegionViewSubfile.Parse() exactly.
    """
    name = cfg["terrain_name"].encode("ascii")
    buf  = bytearray()

    buf += struct.pack("<HH", 1, 13)         # MajorVersion=1, MinorVersion=13 (Rush Hour)
    buf += struct.pack("<II", 0, 0)          # TileXLocation, TileYLocation
    buf += struct.pack("<II", 1, 1)          # CitySizeX raw=1 (×64=64), CitySizeY raw=1
    buf += struct.pack("<III", 0, 0, 0)      # ResidentialPop, CommercialPop, IndustrialPop

    # MinorVersion(13) > 9 → skip 4 unknown bytes
    buf += struct.pack("<I", 0)

    # MinorVersion(13) > 10 → MayorRating (1 byte)
    buf += struct.pack("<B", 0)

    buf += struct.pack("<BB", 0, 0)          # StarCount, TutorialFlag
    buf += struct.pack("<I", 0)              # CityGuid
    buf += b"\x00" * 20                     # 5 unknown uint32s (skipped in parser)
    buf += struct.pack("<B", 0)              # ModeFlag = 0 (God Mode)

    # String fields: uint32 length prefix + ASCII bytes
    for s in [name, b"", b"", b"", b""]:    # CityName, FormerCity, Mayor, Desc, DefaultMayor
        buf += struct.pack("<I", len(s)) + s

    buf += b"\x00" * 24                     # 6 unused uint32s (skipped in parser)
    buf += struct.pack("<III", 0, 0, 0)     # CurrentOccupancy, LimitsOccupancy, MaxOccupancy counts

    return bytes(buf)


def _sc4_terrain_subfile(hmap: list[list[int]]) -> bytes:
    """
    TerrainMapSubfile at TGI 0x29244C6B / 0xA9D3BABE.

    Sims 2 reads this TGI and extracts city size from the explicit width/height
    fields: TERRAIN_VERTS=65 → 65-1=64 lots per side → Small City.

    Format: version(u32) · width(u32) · height(u32) · float32[width×height] row-major.
    """
    verts = _bilinear_upsample(hmap, TERRAIN_VERTS)   # 64×64 → 65×65
    buf   = struct.pack("<III", 1, TERRAIN_VERTS, TERRAIN_VERTS)
    for row in verts:
        for v in row:
            buf += struct.pack("<f", v)
    return buf


def _sc4_zone_subfile(zmap: list[list[int]]) -> bytes:
    """Zone map: version · lot_w · lot_h · uint8[64×64] codes (one per lot)."""
    return struct.pack("<III", 1, TILES, TILES) + bytes(
        v for row in zmap for v in row)


def write_sc4(path: str, cfg: dict,
              hmap: list[list[int]],
              zmap: list[list[int]]) -> None:
    """Write a valid DBPF 1.0 SC4 terrain file readable by The Sims 2."""
    ts = 0  # DBPF timestamps unused by Sims 2; zeroing avoids spurious git diffs

    subfiles = [
        (REGION_VIEW_TYPE, REGION_VIEW_GROUP, REGION_VIEW_INST, _region_view_subfile(cfg)),
        (SC4_TERRAIN_MAP,  SC4_GROUP,         0x00000001,        _sc4_terrain_subfile(hmap)),
        (SC4_ROAD_NET,     SC4_GROUP,         0x00000001,        encode_roads(cfg)),
        (SC4_ZONE_MAP,     SC4_GROUP,         0x00000001,        _sc4_zone_subfile(zmap)),
    ]

    # Layout:  header (96 B)  |  subfile data...  |  index table
    data_blob    = b""
    index_entries: list[tuple] = []
    offset = 96
    for type_id, group_id, inst_id, data in subfiles:
        index_entries.append((type_id, group_id, inst_id, offset, len(data)))
        data_blob += data
        offset    += len(data)

    idx_off  = offset
    idx_blob = b"".join(_dbpf_entry(*e) for e in index_entries)
    idx_sz   = len(idx_blob)

    header = _dbpf_header(len(subfiles), idx_off, idx_sz, ts)

    with open(path, "wb") as f:
        f.write(header + data_blob + idx_blob)


# ---------------------------------------------------------------------------
# Preview PNG (stdlib only)
# ---------------------------------------------------------------------------

ZONE_COLOURS: dict[int, tuple | None] = {
    Z_URBAN:    (200, 130,  80),   # terra cotta — urban building slot
    Z_COMM:     (255, 200,  40),   # yellow      — commercial slot
    Z_SVC:      ( 80, 130, 220),   # blue        — service slot
    Z_SPORT:    ( 50, 190,  70),   # bright green— sports slot
    Z_EDU:      (180,  80, 200),   # purple      — education slot
    Z_RES:      (230, 215, 165),   # light tan   — residential slot
    Z_SPECIAL:  (220,  60, 160),   # magenta     — special slot
    Z_BEACH_F:  (245, 230, 140),   # pale sand   — beach facility slot
    Z_FOREST_F: ( 30,  90,  30),   # dark green  — forest facility slot
    Z_BRIDGE:   (120,  90,  60),   # brown       — bridge slot
    Z_ROAD:     ( 50,  50,  50),   # dark gray   — road
    Z_BEACH:    (240, 220, 155),   # sand        — natural beach
    Z_FOREST:   ( 40, 110,  40),   # green       — natural forest
    Z_EMPTY:    None,              # height-based colour
}

def _height_colour(h_norm: float) -> tuple:
    if h_norm < 0.13: return ( 65, 105, 225)
    if h_norm < 0.15: return (135, 170, 210)
    if h_norm < 0.17: return (240, 220, 160)
    if h_norm < 0.35: return (100, 155,  70)
    if h_norm < 0.60: return ( 75, 120,  55)
    return                   (130, 120, 100)


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


def _set_px(pixels, px, py, colour, pw: int, ph: int, half: int = 1) -> None:
    for dy in range(-half, half + 1):
        for dx in range(-half, half + 1):
            nx, ny = px + dx, py + dy
            if 0 <= nx < pw and 0 <= ny < ph:
                pixels[ny][nx] = colour


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

    def px(tx): return int(tx / TILES * pw)
    def py(ty): return int(ty / TILES * ph)

    pixels = []
    for y in range(ph):
        row = []
        for x in range(pw):
            ttx = min(int(x / pw * TILES), TILES - 1)
            tty = min(int(y / ph * TILES), TILES - 1)
            z   = zmap[tty][ttx]
            c   = ZONE_COLOURS.get(z)
            if c is None:
                c = _height_colour(hmap[tty][ttx] / 65535)
            row.append(c)
        pixels.append(row)

    road_c   = (50, 50, 50)
    bridge_c = (140, 100, 60)

    for seg in cfg["roads"]["segments"]:
        for ttx, tty in _line_tiles(*seg["from"], *seg["to"]):
            _set_px(pixels, px(ttx), py(tty), road_c, pw, ph, 0)

    ra = cfg["roads"]["roundabout"]
    cx, cy, r = ra["center_tile"][0], ra["center_tile"][1], ra["radius_tiles"]
    for deg in range(0, 360, 2):
        a = math.radians(deg)
        _set_px(pixels, px(int(cx + r * math.cos(a))),
                py(int(cy + r * math.sin(a))), road_c, pw, ph, 0)

    br = cfg["roads"]["bridge"]
    for ttx, tty in _line_tiles(*br["from"], *br["to"]):
        _set_px(pixels, px(ttx), py(tty), bridge_c, pw, ph, 0)

    out = ROOT / "terrains" / "previews" / cfg["preview"]["filename"]
    write_png(str(out), pixels, pw, ph)
    return str(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg     = load_config()
    catalog = load_catalog()

    print("Expanding catalog …")
    all_lots = expand_catalog(catalog)
    print(f"  {len(all_lots)} lots total")

    print("Placing lots …")
    placed = place_all_lots(cfg, all_lots)
    print(f"  {len(placed)} lots placed")

    with open(PLACEMENTS_PATH, "w") as f:
        json.dump({"total": len(placed), "lots": placed}, f, indent=2, ensure_ascii=False)
    print(f"[OK] Placements   -> {PLACEMENTS_PATH}")

    hmap = build_heightmap(cfg)
    zmap = build_zone_map(cfg, placed)

    sc4_path = ROOT / cfg["sc4_output"]
    sc4_path.parent.mkdir(parents=True, exist_ok=True)
    write_sc4(str(sc4_path), cfg, hmap, zmap)
    print(f"[OK] SC4 terrain  -> {sc4_path}")
    print(f"     REGION_VIEW : CitySizeX=64 CitySizeY=64 (raw=1×64, small city)")
    print(f"     TERRAIN_MAP : {TERRAIN_VERTS}×{TERRAIN_VERTS} (TGI 0x29244C6B, explicit dims, small city)")
    print(f"     ZONE_MAP    : {TILES}×{TILES} uint8 lot codes | ROAD_NET : road segments")
    print(f"     size_type=0 (small city, 64×64 lots, 1024 m × 1024 m)")

    preview_path = generate_preview(cfg, hmap, zmap)
    print(f"[OK] Preview PNG  -> {preview_path}")

    target = Path.home() / cfg["sims2_target_dir"]
    print(f"\nInstall: cp {sc4_path} '{target}/'")
    print(f"         cp {preview_path} '{target}/'")


if __name__ == "__main__":
    main()
