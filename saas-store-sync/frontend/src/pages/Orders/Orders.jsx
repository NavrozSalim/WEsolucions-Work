import { useCallback, useEffect, useMemo, useState } from 'react';
import {
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    FlaskConical,
    RefreshCw,
    Store as StoreIcon,
    Truck,
    X,
} from 'lucide-react';
import Button from '../../components/ui/Button';
import Input from '../../components/ui/Input';
import Select from '../../components/ui/Select';
import PageHeader from '../../components/design/PageHeader';
import EmptyState from '../../components/design/EmptyState';
import { getCatalogStores } from '../../services/catalogService';
import {
    completeOrderShipping,
    createTestOrder,
    getOrders,
    submitOrderShipping,
} from '../../services/listingService';

const STATUS_STYLES = {
    new: 'bg-sky-50 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300',
    paid: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
    cancelled: 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400',
    refunded: 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300',
    sent: 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300',
    shipping_submitted: 'bg-violet-50 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300',
    shipping_complete: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
};

const STATUS_LABELS = {
    new: 'New',
    paid: 'Paid',
    cancelled: 'Cancelled',
    refunded: 'Refunded',
    sent: 'Sent',
    shipping_submitted: 'Shipping submitted',
    shipping_complete: 'Shipping complete',
};

function money(cents) {
    if (cents == null) return '—';
    return `$${(cents / 100).toFixed(2)}`;
}

function customerName(order) {
    const c = order.customer_info_json;
    if (!c || typeof c !== 'object') return '—';
    const name = [c.firstName || c.first_name, c.lastName || c.last_name].filter(Boolean).join(' ');
    return name || c.name || c.email || '—';
}

function itemsSummary(order) {
    const items = order.line_items_json;
    if (!Array.isArray(items) || items.length === 0) return '—';
    const count = items.reduce((sum, it) => sum + (Number(it?.quantity ?? it?.qty) || 1), 0);
    return `${count} item${count !== 1 ? 's' : ''}`;
}

function ShippingModal({ open, onClose, onSubmit, order, loading }) {
    const [form, setForm] = useState({ tracking_number: '', carrier: '', tracking_url: '', shipped_date: '' });
    const [error, setError] = useState('');

    useEffect(() => {
        if (open) {
            setForm({ tracking_number: '', carrier: '', tracking_url: '', shipped_date: '' });
            setError('');
        }
    }, [open]);

    if (!open) return null;

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!form.tracking_number.trim() || !form.carrier.trim()) {
            setError('Tracking number and carrier are required.');
            return;
        }
        onSubmit(form);
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
            <div
                className="relative w-full max-w-md overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-700 px-6 py-4">
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                        Submit shipping — {order?.invoice_number || order?.external_order_key}
                    </h2>
                    <button type="button" className="rounded-md p-2 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={onClose}>
                        <X className="h-5 w-5" />
                    </button>
                </div>
                <form onSubmit={handleSubmit} className="space-y-4 px-6 py-4">
                    {error && (
                        <div className="rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
                            {error}
                        </div>
                    )}
                    <Input
                        label="Tracking number"
                        value={form.tracking_number}
                        onChange={(e) => setForm((f) => ({ ...f, tracking_number: e.target.value }))}
                        required
                    />
                    <Input
                        label="Carrier"
                        placeholder="e.g. Australia Post"
                        value={form.carrier}
                        onChange={(e) => setForm((f) => ({ ...f, carrier: e.target.value }))}
                        required
                    />
                    <Input
                        label="Tracking URL (optional)"
                        value={form.tracking_url}
                        onChange={(e) => setForm((f) => ({ ...f, tracking_url: e.target.value }))}
                    />
                    <Input
                        label="Shipped date (optional)"
                        type="date"
                        value={form.shipped_date}
                        onChange={(e) => setForm((f) => ({ ...f, shipped_date: e.target.value }))}
                    />
                    <div className="flex justify-end gap-2 pt-2">
                        <Button variant="secondary" type="button" onClick={onClose} disabled={loading}>
                            Cancel
                        </Button>
                        <Button variant="primary" type="submit" disabled={loading}>
                            {loading ? 'Sending…' : 'Send to marketplace'}
                        </Button>
                    </div>
                </form>
            </div>
        </div>
    );
}

