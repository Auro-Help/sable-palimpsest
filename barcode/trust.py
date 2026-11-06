# Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
"""Trust signals for barcode nutrition, shared by build_barcode_db.py (Open
Food Facts) and build_usda_branded.py (USDA FoodData Central Branded Foods).

Signals only: nothing here changes a stored value or drops a row. Every
function takes values per 100 g / 100 ml as stored (energy in kcal and/or
kJ, nutrients in g, sodium in mg).

energy_consistent: the stated energy against the Atwater estimate
    4 x protein + 4 x carbs + 9 x fat (+ 2 x fiber when fiber is known);
    consistent when they differ by at most max(15% of the stated kcal, 20 kcal).
    Stated kcal is energy_kcal, or energy_kj / 4.184 when only kJ is known.
    None when energy, protein, carbs or fat is missing. Alcohol (7 kcal/g) and
    polyols aren't known, so alcoholic drinks and sugar-free sweets can read
    as inconsistent.

implausible: protein + carbs + fat over 105 g, energy over
    950 kcal, sugar over 100 g or over carbs + 0.5 g, fiber over 100 g,
    sodium over 39,400 mg (more than pure salt) or between 0 and 1 mg
    (entered 1000x too small), or any value negative.

trust ('high' | 'medium' | 'low', None when the row has no nutrition at all):
    Open Food Facts (off_trust):
      low    = nutrition quality errors (OFF's data_quality_errors_tags about
               nutrition / energy / sugars / sodium / salt), or
               energy_consistent is False, or implausible
      high   = otherwise, if the producer supplied the data (OFF producers
               platform) or there's a nutrition-table photo
      medium = otherwise
    USDA Branded Foods (usda_trust): manufacturer data, so
      low    = energy_consistent is False, or implausible
      high   = otherwise
"""

import math

KJ_PER_KCAL = 4.184
MAX_MACROS = 105
MAX_KCAL = 950
MAX_SUGAR = 100
SUGAR_OVER_CARBS = 0.5
MAX_FIBER = 100
MAX_SODIUM_MG = 39400
MIN_NONZERO_SODIUM_MG = 1
ENERGY_REL_TOLERANCE = 0.15
ENERGY_ABS_TOLERANCE_KCAL = 20

# Substrings of OFF data-quality error tags that are about nutrition values.
NUTRITION_ERROR_WORDS = ("nutrition", "energy", "sugars", "sodium", "salt")


def _ok(v):
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


def stated_kcal(kcal, kj):
    """The stated energy in kcal: kcal, else kJ / 4.184, else None."""
    if _ok(kcal):
        return kcal
    if _ok(kj):
        return kj / KJ_PER_KCAL
    return None


def energy_consistent(kcal, kj, protein, carbs, fat, fiber):
    stated = stated_kcal(kcal, kj)
    if stated is None or not (_ok(protein) and _ok(carbs) and _ok(fat)):
        return None
    est = 4 * protein + 4 * carbs + 9 * fat + (2 * fiber if _ok(fiber) else 0)
    return abs(stated - est) <= max(ENERGY_REL_TOLERANCE * abs(stated), ENERGY_ABS_TOLERANCE_KCAL)


def implausible(kcal, kj, protein, carbs, fat, fiber, sugar, sodium_mg):
    values = (kcal, kj, protein, carbs, fat, fiber, sugar, sodium_mg)
    if any(_ok(v) and v < 0 for v in values):
        return True
    stated = stated_kcal(kcal, kj)
    if stated is not None and stated > MAX_KCAL:
        return True
    if sum(v for v in (protein, carbs, fat) if _ok(v)) > MAX_MACROS:
        return True
    if _ok(sugar) and (sugar > MAX_SUGAR or (_ok(carbs) and sugar > carbs + SUGAR_OVER_CARBS)):
        return True
    if _ok(fiber) and fiber > MAX_FIBER:
        return True
    if _ok(sodium_mg) and (sodium_mg > MAX_SODIUM_MG or 0 < sodium_mg < MIN_NONZERO_SODIUM_MG):
        return True
    return False


def has_nutrition(*values):
    return any(_ok(v) for v in values)


def nutrition_error_tags(tags):
    """The OFF data-quality error tags that are about nutrition values."""
    return [t for t in tags if isinstance(t, str) and any(w in t for w in NUTRITION_ERROR_WORDS)]


def off_trust(values, nutrition_errors, producer_data, nutrition_photo, consistent):
    """values: (kcal, kj, protein, carbs, fat, fiber, sugar, sodium_mg)."""
    if not has_nutrition(*values):
        return None
    if nutrition_errors or consistent is False or implausible(*values):
        return "low"
    if producer_data or nutrition_photo:
        return "high"
    return "medium"


def usda_trust(values, consistent):
    """values: (kcal, kj, protein, carbs, fat, fiber, sugar, sodium_mg)."""
    if not has_nutrition(*values):
        return None
    if consistent is False or implausible(*values):
        return "low"
    return "high"
