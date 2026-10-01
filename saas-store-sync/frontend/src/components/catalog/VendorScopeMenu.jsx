import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown } from 'lucide-react';
import { placeFixedMenu } from '../../utils/fixedMenuPosition';

/**
 * Start Scraping / Manual sync menu when a store has more than one vendor.
 * Choosing "All vendors" calls onSelect(null). A vendor row calls onSelect(vendor).
 */
export default function VendorScopeMenu({
    label,
    title,
    disabled = false,
    icon: Icon = null,
    iconClassName = '',
    vendors = [],
    onSelect,
    allLabel = 'All vendors',
    className = '',
}) {
    const [open, setOpen] = useState(false);
    const [menuPos, setMenuPos] = useState({ top: 0, left: 0 });
    const triggerRef = useRef(null);
    const menuRef = useRef(null);

    const updatePosition = useCallback(() => {
        setMenuPos(placeFixedMenu(triggerRef.current, menuRef.current, { align: 'left' }));
    }, []);

    useLayoutEffect(() => {
        if (!open) return undefined;
        updatePosition();
        const id = requestAnimationFrame(() => updatePosition());
        const onScroll = () => updatePosition();
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', onScroll);
        return () => {
            cancelAnimationFrame(id);
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', onScroll);
        };
    }, [open, updatePosition]);

    useEffect(() => {
        if (!open) return undefined;
        const handler = (e) => {
            if (triggerRef.current?.contains(e.target) || menuRef.current?.contains(e.target)) return;
            setOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [open]);

    const items = [
        { key: 'all', name: allLabel, vendor: null },
        ...vendors.map((v) => ({
            key: String(v.id || v.code),
            name: v.name || v.code || 'Vendor',
            vendor: v,
        })),
    ];

    const menu = open && createPortal(
        <div
            ref={menuRef}
            role="menu"
            style={{ position: 'fixed', top: menuPos.top, left: menuPos.left, zIndex: 99999 }}
            className="min-w-[15rem] rounded-xl border border-slate-200/90 bg-white py-1.5 shadow-xl shadow-slate-900/10 dark:border-slate-600 dark:bg-slate-900 dark:shadow-black/40"
        >
            {items.map((opt) => (
                <button
                    key={opt.key}
                    type="button"
                    role="menuitem"
                    onClick={() => {
                        setOpen(false);
                        onSelect?.(opt.vendor);
                    }}
                    className="flex w-full items-center gap-3 whitespace-nowrap px-4 py-2.5 text-left text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/90"
                >
                    <span>{opt.name}</span>
                </button>
            ))}
        </div>,
        document.body,
    );

    return (
        <>
            <button
                ref={triggerRef}
                type="button"
                aria-haspopup="menu"
                aria-expanded={open}
                disabled={disabled}
                title={title}
                onClick={() => {
                    if (disabled) return;
                    setOpen((o) => {
                        if (!o) setMenuPos(placeFixedMenu(triggerRef.current, null, { align: 'left' }));
                        return !o;
                    });
                }}
                className={`inline-flex items-center justify-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-900 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700 ${className}`.trim()}
            >
                {Icon ? <Icon className={`h-4 w-4 ${iconClassName}`.trim()} /> : null}
                {label}
                <ChevronDown className="h-4 w-4 opacity-70" />
            </button>
            {menu}
        </>
    );
}
