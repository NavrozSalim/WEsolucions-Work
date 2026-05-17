import { useState, useEffect, useRef } from 'react';
import { X, CheckCircle2 } from 'lucide-react';
import Button from '../ui/Button';
import {
    getMydealTemplateStatus,
    uploadMydealTemplate,
} from '../../services/catalogService';

const STEPS = [
    { key: 'price', title: 'Price template', hint: 'Upload Mydeal Price CSV (DealID, SKU, Price(IncGST), RRP(IncGST), …).' },
    { key: 'inventory', title: 'Inventory template', hint: 'Upload Mydeal Inventory CSV (StockOnHand, Discontinued, MyDealApproved, …).' },
];

export default function MydealUploadModal({ open, onClose, storeId, onComplete }) {
    const [step, setStep] = useState(0);
    const [status, setStatus] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState('');
    const [priceDone, setPriceDone] = useState(false);
    const [inventoryDone, setInventoryDone] = useState(false);
    const fileRef = useRef(null);

    useEffect(() => {
        if (!open || !storeId) return;
        setStep(0);
        setError('');
        setPriceDone(false);
        setInventoryDone(false);
        getMydealTemplateStatus(storeId)
            .then((r) => {
                const s = r.data || {};
                setStatus(s);
                setPriceDone(!!s.price_uploaded);
                setInventoryDone(!!s.inventory_uploaded);
                if (s.price_uploaded && !s.inventory_uploaded) setStep(1);
            })
            .catch(() => setStatus(null));
    }, [open, storeId]);

    if (!open) return null;

    const current = STEPS[step];
    const kind = current?.key;

    const handleFile = (e) => {
        const file = e.target.files?.[0];
        if (!file || !storeId) return;
        setUploading(true);
        setError('');
        uploadMydealTemplate(storeId, kind, file)
            .then((res) => {
                const s = res.data?.status || res.data;
                setStatus(s);
                if (kind === 'price') setPriceDone(true);
                if (kind === 'inventory') setInventoryDone(true);
                if (kind === 'price') setStep(1);
                else {
                    onComplete?.();
                    onClose();
                }
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

    const profile = status?.profile || 'TFS';
    const allDone = priceDone && inventoryDone;

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            role="dialog"
            aria-modal="true"
        >
            <div className="w-full max-w-lg rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl">
                <div className="flex items-center justify-between border-b border-slate-200 dark:border-slate-700 px-5 py-4">
                    <div>
                        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Upload Mydeal templates</h2>
                        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Profile: {profile} — upload Price, then Inventory.</p>
                    </div>
                    <button type="button" onClick={onClose} className="p-1 rounded-md text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Close">
                        <X className="h-5 w-5" />
                    </button>
                </div>
                <div className="px-5 py-4 space-y-4">
                    <div className="flex gap-2">
                        {STEPS.map((s, i) => (
                            <div
                                key={s.key}
                                className={`flex-1 rounded-lg border px-3 py-2 text-xs ${
                                    i === step
                                        ? 'border-accent-500 bg-accent-50 dark:bg-accent-900/20'
                                        : (s.key === 'price' ? priceDone : inventoryDone)
                                            ? 'border-emerald-300 dark:border-emerald-700'
                                            : 'border-slate-200 dark:border-slate-700'
                                }`}
                            >
                                <span className="font-semibold block">{s.title}</span>
                                {(s.key === 'price' ? priceDone : inventoryDone) && (
                                    <span className="inline-flex items-center gap-1 mt-1 text-emerald-600">
                                        <CheckCircle2 className="h-3 w-3" /> Done
                                    </span>
                                )}
                            </div>
                        ))}
                    </div>
                    {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
                    {!allDone && current && (
                        <>
                            <p className="text-sm text-slate-600 dark:text-slate-400">{current.hint}</p>
                            <input
                                ref={fileRef}
                                type="file"
                                accept=".csv,text/csv"
                                className="block w-full text-sm"
                                disabled={uploading}
                                onChange={handleFile}
                            />
                        </>
                    )}
                    {allDone && (
                        <p className="text-sm text-emerald-700 dark:text-emerald-300">
                            Both templates uploaded. Use Upload Catalog for vendor URLs, then Download Templates after scraping.
                        </p>
                    )}
                </div>
                <div className="flex justify-end gap-2 border-t border-slate-200 dark:border-slate-700 px-5 py-3">
                    <Button variant="secondary" onClick={onClose} disabled={uploading}>Close</Button>
                </div>
            </div>
        </div>
    );
}