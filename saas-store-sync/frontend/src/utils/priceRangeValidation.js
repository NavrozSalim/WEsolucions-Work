/** @param {unknown} v */
function parseFrom(v) {
    if (v === '' || v === null || v === undefined) return null;
    const n = typeof v === 'number' ? v : parseFloat(String(v));
    return Number.isFinite(n) ? n : null;
}

/** Upper bound: empty => must still be provided; 999999999 expected on last tier. */
function parseTo(v) {
    if (v === '' || v === null || v === undefined) return null;
    const s = String(v).trim().toUpperCase();
    if (s === 'MAX') return 999999999;
    const n = parseFloat(s);
    return Number.isFinite(n) ? n : null;
}

function parseMargin(v) {
    if (v === '' || v === null || v === undefined) return null;
    const n = typeof v === 'number' ? v : parseFloat(String(v));
    return Number.isFinite(n) ? n : null;
}

const MAX_RANGE = 999999999;

/**
 * Validates tiered vendor price rows: non-negative bounds, positive margin,
 * every tier except the last has an upper bound, each tier starts at previous upper bound
 * (backend treats non-final upper bounds as exclusive so boundaries are not double-counted),
 * and the last tier must end at 999999999.
 * @param {Array<{ vendor_id?: string, range_margins?: Array<{ from_value?: unknown, to_value?: unknown, margin_type?: string, margin_percentage?: unknown }> }>} vendorPriceSettings
 * @returns {string[]} human-readable errors (deduped)
 */
export function validateVendorPriceSettings(vendorPriceSettings) {
    const errs = [];
    const list = vendorPriceSettings || [];

    list.forEach((vp) => {
        if (!vp.vendor_id) return;
        const ranges = vp.range_margins || [];
        if (ranges.length === 0) {
            errs.push('Each vendor needs at least one price tier.');
            return;
        }

        ranges.forEach((r, ri) => {
            const from = parseFrom(r.from_value);
            const to = parseTo(r.to_value);
            const margin = parseMargin(r.margin_percentage);

            if (from === null || from < 0) {
                errs.push(`Price tier ${ri + 1}: "From" must be a non‑negative number.`);
            }
            if (to !== null && to < 0) {
                errs.push(`Price tier ${ri + 1}: "To" must be a non‑negative number.`);
            }
            if (from !== null && to !== null && from > to) {
                errs.push(`Price tier ${ri + 1}: "From" cannot be greater than "To".`);
            }
            if (margin === null || margin < 0) {
                errs.push(`Price tier ${ri + 1}: Margin value must be zero or greater.`);
            }
        });

        for (let i = 0; i < ranges.length - 1; i += 1) {
            const to = parseTo(ranges[i].to_value);
            if (to === null) {
                errs.push(
                    `Price tiers must be continuous: tier ${i + 1} needs a maximum price before starting tier ${i + 2}.`
                );
            }
        }

        for (let i = 1; i < ranges.length; i += 1) {
            const prevTo = parseTo(ranges[i - 1].to_value);
            const currFrom = parseFrom(ranges[i].from_value);
            if (prevTo === null || currFrom === null) continue;
            if (Math.abs(currFrom - prevTo) > 1e-6) {
                errs.push(
                    `Price ranges must be continuous: after a tier ending at ${prevTo}, the next tier must start at ${prevTo} (not ${currFrom}). Decimals are allowed.`,
                );
            }
        }

        const lastRange = ranges[ranges.length - 1];
        const lastTo = parseTo(lastRange.to_value);
        if (lastTo === null) {
            errs.push(`The last price tier must have "To" set to ${MAX_RANGE}.`);
        } else if (lastTo !== MAX_RANGE) {
            errs.push(`The last price tier "To" must be ${MAX_RANGE} (currently ${lastTo}).`);
        }
    });

    return [...new Set(errs)];
}

/**
 * @param {unknown} v
 * @returns {boolean}
 */
export function isNonNegativePercent(v) {
    if (v === '' || v === null || v === undefined) return false;
    const n = parseFloat(String(v));
    return Number.isFinite(n) && n >= 0;
}
