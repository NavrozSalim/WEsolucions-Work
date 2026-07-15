import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
    Ban,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    ExternalLink,
    FlaskConical,
    Mail,
    MapPin,
    MessageSquare,
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
    cancelOrder,
    completeOrderShipping,
    createTestOrder,
    getOrderCancelReasons,
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

/** Unique vendor/source URLs for the top orders list column. */
function sourceLinks(order) {
    const fromDetails = orderDetails(order).sourceLinks;
    if (Array.isArray(fromDetails) && fromDetails.length > 0) {
        return fromDetails.filter(Boolean);
    }
    const items = orderDetails(order).lineItems || [];
    const seen = new Set();
    const links = [];
    for (const it of items) {
        const url = (it?.vendorUrl || it?.vendor_url || '').trim();
        if (url && !seen.has(url)) {
            seen.add(url);
            links.push(url);
        }
    }
    return links;
}

function SourceLinkCell({ order }) {
    const links = sourceLinks(order);
    if (links.length === 0) {
        return <span className="text-slate-400">—</span>;
    }
    if (links.length === 1) {
        return (
            <a
                href={links[0]}
                target="_blank"
                rel="noreferrer"
                title={links[0]}
                className="inline-flex items-center gap-1 text-accent-600 hover:underline dark:text-accent-400"
            >
                <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                <span className="max-w-[140px] truncate">Source</span>
            </a>
        );
    }
    return (
        <span className="inline-flex flex-col gap-0.5">
            <a
                href={links[0]}
                target="_blank"
                rel="noreferrer"
                title={links[0]}
                className="inline-flex items-center gap-1 text-accent-600 hover:underline dark:text-accent-400"
            >
                <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                Source
            </a>
            <span className="text-[11px] text-slate-500 dark:text-slate-400">
                +{links.length - 1} more
            </span>
        </span>
    );
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

function CancelOrderModal({ open, onClose, onSubmit, order, loading, storeId }) {
    const [reason, setReason] = useState('');
    const [otherReason, setOtherReason] = useState('');
    const [reasons, setReasons] = useState([]);
    const [reasonsLoading, setReasonsLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!open || !storeId) return;
        setError('');
        setOtherReason('');
        setReasonsLoading(true);
        getOrderCancelReasons(storeId)
            .then((res) => {
                const list = Array.isArray(res.data?.reasons) ? res.data.reasons : [];
                setReasons(list);
                setReason(list[0]?.value || '');
            })
            .catch(() => {
                setReasons([{ value: 'Out of stock', label: 'Out of stock' }, { value: 'Other', label: 'Other' }]);
                setReason('Out of stock');
                setError('Could not load marketplace reasons; using defaults.');
            })
            .finally(() => setReasonsLoading(false));
    }, [open, storeId]);

    if (!open) return null;

    const handleSubmit = (e) => {
        e.preventDefault();
        const selected = reason.trim();
        if (!selected) {
            setError('Please select a cancellation reason.');
            return;
        }
        const text = selected === 'Other' ? otherReason.trim() : selected;
        if (!text) {
            setError(selected === 'Other' ? 'Please enter a reason.' : 'Please select a cancellation reason.');
            return;
        }
        onSubmit({ reason: text });
    };

    const reasonOptions = [
        { value: '', label: reasonsLoading ? 'Loading reasons…' : 'Select a reason' },
        ...reasons.map((r) => ({ value: r.value, label: r.label || r.value })),
    ];

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
            <div
                className="relative w-full max-w-md overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-700 px-6 py-4">
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                        Cancel order — {order?.invoice_number || order?.external_order_key}
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
                    <p className="text-sm text-slate-600 dark:text-slate-400">
                        This marks the order cancelled here and requests a cancel/refund on the marketplace.
                        Choose a reason from the marketplace list.
                    </p>
                    <Select
                        label="Reason"
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        options={reasonOptions}
                        disabled={reasonsLoading || loading}
                        required
                    />
                    {reason === 'Other' && (
                        <Input
                            label="Other reason"
                            value={otherReason}
                            onChange={(e) => setOtherReason(e.target.value)}
                            placeholder="Describe the reason"
                            required
                        />
                    )}
                    <div className="flex justify-end gap-2 pt-2">
                        <Button variant="secondary" type="button" onClick={onClose} disabled={loading}>
                            Keep order
                        </Button>
                        <Button variant="danger" type="submit" disabled={loading || reasonsLoading || !reason}>
                            {loading ? 'Cancelling…' : 'Cancel order'}
                        </Button>
                    </div>
                </form>
            </div>
        </div>
    );
}

