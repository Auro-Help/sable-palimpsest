#!/usr/bin/env python3
# Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
"""Load USDA FoodData Central Branded Foods into the backend's
barcode_usda table (schema V7), the second
barcode source next to Open Food Facts.

Usage:
    python3 build_usda_branded.py --max-foods 50000            # trial: CSV only
    python3 build_usda_branded.py                              # full run: CSV only
    BARCODE_DATABASE_URL=postgres://... python3 build_usda_branded.py          # full run + load
    BARCODE_DATABASE_URL=postgres://... python3 build_usda_branded.py --load-only
    python3 build_usda_branded.py --input FoodData_Central_branded_food_json_2026-04-30.zip

Without --input the latest Branded Foods JSON release is downloaded (about
200 MB zipped; the JSON inside is about 3.3 GB but is read straight out of
the zip, one food per line, so only the zip touches the disk) into
data/usda/, and deleted after the run unless --keep-zip.

Every branded food with a gtinUpc is kept, from every market and category,
with values exactly as USDA has them. Nutrition comes from foodNutrients,
which for branded foods is per 100 g, or per 100 ml when the serving is
measured in ml (servingSizeUnit; nutrition_per says which, null for any
other unit); labelNutrients (per serving) isn't used. Energy is kcal
(nutrient 1008) and kJ (1062) when present, sugars are Total Sugars (2000,
else 1063 Sugars, Total NLEA), sodium mg (1093). A nutrient in an
unexpected unit is left null. Dates are written as ISO (YYYY-MM-DD).

Codes: rows are keyed like Open Food Facts' codes, so the backend's barcode
spellings find both: digits-only GTINs have their leading zeros removed down
to 8 digits, then are zero-padded to 13 when 9 to 12 digits long (UPC-A
034000722297 -> 0034000722297); a GTIN that isn't 1 to 14 digits is kept as
written (stripped), and gtin_upc always holds USDA's own spelling. USDA lists
some GTINs more than once (re-submissions); the table keeps one per code,
the latest available_date, then modified_date, then fdc_id.

Trust signals (see trust.py): energy_consistent, and trust 'high' (manufacturer
data) unless implausible or energy-inconsistent ('low').

Loading reuses build_barcode_db.py's chunked, resumable load with its disk
guard (--volume-mb / --min-free-mb): rows are upserted per chunk and rows no
longer in the release are deleted at the end. Apply backend/schema first so
the table exists.

License: USDA FoodData Central data is public domain (CC0 1.0); citing
"USDA FoodData Central" is requested, not required.
"""

import argparse
import csv
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.request
import zipfile
from collections import Counter

# Sibling modules load from this folder even under python -I, which leaves
# the script's own directory off sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trust  # noqa: E402
from build_barcode_db import USER_AGENT, Target, load, num, text

DOWNLOAD_PAGE = "https://fdc.nal.usda.gov/download-datasets"
DATASETS_URL = "https://fdc.nal.usda.gov/fdc-datasets/"
HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "data", "barcode_usda.csv.gz")
ZIP_DIR = os.path.join(HERE, "data", "usda")

COLUMNS = ("code", "gtin_upc", "fdc_id", "name", "brand_owner", "brand_name",
           "energy_kcal", "energy_kj", "protein_g", "carbs_g", "fat_g", "fiber_g",
           "sugar_g", "sodium_mg", "nutrition_per", "serving_size", "serving_size_unit",
           "household_serving", "category", "market_country", "data_source",
           "preparation_state", "last_modified", "available_date",
           "energy_consistent", "trust")

USDA_TARGET = Target("barcode_usda", COLUMNS, "barcode_usda_load_chunks")

# column -> ([FDC nutrient ids, first found wins], expected unitName)
_NUTRIENTS = {
    "energy_kcal": ((1008,), "kcal"),
    "energy_kj": ((1062,), "kJ"),
    "protein_g": ((1003,), "g"),
    "carbs_g": ((1005,), "g"),
    "fat_g": ((1004,), "g"),
    "fiber_g": ((1079,), "g"),
    "sugar_g": ((2000, 1063), "g"),
    "sodium_mg": ((1093,), "mg"),
}
_PER = {"g": "100g", "gm": "100g", "grm": "100g", "ml": "100ml", "mlt": "100ml"}
_DIGITS = re.compile(r"^[0-9]{1,14}$")
_DECODER = json.JSONDecoder()


