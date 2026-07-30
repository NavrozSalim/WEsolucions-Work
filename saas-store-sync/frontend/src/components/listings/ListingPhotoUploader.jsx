import { useRef, useState } from 'react';
import { ImagePlus, Loader2, X } from 'lucide-react';
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
 * Supports file upload and pasting Photo URLs / Image URLs (pipe/comma/newline separated).
 */
export default function ListingPhotoUploader({
    storeId,
    value = '',
    onChange,
    required = false,
    label = 'Photo URLs',
}) {
    const inputRef = useRef(null);
    const dragFromRef = useRef(null);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState('');
    const [dragFrom, setDragFrom] = useState(null);
    const [dragOver, setDragOver] = useState(null);
    const [pasteText, setPasteText] = useState('');
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

    const applyPastedUrls = () => {
        const pasted = splitUrls(pasteText);
        if (!pasted.length) {
            setError('Paste one or more image URLs (separate with | , or new lines).');
            return;
        }
        const merged = [...urls];
        pasted.forEach((u) => {
            if (!merged.includes(u)) merged.push(u);
        });
        setUrls(merged);
        setPasteText('');
        setError('');
    };

    const removeAt = (idx) => {
        setUrls(urls.filter((_, i) => i !== idx));
    };

    const reorder = (from, to) => {
        if (from == null || to == null || from === to) return;
        if (from < 0 || to < 0 || from >= urls.length || to >= urls.length) return;
        const copy = [...urls];
        const [item] = copy.splice(from, 1);
        copy.splice(to, 0, item);
        setUrls(copy);
    };

    const clearDrag = () => {
        dragFromRef.current = null;
        setDragFrom(null);
        setDragOver(null);
    };

    return (
        <div className="sm:col-span-2">
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">
                {label} {required ? <span className="text-rose-500">*</span> : null}
            </label>
            <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
                Upload from your computer or paste URLs (same as the bulk template). Drag to reorder — photo 1 is the main image.
            </p>

            <div className="flex flex-wrap gap-3">
                {urls.map((url, idx) => {
                    const isDragging = dragFrom === idx;
                    const isOver = dragOver === idx && dragFrom !== idx;
                    return (
                        <div
                            key={`${url}-${idx}`}
                            draggable
                            onDragStart={(e) => {
                                dragFromRef.current = idx;
                                setDragFrom(idx);
                                e.dataTransfer.effectAllowed = 'move';
                                e.dataTransfer.setData('text/plain', String(idx));
                            }}
                            onDragOver={(e) => {
                                e.preventDefault();
                                e.dataTransfer.dropEffect = 'move';
                                if (dragOver !== idx) setDragOver(idx);
                            }}
                            onDragLeave={() => {
                                if (dragOver === idx) setDragOver(null);
                            }}
                            onDrop={(e) => {
                                e.preventDefault();
                                const from = dragFromRef.current ?? Number(e.dataTransfer.getData('text/plain'));
                                reorder(from, idx);
                                clearDrag();
                            }}
                            onDragEnd={clearDrag}
                            title="Drag to reorder"
                            className={`relative h-24 w-24 cursor-grab overflow-hidden rounded-lg border bg-slate-100 dark:bg-slate-800 active:cursor-grabbing ${
                                isDragging
                                    ? 'border-accent-500 opacity-40'
                                    : isOver
                                      ? 'border-accent-500 ring-2 ring-accent-500/40'
                                      : 'border-slate-200 dark:border-slate-600'
                            }`}
                        >
                            <img
                                src={url}
                                alt={`Photo ${idx + 1}`}
                                draggable={false}
                                className="pointer-events-none h-full w-full object-cover"
                                onError={(e) => {
                                    e.currentTarget.style.opacity = '0.35';
                                }}
                            />
                            <span className="pointer-events-none absolute left-1 bottom-1 rounded bg-black/65 px-1 text-[10px] font-medium text-white">
                                {idx + 1}
                                {idx === 0 ? ' · main' : ''}
                            </span>
                            <button
                                type="button"
                                title="Remove"
                                className="absolute right-1 top-1 rounded-full bg-black/60 p-0.5 text-white hover:bg-black/80"
                                onMouseDown={(e) => e.stopPropagation()}
                                onClick={(e) => {
                                    e.stopPropagation();
                                    removeAt(idx);
                                }}
                            >
                                <X className="h-3.5 w-3.5" />
                            </button>
                        </div>
                    );
                })}

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

            <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
                <div className="min-w-0 flex-1">
                    <label className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-400">
                        Paste {label}
                    </label>
                    <textarea
                        rows={2}
                        value={pasteText}
                        onChange={(e) => setPasteText(e.target.value)}
                        placeholder="https://…/a.jpg | https://…/b.jpg"
                        className="block w-full rounded-md border border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-sm focus:border-accent-500 focus:ring-1 focus:ring-accent-500 px-3 py-2 text-sm outline-none"
                    />
                </div>
                <button
                    type="button"
                    onClick={applyPastedUrls}
                    className="shrink-0 rounded-md border border-slate-200 dark:border-slate-600 px-3 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800"
                >
                    Add URLs
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
