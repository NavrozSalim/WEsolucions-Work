import { useState, useEffect, useRef } from 'react';
import { X, CheckCircle2, RefreshCw } from 'lucide-react';
import Button from '../ui/Button';
import {
    getMydealTemplateStatus,
    uploadMydealTemplate,
} from '../../services/catalogService';

const STEPS = [
    {
        key: 'price',
        title: 'Price template',
        hint: 'CSV with DealID, SKU, Price(IncGST), RRP(IncGST), … Uploading replaces any previous price template for this store.',
    },
    {
        key: 'inventory',
        title: 'Inventory template',
        hint: 'CSV with StockOnHand, Discontinued, WMPApproved, … Uploading replaces any previous inventory template for this store.',
    },
];

export default function MydealUploadModal({ open, onClose, storeId, onComplete }) {
    const [step, setStep] = useState(0);
    const [status, setStatus] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState('');
    const [successMsg, setSuccessMsg] = useState('');
    const [priceDone, setPriceDone] = useState(false);
    const [inventoryDone, setInventoryDone] = useState(false);
    const fileRef = useRef(null);
    const zipRef = useRef(null);

    const refreshStatus = () => {
        if (!storeId) return Promise.resolve();
        return getMydealTemplateStatus(storeId).then((r) => {
            const s = r.data || {};
            setStatus(s);
            setPriceDone(!!s.price_uploaded);
            setInventoryDone(!!s.inventory_uploaded);
            return s;
        });
    };

    useEffect(() => {
        if (!open || !storeId) return;
        setStep(0);
        setError('');
        setSuccessMsg('');
        refreshStatus().catch(() => setStatus(null));
    }, [open, storeId]);

    if (!open) return null;

    const current = STEPS[step];
    const kind = current?.key;
    const storeLabel = status?.store_name || status?.profile || 'Store';
    const hasExisting = !!(status?.price_uploaded || status?.inventory_uploaded);
    const allDone = priceDone && inventoryDone;

    const applyUploadResult = (res, uploadedKind) => {
        const data = res.data || {};
        const s = data.status || data;
        setStatus(s);
        if (uploadedKind === 'zip') {
            setPriceDone(!!s.price_uploaded);
            setInventoryDone(!!s.inventory_uploaded);
            const kinds = data.kinds || [];
            setSuccessMsg(
                `Replaced ${kinds.join(' and ')} template(s) from ZIP. Run scrape again if your catalog SKUs changed, then download updated CSVs.`
            );
        } else {
            if (uploadedKind === 'price') setPriceDone(true);
            if (uploadedKind === 'inventory') setInventoryDone(true);
            const replaced = data.replaced;
            const n = data.row_count ?? 0;
            setSuccessMsg(
                replaced
                    ? `Replaced previous ${uploadedKind} template (${n} rows). Re-scrape if SKUs changed, then download templates.`
                    : `Saved ${uploadedKind} template (${n} rows).`
            );
            if (uploadedKind === 'price' && !s?.inventory_uploaded) setStep(1);
        }
    };

    const handleFile = (e) => {
        const file = e.target.files?.[0];
        if (!file || !storeId) return;
        setUploading(true);
        setError('');
        setSuccessMsg('');
        uploadMydealTemplate(storeId, kind, file)
            .then((res) => {
                applyUploadResult(res, kind);
            })
            .catch((err) => {
                const d = err.response?.data;
                setError(d?.error || d?.detail || err.message || 'Upload failed');
            })
            .finally(() => {
                setUploading(false);
                if (fileRef.current) fileRef.current.value = '';
            });
    };

    const handleZip = (e) => {
        const file = e.target.files?.[0];
        if (!file || !storeId) return;
        setUploading(true);
        setError('');
        setSuccessMsg('');
        uploadMydealTemplate(storeId, 'zip', file)
            .then((res) => {
                applyUploadResult(res, 'zip');
            })
            .catch((err) => {
                const d = err.response?.data;
                setError(d?.error || d?.detail || err.message || 'ZIP upload failed');
            })
            .finally(() => {
                setUploading(false);
                if (zipRef.current) zipRef.current.value = '';
            });
    };

    const handleClose = () => {
        if (allDone) onComplete?.();
        onClose();
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            role="dialog"
            aria-modal="true"
        >
            <div className="w-full max-w-lg rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl max-h-[90vh] flex flex-col">
                <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-700 px-5 py-4 shrink-0">
                    <div>
                        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                            {hasExisting ? 'Replace Mydeal templates' : 'Upload Mydeal templates'}
                        </h2>
                        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                            Store: {storeLabel} — each upload overwrites that template type for this store only.
                        </p>
                    </div>
                    <button type="button" onClick={handleClose} className="p-1 rounded-md text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Close">
                        <X className="h-5 w-5" />
                    </button>
                </div>
                <div className="px-5 py-4 space-y-4 overflow-y-auto flex-1 min-h-0">
                    {hasExisting && (
                        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-3 py-2.5 text-sm text-amber-900 dark:text-amber-200">
                            <strong>Update from Mydeal?</strong> Upload new Price and/or Inventory CSV files here.
                            They fully replace the stored templates. Your catalog listings are unchanged until you
                            re-upload catalog and scrape.
                        </div>
                    )}

                    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40 p-3 space-y-2">
                        <p className="text-xs font-semibold text-slate-700 dark:text-slate-300">Replace both (ZIP)</p>
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            ZIP with Mydeal Price and/or Inventory CSV files (same headers as Mydeal portal).
                        </p>
                        <input
                            ref={zipRef}
                            type="file"
                            accept=".zip,application/zip"
                            className="block w-full text-sm"
                            disabled={uploading}
                            onChange={handleZip}
                        />
                    </div>

                    <div className="flex gap-2">
                        {STEPS.map((s, i) => (
                            <button
                                key={s.key}
                                type="button"
                                onClick={() => { setStep(i); setError(''); setSuccessMsg(''); }}
                                className={`flex-1 rounded-lg border px-3 py-2 text-xs text-left transition ${
                                    i === step
                                        ? 'border-accent-500 bg-accent-50 dark:bg-accent-900/20'
                                        : (s.key === 'price' ? priceDone : inventoryDone)
                                            ? 'border-emerald-300 dark:border-emerald-700 hover:bg-emerald-50/50 dark:hover:bg-emerald-900/10'
                                            : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800'
                                }`}
                            >
                                <span className="font-semibold block">{s.title}</span>
                                {(s.key === 'price' ? priceDone : inventoryDone) ? (
                                    <span className="inline-flex items-center gap-1 mt-1 text-emerald-600 dark:text-emerald-400">
                                        <CheckCircle2 className="h-3 w-3" /> Saved
                                    </span>
                                ) : (
                                    <span className="mt-1 text-slate-400">Not uploaded</span>
                                )}
                            </button>
                        ))}
                    </div>

                    {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
                    {successMsg && (
                        <p className="text-sm text-emerald-700 dark:text-emerald-300">{successMsg}</p>
                    )}

                    {current && (
                        <div className="space-y-2">
                            <p className="text-sm text-slate-600 dark:text-slate-400 flex items-center gap-1.5">
                                {(current.key === 'price' ? priceDone : inventoryDone) && (
                                    <RefreshCw className="h-3.5 w-3.5 shrink-0" aria-hidden />
                                )}
                                {current.hint}
                            </p>
                            <input
                                ref={fileRef}
                                type="file"
                                accept=".csv,text/csv"
                                className="block w-full text-sm"
                                disabled={uploading}
                                onChange={handleFile}
                            />
                        </div>
                    )}

                    {allDone && (
                        <p className="text-sm text-slate-600 dark:text-slate-400">
                            Both templates are stored. Upload catalog (if needed), scrape, then use{' '}
                            <strong>Download templates</strong> on the catalog page.
                        </p>
                    )}
                </div>
                <div className="flex justify-end gap-2 border-t border-slate-200 dark:border-slate-700 px-5 py-3 shrink-0">
                    <Button variant="secondary" onClick={handleClose} disabled={uploading}>Close</Button>
                </div>
            </div>
        </div>
    );
}
