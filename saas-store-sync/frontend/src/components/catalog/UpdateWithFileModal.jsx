import { useState, useRef, useCallback } from 'react';
import { UploadCloud, FileSpreadsheet, X, Download } from 'lucide-react';
import Button from '../ui/Button';
import Select from '../ui/Select';

const ACCEPT = '.xlsx,.xls,.csv';

export default function UpdateWithFileModal({
    open, onClose, onUpload, storeName, storeMarketplace, storeId, downloadSample, loading = false,
    file, setFile, template, setTemplate,
}) {
    const [dragActive, setDragActive] = useState(false);
    const [error, setError] = useState('');
    const fileInputRef = useRef(null);

    const validateFile = (f) => {
        if (!f) return false;
        const ext = (f.name || '').toLowerCase();
        if (!ext.endsWith('.xlsx') && !ext.endsWith('.xls') && !ext.endsWith('.csv')) {
            setError('Please upload XLSX, XLS, or CSV file.');
            return false;
        }
        if (f.size > 50 * 1024 * 1024) {
            setError('File size must be under 50MB.');
            return false;
        }
        setError('');
        return true;
    };

    const handleFile = useCallback((f) => {
        if (!f) return;
        if (validateFile(f)) setFile(f);
    }, [setFile]);

    const handleDrop = (e) => {
        e.preventDefault();
        setDragActive(false);
        const f = e.dataTransfer?.files?.[0];
        handleFile(f);
    };

    const handleDragOver = (e) => {
        e.preventDefault();
        setDragActive(true);
    };

    const handleDragLeave = () => setDragActive(false);

    const handleInputChange = (e) => {
        const f = e.target.files?.[0];
        handleFile(f);
        e.target.value = '';
    };

    const handleBrowse = () => fileInputRef.current?.click();

    const handleRemove = () => {
        setFile(null);
        setError('');
    };

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!storeId) {
            setError('No store selected. Please go back and select a store first.');
            return;
        }
        if (!file) {
            setError('Please select a file.');
            return;
        }
        if (!validateFile(file)) return;
        onUpload(file);
    };

    const handleClose = () => {
        setError('');
        onClose();
    };

    if (!open) return null;
    const mk = (storeMarketplace || '').trim().toLowerCase();
    const isWalmart = mk === 'walmart';
    const isReverb = mk === 'reverb';
    const isSears = mk === 'sears';

    const templateHint = (() => {
        if (isWalmart) {
            return 'Walmart template: Vendor Name, Vendor ID, Marketplace Name, Store Name, SKU, Vendor URL, Action, Pack QTY, Prep Fees, Shipping Fees.';
        }
        if (isSears) {
            return 'Sears template: all SKU/variant columns (Parent/Child/Marketplace ID/Vendor SKU), Vendor URL, and Action — no Walmart fee columns.';
        }
        if (isReverb) {
            return 'Reverb template: Vendor Name, Vendor ID, Marketplace Name, Store Name, SKU, Vendor URL, Action.';
        }
        return 'Pick a store to download the matching template, or use the generic template (all columns).';
    })();

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={handleClose} aria-hidden="true" />
            <div
                className="relative w-full max-w-lg rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between px-6 py-5 border-b border-slate-200 dark:border-slate-700">
                    <div>
                        <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100">Update with file</h2>
                        {storeName && (
                            <p className="mt-1 text-sm font-medium text-accent-600 dark:text-accent-400">
                                Upload file for {storeName}{storeMarketplace ? ` (${storeMarketplace})` : ''}
                            </p>
                        )}
                    </div>
                    <button
                        type="button"
                        className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition"
                        onClick={handleClose}
                        aria-label="Close"
                    >
                        <X className="h-5 w-5" />
                    </button>
                </div>

                <form onSubmit={handleSubmit} className="p-6 space-y-6">
                    {!storeId && (
                        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-sm text-amber-800 dark:text-amber-200">
                            No store selected. Please close this modal and select a store first.
                        </div>
                    )}

                    <div>
                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Template</label>
                        <Select
                            value={template}
                            onChange={(e) => setTemplate(e.target.value)}
                            options={[
                                {
                                    value: 'standard',
                                    label: isWalmart
                                        ? 'Walmart catalog template'
                                        : isSears
                                          ? 'Sears catalog template'
                                          : isReverb
                                            ? 'Reverb catalog template'
                                            : 'Catalog template (matches store marketplace)',
                                },
                                { value: 'download', label: 'Download sample CSV' },
                            ]}
                            className="w-full"
                        />
                        {template === 'download' && (
                            <button
                                type="button"
                                onClick={() => downloadSample?.()}
                                className="mt-2 inline-flex items-center gap-2 text-sm text-accent-600 dark:text-accent-400 hover:underline"
                            >
                                <Download className="h-4 w-4" />
                                Download CSV template
                            </button>
                        )}
                        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{templateHint}</p>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">File</label>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept={ACCEPT}
                            onChange={handleInputChange}
                            className="hidden"
                        />
                        <div
                            onDrop={handleDrop}
                            onDragOver={handleDragOver}
                            onDragLeave={handleDragLeave}
                            onClick={handleBrowse}
                            className={`
                                relative min-h-[160px] rounded-lg border-2 border-dashed flex flex-col items-center justify-center gap-2 p-6 cursor-pointer transition
                                ${dragActive
                                    ? 'border-accent-500 bg-accent-50/50 dark:bg-accent-900/20'
                                    : file
                                        ? 'border-slate-300 dark:border-slate-600 bg-slate-50 dark:bg-slate-800/50'
                                        : 'border-slate-300 dark:border-slate-600 hover:border-slate-400 dark:hover:border-slate-500 hover:bg-slate-50/50 dark:hover:bg-slate-800/30'
                                }
                            `}
                        >
                            {file ? (
                                <div className="flex flex-col items-center gap-2">
                                    <FileSpreadsheet className="h-10 w-10 text-slate-500 dark:text-slate-400" />
                                    <span className="text-sm font-medium text-slate-900 dark:text-slate-100 truncate max-w-full px-4">
                                        {file.name}
                                    </span>
                                    <span className="text-xs text-slate-500 dark:text-slate-400">
                                        {(file.size / 1024).toFixed(1)} KB
                                    </span>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        onClick={(e) => { e.stopPropagation(); handleRemove(); }}
                                        className="mt-1"
                                    >
                                        Remove file
                                    </Button>
                                </div>
                            ) : (
                                <>
                                    <UploadCloud className="h-10 w-10 text-slate-400 dark:text-slate-500" />
                                    <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                                        Drop your file here or click to browse
                                    </p>
                                    <p className="text-xs text-slate-500 dark:text-slate-400">
                                        XLSX, XLS, or CSV — max 50MB
                                    </p>
                                </>
                            )}
                        </div>
                    </div>

                    {error && (
                        <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>
                    )}

                    <div className="flex gap-3 justify-end pt-2">
                        <Button type="button" variant="secondary" onClick={handleClose}>
                            Cancel
                        </Button>
                        <Button
                            type="submit"
                            variant="primary"
                            disabled={!storeId || !file || loading}
                        >
                            {loading ? 'Uploading…' : 'Upload Catalog'}
                        </Button>
                    </div>
                </form>
            </div>
        </div>
    );
}
