#!/usr/bin/env python3
# Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
"""Load every Open Food Facts product into the backend's
barcode_product table (schema V3, V4, V6 and V7).

Usage:
    python3 build_barcode_db.py --max-lines 200000             # trial: CSV only
    python3 build_barcode_db.py                                # full run: CSV only
    python3 build_barcode_db.py --database-url postgres://user:pass@host:5432/db
    python3 build_barcode_db.py --input off.jsonl.gz --database-url ...
    BARCODE_DATABASE_URL=postgres://... python3 build_barcode_db.py --load-only   # existing CSV

The dump is about 13 GB compressed. Streaming needs no disk space and
resumes after dropped connections; to rerun often, download it once and
pass --input instead:
    curl -L -o off.jsonl.gz \\
        https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz

Every product with a barcode is kept, worldwide, with values exactly as Open
Food Facts has them: no cleaning, no plausibility checks, no conversions
(except removing NUL characters, which Postgres can't store, and sodium,
which OFF records in grams and is stored in milligrams: sodium_mg = g x 1000,
rounded to 6 decimals to drop float noise; a new-format sodium value already
in mg is kept, one in any other unit is left null). Nutrition comes
from the old flat nutriments fields or, for newer products, from
nutrition.aggregated_set, stored with its basis (nutrition_per,
nutrition_preparation; null for the old format, which doesn't record it).
(energy is stored as both kcal and kJ, whichever OFF has). Sugars (g) and
sodium (mg) are per the same basis as the rest, and so is the serving:
serving_size is the label text, serving_g OFF's serving_quantity, as-is. Nutrition fields
are null when OFF has none or a non-numeric value. Names are kept in every
language OFF has, as a JSON map.

Trust signals (V7; see trust.py) are added per product, computed from what
OFF has, without changing any value: quality_errors (how many
data_quality_errors_tags; null when OFF has none recorded), nutrition_errors
(whether any of them is about nutrition), producer_data (supplied by the
manufacturer on the OFF producers platform: owner org-*, or a 'producers'
data source), nutrition_photo (a nutrition-table photo is selected),
last_modified (last_modified_t), energy_consistent (stated energy vs
4p + 4c + 9f + 2 fiber) and trust ('high' / 'medium' / 'low').

Always writes a gzipped CSV (--csv); with --database-url (or
BARCODE_DATABASE_URL) it also loads it with psql, in resumable chunks (rerun
after a dropped connection to continue; see load()): rows from this source
('off') are upserted, 'off' rows no longer in the dump are deleted, and rows
from any other source are left alone. Pass --volume-mb for a database on a
small volume, so the load stops before the disk fills. Apply backend/schema
first so the table exists.

The data is ODbL (Open Database License): the app must credit Open Food
Facts, and the 'off' rows must stay available under ODbL.
"""

import argparse
import csv
import gzip
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import datetime
import urllib.parse
import urllib.request
from collections import Counter

# Sibling modules load from this folder even under python -I, which leaves
# the script's own directory off sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trust  # noqa: E402

DUMP_URL = "https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz"
USER_AGENT = "sable-palimpsest/1.0 (https://github.com/Auro-Help/sable-palimpsest)"
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                        "barcode_product.csv.gz")
COLUMNS = ("code", "name", "lang", "names", "brand",
           "energy_kcal", "energy_kj", "protein_g", "carbs_g", "fat_g", "fiber_g",
           "sugar_g", "sodium_mg", "nutrition_per", "nutrition_preparation",
           "serving_size", "serving_g", "halal", "countries",
           "quality_errors", "nutrition_errors", "producer_data", "nutrition_photo",
           "last_modified", "energy_consistent", "trust")

# Newer OFF products keep nutrition in nutrition.aggregated_set instead of
# the flat nutriments *_100g fields: column -> (nutrient, {unit it may be
# in: factor to the column's unit}). Only sodium is converted (g -> mg).
_AGGREGATED = {
    "energy_kcal": ("energy-kcal", {"kcal": 1}),
    "energy_kj": ("energy-kj", {"kJ": 1}),
    "protein_g": ("proteins", {"g": 1}),
    "carbs_g": ("carbohydrates", {"g": 1}),
    "fat_g": ("fat", {"g": 1}),
    "fiber_g": ("fiber", {"g": 1}),
    "sugar_g": ("sugars", {"g": 1}),
    "sodium_mg": ("sodium", {"g": 1000, "mg": 1}),
}
_NUTRITION = tuple(_AGGREGATED) + ("nutrition_per", "nutrition_preparation")

