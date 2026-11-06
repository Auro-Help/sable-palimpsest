-- Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
-- Barcode nutrition trust signals, and USDA FoodData Central Branded Foods
-- as a second barcode source. Both are loaded by barcode/
-- (build_barcode_db.py, build_usda_branded.py; rules in barcode/trust.py).
-- Signals only: stored values stay exactly as the source
-- has them, and no row is dropped for being untrustworthy.
--
-- Trust signals on Open Food Facts rows (all NULL until the loader is rerun):
--   quality_errors     how many OFF data_quality_errors_tags (NULL: none recorded)
--   nutrition_errors   whether any of them is about nutrition values
--   producer_data      supplied by the manufacturer on the OFF producers platform
--   nutrition_photo    a nutrition-table photo is selected
--   last_modified      OFF's last_modified_t
--   energy_consistent  stated kcal within max(15%, 20 kcal) of 4p + 4c + 9f (+ 2 fiber);
--                      NULL when energy, protein, carbs or fat is missing
--   trust              'high' | 'medium' | 'low' (NULL: no nutrition at all)

ALTER TABLE barcode_product
    ADD COLUMN quality_errors    INTEGER,
    ADD COLUMN nutrition_errors  BOOLEAN,
    ADD COLUMN producer_data     BOOLEAN,
    ADD COLUMN nutrition_photo   BOOLEAN,
    ADD COLUMN last_modified     TIMESTAMPTZ,
    ADD COLUMN energy_consistent BOOLEAN,
    ADD COLUMN trust             TEXT;

-- USDA FoodData Central Branded Foods (public domain, CC0), every branded
-- food with a GTIN, all markets and categories. Nutrition is per 100 g, or
-- 100 ml (nutrition_per), as USDA has it; sodium in mg. code is the GTIN
-- spelled like Open Food Facts codes (leading zeros removed down to 8 digits,
-- then padded to 13 when 9-12 digits) so the lookup's barcode spellings find
-- both tables; gtin_upc is USDA's own spelling. trust is 'high' (manufacturer
-- data) unless the values are implausible or energy-inconsistent ('low').

CREATE TABLE barcode_usda (
    code              TEXT             PRIMARY KEY,
    gtin_upc          TEXT             NOT NULL,
    fdc_id            BIGINT,
    name              TEXT,                           -- USDA description
    brand_owner       TEXT,
    brand_name        TEXT,
    energy_kcal       DOUBLE PRECISION,
    energy_kj         DOUBLE PRECISION,
    protein_g         DOUBLE PRECISION,
    carbs_g           DOUBLE PRECISION,
    fat_g             DOUBLE PRECISION,
    fiber_g           DOUBLE PRECISION,
    sugar_g           DOUBLE PRECISION,               -- Total Sugars
    sodium_mg         DOUBLE PRECISION,
    nutrition_per     TEXT,                           -- '100g' / '100ml'; NULL for other serving units
    serving_size      DOUBLE PRECISION,               -- in serving_size_unit
    serving_size_unit TEXT,
    household_serving TEXT,                           -- e.g. '0.25 cup'
    category          TEXT,                           -- brandedFoodCategory
    market_country    TEXT,
    data_source       TEXT,                           -- 'GDSN' (GS1, from the manufacturer) / 'LI' (Label Insight)
    preparation_state TEXT,
    last_modified     DATE,
    available_date    DATE,
    energy_consistent BOOLEAN,
    trust             TEXT,
    updated_at        TIMESTAMPTZ      NOT NULL DEFAULT now()
);
