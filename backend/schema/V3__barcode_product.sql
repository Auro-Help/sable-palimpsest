-- Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
-- Packaged foods worldwide, keyed by barcode, for barcode scanning in the
-- app. Loaded from the Open Food Facts dump by
-- barcode/build_barcode_db.py (rows with
-- source = 'off'), which replaces its own rows on each run and leaves any
-- other source alone.
--
-- Values are stored exactly as Open Food Facts has them (no cleaning), so any
-- nutrition field may be null or implausible. Nutrition is per 100 g; energy
-- is kept in both units because OFF products may have either.
--
-- Open Food Facts data is ODbL: the app must credit Open Food Facts, and this
-- table's 'off' rows must stay available under ODbL.

CREATE TABLE barcode_product (
    code          TEXT             PRIMARY KEY,   -- as OFF stores it
    name          TEXT,                           -- OFF's main product_name
    lang          TEXT,                           -- language of the main name
    names         JSONB,                          -- {"en": "...", "ar": "...", ...}
    brand         TEXT,
    energy_kcal   DOUBLE PRECISION,
    energy_kj     DOUBLE PRECISION,
    protein_g     DOUBLE PRECISION,
    carbs_g       DOUBLE PRECISION,
    fat_g         DOUBLE PRECISION,
    fiber_g       DOUBLE PRECISION,
    serving_size  TEXT,                           -- label text, e.g. "30 g (2 biscuits)"
    serving_g     DOUBLE PRECISION,
    halal         BOOLEAN,                        -- TRUE when labeled halal, NULL when unknown
    countries     TEXT,                           -- OFF tags, e.g. 'en:france,en:saudi-arabia'
    source        VARCHAR(16)      NOT NULL DEFAULT 'off',
    updated_at    TIMESTAMPTZ      NOT NULL DEFAULT now()
);

CREATE INDEX idx_barcode_product_source ON barcode_product (source);
