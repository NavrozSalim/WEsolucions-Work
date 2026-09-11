function messageFromJsonBody(data) {
    if (!data || typeof data !== 'object') return '';
    if (typeof Blob !== 'undefined' && data instanceof Blob) return '';
    const detail = data.detail;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (Array.isArray(detail)) {
        const joined = detail.map((x) => (typeof x === 'string' ? x : x?.msg || x?.detail || '')).filter(Boolean).join('; ');
        if (joined) return joined;
    }
    if (typeof data.error === 'string' && data.error.trim()) return data.error.trim();
    if (typeof data.message === 'string' && data.message.trim()) return data.message.trim();
    return '';
}

export function filenameFromContentDisposition(header, fallback) {
    const raw = String(header || '');
    if (raw) {
        const star = raw.match(/filename\*=UTF-8''([^;]+)/i);
        if (star?.[1]) {
            try {
                return decodeURIComponent(star[1].trim().replace(/^["']|["']$/g, ''));
            } catch {
                /* keep looking */
            }
        }
        const plain = raw.match(/filename="?([^";\n]+)"?/i);
        if (plain?.[1]) return plain[1].trim();
    }
    return fallback || 'download';
}

/** Save a file in the browser. Link must be in the document or some browsers skip the download. */
export function saveBlob(data, filename, mimeType) {
    const blob = data instanceof Blob
        ? data
        : new Blob([data], mimeType ? { type: mimeType } : undefined);
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename || 'download');
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    window.setTimeout(() => {
        link.remove();
        window.URL.revokeObjectURL(url);
    }, 1500);
}

export function saveResponseFile(res, fallbackFilename, mimeType) {
    const cd = res?.headers?.['content-disposition'] || res?.headers?.['Content-Disposition'] || '';
    const filename = filenameFromContentDisposition(cd, fallbackFilename);
    saveBlob(res.data, filename, mimeType);
    return filename;
}

export async function readAxiosErrorMessage(err, fallback = 'Download failed.') {
    if (!err) return fallback;
    const fromJson = messageFromJsonBody(err.response?.data);
    if (fromJson) return fromJson;
    const data = err.response?.data;
    if (typeof Blob !== 'undefined' && data instanceof Blob) {
        try {
            const text = (await data.text()).trim();
            if (!text) return fallback;
            try {
                return messageFromJsonBody(JSON.parse(text)) || fallback;
            } catch {
                if (text.startsWith('<')) {
                    const status = err.response?.status;
                    if (status === 404) return 'File not found.';
                    if (status === 502 || status === 504 || status === 503) {
                        return 'The server took too long to build this file. Try again or export a smaller set.';
                    }
                    return fallback;
                }
                return text.slice(0, 240);
            }
        } catch {
            return fallback;
        }
    }
    if (typeof data === 'string' && data.trim()) return data.trim();
    if (err.code === 'ECONNABORTED') return 'The download took too long. Try again.';
    if (err.response?.status === 404) return 'File not found.';
    return err.message || fallback;
}

export async function wrapDownloadError(err, fallback = 'Download failed.') {
    const msg = await readAxiosErrorMessage(err, fallback);
    const next = new Error(msg);
    next.cause = err;
    next.response = err?.response;
    next.code = err?.code;
    next.isDownloadError = true;
    return next;
}

/**
 * GET a file, save it, and turn JSON-as-blob errors into a normal Error.message.
 * Pass the shared axios `api` client so this util does not import services.
 */
export async function apiDownload(client, path, { params, fallbackFilename, mimeType } = {}) {
    try {
        const res = await client.get(path, { params, responseType: 'blob' });
        const ct = String(res.headers?.['content-type'] || '');
        if (ct.includes('application/json')) {
            throw await wrapDownloadError({ response: { data: res.data, status: res.status } }, 'Download failed.');
        }
        saveResponseFile(res, fallbackFilename, mimeType);
        return res;
    } catch (err) {
        if (err?.isDownloadError) throw err;
        throw await wrapDownloadError(err, 'Download failed.');
    }
}
