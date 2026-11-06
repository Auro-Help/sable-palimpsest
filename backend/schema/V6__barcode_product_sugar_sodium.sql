-- Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
-- Sugars and sodium for barcode_product, per the same basis as the
-- other nutrition columns (nutrition_per). Loaded by
-- barcode/build_barcode_db.py exactly as Open Food Facts has them,
-- except that sodium is converted to milligrams (OFF records it in grams);
-- NULL when the product doesn't record it, or records it in a unit the loader
-- doesn't recognise.

ALTER TABLE barcode_product
    ADD COLUMN sugar_g   DOUBLE PRECISION,
    ADD COLUMN sodium_mg DOUBLE PRECISION;
