# Barcode food data

The method for building a barcode food database from
[Open Food Facts](https://world.openfoodfacts.org) and
[USDA FoodData Central](https://fdc.nal.usda.gov/download-datasets) Branded Foods.

**Contains information from [Open Food Facts](https://world.openfoodfacts.org), which is made
available here under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).**
Credit: (c) Open Food Facts contributors.

This repository is an offer under section 4.6(b) of the ODbL. It is free to use and holds the method
that makes all the differences between Open Food Facts and the derivative database. Anyone can
rebuild an equivalent database with it. The database itself is not stored here.

## What is here

| Path | What it does |
|---|---|
| [`ALTERATIONS.md`](ALTERATIONS.md) | Every difference from the Open Food Facts data, in plain language |
| [`barcode/build_barcode_db.py`](barcode/build_barcode_db.py) | Reads the Open Food Facts dump and writes the product table |
| [`barcode/build_usda_branded.py`](barcode/build_usda_branded.py) | Reads the USDA Branded Foods release and writes the USDA table |
| [`barcode/trust.py`](barcode/trust.py) | The rules for the added trust signals |
| [`backend/BarcodeLookup.java`](backend/BarcodeLookup.java) | The barcode spellings each scan tries |
| [`backend/BarcodeMerge.java`](backend/BarcodeMerge.java) | How one answer is chosen and merged from both sources |
| [`backend/BarcodeProductResponse.java`](backend/BarcodeProductResponse.java) | The answer's fields and how each source maps onto them |
| [`backend/schema/`](backend/schema/) | The database tables |

These files show the method and aren't a standalone application. Product-specific names were changed,
and a step that cleared old temporary tables was removed. The rest of the logic wasn't changed.
`BarcodeLookup.java` compiles alone. The other two Java files need entity classes and a host application.

## Source data

| Source | Where to get it | Licence |
|---|---|---|
| Open Food Facts | [Data page](https://world.openfoodfacts.org/data), or the [JSONL dump](https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz) (about 13 GB) | Database: [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Contents: [DbCL 1.0](https://opendatacommons.org/licenses/dbcl/1-0/) |
| USDA FoodData Central, Branded Foods | [Download page](https://fdc.nal.usda.gov/download-datasets) | Public domain ([CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)) |

**Versions.** The database was built from the releases available around 2026-10-10. Open Food Facts
replaces its dump regularly, so a rebuild today uses newer data and gives newer values.

The USDA rows aren't copied here. The release from that time is
[`FoodData_Central_branded_food_json_2026-04-30.zip`](https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_branded_food_json_2026-04-30.zip).
Pass it with `--input`, or leave `--input` out to download the latest release.

This method uses no Open Food Facts product images. It stores only a yes/no flag saying that a
nutrition-table photo exists.

## Rebuild it

You need Python 3.9 or later. `psql` is needed only to load the result into PostgreSQL.

```bash
cd barcode
python3 -u build_barcode_db.py --max-lines 200000   # trial, CSV only
python3 -u build_barcode_db.py                      # every product, CSV only
python3 -u build_usda_branded.py                    # latest USDA release, CSV only
```

Each script writes a gzipped CSV under `barcode/data/`. To load one into PostgreSQL, apply the SQL in
`backend/schema/` in version order, then set `BARCODE_DATABASE_URL` and run the same script.
`--volume-mb` also needs a database role that can read `pg_ls_waldir()`.

The schema numbers skip V5, which is unrelated to this data, and V8, which only drops
`idx_barcode_product_source`. Neither changes any rows.

## Licences

- **Data:** anything you build from Open Food Facts with this method stays under the
  [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Keep the notice above and credit
  Open Food Facts with a link to <https://openfoodfacts.org>. See [`DATA-LICENSE.md`](DATA-LICENSE.md).
- **Code and documentation:** Copyright (c) 2026 Auro Help Ltd, under the MIT License in
  [`LICENSE`](LICENSE). Keep this copyright notice in copies. The ODbL doesn't cover computer programs
  (section 2.3a).
- **Not covered:** this copyright doesn't claim Open Food Facts or USDA data. Those stay with their
  owners under the licences above.

## No warranty

Open Food Facts and USDA data is entered or supplied by others and may be wrong. It is provided as is,
without warranty, and must not be used for medical purposes. Product names and brands may be
trademarks of their owners.

Questions or corrections: open an issue.
