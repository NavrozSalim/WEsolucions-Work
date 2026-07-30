import { useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, ImagePlus, Loader2, X } from 'lucide-react';
import { uploadListingPhotos } from '../../services/listingService';

function splitUrls(text) {
    return String(text || '')
        .split(/[|;\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
}

/**
 * Multi-image upload for Create/Edit listing. Stores public URLs in parent via onChange(urlsText).
 * First URL is the primary photo (order used when publishing to Reverb).
 */
export default function ListingPhotoUploader({ storeId, value = '', onChange, required = false }) {
    const inputRef = useRef(null);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState('');
    const urls = splitUrls(value);

    const setUrls = (next) => {
        onChange?.(next.join('|'));
    };

    const handleFiles = async (fileList) => {
        const files = Array.from(fileList || []).filter((f) => f.type?.startsWith('image/'));
        if (!files.length) {
            setError('Choose image files (JPG, PNG, WEBP, or GIF).');
            return;
        }
        if (!storeId) {
            setError('Select a store first.');
            return;
        }
        setUploading(true);
        setError('');
        try {
            const res = await uploadListingPhotos(storeId, files);
            const newUrls = res.data?.urls || [];
            if (!newUrls.length) {
                setError('Upload failed — no URLs returned.');
                return;
            }
            setUrls([...urls, ...newUrls]);
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not upload images.');
        } finally {
            setUploading(false);
            if (inputRef.current) inputRef.current.value = '';
        }
    };

    const removeAt = (idx) => {
        setUrls(urls.filter((_, i) => i !== idx));
    };

    const moveAt = (idx, delta) => {
        const next = idx + delta;
        if (next < 0 || next >= urls.length) return;
        const copy = [...urls];
        const [item] = copy.splice(idx, 1);
        copy.splice(next, 0, item);
        setUrls(copy);
    };

    return (
        <div className="sm:col-span-2">
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                Photos {required ? <span className="text-rose-500">*</span> : null}
            </label>
            <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
                Upload product photos from your computer. They are hosted for Reverb to fetch when you publish.
                Use the arrows to set order — photo 1 is the main image.
            </p>

            <div className="flex flex-wrap gap-3">
                {urls.map((url, idx) => (
                    <div
                        key={`${url}-${idx}`}
                        className="relative h-24 w-24 overflow-hidden rounded-lg border border-slate-200 dark:border-slate-600 bg-slate-100 dark:bg-slate-800"
                    >
                        <img
                            src={url}
                            alt={`Photo ${idx + 1}`}
                            className="h-full w-full object-cover"
                            onError={(e) => {
                                e.currentTarget.style.opacity = '0.35';
                            }}
                        />
                        <span className="absolute left-1 bottom-1 rounded bg-black/65 px-1 text-[10px] font-medium text-white">
                            {idx + 1}
                            {idx === 0 ? ' · main' : ''}
                        </span>
                        <button
                            type="button"
                            title="Remove"
                            className="absolute right-1 top-1 rounded-full bg-black/60 p-0.5 text-white hover:bg-black/80"
                            onClick={() => removeAt(idx)}
                        >
                            <X className="h-3.5 w-3.5" />
                        </button>
                        <div className="absolute left-1 top-1 flex gap-0.5">
                            <button
                                type="button"
                                title="Move earlier"
                                disabled={idx === 0}
                                className="rounded-full bg-black/60 p-0.5 text-white hover:bg-black/80 disabled:opacity-30"
                                onClick={() => moveAt(idx, -1)}
                            >
                                <ChevronLeft className="h-3.5 w-3.5" />
                            </button>
                            <button
                                type="button"
                                title="Move later"
                                disabled={idx === urls.length - 1}
                                className="rounded-full bg-black/60 p-0.5 text-white hover:bg-black/80 disabled:opacity-30"
                                onClick={() => moveAt(idx, 1)}
                            >
                                <ChevronRight className="h-3.5 w-3.5" />
                            </button>
                        </div>
                    </div>
                ))}

                <button
                    type="button"
                    disabled={uploading}
                    onClick={() => inputRef.current?.click()}
                    className="flex h-24 w-24 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-slate-300 dark:border-slate-600 text-slate-500 dark:text-slate-400 hover:border-accent-500 hover:text-accent-600 disabled:opacity-60"
                >
                    {uploading ? (
                        <Loader2 className="h-6 w-6 animate-spin" />
                    ) : (
                        <ImagePlus className="h-6 w-6" />
                    )}
                    <span className="text-[11px] font-medium">{uploading ? 'Uploading…' : 'Add photos'}</span>
                </button>
            </div>

            <input
                ref={inputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/gif,.jpg,.jpeg,.png,.webp,.gif"
                multiple
                className="hidden"
                onChange={(e) => handleFiles(e.target.files)}
            />

            {error && (
                <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>
            )}
            {required && urls.length === 0 && !error && (
                <p className="mt-2 text-xs text-slate-500">At least one photo is required.</p>
            )}
        </div>
    );
}
