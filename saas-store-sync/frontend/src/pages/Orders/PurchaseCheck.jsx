import { AlertTriangle, ExternalLink, Loader2, RefreshCw, ShoppingCart } from 'lucide-react';

/** Auto Purchasing: stock / profit check for one order, with Process and Force process. */

function money(cents) {
    if (cents == null || Number.isNaN(Number(cents))) return '—';
    const n = Number(cents) / 100;
    return `${n < 0 ? '-' : ''}A$${Math.abs(n).toFixed(2)}`;
}

const LINE_RESULT = {
    ok: { label: 'OK', cls: 'text-emerald-700 dark:text-emerald-400' },
    not_mapped: { label: 'Not mapped', cls: 'text-rose-600 dark:text-rose-400' },
    other_vendor: { label: 'Other vendor', cls: 'text-amber-700 dark:text-amber-300' },
    out_of_stock: { label: 'Out of stock', cls: 'text-rose-600 dark:text-rose-400' },
    stock_unknown: { label: 'Stock unknown', cls: 'text-amber-700 dark:text-amber-300' },
    scrape_failed: { label: 'Scrape failed', cls: 'text-amber-700 dark:text-amber-300' },
    no_delivery: { label: 'No delivery', cls: 'text-rose-600 dark:text-rose-400' },
};

/** Where a line's shipping came from (hover the amount for the vendor's own wording). */
const SHIPPING_SOURCE = {
    quoted: 'vendor quote',
    page: 'item page (not postcode)',
    included: 'in price',
    feed: 'feed price',
    none: 'none shown',
};

/** True while the order is being bought or was bought: no Recheck, no second Process. */
export function purchaseLocked(check) {
    return ['queued', 'purchasing', 'purchased'].includes(check?.purchase_status);
}

export function PurchaseCheckBadge({ check }) {
    if (!check) return null;
    if (check.purchase_status === 'queued' || check.purchase_status === 'purchasing') {
        return (
            <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700 dark:bg-sky-900/30 dark:text-sky-300">
                <Loader2 className="h-3 w-3 animate-spin" /> {check.purchase_status === 'queued' ? 'Waiting to buy' : 'Buying'}
            </span>
        );
    }
    if (check.purchase_status === 'purchased') {
        return (
            <span
                title={check.purchase_error || undefined}
                className="mt-1 inline-block rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200"
            >
                Purchased{check.vendor_order_number ? ` · ${check.vendor_order_number}` : ''}{check.purchase_error ? ' (!)' : ''}
            </span>
        );
    }
    if (check.purchase_status === 'failed') {
        return (
            <span title={check.purchase_error} className="mt-1 inline-block rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700 dark:bg-rose-900/30 dark:text-rose-300">
                Purchase failed
            </span>
        );
    }
    if (check.status === 'checking') {
        return (
            <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700 dark:bg-sky-900/30 dark:text-sky-300">
                <Loader2 className="h-3 w-3 animate-spin" /> Checking
            </span>
        );
    }
    if (check.status === 'buyable') {
        return (
            <span className="mt-1 inline-block rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
                Ready to buy · {money(check.earning_cents)}
            </span>
        );
    }
    const first = (check.reasons || [])[0] || 'Blocked';
    const short = first.split(':')[0];
    return (
        <span
            title={(check.reasons || []).join('\n')}
            className="mt-1 inline-block rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700 dark:bg-rose-900/30 dark:text-rose-300"
        >
            {short}{(check.reasons || []).length > 1 ? ` +${check.reasons.length - 1}` : ''}
        </span>
    );
}

function SumRow({ label, value, strong, tone }) {
    return (
        <div className="flex items-baseline justify-between gap-4">
            <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
            <dd className={`tabular-nums ${strong ? 'font-semibold' : ''} ${tone || 'text-slate-900 dark:text-slate-100'}`}>{value}</dd>
        </div>
    );
}