def normalize_code(gtin):
    """The table key for a GTIN (see the module docstring), or None."""
    raw = text(gtin)
    if raw is None:
        return None
    code = raw.strip()
    if not _DIGITS.match(code):
        return code
    while len(code) > 8 and code[0] == "0":
        code = code[1:]
    if 9 <= len(code) <= 12:
        code = code.zfill(13)
    return code


def iso_date(value):
    """USDA's M/D/YYYY (or ISO) date as YYYY-MM-DD, or None."""
    value = text(value)
    if value is None:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def nutrients(food, stats):
    by_id = {}
    for fn in food.get("foodNutrients") or []:
        nutrient = fn.get("nutrient") or {}
        if nutrient.get("id") is not None and nutrient["id"] not in by_id:
            by_id[nutrient["id"]] = (num(fn.get("amount")), nutrient.get("unitName"))
    values = []
    for column, (ids, unit) in _NUTRIENTS.items():
        value = None
        for nid in ids:
            if nid in by_id and by_id[nid][0] is not None:
                amount, got = by_id[nid]
                if got == unit:
                    value = amount
                else:
                    stats[f"{column} in {got} (left null)"] += 1
                break
        values.append(value)
    return values


def extract(food, stats):
    code = normalize_code(food.get("gtinUpc"))
    if code is None:
        return None
    values = nutrients(food, stats)
    unit = text(food.get("servingSizeUnit"))
    per = _PER.get(unit.strip().lower()) if unit else None
    if unit and per is None:
        stats[f"serving unit {unit} (nutrition_per null)"] += 1
    kcal, kj, protein, carbs, fat, fiber = values[:6]
    consistent = trust.energy_consistent(kcal, kj, protein, carbs, fat, fiber)
    level = trust.usda_trust(values, consistent)
    return (
        code,
        text(food.get("gtinUpc")).strip(),
        food.get("fdcId") if isinstance(food.get("fdcId"), int) else None,
        text(food.get("description")),
        text(food.get("brandOwner")),
        text(food.get("brandName")),
        *values,
        per,
        num(food.get("servingSize")),
        unit,
        text(food.get("householdServingFullText")),
        text(food.get("brandedFoodCategory")),
        text(food.get("marketCountry")),
        text(food.get("dataSource")),
        text(food.get("preparationStateCode")),
        iso_date(food.get("modifiedDate")),
        iso_date(food.get("availableDate")),
        consistent,
        level,
    )


def foods(zip_path, max_foods=None):
    """Each branded food in the release's JSON, read from inside the zip.
    The file is {"BrandedFoods": [ with one food per line."""
    with zipfile.ZipFile(zip_path) as z:
        member = next(n for n in z.namelist() if n.endswith(".json"))
        with z.open(member) as f:
            n = 0
            for line in f:
                line = line.strip().rstrip(b",")
                if not line.startswith(b"{") or line.startswith(b'{"BrandedFoods"'):
                    continue
                # raw_decode: the last food line also closes the array (...}]}).
                yield _DECODER.raw_decode(line.decode("utf-8"))[0]
                n += 1
                if max_foods and n >= max_foods:
                    return


def latest_zip_url():
    req = urllib.request.Request(DOWNLOAD_PAGE, headers={"User-Agent": USER_AGENT})
    page = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    names = sorted(set(re.findall(r"FoodData_Central_branded_food_json_\d{4}-\d{2}-\d{2}\.zip", page)))
    if not names:
        sys.exit(f"Error: no Branded Foods JSON release found on {DOWNLOAD_PAGE}")
    return DATASETS_URL + names[-1]


