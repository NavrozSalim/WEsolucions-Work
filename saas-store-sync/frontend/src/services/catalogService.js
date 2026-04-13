import api from './api';

export const getCatalogStores = (marketplaceId) =>
    api.get(marketplaceId ? `/catalog/stores/?marketplace_id=${marketplaceId}` : '/catalog/stores/');

/** Fetch all product pages (server uses pagination). */
export const getProducts = async (storeId) => {
    const pageSize = 200;
    let page = 1;
    const all = [];
    let hasNext = true;
    while (hasNext) {
        const res = await api.get(`/stores/${storeId}/products/`, {
            params: { page, page_size: pageSize },
        });
        const d = res.data;
        const chunk = Array.isArray(d?.results) ? d.results : Array.isArray(d) ? d : [];
        all.push(...chunk);
        hasNext = Boolean(d?.next);
        page += 1;
        if (page > 200) break;
    }
    return { data: all };
};

export const getCatalogActivityLogs = (storeId) =>
    api.get(`/stores/${storeId}/catalog/activity-logs/`);

export const deleteProduct = (storeId, productId) => api.delete(`/stores/${storeId}/products/${productId}/`);

export const resetProductSyncStatus = (storeId, productId) =>
    api.post(`/stores/${storeId}/products/${productId}/reset_sync_status/`);

export const clearCatalog = (storeId) => api.delete(`/stores/${storeId}/catalog/clear/`);

/** List upload history for a store */
export const getCatalogUploads = (storeId) =>
    api.get(`/stores/${storeId}/catalog/uploads/`);

/** Get upload detail with rows */
export const getCatalogUploadDetail = (storeId, uploadId) =>
    api.get(`/stores/${storeId}/catalog/uploads/${uploadId}/`);

/** Delete catalog upload and linked product mappings */
export const deleteCatalogUpload = (storeId, uploadId) =>
    api.delete(`/stores/${storeId}/catalog/uploads/${uploadId}/delete/`);

