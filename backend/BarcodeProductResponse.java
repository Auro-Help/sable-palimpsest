// Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
package barcode.dto;

import barcode.entity.BarcodeProduct;
import barcode.entity.BarcodeUsda;

import java.math.BigDecimal;
import java.util.Locale;
import java.util.Map;

/**
 * A barcode lookup hit, with values exactly as stored (neither source is
 * cleaned), so every nutrition field may be null. Nutrition is per
 * {@code nutritionPer} ({@code 100g} / {@code 100ml}; null when the source
 * didn't record it, in which case it's per 100 g, or per 100 ml for
 * drinks), for the food {@code nutritionPreparation} ({@code as_sold} /
 * {@code prepared}); energy comes in whichever units the product has. Every
 * nutrient is in grams except {@code sodium}, in milligrams. {@code servingSize}
 * is the label's serving text and {@code servingG} its weight (g, or ml for
 * drinks). {@code names} maps language code to name; {@code name} is the
 * product's main name, in {@code lang}.
 * <p>
 * {@code source} is the source that came first: {@code "off"} (Open Food
 * Facts, which the app must credit, ODbL) or {@code "usda"} (USDA FoodData
 * Central Branded Foods, public domain). {@code trust} ({@code high} /
 * {@code medium} / {@code low}, null when unknown) rates those values, from
 * the loader's signals and the cross-check between sources (see
 * {@code BarcodeMerge}). {@code nutritionPhoto}: Open Food Facts has a
 * nutrition-table photo (null for USDA). {@code producerData}: the
 * manufacturer supplied the data (OFF producers platform; for USDA, true for
 * GDSN / NZGDSN submissions, false for Label Insight and Euromonitor
 * transcriptions; null when unknown).
 */
public record BarcodeProductResponse(String code, String name, String lang, Map<String, String> names,
                                     String brand, Double energyKcal, Double energyKj, Double protein,
                                     Double carbs, Double fat, Double fiber, Double sugar,
                                     Double sodium, String nutritionPer,
                                     String nutritionPreparation, String servingSize,
                                     Double servingG, Boolean halal, String source,
                                     String trust, Boolean nutritionPhoto, Boolean producerData) {

    public static final String SOURCE_USDA = "usda";

    public static BarcodeProductResponse from(BarcodeProduct p) {
        return new BarcodeProductResponse(
                p.getCode(), p.getName(), p.getLang(), p.getNames(), p.getBrand(),
                p.getEnergyKcal(), p.getEnergyKj(), p.getProteinG(), p.getCarbsG(), p.getFatG(),
                p.getFiberG(), p.getSugarG(), p.getSodiumMg(), p.getNutritionPer(), p.getNutritionPreparation(),
                p.getServingSize(), p.getServingG(), p.getHalal(), p.getSource(),
                p.getTrust(), p.getNutritionPhoto(), p.getProducerData());
    }

    public static BarcodeProductResponse from(BarcodeUsda u) {
        return new BarcodeProductResponse(
                u.getCode(), u.getName(), u.getName() == null ? null : "en", null,
                u.getBrandName() != null ? u.getBrandName() : u.getBrandOwner(),
                u.getEnergyKcal(), u.getEnergyKj(), u.getProteinG(), u.getCarbsG(), u.getFatG(),
                u.getFiberG(), u.getSugarG(), u.getSodiumMg(), u.getNutritionPer(),
                preparation(u.getPreparationState()), servingText(u), servingG(u), null, SOURCE_USDA,
                u.getTrust(), null, manufacturerSubmitted(u.getDataSource()));
    }

    /** GDSN / NZGDSN data comes from the manufacturer; LI and Euromonitor are third-party transcriptions. */
    static boolean manufacturerSubmitted(String dataSource) {
        if (dataSource == null) return false;
        String s = dataSource.strip().toUpperCase(Locale.ROOT);
        return s.equals("GDSN") || s.equals("NZGDSN");
    }

    /** "0.25 cup (28 g)", "28 g" or the household text alone; null when USDA has neither. */
    static String servingText(BarcodeUsda u) {
        String household = u.getHouseholdServing() == null || u.getHouseholdServing().isBlank()
                ? null : u.getHouseholdServing().strip();
        String measured = u.getServingSize() == null ? null
                : (BigDecimal.valueOf(u.getServingSize()).stripTrailingZeros().toPlainString()
                + (u.getServingSizeUnit() == null ? "" : " " + u.getServingSizeUnit()));
        if (household != null && measured != null) return household + " (" + measured + ")";
        return household != null ? household : measured;
    }

    /** The serving in g (or ml), when USDA measured it in either. */
    static Double servingG(BarcodeUsda u) {
        if (u.getServingSize() == null || u.getServingSizeUnit() == null) return null;
        return switch (u.getServingSizeUnit().strip().toLowerCase(Locale.ROOT)) {
            case "g", "gm", "grm", "ml", "mlt" -> u.getServingSize();
            default -> null;
        };
    }

    private static String preparation(String state) {
        if (state == null) return null;
        return switch (state.strip().toUpperCase(Locale.ROOT)) {
            case "UNPREPARED", "READY_TO_EAT", "READY_TO_DRINK" -> "as_sold";
            case "PREPARED" -> "prepared";
            default -> null;
        };
    }
}