function PurchaseStatusNote({ check }) {
    const ps = check?.purchase_status;
    if (!ps) return null;
    const when = (iso) => (iso ? new Date(iso).toLocaleString() : '');
    if (ps === 'queued' || ps === 'purchasing') {
        return (
            <p className="flex items-center gap-1.5 rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-700 dark:border-sky-800 dark:bg-sky-900/20 dark:text-sky-300">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {ps === 'queued' ? 'Waiting for the worker laptop to buy this order.' : 'The worker laptop is buying this order.'}
                {check.purchase_requested_at && <span className="opacity-75">Sent {when(check.purchase_requested_at)}.</span>}
            </p>
        );
    }
    if (ps === 'purchased') {
        return (
            <div className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-200">
                <p className="font-semibold">
                    Purchased{check.vendor_order_number ? ` · vendor order ${check.vendor_order_number}` : ''}
                    {check.purchased_at && <span className="font-normal opacity-75"> · {when(check.purchased_at)}</span>}
                </p>
                {check.purchase_error && <p className="mt-1 text-amber-700 dark:text-amber-300">{check.purchase_error}</p>}
            </div>
        );
    }
    return (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
            <span className="font-semibold">Purchase failed.</span> {check.purchase_error}
        </p>
    );
}

export function PurchaseCheckPanel({ check, onRecheck, rechecking, onProcess, onForce, processing }) {
    const lines = check?.lines || [];
    const checking = check?.status === 'checking';
    const buyable = check?.status === 'buyable';
    const locked = purchaseLocked(check);
    const earning = check?.earning_cents;
    const feeLabel = lines.some((ln) => ln.commission_cents != null)
        ? 'Fee (commission from file)'
        : lines.find((ln) => ln.fee_percent)
            ? `Fee (${Number(lines.find((ln) => ln.fee_percent).fee_percent)}%)`
            : 'Fee';
    return (
        <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3.5 dark:border-slate-700 dark:bg-slate-800/40">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Auto Purchasing check
                </p>
                <div className="flex flex-wrap items-center gap-2">
                    {!locked && (
                        <button
                            type="button"
                            onClick={onRecheck}
                            disabled={rechecking || checking}
                            className="inline-flex min-h-8 items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200"
                            title="Find the catalog mapping again and re-read the vendor price and stock"
                        >
                            <RefreshCw className={`h-3.5 w-3.5 ${rechecking || checking ? 'animate-spin' : ''}`} />
                            {checking ? 'Checking…' : 'Recheck'}
                        </button>
                    )}
                    {buyable && !locked && onProcess && (
                        <button
                            type="button"
                            onClick={onProcess}
                            disabled={processing}
                            className="inline-flex min-h-8 items-center gap-1 rounded-md bg-emerald-600 px-2.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
                            title="Buy this order from the store's Auto buying vendor on the worker laptop"
                        >
                            <ShoppingCart className="h-3.5 w-3.5" /> {processing ? 'Sending…' : 'Process'}
                        </button>
                    )}
                    {check?.can_force && !locked && onForce && (
                        <button
                            type="button"
                            onClick={onForce}
                            disabled={processing}
                            className="inline-flex min-h-8 items-center gap-1 rounded-md bg-amber-500 px-2.5 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-60"
                            title="Buy this order even though it makes a loss"
                        >
                            <AlertTriangle className="h-3.5 w-3.5" /> {processing ? 'Sending…' : 'Force process'}
                        </button>
                    )}
                </div>
            </div>

            {!check ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">Not checked yet. Click Recheck.</p>
            ) : checking && !lines.length ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">Checking catalog mapping, vendor price and stock…</p>
            ) : (
                <div className="space-y-3">
                    <PurchaseStatusNote check={check} />
                    {check.forced_at && (
                        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
                            Force processed at a loss of {money(Math.abs(check.forced_loss_cents || 0))} on {new Date(check.forced_at).toLocaleString()}.
                        </p>
                    )}
                    {!locked && (check.reasons || []).length > 0 && (
                        <ul className="space-y-1 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300">
                            {check.reasons.map((r) => <li key={r}>{r}</li>)}
                        </ul>
                    )}
                    {buyable && !check.purchase_status && (
                        <p className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300">
                            Ready to buy: every item is in stock at the vendor and the order makes {money(earning)}.
                            Click Process to buy it on the worker laptop.
                        </p>
                    )}
                    <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">
                        <table className="min-w-full text-xs">
                            <thead className="bg-white text-left text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                                <tr>
                                    <th className="px-2.5 py-2 font-semibold">Marketplace SKU</th>
                                    <th className="px-2.5 py-2 font-semibold">Vendor / Vendor ID</th>
                                    <th className="px-2.5 py-2 text-right font-semibold">Qty</th>
                                    <th className="px-2.5 py-2 text-right font-semibold">Vendor price</th>
                                    <th className="px-2.5 py-2 text-right font-semibold">Stock</th>
                                    <th className="px-2.5 py-2 text-right font-semibold">Cost</th>
                                    <th className="px-2.5 py-2 text-right font-semibold">Shipping</th>
                                    <th className="px-2.5 py-2 font-semibold">Result</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 bg-white dark:divide-slate-800 dark:bg-slate-900/40">
                                {lines.map((ln, i) => {
                                    const res = LINE_RESULT[ln.result] || { label: ln.result, cls: '' };
                                    return (
                                        <tr key={`${ln.sku}-${i}`} className="align-top">
                                            <td className="px-2.5 py-2 font-mono text-[11px] text-slate-700 dark:text-slate-300">{ln.sku}</td>
                                            <td className="px-2.5 py-2 text-slate-700 dark:text-slate-300">
                                                {ln.vendor_name || ln.vendor_code || '—'}
                                                {ln.vendor_id && <span className="block font-mono text-[11px] text-slate-500">{ln.vendor_id}</span>}
                                                {ln.vendor_url && (
                                                    <a href={ln.vendor_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-accent-600 hover:underline">
                                                        <ExternalLink className="h-3 w-3" /> Vendor page
                                                    </a>
                                                )}
                                            </td>
                                            <td className="px-2.5 py-2 text-right tabular-nums">{ln.quantity}</td>
                                            <td className="px-2.5 py-2 text-right tabular-nums">
                                                {money(ln.vendor_price_cents)}
                                                {ln.price_source === 'feed' && <span className="block text-[10px] text-slate-500">feed</span>}
                                            </td>
                                            <td className="px-2.5 py-2 text-right tabular-nums">{ln.vendor_stock ?? '—'}</td>
                                            <td className="px-2.5 py-2 text-right tabular-nums">{money(ln.cost_cents)}</td>
                                            <td className="px-2.5 py-2 text-right tabular-nums" title={ln.shipping_text || undefined}>
                                                {ln.shipping_cents == null ? '—' : ln.shipping_cents === 0 ? 'Free' : money(ln.shipping_cents)}
                                                {ln.postcode && <span className="block text-[10px] text-slate-500">to {ln.postcode}</span>}
                                                {SHIPPING_SOURCE[ln.shipping_source] && (
                                                    <span className="block text-[10px] text-slate-500">{SHIPPING_SOURCE[ln.shipping_source]}</span>
                                                )}
                                            </td>
                                            <td className={`px-2.5 py-2 ${res.cls}`}>
                                                <span className="font-medium">{res.label}</span>
                                                {ln.message && ln.result !== 'ok' && <span className="block text-[11px] opacity-80">{ln.message}</span>}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                    <dl className="ml-auto max-w-xs space-y-1 text-sm">
                        <SumRow label="Total Sale" value={money(check.total_sale_cents)} />
                        <SumRow label={feeLabel} value={check.fee_cents == null ? '—' : `- ${money(check.fee_cents)}`} />
                        <SumRow label="After Fees" value={money(check.after_fees_cents)} />
                        <SumRow label="Cost" value={check.cost_cents == null ? '—' : `- ${money(check.cost_cents)}`} />
                        <SumRow label="Vendor shipping" value={check.shipping_cents == null ? '—' : `- ${money(check.shipping_cents)}`} />
                        <SumRow
                            label="Earning"
                            value={money(earning)}
                            strong
                            tone={earning == null ? undefined : earning > 0 ? 'text-emerald-700 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}
                        />
                    </dl>
                    {check.checked_at && (
                        <p className="text-[11px] text-slate-500 dark:text-slate-400">
                            Checked {new Date(check.checked_at).toLocaleString()}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
