/**
 * Viewport-aware position for a fixed portal dropdown.
 * Opens below the trigger, or above when there is not enough space under it.
 */
export function placeFixedMenu(triggerEl, menuEl, { align = 'right', estimatedHeight = 280 } = {}) {
    if (!triggerEl) {
        return align === 'right' ? { top: 0, right: 8 } : { top: 0, left: 0 };
    }
    const r = triggerEl.getBoundingClientRect();
    const gap = 6;
    const pad = 8;
    const menuH = menuEl?.offsetHeight || estimatedHeight;
    const spaceBelow = window.innerHeight - r.bottom - gap;
    const openUp = spaceBelow < menuH + pad;
    const top = openUp
        ? Math.max(pad, r.top - menuH - gap)
        : r.bottom + gap;
    if (align === 'right') {
        return { top, right: Math.max(pad, window.innerWidth - r.right) };
    }
    const menuW = menuEl?.offsetWidth || 240;
    let left = r.left;
    if (left + menuW > window.innerWidth - pad) {
        left = Math.max(pad, window.innerWidth - menuW - pad);
    }
    return { top, left };
}