# product_name_<lang> fields, e.g. product_name_en, product_name_pt_br;
# not the *_imported / *_debug_tags bookkeeping fields.
_NAME_FIELD_RE = re.compile(r"^product_name_([a-z]{2,3}(?:_[a-z]{2})?)$")


def text(value):
    """A string as-is, or None for missing / empty / non-string values.

    The one change: NUL characters are removed, since Postgres can't store
    them in text or JSONB (a handful of OFF names have them, as corruption).
    """
    if not isinstance(value, str):
        return None
    value = value.replace("\x00", "")
    return value if value.strip() else None


def num(value):
    """A finite number, or None. Strings that are numbers are accepted."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def g_to_mg(grams):
    """Sodium in grams (how OFF records it) to milligrams, or None."""
    return None if grams is None else round(grams * 1000, 6)


def nutrition(product, stats):
    """(kcal, kJ, protein, carbs, fat, fiber, sugar g, sodium mg, per,
    preparation), in _NUTRITION's order.

    Products in the old format have flat nutriments *_100g fields; their
    basis isn't recorded (OFF folds per-100 ml into them), so per and
    preparation are None; their sodium_100g is always grams (whatever
    sodium_unit the label was typed in), stored as mg. Products in the new
    format have nutrition.aggregated_set, whose values are stored with its
    own "per" (100g / 100ml) and "preparation" (as_sold / prepared). A value
    in an unexpected unit is left null rather than converted (sodium in g or
    mg is stored as mg).
    """
    nut = product.get("nutriments") or {}
    kj = num(nut.get("energy-kj_100g"))
    if kj is None:
        kj = num(nut.get("energy_100g"))  # OFF's generic energy field is kJ
    old = (num(nut.get("energy-kcal_100g")), kj, num(nut.get("proteins_100g")),
           num(nut.get("carbohydrates_100g")), num(nut.get("fat_100g")), num(nut.get("fiber_100g")),
           num(nut.get("sugars_100g")), g_to_mg(num(nut.get("sodium_100g"))))
    if any(v is not None for v in old):
        stats["nutrition from old format"] += 1
        return old + (None, None)

    agg = (product.get("nutrition") or {}).get("aggregated_set") or {}
    nutrients = agg.get("nutrients") or {}
    if not isinstance(nutrients, dict) or not nutrients:
        return (None,) * len(_NUTRITION)
    values = []
    for column, (key, units) in _AGGREGATED.items():
        entry = nutrients.get(key)
        if not isinstance(entry, dict) or num(entry.get("value")) is None:
            values.append(None)
        elif entry.get("unit") not in units:
            stats[f"{key} in {entry.get('unit')} (left null)"] += 1
            values.append(None)
        elif units[entry["unit"]] == 1:
            values.append(num(entry.get("value")))
        else:
            values.append(round(num(entry.get("value")) * units[entry["unit"]], 6))
    if all(v is None for v in values):
        return (None,) * len(_NUTRITION)
    stats["nutrition from new format"] += 1
    return tuple(values) + (text(agg.get("per")), text(agg.get("preparation")))


def producer_data(product):
    """Whether the manufacturer supplied the data through the Open Food Facts
    producers platform: the product has an owner (org-<producer>), or the
    data came from a producer import (data_sources_tags 'producers')."""
    owner = product.get("owner")
    if isinstance(owner, str) and owner.startswith("org-"):
        return True
    return "producers" in (product.get("data_sources_tags") or [])


def nutrition_photo(product):
    """Whether a nutrition-table photo is selected: images.selected.nutrition
    (current format) or an images.nutrition_<lang> key (older format)."""
    images = product.get("images")
    if not isinstance(images, dict):
        return False
    selected = images.get("selected")
    if isinstance(selected, dict) and selected.get("nutrition"):
        return True
    return any(isinstance(k, str) and k.startswith("nutrition") for k in images)


def timestamp(epoch):
    """An OFF epoch-seconds field as an ISO UTC timestamp, or None."""
    t = num(epoch)
    if t is None or t <= 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def signals(product, values, stats):
    """(quality_errors, nutrition_errors, producer_data, nutrition_photo,
    last_modified, energy_consistent, trust); see trust.py."""
    tags = product.get("data_quality_errors_tags")
    if isinstance(tags, list):
        quality_errors = len(tags)
        nutrition_errors = bool(trust.nutrition_error_tags(tags))
    else:
        quality_errors = nutrition_errors = None
    producer = producer_data(product)
    photo = nutrition_photo(product)
    kcal, kj, protein, carbs, fat, fiber = values[:6]
    consistent = trust.energy_consistent(kcal, kj, protein, carbs, fat, fiber)
    level = trust.off_trust(values, nutrition_errors, producer, photo, consistent)
    stats[f"trust {level}"] += 1
    return (quality_errors, nutrition_errors, producer, photo,
            timestamp(product.get("last_modified_t")), consistent, level)


def extract(product, stats):
    code = text(product.get("code"))
    if code is None:
        return None
    names = {}
    for key, value in product.items():
        m = _NAME_FIELD_RE.match(key)
        if m and text(value):
            names[m.group(1)] = text(value)
    nut = nutrition(product, stats)
    return (
        code.strip(),
        text(product.get("product_name")),
        text(product.get("lang")),
        json.dumps(names, ensure_ascii=False) if names else None,
        text(product.get("brands")),
        *nut,
        text(product.get("serving_size")),
        num(product.get("serving_quantity")),
        True if "en:halal" in (product.get("labels_tags") or []) else None,
        ",".join(t for t in (product.get("countries_tags") or []) if isinstance(t, str)) or None,
        *signals(product, nut[:8], stats),
    )


class ResumableDownload(io.RawIOBase):
    """Read-only stream over an HTTP download that reconnects with a Range
    request when the connection drops, so a multi-GB dump survives resets
    without restarting (the gzip decoder just sees one continuous stream)."""

    def __init__(self, url, retries=20):
        self.url = url
        self.retries = retries
        self.offset = 0
        self.resp = None
        self._connect()

    def _connect(self):
        for attempt in range(1, self.retries + 1):
            try:
                headers = {"User-Agent": USER_AGENT}
                if self.offset:
                    headers["Range"] = f"bytes={self.offset}-"
                self.resp = urllib.request.urlopen(
                    urllib.request.Request(self.url, headers=headers), timeout=60)
                if self.offset and self.resp.status != 206:
                    raise OSError("server ignored the Range request")
                return
            except OSError as e:
                wait = min(60, 2 ** attempt)
                print(f"  connection failed at byte {self.offset:,} ({e}); "
                      f"retry {attempt}/{self.retries} in {wait}s")
                time.sleep(wait)
        raise OSError(f"giving up on {self.url} after {self.retries} retries")

    def readable(self):
        return True

    def readinto(self, buf):
        for _ in range(self.retries):
            try:
                n = self.resp.readinto(buf)
                self.offset += n
                return n
            except OSError as e:
                print(f"  stream dropped at byte {self.offset:,} ({e}); resuming")
                self.resp.close()
                self._connect()
        raise OSError("stream kept dropping; giving up")


def open_dump(path):
    if path:
        print(f"Reading {path} ...")
        raw = open(path, "rb")
        if not path.endswith(".gz"):
            return raw
    else:
        print(f"Streaming {DUMP_URL} ...")
        raw = io.BufferedReader(ResumableDownload(DUMP_URL), buffer_size=1 << 20)
    return io.BufferedReader(gzip.GzipFile(fileobj=raw), buffer_size=1 << 20)


# The load upserts the CSV in chunks, each in its own transaction straight
# from a temp table, with a VACUUM after each, so the table never holds more
# than one chunk of dead rows. (Staging the whole dump first and swapping in
# one transaction can fill a small volume.) A
# dropped connection costs one chunk: each chunk commits with its row in the
# chunk log, and a rerun skips logged chunks. Every upserted row gets
# updated_at = now(), so after the last chunk the 'off' rows older than the
# load's start are the ones no longer in the dump, and are deleted. Rows from
# any other source are never touched. Before each chunk the free space on the
# volume is checked, and the load stops while it's below --min-free-mb.
#
# The same load serves build_usda_branded.py (barcode_usda, which has
# no source column: every row is USDA's, so rows older than the load's start
# are all deleted). A Target names the table and how its rows are owned.
CHUNK_ROWS = 250_000


class Target:
    def __init__(self, table, columns, log, source=None, old_helpers=None):
        self.table = table
        self.columns = columns
        self.log = log              # chunk log table, one per target
        self.source = source        # rows owned: WHERE source = <source>; None = all rows
        self.old_helpers = old_helpers  # tables from older load designs, dropped first


OFF_TARGET = Target("barcode_product", COLUMNS, "barcode_load_chunks", source="off")

PREPARE_SQL = """
\\set ON_ERROR_STOP on
BEGIN;
{drop_old}
DO $$
BEGIN
  -- Nested: PL/pgSQL would plan the EXISTS before the table is known to exist.
  IF to_regclass('{log}') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM {log} WHERE csv_id <> '{csv_id}') THEN
      DROP TABLE {log};
    END IF;
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS {log} (
  chunk int PRIMARY KEY, rows int NOT NULL, csv_id text NOT NULL,
  started timestamptz NOT NULL DEFAULT now());
