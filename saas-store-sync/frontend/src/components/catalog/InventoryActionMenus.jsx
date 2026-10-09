import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, ChevronDown, RotateCcw } from 'lucide-react';
import { placeFixedMenu } from '../../utils/fixedMenuPosition';

/** Same Critical action choices on complete-store and inventory-only connections. */
export const CRITICAL_ACTION_OPTIONS = [
    {
        id: 'failed_zero',
        label: 'Zero inventory against failed products',
        modalTitle: 'Zero failed listing inventory',
        modalMessage:
            'Every active listing with status Failed, or Needs attention when that status exists, will have local stock set to 0 and inventory pushed to the marketplace (where connected). Synced listings are not changed and the store stays active.',
        confirmLabel: 'Yes, zero failed listings',
    },
    {
        id: 'full_critical',
        label: 'Critical option',
        modalTitle: 'Critical action',
        modalMessage:
            'If you click Yes, all listing inventory for this store will be set to 0 on the marketplace (where possible), local stock will be cleared, and this store will be deactivated including its scheduled sync toggle. Only use this if something went wrong and you need an immediate stop.',
        confirmLabel: 'Yes, zero inventory and deactivate',
    },
];

/**
 * Same Reset status choices on both connections.
 * ``needs_attention`` is omitted for complete stores — that status does not exist there.
 */
export const RESET_PENDING_OPTIONS = [
    {
        scope: 'failed',
        label: 'Reset failed → Pending',
        modalTitle: 'Reset failed products',
        modalMessage:
            'Every active listing with status Failed will be marked Pending. Scrape errors are cleared so you can run Start Scraping again. Synced listings are not changed.',
    },
    {
        scope: 'scraped',
        label: 'Reset scraped → Pending',
        modalTitle: 'Reset scraped products',
        modalMessage:
            'Every active listing with status Scraped will be marked Pending so you can run Start Scraping again. Synced, failed, and needs-attention listings are not changed. This does not fetch prices by itself.',
    },
    {
        scope: 'needs_attention',
        label: 'Reset needs attention → Pending',
        modalTitle: 'Reset needs-attention products',
        modalMessage:
            'Every active listing with status Needs attention will be marked Pending. Scrape errors are cleared so you can run Start Scraping again. Other listings are not changed.',
    },
    {
        scope: 'all',
        label: 'Reset all → Pending',
        modalTitle: 'Reset all to Pending',
        modalMessage:
            'Every active product listing for this store will be marked Pending (scraped/synced rows included). This clears scrape errors so you can run Start Scraping again. It does not fetch prices by itself.',
    },
];

export function resetOptionsForConnection(managementMode) {
    if (managementMode === 'full_store') {
        return RESET_PENDING_OPTIONS.filter((opt) => opt.scope !== 'needs_attention');
    }
    return RESET_PENDING_OPTIONS;
}

function ActionMenu({ open, menuPos, menuRef, children }) {
    if (!open) return null;
    return createPortal(
        <div
            ref={menuRef}
            role="menu"
            style={{ position: 'fixed', top: menuPos.top, left: menuPos.left, zIndex: 99999 }}
            className="min-w-60 rounded-xl border border-slate-200/90 bg-white py-1.5 shadow-xl shadow-slate-900/10 dark:border-slate-600 dark:bg-slate-900 dark:shadow-black/40"
        >
            {children}
        </div>,
        document.body,
    );
}

function useActionMenu() {
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

    const toggle = () => {
        setOpen((current) => {
            if (!current) setMenuPos(placeFixedMenu(triggerRef.current, null, { align: 'left' }));
            return !current;
        });
    };

    return { open, setOpen, menuPos, triggerRef, menuRef, toggle };
}

export function CriticalActionDropdown({ disabled, loading, options = CRITICAL_ACTION_OPTIONS, onSelectAction }) {
    const { open, setOpen, menuPos, triggerRef, menuRef, toggle } = useActionMenu();

    return (
        <>
            <button
                ref={triggerRef}
                type="button"
                aria-haspopup="menu"
                aria-expanded={open}
                disabled={disabled}
                title="Emergency inventory actions"
                onClick={() => {
                    if (disabled) return;
                    toggle();
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-white px-3 py-2 text-sm font-medium text-rose-700 shadow-xs transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-rose-900 dark:bg-slate-800 dark:text-rose-300 dark:hover:bg-rose-950/40"
            >
                <AlertTriangle className={`h-4 w-4 ${loading ? 'animate-pulse' : ''}`} />
                Critical action
                <ChevronDown className="h-4 w-4 opacity-70" />
            </button>
            <ActionMenu open={open} menuPos={menuPos} menuRef={menuRef}>
                {options.map((opt) => (
                    <button
                        key={opt.id}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                            setOpen(false);
                            onSelectAction(opt);
                        }}
                        className="flex w-full items-center gap-3 whitespace-nowrap px-4 py-2.5 text-left text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/90"
                    >
                        <AlertTriangle className="h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400" />
                        <span>{opt.label}</span>
                    </button>
                ))}
            </ActionMenu>
        </>
    );
}

export function ResetPendingDropdown({ disabled, loading, options = RESET_PENDING_OPTIONS, onSelectScope }) {
    const { open, setOpen, menuPos, triggerRef, menuRef, toggle } = useActionMenu();

    return (
        <>
            <button
                ref={triggerRef}
                type="button"
                aria-haspopup="menu"
                aria-expanded={open}
                disabled={disabled}
                title="Reset listing status so you can scrape again"
                onClick={() => {
                    if (disabled) return;
                    toggle();
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-xs transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700/80"
            >
                <RotateCcw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                Reset status
                <ChevronDown className="h-4 w-4 opacity-70" />
            </button>
            <ActionMenu open={open} menuPos={menuPos} menuRef={menuRef}>
                {options.map((opt) => (
                    <button
                        key={opt.scope}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                            setOpen(false);
                            onSelectScope(opt);
                        }}
                        className="flex w-full items-center gap-3 whitespace-nowrap px-4 py-2.5 text-left text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/90"
                    >
                        <RotateCcw className="h-4 w-4 shrink-0" />
                        <span>{opt.label}</span>
                    </button>
                ))}
            </ActionMenu>
        </>
    );
}
