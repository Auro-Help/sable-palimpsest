// Copyright (c) 2026 Auro Help Ltd. SPDX-License-Identifier: MIT
package barcode.service;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** The spellings a scanned barcode is looked up under. Pure: no I/O. */
public final class BarcodeLookup {

    private BarcodeLookup() {
    }

    /**
     * The codes to try, in order: the scanned digits as-is, then the same
     * barcode in its other common spelling. Codes are stored as Open Food
     * Facts has them, and scanners report UPC-A as 12 digits or as EAN-13
     * with a leading 0 (GTIN-14 adds another). Empty when the input isn't
     * 1 to 14 digits.
     */
    public static List<String> candidates(String raw) {
        if (raw == null) return List.of();
        String code = raw.strip();
        if (code.isEmpty() || code.length() > 14 || !code.chars().allMatch(c -> c >= '0' && c <= '9')) {
            return List.of();
        }
        Set<String> out = new LinkedHashSet<>();
        out.add(code);
        String trimmed = code;
        while (trimmed.length() > 8 && trimmed.charAt(0) == '0') {
            trimmed = trimmed.substring(1);
            out.add(trimmed);
        }
        for (String c : List.copyOf(out)) {
            for (int len = c.length() + 1; len <= 14; len++) {
                out.add("0".repeat(len - c.length()) + c);
            }
        }
        return List.copyOf(out);
    }
}
