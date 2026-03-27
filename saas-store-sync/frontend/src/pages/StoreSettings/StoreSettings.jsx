import { useState, useEffect, useCallback, useRef, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence } from 'framer-motion';
import { Search, Plus, DollarSign, Trash2, Copy, Store, Wifi, WifiOff, Clock, RefreshCw, MoreVertical } from 'lucide-react';
import {
    getStores,
    getStore,
    deleteStore,
    getMarketplaces,
    updateStoreActive,
    validateStore,
    triggerStoreUpdate,
    pollStoreUpdateJob,
    formatStoreUpdateResult,
} from '../../services/storeService';
import Button from '../../components/ui/Button';
import ConfirmModal from '../../components/ui/ConfirmModal';
import Toggle from '../../components/ui/Toggle';
import Toast from '../../components/ui/Toast';
import EmptyState from '../../components/design/EmptyState';
import CreateStoreModal from '../../components/stores/CreateStoreModal';
import StoreSettingsModal from '../../components/stores/StoreSettingsModal';

function ActionsDropdown({ store, conn, validatingId, updatingId, onValidate, onUpdate, onEdit, onDuplicate, onDelete }) {
    const [open, setOpen] = useState(false);
    const [menuPos, setMenuPos] = useState({ top: 0, right: 0 });
    const triggerRef = useRef(null);
    const menuRef = useRef(null);

    const updatePosition = useCallback(() => {
        const el = triggerRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        setMenuPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    }, []);

    useLayoutEffect(() => {
        if (!open) return;
        updatePosition();
        const onScroll = () => updatePosition();
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', onScroll);
        return () => {
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', onScroll);
        };
    }, [open, updatePosition]);

    useEffect(() => {
        if (!open) return;
        const handler = (e) => {
            const t = e.target;
            if (
                triggerRef.current?.contains(t)
                || menuRef.current?.contains(t)
            ) return;
            setOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [open]);

    const items = [
        {
            label: conn === 'connected' ? 'Re-verify' : 'Connect',
            icon: <Wifi className={`h-4 w-4 shrink-0 ${validatingId === store.id ? 'animate-pulse' : ''}`} />,
            onClick: () => onValidate(store),
            disabled: validatingId === store.id,
            className: conn === 'connected'
                ? 'text-emerald-600 dark:text-emerald-400'
                : 'text-amber-600 dark:text-amber-400',
        },
        {
            label: 'Update',
            icon: <RefreshCw className={`h-4 w-4 shrink-0 ${updatingId === store.id ? 'animate-spin' : ''}`} />,
            onClick: () => onUpdate(store),
            disabled: updatingId === store.id || conn !== 'connected',
            className: 'text-slate-700 dark:text-slate-300',
        },
        {
            label: 'Edit',
            icon: <DollarSign className="h-4 w-4 shrink-0" />,
            onClick: () => onEdit(store),
            className: 'text-accent-700 dark:text-accent-400',
        },
        {
            label: 'Duplicate',
            icon: <Copy className="h-4 w-4 shrink-0" />,
            onClick: () => onDuplicate(store),
            className: 'text-slate-700 dark:text-slate-300',
        },
        { divider: true },
        {
            label: 'Delete',
            icon: <Trash2 className="h-4 w-4 shrink-0" />,
            onClick: () => onDelete(store),
            className: 'text-rose-600 dark:text-rose-400',
        },
    ];

    const menu = open && createPortal(
        <div
            ref={menuRef}
            role="menu"
            style={{
                position: 'fixed',
                top: menuPos.top,
                right: menuPos.right,
                zIndex: 99999,
            }}
            className="min-w-[13.5rem] rounded-xl border border-slate-200/90 bg-white py-1.5 shadow-xl shadow-slate-900/10 dark:border-slate-600 dark:bg-slate-900 dark:shadow-black/40 overflow-visible"
        >
            {items.map((item, i) =>
                item.divider ? (
                    <div key={i} className="my-1.5 border-t border-slate-100 dark:border-slate-700/80" role="separator" />
                ) : (
                    <button
                        key={i}
                        type="button"
                        role="menuitem"
                        disabled={item.disabled}
                        onClick={() => { setOpen(false); item.onClick(); }}
                        className={`flex w-full items-center gap-3 whitespace-nowrap px-4 py-2.5 text-left text-sm font-medium transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/90 disabled:cursor-not-allowed disabled:opacity-40 ${item.className}`}
                    >
                        {item.icon}
                        <span>{item.label}</span>
                    </button>
                )
            )}
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
                onClick={() => {
                    setOpen((o) => {
                        if (!o && triggerRef.current) {
                            const r = triggerRef.current.getBoundingClientRect();
                            setMenuPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
                        }
                        return !o;
                    });
                }}
                className="inline-flex size-8 items-center justify-center rounded-lg text-slate-500 ring-offset-2 ring-offset-white transition hover:bg-slate-100 hover:text-slate-800 focus:outline-none focus:ring-2 focus:ring-accent-500 dark:text-slate-400 dark:ring-offset-slate-900 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                title="Actions"
            >
                <MoreVertical className="h-4 w-4" />
            </button>
            {menu}
        </>
    );
}

export default function StoreSettings() {
    const [stores, setStores] = useState([]);
    const [marketplaces, setMarketplaces] = useState([]);
    const [search, setSearch] = useState('');
    const [loading, setLoading] = useState(true);
    const [toast, setToast] = useState(null);
    const [createModalOpen, setCreateModalOpen] = useState(false);
    const [settingsStore, setSettingsStore] = useState(null);
    const [deleteConfirm, setDeleteConfirm] = useState(null);
    const [duplicateStore, setDuplicateStore] = useState(null);
    const [deletingId, setDeletingId] = useState(null);
    const [validatingId, setValidatingId] = useState(null);
    const [updatingId, setUpdatingId] = useState(null);

    const showToast = useCallback((msg, variant = 'info', duration = 4000) => {
        setToast({ msg, variant, duration });
    }, []);

    const dismissToast = useCallback(() => setToast(null), []);

    const refresh = useCallback(() => {
        setLoading(true);
        Promise.all([getStores(), getMarketplaces()])
            .then(([storesRes, mktRes]) => {
                const list = storesRes.data?.results || storesRes.data || [];
                setStores(Array.isArray(list) ? list : []);
                setMarketplaces(Array.isArray(mktRes.data) ? mktRes.data : []);
            })
            .catch(() => setStores([]))
            .finally(() => setLoading(false));
    }, []);

    useEffect(() => refresh(), [refresh]);

    const filteredStores = stores.filter((s) =>
        !search || (s.name || '').toLowerCase().includes(search.toLowerCase())
    );

    const handleOpenSettings = (store) => {
        getStore(store.id)
            .then((res) => setSettingsStore(res.data))
            .catch(() => showToast('Failed to load store details', 'error'));
    };

    const handleDuplicate = (store) => {
        getStore(store.id)
            .then((res) => setDuplicateStore(res.data))
            .catch(() => showToast('Failed to load store for duplication', 'error'));
    };

    const handleDeleteStore = (store) => {
        if (!store) return;
        setDeletingId(store.id);
        deleteStore(store.id)
            .then(() => {
                setDeleteConfirm(null);
                refresh();
                showToast(`Store "${store.name}" deleted.`, 'success');
            })
            .catch((err) => showToast(err.response?.data?.detail || 'Failed to delete store', 'error'))
            .finally(() => setDeletingId(null));
    };

    const handleValidate = (store) => {
        setValidatingId(store.id);
        validateStore(store.id)
            .then(() => {
                showToast(`"${store.name}" connected successfully.`, 'success');
                refresh();
            })
            .catch((err) => {
                showToast(err.response?.data?.message || 'Connection failed.', 'error');
                refresh();
            })
            .finally(() => setValidatingId(null));
    };

    const handleTriggerUpdate = (store) => {
        if (store.connection_status !== 'connected') {
            showToast('Connect store first.', 'error');
            return;
        }
        setUpdatingId(store.id);
        setToast({
            msg: `Updating "${store.name}" — scraping prices and pushing to marketplace…`,
            variant: 'info',
            duration: 180000,
        });
        triggerStoreUpdate(store.id, true)
            .then((res) => {
                const d = res.data;
                const jobId = d?.job_id;
                if (jobId) {
                    return pollStoreUpdateJob(store.id, jobId).then((result) => {
                        const { message, variant } = formatStoreUpdateResult(result);
                        setToast({ msg: message, variant, duration: variant === 'error' ? 12000 : 8000 });
                        refresh();
                    });
                }
                const { message, variant } = formatStoreUpdateResult(d);
                setToast({ msg: message, variant, duration: variant === 'error' ? 12000 : 8000 });
                refresh();
                return Promise.resolve();
            })
            .catch((err) => {
                const detail =
                    err.response?.data?.error ||
                    err.response?.data?.detail ||
                    err.message ||
                    'Update failed.';
                setToast({ msg: detail, variant: 'error', duration: 12000 });
            })
            .finally(() => setUpdatingId(null));
    };

    const handleToggleActive = (store, nextActive) => {
        if (!store) return;
        const prevActive = store.is_active !== false;
        setStores((prev) => prev.map((s) => (s.id === store.id ? { ...s, is_active: nextActive } : s)));
        updateStoreActive(store.id, nextActive)
            .then(() => showToast(`"${store.name}" ${nextActive ? 'activated' : 'deactivated'}.`))
            .catch((err) => {
                setStores((prev) => prev.map((s) => (s.id === store.id ? { ...s, is_active: prevActive } : s)));
                showToast(err.response?.data?.detail || 'Failed to update', 'error');
            });
    };

    return (
        <div className="space-y-6">
            {/* Toast - fixed top-right */}
            <AnimatePresence mode="wait">
                {toast && (
                    <Toast
                        key={toast.msg}
                        open={!!toast}
                        message={toast.msg}
                        variant={toast.variant}
                        duration={toast.duration ?? 4000}
                        onClose={dismissToast}
                    />
                )}
            </AnimatePresence>

            {/* Header */}
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Store Settings</h1>
                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                        Manage your connected stores, pricing rules, and inventory settings.
                    </p>
                </div>
                <Button variant="primary" onClick={() => setCreateModalOpen(true)}>
                    <Plus className="h-4 w-4 mr-2" />
                    Create New Store
                </Button>
            </div>

            {/* Table card */}
            <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 overflow-hidden">
                {/* Search */}
                <div className="p-4 border-b border-slate-200 dark:border-slate-700">
                    <div className="relative max-w-xs">
                        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                        <input
                            type="text"
                            placeholder="Search by name…"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            className="w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 py-2 pl-9 pr-3 text-sm text-slate-900 dark:text-slate-100 placeholder:text-slate-400 focus:border-accent-500 focus:ring-1 focus:ring-accent-500 outline-none transition"
                        />
                    </div>
                </div>

                {/* Content */}
                {loading ? (
                    <div className="flex justify-center py-16">
                        <div className="flex flex-col items-center gap-3">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 dark:border-slate-700 border-t-accent-500" />
                            <p className="text-sm text-slate-500 dark:text-slate-400">Loading stores…</p>
                        </div>
                    </div>
                ) : filteredStores.length === 0 ? (
                    <EmptyState
                        icon={stores.length === 0 ? Store : Search}
                        title={stores.length === 0 ? 'No stores yet' : 'No results found'}
                        description={
                            stores.length === 0
                                ? 'Create your first store to start syncing products.'
                                : 'No stores match your search. Try a different term.'
                        }
                        action={
                            stores.length === 0 ? (
                                <Button variant="primary" onClick={() => setCreateModalOpen(true)}>
                                    <Plus className="h-4 w-4 mr-2" />
                                    Create New Store
                                </Button>
                            ) : null
                        }
                    />
                ) : (
                    <div className="overflow-x-auto">
                        <table className="table-base">
                            <thead>
                                <tr>
                                    <th>Store Name</th>
                                    <th className="w-[140px]">Marketplace</th>
                                    <th className="w-[80px]">Region</th>
                                    <th className="w-[110px]">Connection</th>
                                    <th className="w-[120px]">Schedule</th>
                                    <th className="w-[70px] text-center">Active</th>
                                    <th className="w-[80px] text-right">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filteredStores.map((store) => {
                                    const conn = store.connection_status || 'pending';
                                    const sched = store.sync_schedule;
                                    const schedLabel = sched?.enabled
                                        ? (sched.crontab_hour?.includes('/') ? `Every ${sched.crontab_hour.replace('*/', '')}h` : `Daily ${sched.crontab_hour}:${(sched.crontab_minute || '0').padStart(2, '0')}`)
                                        : null;
                                    return (
                                        <tr key={store.id}>
                                            <td className="align-middle">
                                                <div className="flex items-center gap-3">
                                                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-slate-100 dark:bg-slate-800">
                                                        <Store className="h-4 w-4 text-slate-500 dark:text-slate-400" />
                                                    </div>
                                                    <span className="font-medium text-slate-900 dark:text-slate-100">{store.name}</span>
                                                </div>
                                            </td>
                                            <td className="text-slate-600 dark:text-slate-400 text-sm">
                                                {store.marketplace_name || '—'}
                                            </td>
                                            <td className="text-slate-500 dark:text-slate-400 text-sm">
                                                {store.region || '—'}
                                            </td>
                                            <td className="align-middle">
                                                <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full ${
                                                    conn === 'connected'
                                                        ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400'
                                                        : conn === 'error'
                                                            ? 'bg-rose-50 text-rose-700 dark:bg-rose-900/20 dark:text-rose-400'
                                                            : 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400'
                                                }`}>
                                                    {conn === 'connected' ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
                                                    {conn === 'connected' ? 'Connected' : conn === 'error' ? 'Error' : 'Pending'}
                                                </span>
                                            </td>
                                            <td className="align-middle text-sm text-slate-500 dark:text-slate-400">
                                                {schedLabel ? (
                                                    <span className="inline-flex items-center gap-1">
                                                        <Clock className="h-3 w-3" />
                                                        {schedLabel}
                                                    </span>
                                                ) : '—'}
                                            </td>
                                            <td className="text-center align-middle">
                                                <div className="flex justify-center items-center">
                                                    <Toggle
                                                        checked={store.is_active !== false}
                                                        onChange={(val) => handleToggleActive(store, val)}
                                                    />
                                                </div>
                                            </td>
                                            <td className="text-right align-middle">
                                                <ActionsDropdown
                                                    store={store}
                                                    conn={conn}
                                                    validatingId={validatingId}
                                                    updatingId={updatingId}
                                                    onValidate={handleValidate}
                                                    onUpdate={handleTriggerUpdate}
                                                    onEdit={handleOpenSettings}
                                                    onDuplicate={handleDuplicate}
                                                    onDelete={(s) => setDeleteConfirm(s)}
                                                />
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Modals */}
            <CreateStoreModal
                open={createModalOpen}
                onClose={() => setCreateModalOpen(false)}
                onSuccess={() => { refresh(); showToast('Store created.', 'success'); }}
                marketplaces={marketplaces}
            />
            <CreateStoreModal
                open={!!duplicateStore}
                onClose={() => setDuplicateStore(null)}
                onSuccess={() => { setDuplicateStore(null); refresh(); showToast('Store duplicated.', 'success'); }}
                copyFromStore={duplicateStore}
                marketplaces={marketplaces}
            />
            <StoreSettingsModal
                open={!!settingsStore}
                onClose={() => setSettingsStore(null)}
                onSuccess={() => { setSettingsStore(null); refresh(); showToast('Settings saved.', 'success'); }}
                store={settingsStore}
                marketplaces={marketplaces}
            />
            <ConfirmModal
                open={!!deleteConfirm}
                title="Delete store"
                message={`Delete "${deleteConfirm?.name}"? All products, mappings, and settings will be permanently removed. This cannot be undone.`}
                confirmLabel="Delete"
                variant="danger"
                loading={deletingId === deleteConfirm?.id}
                onConfirm={() => handleDeleteStore(deleteConfirm)}
                onCancel={() => setDeleteConfirm(null)}
            />
        </div>
    );
}
