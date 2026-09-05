import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
    FileDown,
    FlaskConical,
    Mail,
    MessageSquare,
    Receipt,
    RefreshCw,
    Send,
    User,
} from 'lucide-react';
import Button from '../../components/ui/Button';
import Select from '../../components/ui/Select';
import PageHeader from '../../components/design/PageHeader';
import EmptyState from '../../components/design/EmptyState';
import { getCatalogStores } from '../../services/catalogService';
import {
    createTestTicket,
    exportTicketsExcel,
    getTickets,
    replyToTicket,
} from '../../services/listingService';

const STATUS_STYLES = {
    open: 'bg-sky-50 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300',
    pending: 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300',
    answered: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
    closed: 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400',
};

function formatDate(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
}

export default function Tickets() {
    const [searchParams, setSearchParams] = useSearchParams();
    const initialStore = searchParams.get('store') || '';
    const initialTicket = searchParams.get('ticket') || null;

    const [stores, setStores] = useState([]);
    const [storesLoading, setStoresLoading] = useState(true);
    const [selectedStore, setSelectedStore] = useState(initialStore);
    const [tickets, setTickets] = useState([]);
    const [loading, setLoading] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [creatingTest, setCreatingTest] = useState(false);
    const [message, setMessage] = useState(null);
    const [selectedId, setSelectedId] = useState(initialTicket);
    const [replyBody, setReplyBody] = useState('');
    const [replyLoading, setReplyLoading] = useState(false);
    const [exporting, setExporting] = useState(false);

    useEffect(() => {
        getCatalogStores()
            .then((res) => {
                const managed = (Array.isArray(res.data) ? res.data : []).filter(
                    (s) => s.management_mode === 'full_store'
                );
                setStores(managed);
                if (initialStore && managed.some((s) => s.id === initialStore)) {
                    setSelectedStore(initialStore);
                } else if (!initialStore && managed.length === 1) {
                    setSelectedStore(managed[0].id);
                }
            })
            .catch(() => setMessage({ text: 'Failed to load stores.', variant: 'error' }))
            .finally(() => setStoresLoading(false));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const loadTickets = useCallback(
        (refresh = false) => {
            if (!selectedStore) return;
            const setter = refresh ? setRefreshing : setLoading;
            setter(true);
            getTickets(selectedStore, { refresh })
                .then((res) => {
                    const list = Array.isArray(res.data?.tickets) ? res.data.tickets : [];
                    setTickets(list);
                    const r = res.data?.refresh;
                    if (refresh && r) {
                        const variant = !r.ok
                            ? 'error'
                            : r.marketplace_supported === false
                              ? 'warning'
                              : 'success';
                        setMessage({ text: r.message || 'Tickets refreshed.', variant });
                    }
                    setSelectedId((prev) => {
                        const want = initialTicket || prev;
                        if (want && list.some((t) => t.id === want)) return want;
                        return list[0]?.id || null;
                    });
                })
                .catch((err) => {
                    setMessage({
                        text: err.response?.data?.detail || 'Failed to load tickets.',
                        variant: 'error',
                    });
                })
                .finally(() => setter(false));
        },
        [selectedStore, initialTicket]
    );

    useEffect(() => {
        setTickets([]);
        setReplyBody('');
        setMessage(null);
        if (selectedStore) {
            setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.set('store', selectedStore);
                return next;
            }, { replace: true });
            loadTickets(false);
        }
    }, [selectedStore, loadTickets, setSearchParams]);

    useEffect(() => {
        if (!selectedId || !selectedStore) return;
        setSearchParams((prev) => {
            const next = new URLSearchParams(prev);
            next.set('store', selectedStore);
            next.set('ticket', selectedId);
            return next;
        }, { replace: true });
    }, [selectedId, selectedStore, setSearchParams]);

    const selected = useMemo(
        () => tickets.find((t) => t.id === selectedId) || null,
        [tickets, selectedId]
    );

    const handleCreateTest = () => {
        setCreatingTest(true);
        createTestTicket(selectedStore)
            .then((res) => {
                setMessage({ text: res.data?.message || 'Test ticket created.', variant: 'success' });
                loadTickets(false);
            })
            .catch((err) => {
                setMessage({
                    text: err.response?.data?.detail || err.response?.data?.message || 'Test ticket failed.',
                    variant: 'error',
                });
            })
            .finally(() => setCreatingTest(false));
    };

    const handleReply = (e) => {
        e.preventDefault();
        if (!selected || !replyBody.trim()) return;
        setReplyLoading(true);
        replyToTicket(selectedStore, selected.id, { body: replyBody.trim() })
            .then((res) => {
                const marketplaceOk = res.data?.marketplace_ok === true;
                setMessage({
                    text: res.data?.message || 'Reply saved.',
                    variant: marketplaceOk ? 'success' : 'warning',
                });
                setReplyBody('');
                loadTickets(false);
            })
            .catch((err) => {
                setMessage({
                    text: err.response?.data?.detail || err.response?.data?.message || 'Reply failed.',
                    variant: 'error',
                });
            })
            .finally(() => setReplyLoading(false));
    };

    const selectedStoreData = useMemo(
        () => stores.find((s) => s.id === selectedStore),
        [stores, selectedStore]
    );
    const isBunnings =
        (selectedStoreData?.marketplace_code || selectedStoreData?.marketplace_name || '')
            .toString()
            .trim()
            .toLowerCase() === 'bunnings';

    const handleExportExcel = () => {
        if (!selectedStore || exporting) return;
        setExporting(true);
        exportTicketsExcel(selectedStore, selectedStoreData?.name || '')
            .then(() => setMessage({ text: 'Tickets Excel downloaded.', variant: 'success' }))
            .catch((err) => {
                setMessage({
                    text: err.response?.data?.detail || 'Failed to export tickets.',
                    variant: 'error',
                });
            })
            .finally(() => setExporting(false));
    };

    const storeOptions = [
        { value: '', label: storesLoading ? 'Loading stores…' : 'Select a store' },
        ...stores.map((s) => ({
            value: s.id,
            label: `${s.name}${s.marketplace_name ? ` (${s.marketplace_name})` : ''}`,
        })),
    ];

    return (
        <div className="space-y-4">
            <PageHeader
                title="Tickets"
                description="Customer messages from your marketplace stores. Synced hourly; reply here to answer the customer."
            />

            {message && (
                <div
                    className={`rounded-lg border px-4 py-3 text-sm ${
                        message.variant === 'error'
                            ? 'border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-300'
                            : message.variant === 'warning'
                              ? 'border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200'
                              : 'border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300'
                    }`}
                >
                    {message.text}
                </div>
            )}

            <div className="flex flex-wrap items-end gap-3">
                <div className="w-full max-w-xs">
                    <Select
                        label="Managed store"
                        value={selectedStore}
                        onChange={(e) => setSelectedStore(e.target.value)}
                        options={storeOptions}
                        disabled={storesLoading}
                    />
                </div>
                <Button
                    variant="secondary"
                    type="button"
                    onClick={() => loadTickets(true)}
                    disabled={!selectedStore || refreshing || loading}
                >
                    <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
                    {refreshing ? 'Syncing…' : 'Fetch from marketplace'}
                </Button>
                <Button
                    variant="secondary"
                    type="button"
                    onClick={handleExportExcel}
                    disabled={!selectedStore || exporting || loading || tickets.length === 0}
                >
                    <FileDown className={`mr-2 h-4 w-4 ${exporting ? 'opacity-50' : ''}`} />
                    {exporting ? 'Exporting…' : 'Export Excel'}
                </Button>
                {!isBunnings && (
                <Button
                    variant="secondary"
                    type="button"
                    onClick={handleCreateTest}
                    disabled={!selectedStore || creatingTest}
                >
                    <FlaskConical className="mr-2 h-4 w-4" />
                    {creatingTest ? 'Creating…' : 'Create test ticket'}
                </Button>
                )}
            </div>

            {!selectedStore ? (
                <EmptyState
                    icon={MessageSquare}
                    title="Select a store"
                    description="Choose a managed store to view and answer customer tickets."
                />
            ) : loading ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">Loading tickets…</p>
            ) : (
                <div className="grid gap-4 lg:grid-cols-[26rem_1fr] min-h-[28rem]">
                    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 overflow-hidden">
                        <div className="border-b border-slate-200 dark:border-slate-700 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                            Inbox ({tickets.length})
                        </div>
                        {tickets.length === 0 ? (
                            <p className="px-4 py-8 text-sm text-slate-500 dark:text-slate-400">
                                No tickets yet. Use “Fetch from marketplace” (hourly sync also runs automatically)
                                {isBunnings ? '.' : ' or “Create test ticket”.'}
                            </p>
                        ) : (
                            <ul className="divide-y divide-slate-100 dark:divide-slate-800 max-h-[32rem] overflow-y-auto">
                                {tickets.map((t) => (
                                    <li key={t.id}>
                                        <button
                                            type="button"
                                            onClick={() => setSelectedId(t.id)}
                                            className={`w-full text-left px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/60 ${
                                                selectedId === t.id ? 'bg-slate-50 dark:bg-slate-800/80' : ''
                                            }`}
                                        >
                                            <div className="flex items-start justify-between gap-2">
                                                <p className="font-semibold text-sm text-slate-900 dark:text-slate-100 line-clamp-1">
                                                    {t.customer_name || t.customer_email || 'Unknown customer'}
                                                </p>
                                                {t.unread_count > 0 && (
                                                    <span className="shrink-0 rounded-full bg-sky-600 text-white text-[10px] px-1.5 py-0.5">
                                                        {t.unread_count}
                                                    </span>
                                                )}
                                            </div>
                                            <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300 line-clamp-1">
                                                {t.subject || 'Customer message'}
                                            </p>
                                            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400">
                                                <span className="inline-flex items-center gap-1">
                                                    <Receipt className="h-3 w-3" />
                                                    {t.related_order_key
                                                        ? `Invoice ${t.related_order_key}`
                                                        : 'No invoice linked'}
                                                </span>
                                                <span className={`inline-block rounded-full px-2 py-0.5 font-medium ${STATUS_STYLES[t.status] || STATUS_STYLES.open}`}>
                                                    {t.status}
                                                </span>
                                                <span className="ml-auto">{formatDate(t.last_message_at || t.created_at)}</span>
                                            </div>
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>

                    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 flex flex-col min-h-[28rem]">
                        {!selected ? (
                            <div className="flex flex-1 items-center justify-center p-8 text-sm text-slate-500">
                                Select a ticket to view the conversation.
                            </div>
                        ) : (
                            <>
                                <div className="border-b border-slate-200 dark:border-slate-700 px-5 py-4">
                                    <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
                                        {selected.subject || 'Customer message'}
                                    </h2>
                                    <div className="mt-2 grid gap-1.5 text-sm text-slate-600 dark:text-slate-300 sm:grid-cols-2">
                                        <p className="inline-flex items-center gap-1.5 min-w-0">
                                            <User className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                            <span className="font-medium text-slate-900 dark:text-slate-100 truncate">
                                                {selected.customer_name || '—'}
                                            </span>
                                            {selected.customer_email ? (
                                                <span className="text-xs text-slate-500 truncate">· {selected.customer_email}</span>
                                            ) : null}
                                        </p>
                                        <p className="inline-flex items-center gap-1.5">
                                            <Receipt className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                            {selected.related_order_key ? (
                                                <Link
                                                    to={`/orders?store=${encodeURIComponent(selectedStore)}&order=${encodeURIComponent(selected.related_order_key)}`}
                                                    className="font-medium text-sky-600 hover:underline dark:text-sky-400"
                                                >
                                                    Invoice {selected.related_order_key}
                                                </Link>
                                            ) : (
                                                <span className="text-slate-500">No invoice linked</span>
                                            )}
                                        </p>
                                        <p className="inline-flex items-center gap-1.5 text-xs text-slate-500 sm:col-span-2">
                                            <Mail className="h-3.5 w-3.5" />
                                            Status: <span className="capitalize">{selected.status}</span>
                                            {' · '}
                                            <span className="capitalize">{selected.environment}</span>
                                        </p>
                                    </div>
                                </div>

                                <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4 max-h-[22rem]">
                                    {(selected.messages || []).map((m) => {
                                        const outbound = m.direction === 'outbound';
                                        return (
                                            <div
                                                key={m.id}
                                                className={`flex ${outbound ? 'justify-end' : 'justify-start'}`}
                                            >
                                                <div
                                                    className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                                                        outbound
                                                            ? 'bg-sky-600 text-white'
                                                            : 'bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-100'
                                                    }`}
                                                >
                                                    <p className="whitespace-pre-wrap">{m.body || '—'}</p>
                                                    <p className={`mt-1 text-[11px] ${outbound ? 'text-sky-100' : 'text-slate-400'}`}>
                                                        {m.sender_name || m.sender_type || m.direction}
                                                        {' · '}
                                                        {formatDate(m.sent_at || m.created_at)}
                                                        {outbound && (
                                                            <>
                                                                {' · '}
                                                                {m.delivered_to_marketplace ? 'Delivered' : 'Saved locally'}
                                                            </>
                                                        )}
                                                    </p>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>

                                <form onSubmit={handleReply} className="border-t border-slate-200 dark:border-slate-700 p-4">
                                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                                        Reply to customer
                                    </label>
                                    <textarea
                                        value={replyBody}
                                        onChange={(e) => setReplyBody(e.target.value)}
                                        rows={3}
                                        placeholder="Type your reply…"
                                        className="w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100"
                                    />
                                    <div className="mt-2 flex justify-end">
                                        <Button type="submit" disabled={replyLoading || !replyBody.trim()}>
                                            <Send className="mr-2 h-4 w-4" />
                                            {replyLoading ? 'Sending…' : 'Send reply'}
                                        </Button>
                                    </div>
                                </form>
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
