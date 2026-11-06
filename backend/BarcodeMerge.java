// Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
package barcode.service;

import barcode.dto.BarcodeProductResponse;
import barcode.entity.BarcodeProduct;
import barcode.entity.BarcodeUsda;

/**
 * Picks and merges the best barcode record from Open Food Facts
 * ({@code barcode_product}) and USDA Branded Foods
 * ({@code barcode_usda}) for one barcode. Pure: no I/O.
 * <ol>
 *   <li><b>Precedence:</b> USDA when it has the product and its trust isn't
 *   {@code low} (manufacturer data); otherwise Open Food Facts; otherwise
 *   (only a {@code low} USDA row) USDA.</li>
 *   <li><b>Fill-in:</b> fields the chosen record lacks come from the other one:
 *   names in other languages, brand, halal, serving and preparation always;
 *   the nutrients and their basis only when the two agree on energy (or
 *   either lacks it), are on the same basis (or one doesn't record it), and
 *   the other isn't {@code low} trust, so a response never mixes
 *   disagreeing or differently-based values.</li>
 *   <li><b>Cross-check:</b> when both have energy and differ by more than
 *   {@value #KCAL_DISAGREEMENT} of the larger, an Open Food Facts response
 *   is marked {@code low} trust.</li>
 * </ol>
 * The chosen record's code, source and trust are what the response carries.
 */
public final class BarcodeMerge {

    /** Relative kcal difference above which the sources disagree. */
    public static final double KCAL_DISAGREEMENT = 0.20;
    private static final double KJ_PER_KCAL = 4.184;
    private static final String LOW = "low";

    private BarcodeMerge() {
    }

    /** The response for these records (either may be null, not both). */
    public static BarcodeProductResponse best(BarcodeProduct off, BarcodeUsda usda) {
        if (off == null && usda == null) {
            throw new IllegalArgumentException("no record");
        }
        if (off == null) return BarcodeProductResponse.from(usda);
        if (usda == null) return BarcodeProductResponse.from(off);

        BarcodeProductResponse o = BarcodeProductResponse.from(off);
        BarcodeProductResponse u = BarcodeProductResponse.from(usda);
        boolean disagree = disagree(kcal(o), kcal(u));
        if (!LOW.equals(usda.getTrust())) {
            return fill(u, o, !disagree && !LOW.equals(o.trust()), u.trust());
        }
        // USDA is low trust here, so its nutrients never fill in.
        return fill(o, u, false, disagree ? LOW : o.trust());
    }

    /** Stated kcal: energyKcal, else energyKj / 4.184, else null. */
    static Double kcal(BarcodeProductResponse r) {
        if (r.energyKcal() != null) return r.energyKcal();
        return r.energyKj() == null ? null : r.energyKj() / KJ_PER_KCAL;
    }

    /** Both known and more than {@link #KCAL_DISAGREEMENT} apart, relative to the larger. */
    static boolean disagree(Double a, Double b) {
        if (a == null || b == null) return false;
        double max = Math.max(Math.abs(a), Math.abs(b));
        return max > 0 && Math.abs(a - b) > KCAL_DISAGREEMENT * max;
    }

    private static BarcodeProductResponse fill(BarcodeProductResponse p, BarcodeProductResponse s,
                                               boolean nutrients, String trust) {
        boolean sameBasis = p.nutritionPer() == null || s.nutritionPer() == null
                || p.nutritionPer().equals(s.nutritionPer());
        boolean n = nutrients && sameBasis;
        String name = p.name() != null ? p.name() : s.name();
        String lang = p.name() != null ? p.lang() : s.lang();
        return new BarcodeProductResponse(
                p.code(), name, lang, or(p.names(), s.names()), or(p.brand(), s.brand()),
                n ? or(p.energyKcal(), s.energyKcal()) : p.energyKcal(),
                n ? or(p.energyKj(), s.energyKj()) : p.energyKj(),
                n ? or(p.protein(), s.protein()) : p.protein(),
                n ? or(p.carbs(), s.carbs()) : p.carbs(),
                n ? or(p.fat(), s.fat()) : p.fat(),
                n ? or(p.fiber(), s.fiber()) : p.fiber(),
                n ? or(p.sugar(), s.sugar()) : p.sugar(),
                n ? or(p.sodium(), s.sodium()) : p.sodium(),
                n ? or(p.nutritionPer(), s.nutritionPer()) : p.nutritionPer(),
                or(p.nutritionPreparation(), s.nutritionPreparation()),
                or(p.servingSize(), s.servingSize()), or(p.servingG(), s.servingG()),
                or(p.halal(), s.halal()), p.source(), trust, p.nutritionPhoto(), p.producerData());
    }

    private static <T> T or(T a, T b) {
        return a != null ? a : b;
    }
}
