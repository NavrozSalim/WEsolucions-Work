import { useRef, useState } from 'react';
import { Download, UploadCloud, X } from 'lucide-react';
import Button from '../ui/Button';
import { bulkUploadListings, downloadListingTemplate } from '../../services/listingService';

/** Bulk-create managed store listings from a CSV/XLSX template. */
export default function BulkListingModal({ open, onClose, onImported, storeId }) {
    const fileRef = useRef(null);
    const [file, setFile] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState(null);

    if (!open) return null;

    const reset = () => {
        setFile(null);
        setError('');
        setResult(null);
        if (fileRef.current) fileRef.current.value = '';
    };

    const handleClose = () => {
        reset();
        onClose();
    };

    const handleTemplate = () => {
        downloadListingTemplate(storeId)
            .then((res) => {
                const url = URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }));
                const a = document.createElement('a');
                a.href = url;
                a.download = 'listing_template.csv';
                a.click();
                URL.revokeObjectURL(url);
            })
            .catch(() => setError('Could not download the template.'));
    };

    const handleUpload = () => {
        if (!file) {
            setError('Choose a CSV or XLSX file first.');
            return;
        }
        setUploading(true);
        setError('');
        setResult(null);
        bulkUploadListings(storeId, file)
            .then((res) => {
                setResult(res.data);
                onImported?.(res.data);
            })
            .catch((err) => {
                setError(err.response?.data?.detail || 'Upload failed.');
            })
            .finally(() => setUploading(false));
    };

    const invalidRows = (result?.rows || []).filter((r) => !r.valid);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={handleClose} aria-hidden="true" />
            <div
                className="relative flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex shrink-0 items-center justify-between border-b border-slate-200 dark:border-slate-700 px-6 py-4">
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Bulk listing upload</h2>
                    <button type="button" className="rounded-md p-2 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={handleClose}>
                        <X className="h-5 w-5" />
                    </button>
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
                    <p className="text-sm text-slate-600 dark:text-slate-400">
                        Download the template, fill one row per variant, then upload it. Valid rows are imported as
                        created products (upserted by Variant Key); rows with errors are listed below so you can fix and re-upload.
                    </p>

                    <div className="mt-4 flex flex-wrap items-center gap-2">
                        <Button variant="secondary" size="sm" onClick={handleTemplate}>
                            <Download className="mr-1.5 h-4 w-4" />
                            Download template
                        </Button>
                        <input
                            ref={fileRef}
                            type="file"
                            accept=".csv,.xlsx"
                            onChange={(e) => setFile(e.target.files?.[0] || null)}
                            className="text-sm text-slate-600 dark:text-slate-400 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 dark:file:bg-slate-800 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700 dark:file:text-slate-300"
                        />
                    </div>

                    {error && (
                        <div className="mt-4 rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 px-4 py-3 text-sm text-rose-700 dark:text-rose-300">
                            {error}
                        </div>
                    )}

                    {result && (
                        <div className="mt-4 space-y-3">
                            <div className="rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-300">
                                Imported {result.imported} of {result.total_rows} row(s).
                                {invalidRows.length > 0 && ` ${invalidRows.length} row(s) had errors.`}
                            </div>
                            {invalidRows.length > 0 && (
                                <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                                    <table className="w-full text-left text-xs">
                                        <thead className="bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
                                            <tr>
                                                <th className="px-3 py-2">Row</th>
                                                <th className="px-3 py-2">Variant</th>
                                                <th className="px-3 py-2">Errors</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {invalidRows.map((r) => (
                                                <tr key={r.row_number} className="border-t border-slate-100 dark:border-slate-800">
                                                    <td className="px-3 py-2 text-slate-600 dark:text-slate-400">{r.row_number}</td>
                                                    <td className="px-3 py-2 text-slate-800 dark:text-slate-200">{r.variant_key || r.sku || '—'}</td>
                                                    <td className="px-3 py-2 text-rose-600 dark:text-rose-400">{(r.errors || []).join(' ')}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                <div className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-200 dark:border-slate-700 px-6 py-4">
                    <Button variant="secondary" onClick={handleClose} disabled={uploading}>
                        Close
                    </Button>
                    <Button variant="primary" onClick={handleUpload} disabled={uploading || !file}>
                        <UploadCloud className="mr-1.5 h-4 w-4" />
                        {uploading ? 'Uploading…' : 'Upload & import'}
                    </Button>
                </div>
            </div>
        </div>
    );
}