function DetailSection({ title, children, className = '', align = 'left' }) {
    return (
        <div className={`${className} ${align === 'right' ? 'text-right' : ''}`}>
            <p
                className={`mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 ${
                    align === 'right' ? 'text-right' : ''
                }`}
            >
                {title}
            </p>
            <div className={`text-sm text-slate-700 dark:text-slate-300 ${align === 'right' ? 'text-right' : ''}`}>
                {children}
            </div>
        </div>
    );
}

function InfoCard({ title, children }) {
    return (
        <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900/60 p-3.5 h-full">
            <p className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                {title}
            </p>
            <div className="text-sm text-slate-700 dark:text-slate-300">{children}</div>
        </div>
    );
}

function InfoRow({ label, children }) {
    return (
        <div className="grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-0.5 items-baseline">
            <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
            <dd className="text-right font-medium text-slate-900 dark:text-slate-100 break-words">{children}</dd>
        </div>
    );
}

function OrderDetailPanel({ order, onOpenTicket }) {
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
        <div className="space-y-6">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                <InfoCard title="Customer">
                    <p className="font-medium text-slate-900 dark:text-slate-100">{customerName(order)}</p>
                    {customer.email && (
                        <p className="mt-1.5 flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
                            <Mail className="h-3.5 w-3.5 shrink-0" />
                            <a href={`mailto:${customer.email}`} className="hover:underline truncate">{customer.email}</a>
                        </p>
                    )}
                    {customer.phone && (
                        <p className="mt-1 flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
                            <Phone className="h-3.5 w-3.5 shrink-0" />
                            <a href={`tel:${customer.phone}`} className="hover:underline">{customer.phone}</a>
                        </p>
                    )}
                    {!customer.email && !customer.phone && customerName(order) === '—' && (
                        <p className="text-slate-500">No customer details from the marketplace.</p>
                    )}
                </InfoCard>

                <InfoCard title="Shipping address">
                    {(shippingAddr || marketplaceShipAddr) ? (
                        <div className="flex gap-1.5">
                            <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                            <div className="space-y-0.5">
                                {(shippingAddr || marketplaceShipAddr).map((line) => (
                                    <p key={line}>{line}</p>
                                ))}
                            </div>
                        </div>
                    ) : (
                        <p className="text-slate-500">No shipping address.</p>
                    )}
                </InfoCard>

                <InfoCard title="Order info">
                    <dl className="space-y-1.5">
                        <InfoRow label="Invoice">{order.invoice_number || '—'}</InfoRow>
                        <InfoRow label="Order key">
                            <span className="font-mono text-xs">{order.external_order_key || '—'}</span>
                        </InfoRow>
                        <InfoRow label="Ordered">{formatDate(dates.orderedAt || order.created_at)}</InfoRow>
                        {dates.paidAt && (
                            <InfoRow label="Paid">{formatDate(dates.paidAt)}</InfoRow>
                        )}
                        {d.marketplaceStatus && (
                            <InfoRow label="Marketplace status">
                                <span className="capitalize">{String(d.marketplaceStatus)}</span>
                            </InfoRow>
                        )}
                        <InfoRow label="Environment">
                            <span className="capitalize">{order.environment}</span>
                        </InfoRow>
                    </dl>
                </InfoCard>
            </div>

            {Array.isArray(order.related_tickets) && order.related_tickets.length > 0 && (
                <InfoCard title="Related tickets">
                    <ul className="space-y-2">
                        {order.related_tickets.map((t) => (
                            <li key={t.id}>
                                <button
                                    type="button"
                                    onClick={() => onOpenTicket?.(t.id)}
                                    className="inline-flex items-center gap-2 text-sm text-sky-600 hover:underline dark:text-sky-400"
                                >
                                    <MessageSquare className="h-4 w-4" />
                                    <span className="font-medium">{t.customer_name || t.subject || 'Ticket'}</span>
                                    <span className="text-xs text-slate-500 capitalize">· {t.status}</span>
                                    {t.unread_count > 0 && (
                                        <span className="rounded-full bg-sky-600 px-1.5 py-0.5 text-[10px] text-white">
                                            {t.unread_count}
                                        </span>
                                    )}
                                </button>
                                {t.subject && (
                                    <p className="mt-0.5 pl-6 text-xs text-slate-500">{t.subject}</p>
                                )}
                            </li>
                        ))}
                    </ul>
                </InfoCard>
            )}

            {billingAddr && (
                <InfoCard title="Billing address">
                    {billingAddr.map((line) => (
                        <p key={line}>{line}</p>
                    ))}
                </InfoCard>
            )}

            <DetailSection title="Line items">
                {lineItems.length > 0 ? (
                    <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                        <table className="w-full table-fixed text-left text-xs">
                            <colgroup>
                                <col className="w-[42%]" />
                                <col className="w-[22%]" />
                                <col className="w-[8%]" />
                                <col className="w-[14%]" />
                                <col className="w-[14%]" />
                            </colgroup>
                            <thead className="bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
                                <tr>
                                    <th className="px-3 py-2.5 font-semibold">Product</th>
                                    <th className="px-3 py-2.5 font-semibold">Marketplace SKU</th>
                                    <th className="px-3 py-2.5 text-right font-semibold">Qty</th>
                                    <th className="px-3 py-2.5 text-right font-semibold">Price</th>
                                    <th className="px-3 py-2.5 text-right font-semibold">Line total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {lineItems.map((it, i) => {
                                    const qty = Number(it.quantity ?? it.qty) || 1;
                                    const unit = it.priceCents;
                                    const lineTotal = unit != null ? unit * qty : null;
                                    const marketplaceSku = (
                                        it.marketplaceSku
                                        || it.sku
                                        || it.externalVariantKey
                                        || it.variantKey
                                        || ''
                                    ).trim();
                                    return (
                                        <tr key={it.lineItemId || i} className="border-t border-slate-100 dark:border-slate-800">
                                            <td className="px-3 py-2.5 text-slate-800 dark:text-slate-200 align-top">
                                                {it.title || it.name || '—'}
                                            </td>
                                            <td className="px-3 py-2.5 font-mono text-slate-600 dark:text-slate-400 align-top break-all">
                                                {marketplaceSku || '—'}
                                            </td>
                                            <td className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap align-top">{qty}</td>
                                            <td className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap align-top">{money(unit, currency)}</td>
                                            <td className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap font-medium align-top">{money(lineTotal, currency)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                ) : (
                    <p className="text-slate-500">No line item details from the marketplace.</p>
                )}
            </DetailSection>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_16rem] lg:items-start">
                <InfoCard title="Shipping / tracking">
                    {(marketplaceShip.status || marketplaceShip.trackingNumber || marketplaceShip.carrier) ? (
                        <dl className="mb-3 space-y-1.5">
                            {marketplaceShip.status && (
                                <InfoRow label="Shipping status">
                                    <span className="capitalize">{String(marketplaceShip.status).replace(/_/g, ' ')}</span>
                                </InfoRow>
                            )}
                            {marketplaceShip.method && (
                                <InfoRow label="Method">{marketplaceShip.method}</InfoRow>
                            )}
                            {(marketplaceShip.carrier || marketplaceShip.trackingNumber) && (
                                <InfoRow label="Tracking">
                                    {[marketplaceShip.carrier, marketplaceShip.trackingNumber].filter(Boolean).join(' · ')}
                                    {marketplaceShip.trackingUrl && (
                                        <>
                                            {' · '}
                                            <a href={marketplaceShip.trackingUrl} target="_blank" rel="noreferrer" className="text-accent-600 hover:underline">
                                                Track
                                            </a>
                                        </>
                                    )}
                                </InfoRow>
                            )}
                            {marketplaceShip.dispatchedAt && (
                                <InfoRow label="Dispatched">{formatDate(marketplaceShip.dispatchedAt)}</InfoRow>
                            )}
                        </dl>
                    ) : null}

                    <p className="mb-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">Submitted from this app</p>
                    {order.shipments?.length ? (
                        <ul className="space-y-1.5 text-slate-600 dark:text-slate-400">
                            {order.shipments.map((sh) => (
                                <li key={sh.id} className="rounded-md bg-slate-50 dark:bg-slate-800/60 px-2.5 py-1.5">
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
                </InfoCard>

                <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900/60 p-3.5">
                    <p className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 text-right">
                        Totals
                    </p>
                    <dl className="space-y-1.5 text-sm">
                        {totals.subtotalCents != null && (
                            <div className="flex justify-between gap-6">
                                <dt className="text-slate-500 dark:text-slate-400">Subtotal</dt>
                                <dd className="tabular-nums text-slate-900 dark:text-slate-100">{money(totals.subtotalCents, currency)}</dd>
                            </div>
                        )}
                        {totals.shippingCents != null && (
                            <div className="flex justify-between gap-6">
                                <dt className="text-slate-500 dark:text-slate-400">Shipping</dt>
                                <dd className="tabular-nums text-slate-900 dark:text-slate-100">{money(totals.shippingCents, currency)}</dd>
                            </div>
                        )}
                        {totals.taxCents != null && (
                            <div className="flex justify-between gap-6">
                                <dt className="text-slate-500 dark:text-slate-400">Tax / GST</dt>
                                <dd className="tabular-nums text-slate-900 dark:text-slate-100">{money(totals.taxCents, currency)}</dd>
                            </div>
                        )}
                        {totals.discountCents != null && Number(totals.discountCents) !== 0 && (
                            <div className="flex justify-between gap-6">
                                <dt className="text-slate-500 dark:text-slate-400">Discount</dt>
                                <dd className="tabular-nums text-slate-900 dark:text-slate-100">-{money(Math.abs(totals.discountCents), currency)}</dd>
                            </div>
                        )}
                        <div className="flex justify-between gap-6 border-t border-slate-200 dark:border-slate-700 pt-2 mt-1 font-semibold text-slate-900 dark:text-slate-100">
                            <dt>Total</dt>
                            <dd className="tabular-nums">{money(totals.totalCents ?? order.total_amount_cents, currency)}</dd>
                        </div>
                    </dl>
                </div>
            </div>
        </div>
    );
}

export default function Orders() {
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const initialStore = searchParams.get('store') || '';
    const initialOrderKey = searchParams.get('order') || '';

    const [stores, setStores] = useState([]);
    const [storesLoading, setStoresLoading] = useState(true);
    const [selectedStore, setSelectedStore] = useState(initialStore);
    const [orders, setOrders] = useState([]);
    const [loading, setLoading] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [creatingTest, setCreatingTest] = useState(false);
    const [message, setMessage] = useState(null);
    const [expandedId, setExpandedId] = useState(null);
    const [shippingOrder, setShippingOrder] = useState(null);
    const [shippingLoading, setShippingLoading] = useState(false);
    const [completingId, setCompletingId] = useState(null);
    const [cancelOrderRow, setCancelOrderRow] = useState(null);
    const [cancelLoading, setCancelLoading] = useState(false);

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

    const loadOrders = useCallback(
        (refresh = false) => {
            if (!selectedStore) return;
            const setter = refresh ? setRefreshing : setLoading;
            setter(true);
            getOrders(selectedStore, { refresh })
                .then((res) => {
                    const list = Array.isArray(res.data?.orders) ? res.data.orders : [];
                    setOrders(list);
                    const r = res.data?.refresh;
                    if (refresh && r) {
                        setMessage({ text: r.message || 'Orders refreshed.', variant: r.ok ? 'success' : 'error' });
                    }
                    if (initialOrderKey) {
                        const match = list.find((o) => {
                            const inv = String(o.invoice_number || '').toLowerCase();
                            const key = String(o.external_order_key || '').toLowerCase();
                            const want = initialOrderKey.toLowerCase();
                            return inv === want || key === want;
                        });
                        if (match) setExpandedId(match.id);
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
        [selectedStore, initialOrderKey]
    );

    useEffect(() => {
        setOrders([]);
        setMessage(null);
        setExpandedId(null);
        if (selectedStore) {
            setSearchParams((prev) => {
                const next = new URLSearchParams(prev);
                next.set('store', selectedStore);
                return next;
            }, { replace: true });
            loadOrders(false);
        }
    }, [selectedStore, loadOrders, setSearchParams]);

    const openTicket = (ticketId) => {
        if (!selectedStore || !ticketId) return;
        navigate(`/tickets?store=${encodeURIComponent(selectedStore)}&ticket=${encodeURIComponent(ticketId)}`);
    };

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

    const handleCancelOrder = ({ reason }) => {
        if (!cancelOrderRow) return;
        setCancelLoading(true);
        cancelOrder(selectedStore, cancelOrderRow.id, { reason })
            .then((res) => {
                const marketplaceOk = res.data?.marketplace_ok !== false;
                setMessage({
                    text: res.data?.message || 'Order cancelled.',
                    variant: marketplaceOk ? 'success' : 'warning',
                });
                setCancelOrderRow(null);
                loadOrders(false);
            })
            .catch((err) => {
                setMessage({
                    text: err.response?.data?.detail || err.response?.data?.message || 'Cancel failed.',
                    variant: 'error',
                });
            })
            .finally(() => setCancelLoading(false));
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
                description="Orders from your managed marketplace stores. Expand a row for customer, address, line items, and shipping details."
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
                    description='Create a store with the "Managed store" option to manage orders here.'
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
                                        <th className="px-4 py-2.5">Source</th>
                                        <th className="px-4 py-2.5">Total</th>
                                        <th className="px-4 py-2.5">Status</th>
                                        <th className="px-4 py-2.5">Ticket</th>
                                        <th className="px-4 py-2.5">Ordered</th>
                                        <th className="px-4 py-2.5 text-right">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {orders.map((o) => {
                                        const currency = o.details?.totals?.currency || 'AUD';
                                        const orderedAt = o.details?.dates?.orderedAt || o.created_at;
                                        const tickets = Array.isArray(o.related_tickets) ? o.related_tickets : [];
                                        const primaryTicket = tickets[0] || null;
                                        return (
                                            <Fragment key={o.id}>
                                                <tr className="border-t border-slate-100 dark:border-slate-800">
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
                                                        <SourceLinkCell order={o} />
                                                    </td>
                                                    <td className="px-4 py-2.5 text-slate-700 dark:text-slate-300">
                                                        {money(o.details?.totals?.totalCents ?? o.total_amount_cents, currency)}
                                                    </td>
                                                    <td className="px-4 py-2.5">
                                                        <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[o.status] || STATUS_STYLES.new}`}>
                                                            {STATUS_LABELS[o.status] || o.status}
                                                        </span>
                                                    </td>
                                                    <td className="px-4 py-2.5">
                                                        {primaryTicket ? (
                                                            <button
                                                                type="button"
                                                                title={primaryTicket.subject || 'Open ticket'}
                                                                onClick={() => openTicket(primaryTicket.id)}
                                                                className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700 hover:bg-sky-100 dark:bg-sky-900/30 dark:text-sky-300 dark:hover:bg-sky-900/50"
                                                            >
                                                                <MessageSquare className="h-3.5 w-3.5" />
                                                                {tickets.length > 1 ? `${tickets.length} tickets` : 'Open'}
                                                                {primaryTicket.unread_count > 0 && (
                                                                    <span className="rounded-full bg-sky-600 px-1 text-[10px] text-white">
                                                                        {primaryTicket.unread_count}
                                                                    </span>
                                                                )}
                                                            </button>
                                                        ) : (
                                                            <span className="text-xs text-slate-400">—</span>
                                                        )}
                                                    </td>
                                                    <td className="px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                                                        {formatDate(orderedAt)}
                                                    </td>
                                                    <td className="px-4 py-2.5">
                                                        <div className="flex items-center justify-end gap-1">
                                                            <button
                                                                type="button"
                                                                title="Submit shipping / tracking"
                                                                className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-40"
                                                                onClick={() => setShippingOrder(o)}
                                                                disabled={['cancelled', 'refunded', 'shipping_complete'].includes(o.status)}
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
                                                                title="Cancel order"
                                                                className="rounded-md p-1.5 text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-900/20 disabled:opacity-40"
                                                                onClick={() => setCancelOrderRow(o)}
                                                                disabled={['cancelled', 'refunded', 'shipping_complete'].includes(o.status)}
                                                            >
                                                                <Ban className="h-4 w-4" />
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
                                                    <tr className="border-t border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-800/40">
                                                        <td colSpan={9} className="px-4 py-4">
                                                            <OrderDetailPanel order={o} onOpenTicket={openTicket} />
                                                        </td>
                                                    </tr>
                                                )}
                                            </Fragment>
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
            <CancelOrderModal
                open={!!cancelOrderRow}
                order={cancelOrderRow}
                storeId={selectedStore}
                onClose={() => setCancelOrderRow(null)}
                onSubmit={handleCancelOrder}
                loading={cancelLoading}
            />
        </div>
    );
}
