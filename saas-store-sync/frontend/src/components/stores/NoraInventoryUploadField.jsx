import { useRef, useState } from 'react';
import { UploadCloud } from 'lucide-react';
import Button from '../ui/Button';
import { uploadNoraInventory } from '../../services/storeService';

/** True when vendor row is Nora Inventory (by code or name). */
export function isNoraVendor(vendor) {
    if (!vendor) return false;
    if (typeof vendor === 'string') {
        const s = vendor.trim().toLowerCase();
        return s.includes('nora');
    }
    const code = String(vendor.code || '').trim().toLowerCase();
    const name = String(vendor.name || vendor.label || '').trim().toLowerCase();
    return (
        code === 'noraau'
        || code === 'nora'
        || code.includes('nora')
        || name === 'nora inventory'
        || name.includes('nora')
    );
}

/**
 * Excel upload for Nora Inventory vendor settings.
 * When storeId is set, uploads immediately. Otherwise keeps the File for
 * the parent to upload after store create.
 */
export default function NoraInventoryUploadField({
    storeId = null,
    fileName = '',
    uploadedAt = null,
    pendingFile = null,
    onPendingFile,
    onUploaded,
    onError,
}) {
    const inputRef = useRef(null);
    const [uploading, setUploading] = useState(false);
    const [localMsg, setLocalMsg] = useState('');

    const handlePick = (e) => {
        const file = e.target.files?.[0] || null;
        if (inputRef.current) inputRef.current.value = '';
        if (!file) return;

        if (!storeId) {
            onPendingFile?.(file);
            setLocalMsg(`Selected: ${file.name} (uploads after you save the store)`);
            return;
        }

        setUploading(true);
        setLocalMsg('');
        uploadNoraInventory(storeId, file)
            .then((res) => {
                const msg = res.data?.message || `Uploaded ${file.name}`;
                setLocalMsg(msg);
                onUploaded?.(res.data);
            })
            .catch((err) => {
                const detail = err.response?.data?.detail || 'Nora upload failed.';
                setLocalMsg(detail);
                onError?.(detail);
            })
            .finally(() => setUploading(false));
    };

    const displayName = pendingFile?.name || fileName || '';

    return (
        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/20 p-3 space-y-2">
            <p className="text-sm font-medium text-slate-800 dark:text-slate-200">
                Nora Inventory Excel
            </p>
            <p className="text-xs text-slate-600 dark:text-slate-400">
                Upload the Australian inventory file. System reads sheet &quot;Export inventory&quot;,
                columns BarCode + Available inventory, then cleans -G1/-G2/-G3/-V1/-V2/-V3
                with pivot sum. Stock is applied on Start Scraping; price comes from each
                product&apos;s Vendor URL (eBay AU).
            </p>
            <div className="flex flex-wrap items-center gap-2">
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={uploading}
                    onClick={() => inputRef.current?.click()}
                >
                    <UploadCloud className="mr-1.5 h-4 w-4" />
                    {uploading ? 'Uploading…' : displayName ? 'Replace file' : 'Choose file'}
                </Button>
                <input
                    ref={inputRef}
                    type="file"
                    accept=".xlsx,.xlsm,.xls"
                    className="hidden"
                    onChange={handlePick}
                />
                {displayName && (
                    <span className="text-xs text-slate-600 dark:text-slate-400 truncate max-w-[16rem]">
                        {displayName}
                        {uploadedAt ? ` · ${new Date(uploadedAt).toLocaleString()}` : ''}
                    </span>
                )}
            </div>
            {localMsg && (
                <p className="text-xs text-slate-700 dark:text-slate-300">{localMsg}</p>
            )}
        </div>
    );
}