/** Download original catalog file (reconstructed CSV) via the detail endpoint */
export const downloadCatalogUploadFile = (storeId, uploadId, filename) =>
    api.get(`/stores/${storeId}/catalog/uploads/${uploadId}/?action=download`, { responseType: 'blob' }).then((res) => {
        const url = window.URL.createObjectURL(new Blob([res.data]));
        const link = document.createElement('a');
        link.href = url;
        const safeName = (filename || 'catalog').replace(/\.[^/.]+$/, '');
        link.setAttribute('download', `${safeName}.csv`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });

/** Get error file URL for failed rows (use window.open or <a download> with auth header handled by api) */
export const downloadCatalogUploadErrors = (storeId, uploadId) =>
    api.get(`/stores/${storeId}/catalog/uploads/${uploadId}/errors/`, { responseType: 'blob' }).then((res) => {
        const url = window.URL.createObjectURL(new Blob([res.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `upload_errors_${uploadId}.csv`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });

/** Store-scoped upload. Creates CatalogUpload + rows. Call triggerCatalogSync after to create ProductMappings. */
export const uploadCatalog = (file, storeId) => {
    const formData = new FormData();
    formData.append('file', file);
    if (!storeId) throw new Error('Select a store first');
    return api.post(`/stores/${storeId}/catalog/upload/`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
    });
};

/** Poll a Celery job via the existing sync-job-status endpoint.
 *  Rejects with code='NO_WORKER' if the task stays pending too long (Celery not running). */
function pollCeleryJob(storeId, jobId, { intervalMs = 2000, maxWaitMs = 600000, workerGraceMs = 10000 } = {}) {
    const start = Date.now();
    let sawStarted = false;
    return new Promise((resolve, reject) => {
        const poll = () => {
            api.get(`/stores/${storeId}/sync/jobs/${jobId}/`)
                .then((res) => {
                    const d = res.data;
                    if (d.status !== 'pending') sawStarted = true;
                    if (d.ready) {
                        if (d.successful) return resolve(d.result);
                        return reject(new Error(d.error || 'Task failed'));
                    }
                    if (!sawStarted && Date.now() - start > workerGraceMs) {
                        const err = new Error('No Celery worker detected, falling back to inline.');
                        err.code = 'NO_WORKER';
                        return reject(err);
                    }
                    if (Date.now() - start > maxWaitMs) {
                        return reject(new Error('Task timed out.'));
                    }
                    setTimeout(poll, intervalMs);
                })
                .catch(reject);
        };
        poll();
    });
}

function runWithCeleryFallback(url, body, storeId, { forbidInlineFallback = false } = {}) {
    return api.post(url, body, { timeout: 600000 })
        .then((res) => {
            if (res.data?.job_id) {
                return pollCeleryJob(storeId, res.data.job_id).then((result) => ({ data: result }));
            }
            return res;
        })
        .catch((err) => {
            const transient =
                err.code === 'NO_WORKER' ||
                !err.response ||
                err.response?.status >= 500;
            if (!body.run_inline && forbidInlineFallback && transient) {
                const msg =
                    err.code === 'NO_WORKER'
                        ? 'Background worker is not running. Store-wide vendor scrape must run in Celery. Start the worker and try again.'
                        : (err.response?.data?.detail ||
                              err.response?.data?.error ||
                              err.message ||
                              'Request failed.');
                const wrapped = new Error(msg);
                wrapped.response = err.response;
                wrapped.code = err.code;
                throw wrapped;
            }
            const shouldFallback =
                !body.run_inline &&
                !forbidInlineFallback &&
                transient;
            if (shouldFallback) {
                return api.post(url, { ...body, run_inline: true }, { timeout: 600000 });
            }
            throw err;
        });
}

/** Sync catalog: create Product + ProductMapping from upload. Tries async (Celery) first, falls back to inline. */
export const triggerCatalogSync = (storeId, runInline = false, uploadId = null, options = {}) => {
    const body = {
        run_inline: runInline,
        auto_scrape: options.autoScrape !== false,
    };
    if (uploadId) body.upload_id = uploadId;
    return runWithCeleryFallback(`/stores/${storeId}/catalog/sync/`, body, storeId);
};

/** Scrape vendor URLs for price/stock, apply rules. Async (Celery) for store-wide; upload-scoped may fall back to inline. */
export const triggerCatalogScrape = (storeId, runInline = false, uploadId = null) => {
    const body = { run_inline: runInline };
    if (uploadId) body.upload_id = uploadId;
    const storeWide = !uploadId;
    return runWithCeleryFallback(`/stores/${storeId}/catalog/scrape/`, body, storeId, {
        forbidInlineFallback: storeWide,
    });
};

export const downloadSampleTemplate = (storeId = null) => {
    const path = storeId
        ? `/catalog/sample-template/?store_id=${encodeURIComponent(storeId)}`
        : '/catalog/sample-template/';
    return api.get(path, { responseType: 'blob' }).then((res) => {
        let filename = 'catalog_upload_template.csv';
        const cd = res.headers?.['content-disposition'] || res.headers?.['Content-Disposition'];
        if (cd && cd.includes('filename=')) {
            const m = cd.match(/filename="?([^";\n]+)"?/i);
            if (m) filename = m[1].trim();
        }
        const url = window.URL.createObjectURL(new Blob([res.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', filename);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });
};

/** Export active product mappings as CSV. Optional sync_status filter (e.g. failed). */
export const exportCatalogProducts = (storeId, { syncStatus } = {}) => {
    const path = syncStatus
        ? `/stores/${storeId}/products/export/?sync_status=${encodeURIComponent(syncStatus)}`
        : `/stores/${storeId}/products/export/`;
    return api.get(path, { responseType: 'blob' }).then((res) => {
        const url = window.URL.createObjectURL(new Blob([res.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `catalog_export_${storeId}.csv`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });
};

function pollCatalogCeleryJob(storeId, jobId, { intervalMs = 2000, maxWaitMs = 600000, workerGraceMs = 10000 } = {}) {
    const start = Date.now();
    let sawStarted = false;
    return new Promise((resolve, reject) => {
        const poll = () => {
            api.get(`/stores/${storeId}/catalog/jobs/${jobId}/`)
                .then((res) => {
                    const d = res.data;
                    if (d.status !== 'pending') sawStarted = true;
                    if (d.ready) {
                        if (d.successful) return resolve(d.result);
                        return reject(new Error(d.error || 'Task failed'));
                    }
                    if (!sawStarted && Date.now() - start > workerGraceMs) {
                        const err = new Error('No Celery worker detected, falling back to inline.');
                        err.code = 'NO_WORKER';
                        return reject(err);
                    }
                    if (Date.now() - start > maxWaitMs) {
                        return reject(new Error('Task timed out.'));
                    }
                    setTimeout(poll, intervalMs);
                })
                .catch(reject);
        };
        poll();
    });
}

function runCatalogJobPost(url, body, storeId) {
    return api.post(url, body, { timeout: 600000 })
        .then((res) => {
            if (res.data?.job_id) {
                return pollCatalogCeleryJob(storeId, res.data.job_id).then((result) => ({ data: result }));
            }
            return res;
        })
        .catch((err) => {
            const shouldFallback = !body.run_inline && (
                err.code === 'NO_WORKER' ||
                !err.response ||
                err.response?.status >= 500
            );
            if (shouldFallback) {
                return api.post(url, { ...body, run_inline: true }, { timeout: 600000 });
            }
            throw err;
        });
}

/** Push scraped/synced listings to marketplace (no vendor scrape). */
export const triggerCatalogPushListings = (storeId, runInline = false) =>
    runCatalogJobPost(`/stores/${storeId}/catalog/push-listings/`, { run_inline: runInline }, storeId);

/** Emergency: zero stock everywhere, deactivate store + schedule. Requires confirm: true on server. */
export const triggerCatalogCriticalZero = (storeId, runInline = false) =>
    runCatalogJobPost(
        `/stores/${storeId}/catalog/critical-zero/`,
        { confirm: true, run_inline: runInline },
        storeId,
    );