def download(url, folder):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, url.rsplit("/", 1)[1])
    if os.path.exists(path):
        print(f"Using {path}")
        return path
    print(f"Downloading {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp, open(path + ".part", "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    os.replace(path + ".part", path)
    return path


def best_by_code(zip_path, max_foods, stats):
    """code -> fdc_id of the food kept for it (pass 1)."""
    best = {}
    for food in foods(zip_path, max_foods):
        stats["foods"] += 1
        code = normalize_code(food.get("gtinUpc"))
        if code is None:
            continue
        key = (iso_date(food.get("availableDate")) or "", iso_date(food.get("modifiedDate")) or "",
               food.get("fdcId") or 0)
        if code not in best or key > best[code][0]:
            best[code] = (key, food.get("fdcId"))
    return {code: fdc for code, (_, fdc) in best.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", help="local Branded Foods JSON release zip (default: download the latest)")
    ap.add_argument("--keep-zip", action="store_true", help="keep the downloaded zip in data/usda/")
    ap.add_argument("--csv", default=CSV_PATH, help=f"gzipped CSV output (default {CSV_PATH})")
    ap.add_argument("--database-url", default=os.environ.get("BARCODE_DATABASE_URL"),
                    help="postgres:// URL to load into (default $BARCODE_DATABASE_URL)")
    ap.add_argument("--max-foods", type=int, help="stop after this many foods (trial run)")
    ap.add_argument("--volume-mb", type=int,
                    help="size of the database's volume; with it, the load stops while free "
                         "space is under --min-free-mb")
    ap.add_argument("--min-free-mb", type=int, default=1500,
                    help="free space to keep on the volume (default 1500)")
    ap.add_argument("--load-only", action="store_true",
                    help="skip the release; load the existing --csv into the database")
    args = ap.parse_args()
    if args.max_foods and args.database_url:
        ap.error("--max-foods is for trial runs; loading one would delete every row it missed")
    if args.load_only:
        if not args.database_url:
            ap.error("--load-only needs --database-url or BARCODE_DATABASE_URL")
        if not os.path.exists(args.csv):
            ap.error(f"{args.csv} not found; run without --load-only first")
        load(args.csv, args.database_url, args.volume_mb, args.min_free_mb, USDA_TARGET)
        return

    zip_path = args.input or download(latest_zip_url(), ZIP_DIR)
    stats = Counter()
    t0 = time.time()
    print(f"Pass 1: picking one food per code from {zip_path} ...")
    keep = best_by_code(zip_path, args.max_foods, stats)
    print(f"  {stats['foods']:,} foods, {len(keep):,} codes ({time.time() - t0:.0f}s)")

    print("Pass 2: writing ...")
    os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
    written = set()
    with gzip.open(args.csv, "wt", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(COLUMNS)
        for food in foods(zip_path, args.max_foods):
            row = extract(food, stats)
            if row is None:
                stats["no gtinUpc"] += 1
                continue
            code = row[0]
            if keep.get(code) != food.get("fdcId") or code in written:
                stats["older duplicate of a code"] += 1
                continue
            written.add(code)
            w.writerow(["" if v is None else ("t" if v is True else "f" if v is False else v)
                        for v in row])
            r = dict(zip(COLUMNS, row))
            stats[f"trust {r['trust']}"] += 1
            stats[f"data_source {r['data_source']}"] += 1
            stats[f"energy_consistent {r['energy_consistent']}"] += 1
            if not _DIGITS.match(code):
                stats["code not 1-14 digits (kept as written)"] += 1
            if r["market_country"] != "United States":
                stats["market outside the United States"] += 1
    print(f"Done in {time.time() - t0:.0f}s")
    print(f"\nWrote {args.csv} ({os.path.getsize(args.csv) / 1e6:.0f} MB gzipped)")
    print(f"  {len(written):,} products")
    stats.pop("foods", None)
    for k, n in stats.most_common():
        print(f"  {k}: {n:,}")

    if not args.input and not args.keep_zip:
        os.remove(zip_path)
    if args.database_url:
        load(args.csv, args.database_url, args.volume_mb, args.min_free_mb, USDA_TARGET)
    else:
        print("\nNot loaded: pass --database-url (or set BARCODE_DATABASE_URL) to load it.")


if __name__ == "__main__":
    main()
