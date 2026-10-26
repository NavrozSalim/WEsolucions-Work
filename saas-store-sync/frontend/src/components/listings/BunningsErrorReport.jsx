/** Mirakl error rows saved on a Bunnings listing that was not created. */
export function bunningsErrorsFromListing(listing) {
    const raw = listing?.marketplace_response_json?.bunnings_errors;
    if (!Array.isArray(raw)) return [];
    return raw
        .map((row) => {
            if (!row || typeof row !== 'object') return null;
            const field = String(row.field || row.attribute || '').trim();
            const value = String(row.value || '').trim();
            const code = String(row.code || '').trim();
            const message = String(row.message || row.errors || '').trim();
            if (!field && !value && !code && !message) return null;
            return { field, value, code, message };
        })
        .filter(Boolean);
}

function errorText(row) {
    if (row.code && row.message && !row.message.startsWith(`${row.code}|`) && !row.message.startsWith(`${row.code} `)) {
        return `${row.code} — ${row.message}`;
    }
    return row.message || row.code || 'Bunnings rejected this product.';
}

/** Field, value sent, and the Bunnings message — the same facts as the Mirakl error file. */
export default function BunningsErrorReport({ errors, onDownload, heading = 'Not created on Bunnings' }) {
    if (!errors?.length) return null;
    return (
        <div className="rounded-md border border-rose-200 bg-rose-50/80 dark:border-rose-900 dark:bg-rose-950/30">
            <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                <div>
                    <p className="text-xs font-semibold text-rose-800 dark:text-rose-200">
                        {heading}
                    </p>
                    <p className="text-xs text-rose-700/90 dark:text-rose-300/90">
                        Edit this product, or use Bulk Listing → Update for many products, then publish again.
                    </p>
                </div>
                {onDownload && (
                    <button
                        type="button"
                        onClick={onDownload}
                        className="text-xs font-semibold text-rose-700 underline decoration-rose-300 underline-offset-2 hover:text-rose-900 dark:text-rose-300 dark:hover:text-rose-100"
                    >
                        Download error report
                    </button>
                )}
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                    <thead className="text-rose-700/80 dark:text-rose-300/80">
                        <tr>
                            <th className="px-3 py-1.5 font-semibold">Field</th>
                            <th className="px-3 py-1.5 font-semibold">Value sent</th>
                            <th className="px-3 py-1.5 font-semibold">Bunnings error</th>
                        </tr>
                    </thead>
                    <tbody>
                        {errors.map((row, index) => (
                            <tr key={`${row.field}-${row.code}-${index}`} className="border-t border-rose-100 dark:border-rose-900/60">
                                <td className="px-3 py-1.5 text-slate-800 dark:text-slate-200">{row.field || '—'}</td>
                                <td className="px-3 py-1.5 text-slate-700 dark:text-slate-300">{row.value || '—'}</td>
                                <td className="px-3 py-1.5 text-rose-800 dark:text-rose-200">{errorText(row)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
