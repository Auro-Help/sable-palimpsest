# Alterations to Open Food Facts

This file and the code it links to describe the differences between
[Open Food Facts](https://world.openfoodfacts.org) and the derivative database, as
ODbL section 4.6(b) asks. If this file and the code ever disagree, the code is what ran.

## 1. Loading products (`barcode/build_barcode_db.py`)

**Rows**

- A product without a `code` is dropped, and so is a line that can't be read as JSON.
- Spaces around `code` are removed. If a code appears more than once, the first one in the dump is kept.
- Otherwise every product is kept, from every country, in every language.
- On a reload, Open Food Facts rows no longer in the dump are deleted.

**Fields kept**

| Column | From Open Food Facts |
|---|---|
| `code` | `code` |
| `name`, `lang`, `brand` | `product_name`, `lang`, `brands` |
| `names` | Every `product_name_<lang>`, as a JSON map |
| Nutrition columns | See "Nutrition" below |
| `serving_size`, `serving_g` | `serving_size`, `serving_quantity` |
| `halal` | True when `labels_tags` has `en:halal`, otherwise empty |
| `countries` | `countries_tags`, joined with commas |

All other Open Food Facts fields are left out.

**Text and numbers**

- NUL characters are removed from text, because PostgreSQL can't store them.
- Empty or whitespace-only text becomes null, as does any value that isn't a string.
- Non-numeric and non-finite numbers become null. Numbers written as strings are accepted.

**Nutrition**

- If any old-format value exists, the old flat fields are used: `energy-kcal_100g`, `energy-kj_100g`,
  `proteins_100g`, `carbohydrates_100g`, `fat_100g`, `fiber_100g`, `sugars_100g` and `sodium_100g`.
  `energy_100g` fills in for kJ when `energy-kj_100g` is missing. The basis columns stay null.
- Otherwise `nutrition.aggregated_set` is used, with its `per` and `preparation`.
- **Sodium is the one converted value:** grams become milligrams (× 1000, rounded to 6 decimals). A
  new-format value already in mg is kept.
- A new-format value in any other unit than expected becomes null.

**Added columns (signals only, computed by `barcode/trust.py`)**

These never change a value or drop a row.

| Column | Meaning |
|---|---|
| `quality_errors` | How many `data_quality_errors_tags` the product has |
| `nutrition_errors` | Any of those tags contains `nutrition`, `energy`, `sugars`, `sodium` or `salt` |
| `producer_data` | `owner` starts with `org-`, or `data_sources_tags` has `producers` |
| `nutrition_photo` | A nutrition-table photo is selected (the image isn't stored) |
| `last_modified` | `last_modified_t` as a timestamp |
| `energy_consistent` | Stated energy is within max(15%, 20 kcal) of 4 × protein + 4 × carbs + 9 × fat (+ 2 × fiber when known) |
| `trust` | `low`: nutrition errors, inconsistent energy or implausible values (see "Implausible values" below). `high`: otherwise, producer data or a nutrition photo. `medium`: otherwise. Null when the product has no nutrition |
| `source`, `updated_at` | Always `off`, and the time of the load |

**Implausible values** (per 100 g or ml): protein + carbs + fat over 105 g, energy over 950 kcal, sugar
over 100 g or over carbs + 0.5 g, fiber over 100 g, sodium over 39,400 mg or between 0 and 1 mg, or
any negative value.

## 2. Additional contents: USDA Branded Foods (`barcode/build_usda_branded.py`)

The database keeps a second table from the
[USDA FoodData Central Branded Foods](https://fdc.nal.usda.gov/download-datasets) release, which is
public domain (CC0 1.0). The rows aren't copied here; get them from USDA and run the script.

- Every branded food with a `gtinUpc` is kept. Codes of 1 to 14 digits lose leading zeros down to 8
  digits, then 9 to 12 digit codes are padded to 13. Other codes are kept as written, without spaces
  around them.
- If a code repeats, the latest `availableDate` wins, then `modifiedDate`, then `fdcId`.
- Values are as USDA has them. A nutrient in an unexpected unit becomes null.
- `nutrition_per` is derived from `servingSizeUnit`: `100g` for g, `100ml` for ml, null for other units.
- `energy_consistent` and `trust` are added: `low` when inconsistent or implausible, otherwise `high`,
  and null when the food has no nutrition.

## 3. Answering a scan (`backend/BarcodeLookup.java`, `backend/BarcodeMerge.java`)

Each scan tries the code as scanned, then the same code without leading zeros (down to 8 digits),
then with leading zeros added (up to 14 digits). The first spelling found in each table is used.

With a record from both sources:

1. **Which comes first:** USDA, unless its trust is `low`. Then Open Food Facts.
2. **Filling gaps:** name, other-language names, brand, halal, serving and preparation come from the
   other source when the first lacks them.
3. **Filling nutrients:** values, including their basis, are filled only when all of these are true:
   - USDA comes first.
   - Open Food Facts' trust isn't `low`.
   - The two don't disagree on energy.
   - The nutrition bases match, or one is unknown.
4. **Disagreement:** energy differing by more than 20% of the larger value disagrees, using kJ / 4.184
   when kcal is missing. If Open Food
   Facts comes first, its answer is then marked `low`.

An answer's `source` and `trust` come from the record that came first, except for that `low` mark.
The answer leaves out `countries`, `quality_errors`, `nutrition_errors`, `last_modified` and
`energy_consistent`. These are used only inside the database or at load time.
