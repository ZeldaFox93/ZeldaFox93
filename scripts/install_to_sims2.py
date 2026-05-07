#!/usr/bin/env python3
"""Copy the generated SC4 terrain and preview PNG into the Sims 2 terrain folder."""

import json
import os
import shutil
import sys
from pathlib import Path

ROOT   = Path(__file__).parent.parent
CONFIG = ROOT / "terrain_config.json"


def find_sims2_dir(rel_target: str) -> Path | None:
    candidates = [
        Path.home() / rel_target,
        Path("C:/") / "Users" / os.environ.get("USERNAME", "User") / "Documents" / "EA Games" / "The Sims 2" / "SC4 Terrains",
        Path("/mnt/c/Users") / os.environ.get("USERNAME", "User") / "Documents" / "EA Games" / "The Sims 2" / "SC4 Terrains",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def main() -> None:
    with open(CONFIG) as f:
        cfg = json.load(f)

    sc4_src     = ROOT / cfg["sc4_output"]
    preview_src = ROOT / "terrains" / "previews" / cfg["preview"]["filename"]

    for path in (sc4_src, preview_src):
        if not path.exists():
            print(f"[ERROR] Missing file: {path}")
            print("        Run `python generate_terrain.py` first.")
            sys.exit(1)

    target = find_sims2_dir(cfg["sims2_target_dir"])
    if target is None:
        print("[WARN] Sims 2 SC4 Terrains folder not found automatically.")
        answer = input("Enter full path to SC4 Terrains folder (or press Enter to skip): ").strip()
        if not answer:
            print("Skipping copy. Files are at:")
            print(f"  {sc4_src}")
            print(f"  {preview_src}")
            return
        target = Path(answer)

    target.mkdir(parents=True, exist_ok=True)

    shutil.copy2(sc4_src,     target / sc4_src.name)
    shutil.copy2(preview_src, target / preview_src.name)

    print(f"[OK] Copied terrain files to:\n     {target}")
    print()
    print("Open The Sims 2, go to Create-A-Neighborhood and look for:")
    print(f"  '{cfg['terrain_name']}'")


if __name__ == "__main__":
    main()
