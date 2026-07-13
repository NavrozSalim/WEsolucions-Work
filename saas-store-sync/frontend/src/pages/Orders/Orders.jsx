import { useCallback, useEffect, useMemo, useState } from 'react';
import {
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    FlaskConical,
    Mail,
    MapPin,
    Phone,
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

function money(cents, currency = 'AUD') {
    if (cents == null || Number.isNaN(Number(cents))) return '—';
    try {
        return new Intl.NumberFormat(undefined, {
            style: 'currency',
            currency: currency || 'AUD',
        }).format(Number(cents) / 100);
    } catch {
        return `$${(Number(cents) / 100).toFixed(2)}`;
    }
}

function formatDate(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
}

function orderDetails(order) {
    return order?.details || {};
}

function customerName(order) {
    const c = orderDetails(order).customer || order.customer_info_json || {};
    const name = [c.firstName || c.first_name, c.lastName || c.last_name].filter(Boolean).join(' ');
    return name || c.name || c.email || '—';
}

function itemsSummary(order) {
    const items = orderDetails(order).lineItems || order.line_items_json;
    if (!Array.isArray(items) || items.length === 0) return '—';
    const count = items.reduce((sum, it) => sum + (Number(it?.quantity ?? it?.qty) || 1), 0);
    return `${count} item${count !== 1 ? 's' : ''}`;
}

function formatAddress(addr) {
    if (!addr || typeof addr !== 'object') return null;
    const lines = [
        addr.company,
        addr.line1,
        addr.line2,
        [addr.city, addr.state, addr.postcode].filter(Boolean).join(' '),
        addr.country,
    ].filter(Boolean);
    return lines.length ? lines : null;
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

function DetailSection({ title, children }) {
    return (
        <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{title}</p>
            <div className="text-sm text-slate-700 dark:text-slate-300">{children}</div>
        </div>
    );
}

function OrderDetailPanel({ order }) {
    const d = orderDetails(order);
    const customer = d.customer || {};
    const shippingAddr = formatAddress(d.shippingAddress);
    const billingAddr = formatAddress(d.billingAddress);
    const marketplaceShip = d.shipping || {};
    const marketplaceShipAddr = formatAddress(marketplaceShip.address);
    const totals = d.totals || {};
    const currency = totals.currency || 'AUD';
    const lineItems = Array.isArray(d.lineItems) ? d.lineItems : [];
    const dates = d.dates || {};

    return (
        <div className="space-y-5">
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
                <DetailSection title="Customer">
                    <p className="font-medium text-slate-900 dark:text-slate-100">{customerName(order)}</p>
                    {customer.email && (
                        <p className="mt-1 flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
                            <Mail className="h-3.5 w-3.5 shrink-0" />
                            <a href={`mailto:${customer.email}`} className="hover:underline">{customer.email}</a>
                        </p>
                    )}
                    {customer.phone && (
                        <p className="mt-1 flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
                            <Phone className="h-3.5 w-3.5 shrink-0" />
                            <a href={`tel:${customer.phone}`} className="hover:underline">{customer.phone}</a>
                        </p>
                    )}
                    {!customer.email && !customer.phone && customerName(order) === '—' && (
                        <p className="text-slate-500">No customer details from Lasoo.</p>
                    )}
                </DetailSection>

                <DetailSection title="Shipping address">
                    {(shippingAddr || marketplaceShipAddr) ? (
                        <div className="flex gap-1.5">
                            <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                            <div>
                                {(shippingAddr || marketplaceShipAddr).map((line) => (
                                    <p key={line}>{line}</p>
                                ))}
                            </div>
                        </div>
                    ) : (
                        <p className="text-slate-500">No shipping address.</p>
                    )}
                </DetailSection>

                <DetailSection title="Order info">
                    <dl className="space-y-1 text-slate-600 dark:text-slate-400">
                        <div className="flex justify-between gap-4">
                            <dt>Invoice</dt>
                            <dd className="font-medium text-slate-900 dark:text-slate-100">{order.invoice_number || '—'}</dd>
                        </div>
                        <div className="flex justify-between gap-4">
                            <dt>Order key</dt>
                            <dd className="font-mono text-xs">{order.external_order_key || '—'}</dd>
                        </div>
                        <div className="flex justify-between gap-4">
                            <dt>Ordered</dt>
                            <dd>{formatDate(dates.orderedAt || order.created_at)}</dd>
                        </div>
                        {dates.paidAt && (
                            <div className="flex justify-between gap-4">
                                <dt>Paid</dt>
                                <dd>{formatDate(dates.paidAt)}</dd>
                            </div>
                        )}
                        {d.marketplaceStatus && (
                            <div className="flex justify-between gap-4">
                                <dt>Lasoo status</dt>
                                <dd className="capitalize">{String(d.marketplaceStatus)}</dd>
                            </div>
                        )}
                        <div className="flex justify-between gap-4">
                            <dt>Environment</dt>
                            <dd className="capitalize">{order.environment}</dd>
                        </div>
                    </dl>
                </DetailSection>
            </div>

            {billingAddr && (
                <DetailSection title="Billing address">
                    {billingAddr.map((line) => (
                        <p key={line}>{line}</p>
                    ))}
                </DetailSection>
            )}

            <DetailSection title="Line items">
                {lineItems.length > 0 ? (
                    <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">
                        <table className="w-full text-left text-xs">
                            <thead className="bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
                                <tr>
                                    <th className="px-3 py-2 font-semibold">Product</th>
                                    <th className="px-3 py-2 font-semibold">SKU / Variant</th>
                                    <th className="px-3 py-2 text-right font-semibold">Qty</th>
                                    <th className="px-3 py-2 text-right font-semibold">Price</th>
                                    <th className="px-3 py-2 text-right font-semibold">Line total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {lineItems.map((it, i) => {
                                    const qty = Number(it.quantity ?? it.qty) || 1;
                                    const unit = it.priceCents;
                                    const lineTotal = unit != null ? unit * qty : null;
                                    return (
                                        <tr key={it.lineItemId || i} className="border-t border-slate-100 dark:border-slate-800">
                                            <td className="px-3 py-2 text-slate-800 dark:text-slate-200">
                                                {it.title || it.name || '—'}
                                            </td>
                                            <td className="px-3 py-2 font-mono text-slate-600 dark:text-slate-400">
                                                {it.sku || it.externalVariantKey || it.variantKey || '—'}
                                            </td>
                                            <td className="px-3 py-2 text-right tabular-nums">{qty}</td>
                                            <td className="px-3 py-2 text-right tabular-nums">{money(unit, currency)}</td>
                                            <td className="px-3 py-2 text-right tabular-nums font-medium">{money(lineTotal, currency)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                ) : (
                    <p className="text-slate-500">No line item details from Lasoo.</p>
                )}
            </DetailSection>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                <DetailSection title="Totals">
                    <dl className="space-y-1 max-w-xs">
                        {totals.subtotalCents != null && (
                            <div className="flex justify-between gap-6">
                                <dt>Subtotal</dt>
                                <dd>{money(totals.subtotalCents, currency)}</dd>
                            </div>
                        )}
                        {totals.shippingCents != null && (
                            <div className="flex justify-between gap-6">
                                <dt>Shipping</dt>
                                <dd>{money(totals.shippingCents, currency)}</dd>
                            </div>
                        )}
                        {totals.taxCents != null && (
                            <div className="flex justify-between gap-6">
                                <dt>Tax / GST</dt>
                                <dd>{money(totals.taxCents, currency)}</dd>
                            </div>
                        )}
                        {totals.discountCents != null && Number(totals.discountCents) !== 0 && (
                            <div className="flex justify-between gap-6">
                                <dt>Discount</dt>
                                <dd>-{money(Math.abs(totals.discountCents), currency)}</dd>
                            </div>
                        )}
                        <div className="flex justify-between gap-6 border-t border-slate-200 dark:border-slate-700 pt-1.5 font-semibold text-slate-900 dark:text-slate-100">
                            <dt>Total</dt>
                            <dd>{money(totals.totalCents ?? order.total_amount_cents, currency)}</dd>
                        </div>
                    </dl>
                </DetailSection>

                <DetailSection title="Shipping / tracking">
                    {(marketplaceShip.status || marketplaceShip.trackingNumber || marketplaceShip.carrier) ? (
                        <dl className="mb-3 space-y-1 text-slate-600 dark:text-slate-400">
                            {marketplaceShip.status && (
                                <div className="flex justify-between gap-4">
                                    <dt>Lasoo shipping</dt>
                                    <dd className="capitalize">{String(marketplaceShip.status).replace(/_/g, ' ')}</dd>
                                </div>
                            )}
                            {marketplaceShip.method && (
                                <div className="flex justify-between gap-4">
                                    <dt>Method</dt>
                                    <dd>{marketplaceShip.method}</dd>
                                </div>
                            )}
                            {(marketplaceShip.carrier || marketplaceShip.trackingNumber) && (
                                <div className="flex justify-between gap-4">
                                    <dt>Carrier / tracking</dt>
                                    <dd>
                                        {[marketplaceShip.carrier, marketplaceShip.trackingNumber].filter(Boolean).join(' · ')}
                                        {marketplaceShip.trackingUrl && (
                                            <>
                                                {' · '}
                                                <a href={marketplaceShip.trackingUrl} target="_blank" rel="noreferrer" className="text-accent-600 hover:underline">
                                                    Track
                                                </a>
                                            </>
                                        )}
                                    </dd>
                                </div>
                            )}
                            {marketplaceShip.dispatchedAt && (
                                <div className="flex justify-between gap-4">
                                    <dt>Dispatched</dt>
                                    <dd>{formatDate(marketplaceShip.dispatchedAt)}</dd>
                                </div>
                            )}
                        </dl>
                    ) : null}

                    <p className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">Submitted from this app</p>
                    {order.shipments?.length ? (
                        <ul className="space-y-1 text-slate-600 dark:text-slate-400">
                            {order.shipments.map((sh) => (
                                <li key={sh.id}>
                                    {sh.carrier || '—'} · {sh.tracking_number || 'no tracking'} · {sh.status}
                                    {sh.tracking_url && (
                                        <>
                                            {' · '}
                                            <a href={sh.tracking_url} target="_blank" rel="noreferrer" className="text-accent-600 hover:underline">
                                                Track
                                            </a>
                                        </>
                                    )}
                                    {sh.created_at && ` · ${formatDate(sh.created_at)}`}
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <p className="text-slate-500">No shipments submitted yet. Use the truck icon to add tracking.</p>
                    )}
                </DetailSection>
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
    const [message, setMessage] = useState(null);
    const [expandedId, setExpandedId] = useState(null);
    const [shippingOrder, setShippingOrder] = useState(null);
    const [shippingLoading, setShippingLoading] = useState(false);
    const [completingId, setCompletingId] = useState(null);

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
                description="Orders from your managed marketplace stores. Expand a row for full Lasoo customer, address, line items, and shipping details."
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
                                No orders yet. Use “Fetch from marketplace” to pull the latest orders from Lasoo.
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
                                        <th className="px-4 py-2.5">Ordered</th>
                                        <th className="px-4 py-2.5 text-right">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {orders.map((o) => {
                                        const currency = o.details?.totals?.currency || 'AUD';
                                        const orderedAt = o.details?.dates?.orderedAt || o.created_at;
                                        return (
                                            <>
                                                <tr key={o.id} className="border-t border-slate-100 dark:border-slate-800">
                                                    <td className="px-4 py-2.5 font-medium text-slate-900 dark:text-slate-100">
                                                        {o.invoice_number || o.external_order_key || '—'}
                                                        <p className="text-[11px] font-normal capitalize text-slate-400">{o.environment}</p>
                                                    </td>
                                                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">
                                                        <p>{customerName(o)}</p>
                                                        {o.details?.customer?.email && (
                                                            <p className="text-xs text-slate-500 dark:text-slate-400">{o.details.customer.email}</p>
                                                        )}
                                                    </td>
                                                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">{itemsSummary(o)}</td>
                                                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">
                                                        {money(o.details?.totals?.totalCents ?? o.total_amount_cents, currency)}
                                                    </td>
                                                    <td className="px-4 py-2.5">
                                                        <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[o.status] || STATUS_STYLES.new}`}>
                                                            {STATUS_LABELS[o.status] || o.status}
                                                        </span>
                                                    </td>
                                                    <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                                                        {formatDate(orderedAt)}
                                                    </td>
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
                                                                title="Show full order details"
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
                                                        <td colSpan={7} className="px-4 py-4">
                                                            <OrderDetailPanel order={o} />
                                                        </td>
                                                    </tr>
                                                )}
                                            </>
                                        );
                                    })}
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
