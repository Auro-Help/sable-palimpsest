-- Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
-- What a barcode_product row's nutrition values are per, as Open Food
-- Facts' newer format records it (nutrition.aggregated_set): per is '100g'
-- or '100ml', preparation is 'as_sold' or 'prepared'. Both are NULL for rows
-- from OFF's older flat format, which doesn't record its basis.

ALTER TABLE barcode_product
    ADD COLUMN nutrition_per         TEXT,
    ADD COLUMN nutrition_preparation TEXT;