export default function Orders() {
    const [stores, setStores] = useState([]);
    const [storesLoading, setStoresLoading] = useState(true);
    const [selectedStore, setSelectedStore] = useState('');
    const [orders, setOrders] = useState([]);
    const [loading, setLoading] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [creatingTest, setCreatingTest] = useState(false);
    const [message, setMessage] = useState(null); // { text, variant }
    const [expandedId, setExpandedId] = useState(null);
    const [shippingOrder, setShippingOrder] = useState(null);
    const [shippingLoading, setShippingLoading] = useState(false);
    const [completingId, setCompletingId] = useState(null);

    // Only managed (full_store) stores handle orders through the system.
    useEffect(() => {
        getCatalogStores()
            .then((res) => {
                const managed = (Array.isArray(res.data) ? res.data : []).filter(
                    (s) => s.management_mode === 'full_store'
                );
                setStores(managed);
                if (managed.length === 1) setSelectedStore(managed[0].id);
            })
            .catch(() => setMessage({ text: 'Failed to load stores.', variant: 'error' }))
            .finally(() => setStoresLoading(false));
    }, []);

    const loadOrders = useCallback(
        (refresh = false) => {
            if (!selectedStore) return;
            const setter = refresh ? setRefreshing : setLoading;
            setter(true);
            getOrders(selectedStore, { refresh })
                .then((res) => {
                    setOrders(Array.isArray(res.data?.orders) ? res.data.orders : []);
                    const r = res.data?.refresh;
                    if (refresh && r) {
                        setMessage({ text: r.message || 'Orders refreshed.', variant: r.ok ? 'success' : 'error' });
                    }
                })
                .catch((err) => {
                    setMessage({
                        text: err.response?.data?.detail || 'Failed to load orders.',
                        variant: 'error',
                    });
                })
                .finally(() => setter(false));
        },
        [selectedStore]
    );

    useEffect(() => {
        setOrders([]);
        setMessage(null);
        setExpandedId(null);
        if (selectedStore) loadOrders(false);
    }, [selectedStore, loadOrders]);

    const handleCreateTestOrder = () => {
        setCreatingTest(true);
        createTestOrder(selectedStore)
            .then((res) => {
                setMessage({ text: res.data?.message || 'Test order created.', variant: 'success' });
                loadOrders(true);
            })
            .catch((err) => {
                setMessage({
                    text: err.response?.data?.detail || err.response?.data?.message || 'Test order failed.',
                    variant: 'error',
                });
            })
            .finally(() => setCreatingTest(false));
    };

    const handleSubmitShipping = (form) => {
        if (!shippingOrder) return;
        setShippingLoading(true);
        submitOrderShipping(selectedStore, shippingOrder.id, form)
            .then((res) => {
                setMessage({ text: res.data?.message || 'Shipping info sent.', variant: 'success' });
                setShippingOrder(null);
                loadOrders(false);
            })
            .catch((err) => {
                setMessage({
                    text: err.response?.data?.detail || err.response?.data?.message || 'Shipping submit failed.',
                    variant: 'error',
                });
            })
            .finally(() => setShippingLoading(false));
    };

    const handleComplete = (order) => {
        setCompletingId(order.id);
        completeOrderShipping(selectedStore, order.id)
            .then((res) => {
                setMessage({ text: res.data?.message || 'Shipping marked complete.', variant: 'success' });
                loadOrders(false);
            })
            .catch((err) => {
                setMessage({
                    text: err.response?.data?.detail || err.response?.data?.message || 'Mark complete failed.',
                    variant: 'error',
                });
            })
            .finally(() => setCompletingId(null));
    };

    const selectedStoreData = useMemo(
        () => stores.find((s) => s.id === selectedStore),
        [stores, selectedStore]
    );

    return (
        <div className="space-y-4">
            <PageHeader
                title="Orders"
                description="Orders from your managed marketplace stores. Submit tracking and mark shipments complete."
            />

            {message && (
                <div
                    className={`rounded-lg border px-4 py-3 text-sm ${
                        message.variant === 'error'
                            ? 'border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-300'
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
                        options={[
                            { value: '', label: storesLoading ? 'Loading stores…' : 'Select a store' },
                            ...stores.map((s) => ({
                                value: s.id,
                                label: `${s.name}${s.marketplace_name ? ` (${s.marketplace_name})` : ''}`,
                            })),
                        ]}
                    />
                </div>
                {selectedStore && (
                    <div className="flex items-center gap-2 pb-0.5">
                        <Button variant="primary" size="sm" onClick={() => loadOrders(true)} disabled={refreshing}>
                            <RefreshCw className={`mr-1.5 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
                            {refreshing ? 'Fetching…' : 'Fetch from marketplace'}
                        </Button>
                        {(selectedStoreData?.marketplace_code || '').toLowerCase() === 'lasoo' && (
                            <Button variant="secondary" size="sm" onClick={handleCreateTestOrder} disabled={creatingTest}>
                                <FlaskConical className="mr-1.5 h-4 w-4" />
                                {creatingTest ? 'Creating…' : 'Create test order'}
                            </Button>
                        )}
                    </div>
                )}
            </div>

            {!storesLoading && stores.length === 0 && (
                <EmptyState
                    icon={StoreIcon}
                    title="No managed stores yet"
                    description='Create a store with the "Managed store" option (Reverb or Lasoo) to manage orders here.'
                />
            )}

            {selectedStore && (
                <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
                    <div className="overflow-x-auto">
                        {loading ? (
                            <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">Loading orders…</p>
                        ) : orders.length === 0 ? (
                            <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">
                                No orders yet. Use “Fetch from marketplace” to pull the latest orders.
                            </p>
                        ) : (
                            <table className="w-full text-left text-sm">
                                <thead className="bg-slate-50 dark:bg-slate-800 text-xs uppercase text-slate-500 dark:text-slate-400">
                                    <tr>
                                        <th className="px-4 py-2.5">Invoice</th>
                                        <th className="px-4 py-2.5">Customer</th>
                                        <th className="px-4 py-2.5">Items</th>
                                        <th className="px-4 py-2.5">Total</th>
                                        <th className="px-4 py-2.5">Status</th>
                                        <th className="px-4 py-2.5">Env</th>
                                        <th className="px-4 py-2.5 text-right">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {orders.map((o) => (
                                        <>
                                            <tr key={o.id} className="border-t border-slate-100 dark:border-slate-800">
                                                <td className="px-4 py-2.5 font-medium text-slate-900 dark:text-slate-100">
                                                    {o.invoice_number || o.external_order_key || '—'}
                                                </td>
                                                <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">{customerName(o)}</td>
                                                <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">{itemsSummary(o)}</td>
                                                <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">{money(o.total_amount_cents)}</td>
                                                <td className="px-4 py-2.5">
                                                    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[o.status] || STATUS_STYLES.new}`}>
                                                        {STATUS_LABELS[o.status] || o.status}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400">{o.environment}</td>
                                                <td className="px-4 py-2.5">
                                                    <div className="flex items-center justify-end gap-1">
                                                        <button
                                                            type="button"
                                                            title="Submit shipping / tracking"
                                                            className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                                                            onClick={() => setShippingOrder(o)}
                                                        >
                                                            <Truck className="h-4 w-4" />
                                                        </button>
                                                        <button
                                                            type="button"
                                                            title="Mark shipping complete (delivered)"
                                                            className="rounded-md p-1.5 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 disabled:opacity-40"
                                                            onClick={() => handleComplete(o)}
                                                            disabled={completingId === o.id || o.shipping_status !== 'submitted'}
                                                        >
                                                            <CheckCircle2 className="h-4 w-4" />
                                                        </button>
                                                        <button
                                                            type="button"
                                                            title="Details"
                                                            className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                                                            onClick={() => setExpandedId(expandedId === o.id ? null : o.id)}
                                                        >
                                                            {expandedId === o.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                                                        </button>
                                                    </div>
                                                </td>
                                            </tr>
                                            {expandedId === o.id && (
                                                <tr key={`${o.id}-details`} className="border-t border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-800/40">
                                                    <td colSpan={7} className="px-4 py-3">
                                                        <div className="grid grid-cols-1 gap-4 text-xs lg:grid-cols-2">
                                                            <div>
                                                                <p className="mb-1 font-semibold text-slate-700 dark:text-slate-300">Line items</p>
                                                                {Array.isArray(o.line_items_json) && o.line_items_json.length > 0 ? (
                                                                    <ul className="space-y-1 text-slate-600 dark:text-slate-400">
                                                                        {o.line_items_json.map((it, i) => (
                                                                            <li key={i}>
                                                                                {(it?.quantity ?? it?.qty ?? 1)}× {it?.externalVariantKey || it?.variantKey || it?.sku || it?.name || 'item'}
                                                                                {it?.priceCents != null && ` — ${money(it.priceCents)}`}
                                                                            </li>
                                                                        ))}
                                                                    </ul>
                                                                ) : (
                                                                    <p className="text-slate-500">No line item details.</p>
                                                                )}
                                                            </div>
                                                            <div>
                                                                <p className="mb-1 font-semibold text-slate-700 dark:text-slate-300">Shipments</p>
                                                                {o.shipments?.length ? (
                                                                    <ul className="space-y-1 text-slate-600 dark:text-slate-400">
                                                                        {o.shipments.map((sh) => (
                                                                            <li key={sh.id}>
                                                                                {sh.carrier || '—'} · {sh.tracking_number || 'no tracking'} · {sh.status}
                                                                                {sh.created_at && ` · ${new Date(sh.created_at).toLocaleString()}`}
                                                                            </li>
                                                                        ))}
                                                                    </ul>
                                                                ) : (
                                                                    <p className="text-slate-500">No shipments submitted yet.</p>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </td>
                                                </tr>
                                            )}
                                        </>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                </div>
            )}

            <ShippingModal
                open={!!shippingOrder}
                order={shippingOrder}
                onClose={() => setShippingOrder(null)}
                onSubmit={handleSubmitShipping}
                loading={shippingLoading}
            />
        </div>
    );
}
