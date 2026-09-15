import { useState, useRef, useCallback } from 'react';
import { UploadCloud, FileSpreadsheet, X, Download } from 'lucide-react';
import Button from '../ui/Button';

const ACCEPT = '.xlsx,.xls,.csv';

function marketplaceKind(code, name) {
    const c = String(code || '').trim().toLowerCase();
    if (['walmart', 'sears', 'reverb', 'kogan', 'mydeal'].includes(c)) return c;
    const n = String(name || '').trim().toLowerCase();
    if (n.includes('walmart')) return 'walmart';
    if (n.includes('sears')) return 'sears';
    if (n.includes('reverb')) return 'reverb';
    if (n.includes('kogan')) return 'kogan';
    if (n.includes('mydeal') || n === 'woolworths' || n === 'wmp') return 'mydeal';
    return '';
}

function templateCopy(kind) {
    switch (kind) {
        case 'walmart':
            return {
                catalogTitle: 'Walmart catalog template',
                deleteTitle: 'Walmart delete template',
                catalogHint:
                    'Walmart: Vendor Name, Vendor ID, Marketplace Name, Store Name, SKU, Vendor URL, Action, Pack QTY, Prep Fees, Shipping Fees, Fulfillment Center ID, Lag Time.',
                deleteHint:
                    'Delete only the SKUs in this file. Same Walmart columns; set Action to Delete. Leave Replace store catalog unchecked.',
            };
        case 'sears':
            return {
                catalogTitle: 'Sears catalog template',
                deleteTitle: 'Sears delete template',
                catalogHint:
                    'Sears: Child SKU is required (never N/A). Simple listing: Child SKU, or Parent SKU with Child N/A (copied to Child). Variation: both Parent SKU and Child SKU. Also Vendor Name, Store Name, Vendor URL, Action.',
                deleteHint:
                    'Delete only the SKUs in this file. Same Sears columns; set Action to Delete. Leave Replace store catalog unchecked.',
            };
        case 'reverb':
            return {
                catalogTitle: 'Reverb catalog template',
                deleteTitle: 'Reverb delete template',
                catalogHint:
                    'Reverb: Vendor Name, Vendor ID, Marketplace Name, Store Name, SKU, Vendor URL, Action.',
                deleteHint:
                    'Delete only the SKUs in this file. Same Reverb columns; set Action to Delete. Leave Replace store catalog unchecked.',
            };
        case 'kogan':
            return {
                catalogTitle: 'Kogan catalog template',
                deleteTitle: 'Kogan delete template',
                catalogHint:
                    'Kogan: Vendor Name, Vendor ID, Marketplace Name, Store Name, SKU, Vendor URL, Action.',
                deleteHint:
                    'Delete only the SKUs in this file. Same Kogan columns; set Action to Delete. Leave Replace store catalog unchecked.',
            };
        case 'mydeal':
            return {
                catalogTitle: 'MyDeal catalog template',
                deleteTitle: 'MyDeal delete template',
                catalogHint:
                    'MyDeal catalog: Vendor Name, Vendor ID, Marketplace Name, Store Name, SKU, Vendor URL, Action. Price/Inventory CSVs from the MyDeal portal stay on Download templates on the Catalog page.',
                deleteHint:
                    'Delete only the SKUs in this file. Same MyDeal catalog columns; set Action to Delete. Does not replace the MyDeal Price/Inventory portal files.',
            };
        default:
            return {
                catalogTitle: 'Catalog template (matches store marketplace)',
                deleteTitle: 'Delete template (selected products only)',
                catalogHint:
                    'This store uses the generic catalog columns. Pick a store to download a marketplace-specific file when one exists.',
                deleteHint:
                    'Delete only the SKUs in this file. Set Action to Delete. Leave Replace store catalog unchecked.',
            };
    }
}

export default function UpdateWithFileModal({
    open, onClose, onUpload, storeName, storeMarketplace, storeMarketplaceCode, storeId, downloadSample, loading = false,
    file, setFile,
}) {
    const [dragActive, setDragActive] = useState(false);
    const [error, setError] = useState('');
    const [downloadError, setDownloadError] = useState('');
    const [downloading, setDownloading] = useState('');
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
        setDownloadError('');
        setDownloading('');
        onClose();
    };

    const handleDownload = (action) => {
        if (!downloadSample) return;
        setDownloadError('');
        setDownloading(action);
        Promise.resolve(downloadSample(action))
            .catch((err) => {
                setDownloadError(err?.message || 'Failed to download template');
            })
            .finally(() => setDownloading(''));
    };

    if (!open) return null;
    const kind = marketplaceKind(storeMarketplaceCode, storeMarketplace);
    const copy = templateCopy(kind);

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

                    <div className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                                Catalog template
                            </label>
                            <p className="text-sm text-slate-600 dark:text-slate-400">{copy.catalogTitle}</p>
                            <button
                                type="button"
                                onClick={() => handleDownload('catalog')}
                                disabled={!!downloading}
                                className="mt-2 inline-flex items-center gap-2 text-sm font-medium text-accent-600 dark:text-accent-400 hover:underline disabled:opacity-60"
                            >
                                <Download className="h-4 w-4" />
                                {downloading === 'catalog' ? 'Downloading…' : 'Download catalog template'}
                            </button>
                            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{copy.catalogHint}</p>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                                Delete template
                            </label>
                            <p className="text-sm text-slate-600 dark:text-slate-400">{copy.deleteTitle}</p>
                            <button
                                type="button"
                                onClick={() => handleDownload('delete')}
                                disabled={!!downloading}
                                className="mt-2 inline-flex items-center gap-2 text-sm font-medium text-accent-600 dark:text-accent-400 hover:underline disabled:opacity-60"
                            >
                                <Download className="h-4 w-4" />
                                {downloading === 'delete' ? 'Downloading…' : 'Download delete template'}
                            </button>
                            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{copy.deleteHint}</p>
                        </div>
                        {downloadError && (
                            <p className="text-sm text-rose-600 dark:text-rose-400">{downloadError}</p>
                        )}
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