COMMIT;
"""

CHUNK_SQL = """
\\set ON_ERROR_STOP on
BEGIN;
-- A retry after a commit whose reply was lost finds the chunk logged.
SELECT EXISTS (SELECT 1 FROM {log} WHERE chunk = {chunk}) AS staged \\gset
\\if :staged
ROLLBACK;
\\else
CREATE TEMP TABLE barcode_chunk (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP;
\\copy barcode_chunk ({cols}) FROM PROGRAM {program} WITH (FORMAT csv, HEADER true)
INSERT INTO {table} ({cols}{source_col}, updated_at)
SELECT DISTINCT ON (code) {cols}{source_val}, now() FROM barcode_chunk ORDER BY code
ON CONFLICT (code) DO UPDATE SET
  {updates}, updated_at = now()
{owned_where};
INSERT INTO {log} (chunk, rows, csv_id, started)
SELECT {chunk}, {rows}, '{csv_id}', coalesce(min(started), now()) FROM {log};
COMMIT;
VACUUM {table};
\\endif
"""

# Bytes in use on the data volume: every database plus the WAL directory
# (pg_ls_waldir needs superuser or pg_monitor, so --volume-mb needs one too).
USED_SQL = """
SELECT (SELECT sum(pg_database_size(datname)) FROM pg_database)
     + coalesce((SELECT sum(size) FROM pg_ls_waldir()), 0);
"""

FINISH_SQL = """
\\set ON_ERROR_STOP on
BEGIN;
DELETE FROM {table}
 WHERE {owned} updated_at < (SELECT min(started) FROM {log});
SELECT count(*) FROM {table} {owned_rows};
COMMIT;
VACUUM {table};
DROP TABLE {log};
"""


def split_csv(path):
    """Split the gzipped CSV into CHUNK_ROWS-row gzipped CSVs (each with the
    header) next to it; returns [(chunk path, rows)]. Reuses a finished split."""
    folder = path + ".chunks"
    done = os.path.join(folder, "DONE")
    if os.path.exists(done) and os.path.getmtime(done) >= os.path.getmtime(path):
        with open(done) as f:
            return [(os.path.join(folder, n), int(r)) for n, r in (l.split() for l in f)]
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    chunks = []
    with gzip.open(path, "rt", newline="", encoding="utf-8") as src:
        reader = csv.reader(src)
        header = next(reader)
        out = w = None
        for i, row in enumerate(reader):
            if i % CHUNK_ROWS == 0:
                if out:
                    out.close()
                name = f"chunk_{len(chunks):04d}.csv.gz"
                out = gzip.open(os.path.join(folder, name), "wt", newline="", encoding="utf-8")
                w = csv.writer(out)
                w.writerow(header)
                chunks.append([name, 0])
            w.writerow(row)
            chunks[-1][1] += 1
        if out:
            out.close()
    with open(done, "w") as f:
        f.writelines(f"{n} {r}\n" for n, r in chunks)
    return [(os.path.join(folder, n), r) for n, r in chunks]


def psql(database_url, sql, retries=1, timeout=None):
    """Run [sql] with psql; the password goes through PGPASSWORD, not argv,
    where any local user could read it with ps. Returns stdout; exits after
    [retries] failed attempts. A stalled connection is cut by
    tcp_user_timeout and, failing that, by [timeout] seconds per attempt."""
    url = urllib.parse.urlsplit(database_url)
    env = dict(os.environ)
    if url.password is not None:
        env["PGPASSWORD"] = urllib.parse.unquote(url.password)
        netloc = url.netloc.rsplit("@", 1)[1]
        if url.username:
            netloc = f"{url.username}@{netloc}"
        url = url._replace(netloc=netloc)
    query = "&".join(q for q in (url.query, "keepalives=1&keepalives_idle=30&tcp_user_timeout=60000") if q)
    database_url = urllib.parse.urlunsplit(url._replace(query=query))
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(["psql", database_url, "-X", "-q", "-A", "-t"],
                                    input=sql, text=True, env=env, capture_output=True,
                                    timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"  psql timed out after {timeout}s (attempt {attempt}/{retries})", flush=True)
        else:
            if result.returncode == 0:
                return result.stdout
            print(f"  psql failed (attempt {attempt}/{retries}): {result.stderr.strip()[-300:]}",
                  flush=True)
        if attempt < retries:
            time.sleep(min(60, 5 * attempt))
    print("Error: load failed; loaded chunks are kept (rerun to resume)")
    sys.exit(2)


def load(path, database_url, volume_mb, min_free_mb, target=OFF_TARGET):
    if not shutil.which("psql"):
        print("Error: psql not found (brew install libpq)")
        sys.exit(1)
    st = os.stat(path)
    csv_id = f"{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)}"
    print("Splitting the CSV ...")
    chunks = split_csv(path)
    total = sum(r for _, r in chunks)
    print(f"  {len(chunks)} chunks, {total:,} rows")
    cols = ", ".join(target.columns)
    updates = ",\n  ".join(f"{c} = EXCLUDED.{c}" for c in target.columns if c != "code")
    src = target.source
    fmt = dict(log=target.log, table=target.table, csv_id=csv_id, cols=cols, updates=updates,
               drop_old=f"DROP TABLE IF EXISTS {target.old_helpers};" if target.old_helpers else "",
               source_col=", source" if src else "",
               source_val=f", '{src}'" if src else "",
               owned_where=f"WHERE {target.table}.source = '{src}'" if src else "",
               owned=f"source = '{src}' AND" if src else "",
               owned_rows=f"WHERE source = '{src}'" if src else "")
    psql(database_url, PREPARE_SQL.format(**fmt), retries=5)
    done = {int(c) for c in psql(database_url, f"SELECT chunk FROM {target.log};", retries=5).split()}
    if done:
        print(f"  resuming: {len(done)} chunks already loaded")
    t0 = time.time()
    for i, (chunk, rows) in enumerate(chunks):
        if i in done:
            continue
        if volume_mb:
            used_mb = int(float(psql(database_url, USED_SQL, retries=5)) / 1e6)
            if volume_mb - used_mb < min_free_mb:
                print(f"Error: {volume_mb - used_mb} MB free on the volume, under "
                      f"{min_free_mb} MB; stopping (rerun to resume)")
                sys.exit(2)
        quoted = "'" + chunk.replace("'", "'\\''") + "'"
        program = "'" + f"gzip -dc {quoted}".replace("'", "''") + "'"
        psql(database_url, CHUNK_SQL.format(**fmt, program=program, chunk=i, rows=rows),
             retries=8, timeout=1800)
        free = f", ~{volume_mb - used_mb} MB free before it" if volume_mb else ""
        print(f"  chunk {i + 1}/{len(chunks)} loaded ({time.time() - t0:.0f}s{free})", flush=True)
    loaded = psql(database_url, f"SELECT count(*), coalesce(sum(rows), 0) FROM {target.log};", retries=5)
    n, rows = (int(x) for x in loaded.split("|"))
    if n != len(chunks) or rows != total:
        print(f"Error: loaded {n} chunks / {rows:,} rows, expected {len(chunks)} / {total:,}")
        sys.exit(2)
    print(f"Deleting {src or 'all'} rows no longer in the source ...")
    out = psql(database_url, FINISH_SQL.format(**fmt), retries=3, timeout=3600)
    print(f"  {target.table} rows ({src or 'all'}): {out.strip()}")
    shutil.rmtree(path + ".chunks", ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", help="local .jsonl or .jsonl.gz dump (default: stream from OFF)")
    ap.add_argument("--csv", default=CSV_PATH, help=f"gzipped CSV output (default {CSV_PATH})")
    ap.add_argument("--database-url", default=os.environ.get("BARCODE_DATABASE_URL"),
                    help="postgres:// URL to load into (default $BARCODE_DATABASE_URL; prefer "
                         "the env var, since a password in this flag shows up in ps)")
    ap.add_argument("--max-lines", type=int, help="stop after this many dump lines (trial run)")
    ap.add_argument("--volume-mb", type=int,
                    help="size of the database's volume; with it, the load stops while free "
                         "space is under --min-free-mb")
    ap.add_argument("--min-free-mb", type=int, default=1500,
                    help="free space to keep on the volume (default 1500)")
    ap.add_argument("--load-only", action="store_true",
                    help="skip the dump; load the existing --csv into the database")
    args = ap.parse_args()
    if args.max_lines and args.database_url:
        ap.error("--max-lines is for trial runs; loading one would delete every 'off' row it missed")
    if args.load_only:
        if not args.database_url:
            ap.error("--load-only needs --database-url or BARCODE_DATABASE_URL")
        if not os.path.exists(args.csv):
            ap.error(f"{args.csv} not found; run without --load-only first")
        load(args.csv, args.database_url, args.volume_mb, args.min_free_mb)
        return

    stats = Counter()
    langs = Counter()
    seen = set()
    lines = 0
    t0 = time.time()
    os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
    with open_dump(args.input) as dump, gzip.open(args.csv, "wt", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(COLUMNS)
        for lines, line in enumerate(dump, 1):
            if args.max_lines and lines > args.max_lines:
                lines -= 1
                break
            if lines % 500_000 == 0:
                print(f"  {lines:,} lines, {stats['written']:,} written ({time.time() - t0:.0f}s)")
            try:
                product = json.loads(line)
            except ValueError:
                stats["unreadable line"] += 1
                continue
            row = extract(product, stats)
            if row is None:
                stats["no barcode"] += 1
                continue
            if row[0] in seen:  # the table is keyed by code; first one wins
                stats["duplicate barcode"] += 1
                continue
            seen.add(row[0])
            w.writerow(["" if v is None else ("t" if v is True else "f" if v is False else v)
                        for v in row])
            stats["written"] += 1
            r = dict(zip(COLUMNS, row))
            if r["names"]:
                langs.update(json.loads(r["names"]).keys())
            energy = r["energy_kcal"] if r["energy_kcal"] is not None else r["energy_kj"]
            if None not in (energy, r["protein_g"], r["carbs_g"], r["fat_g"]):
                stats["with energy + protein + carbs + fat"] += 1
            for column in ("sugar_g", "sodium_mg", "serving_g"):
                if r[column] is not None:
                    stats[f"with {column}"] += 1
            if r["name"] is None and r["names"] is None:
                stats["with no name in any language"] += 1
            if r["halal"]:
                stats["labeled halal"] += 1
    print(f"Scanned {lines:,} lines in {time.time() - t0:.0f}s")

    print(f"\nWrote {args.csv} ({os.path.getsize(args.csv) / 1e6:.0f} MB gzipped)")
    print(f"  {stats.pop('written', 0):,} products")
    for k, n in stats.most_common():
        print(f"  {k}: {n:,}")
    print(f"  {len(langs)} name languages; most common: "
          + ", ".join(f"{l} {n:,}" for l, n in langs.most_common(12)))

    if args.database_url:
        load(args.csv, args.database_url, args.volume_mb, args.min_free_mb)
    else:
        print("\nNot loaded: pass --database-url (or set BARCODE_DATABASE_URL) to load it.")


if __name__ == "__main__":
    main()
