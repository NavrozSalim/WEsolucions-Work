import api from './api';

export const getCatalogStores = (marketplaceId) =>
    api.get(marketplaceId ? `/catalog/stores/?marketplace_id=${marketplaceId}` : '/catalog/stores/');

/** Fetch a single page of product mappings.
 *
 * Server pagination lives on the ProductMappingViewSet; we deliberately
 * surface one page at a time so the catalog UI renders immediately even
 * on stores with tens of thousands of rows. Search (``q``) and sync
 * status (``status``) are pushed down to SQL, not filtered client-side.
 *
 * Returns ``{ data, count, page, pageSize, totalPages, next, previous }``.
 */
export const getProducts = async (storeId, options = {}) => {
    const page = Math.max(1, options.page || 1);
    const pageSize = Math.max(1, Math.min(500, options.pageSize || 50));
    const params = { page, page_size: pageSize };
    const q = (options.q || '').trim();
    if (q) params.q = q;
    const status = (options.status || '').trim();
    if (status) params.sync_status = status;
    const config = { params };
    if (options.signal) config.signal = options.signal;

    const res = await api.get(`/stores/${storeId}/products/`, config);
    const d = res.data;
    const results = Array.isArray(d?.results) ? d.results : Array.isArray(d) ? d : [];
    const count = Number.isFinite(d?.count) ? d.count : results.length;
    return {
        data: results,
        count,
        page,
        pageSize,
        totalPages: Math.max(1, Math.ceil(count / pageSize)),
        next: d?.next || null,
        previous: d?.previous || null,
    };
};

export const getCatalogActivityLogs = (storeId) =>
    api.get(`/stores/${storeId}/catalog/activity-logs/`);

export const deleteProduct = (storeId, productId) => api.delete(`/stores/${storeId}/products/${productId}/`);

export const resetProductSyncStatus = (storeId, productId) =>
    api.post(`/stores/${storeId}/products/${productId}/reset_sync_status/`);

/** Patch editable fields on a ProductMapping (currently pack_qty / prep_fees /
 *  shipping_fees for the Fixed pricing method). Partial update — backend
 *  serializer validates that all three are present when the matched tier is
 *  ``fixed``.
 */
export const updateProductMapping = (storeId, productId, patch) =>
    api.patch(`/stores/${storeId}/products/${productId}/`, patch);

export const clearCatalog = (storeId) => api.delete(`/stores/${storeId}/catalog/clear/`);

/** Reset listings to Pending. scope: 'all' | 'failed' | 'needs_attention'. Does not start scraping. */
export const resetCatalogListingsPending = (storeId, scope = 'all') =>
    api.post(`/stores/${storeId}/catalog/reset-pending/`, { confirm: true, scope });

/** @deprecated use resetCatalogListingsPending(storeId, 'all') */
export const resetAllCatalogListingsPending = (storeId) =>
    resetCatalogListingsPending(storeId, 'all');

/** List upload history for a store */
export const getCatalogUploads = (storeId, config = {}) =>
    api.get(`/stores/${storeId}/catalog/uploads/`, {
        timeout: 90_000,
        ...config,
    });

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

/** Store-scoped upload. Creates CatalogUpload + rows. Run Sync catalog (triggerCatalogSync) separately to create ProductMappings. */
export const uploadCatalog = (file, storeId) => {
    const formData = new FormData();
    formData.append('file', file);
    if (!storeId) throw new Error('Select a store first');
    return api.post(`/stores/${storeId}/catalog/upload/`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
    });
};

/** Poll a Celery job via the existing sync-job-status endpoint.
 *  Tolerates transient network errors while polling — a single nginx/HTTP2
 *  hiccup must not kill a long-running scrape (and cause the outer layer to
 *  spawn a duplicate Celery task). Only rejects when the task itself fails,
 *  MAX_POLL_FAILS consecutive polls error at the network level, or the total
 *  wait exceeds maxWaitMs.
 *  Rejects with code='NO_WORKER' if the task stays pending longer than
 *  workerGraceMs (Celery not running). */
function pollCeleryJob(storeId, jobId, { intervalMs = 3500, maxWaitMs = 600000, workerGraceMs = 10000 } = {}) {
    const start = Date.now();
    const MAX_POLL_FAILS = 6;
    let sawStarted = false;
    let consecutiveFails = 0;
    return new Promise((resolve, reject) => {
        const poll = () => {
            api.get(`/stores/${storeId}/sync/jobs/${jobId}/`)
                .then((res) => {
                    consecutiveFails = 0;
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
                .catch((err) => {
                    const isNetErr = !err.response || err.code === 'ERR_NETWORK' || err.message === 'Network Error';
                    if (isNetErr && consecutiveFails < MAX_POLL_FAILS && Date.now() - start < maxWaitMs) {
                        consecutiveFails += 1;
                        return setTimeout(poll, intervalMs);
                    }
                    return reject(err);
                });
        };
        poll();
    });
}

function runWithCeleryFallback(
    url,
    body,
    storeId,
    { forbidInlineFallback = false, skipJobPoll = false } = {},
) {
    return api.post(url, body, { timeout: 600000 })
        .then((res) => {
            if (skipJobPoll || !res.data?.job_id) {
                return res;
            }
            return pollCeleryJob(storeId, res.data.job_id).then((result) => ({ data: result }));
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
    if (options.replaceStoreCatalog === true) {
        body.replace_store_catalog = true;
    }
    return runWithCeleryFallback(`/stores/${storeId}/catalog/sync/`, body, storeId);
};

/** Scrape: async via Celery only; UI polls /catalog/scrape/progress/ (never poll /sync/jobs/). */
export const triggerCatalogScrape = (storeId, runInline = false, uploadId = null) => {
    const body = { run_inline: runInline };
    if (uploadId) body.upload_id = uploadId;
    const storeWide = !uploadId;
    return runWithCeleryFallback(`/stores/${storeId}/catalog/scrape/`, body, storeId, {
        forbidInlineFallback: storeWide,
        skipJobPoll: true,
    });
};

/**
 * Live scrape progress for a store. GET {VITE_API_URL}/stores/{storeId}/catalog/scrape/progress/
 * (storeId must be the store UUID). Used by the Catalog UI for the server scrape strip and HEB tracking.
 */
export const getScrapeProgress = (storeId) =>
    api.get(`/stores/${storeId}/catalog/scrape/progress/`, { timeout: 90_000 });

/** Live Manual sync (marketplace push) progress — poll like scrape progress. */
export const getPushListingsProgress = (storeId) =>
    api.get(`/stores/${storeId}/catalog/push-listings/progress/`, { timeout: 90_000 });

/**
 * Stop running price checks: pending jobs from your computer (HEB, etc.) and/or
 * fetches running on our servers. Safe to call when nothing is running.
 */
export const cancelCatalogScrape = (storeId) =>
    api.post(`/stores/${storeId}/catalog/scrape/cancel/`, {});

/** Map store row from /catalog/stores/ to reverb | walmart | sears for sample CSV columns. */
export const resolveMarketplaceTemplateKind = (store) => {
    if (!store) return '';
    const c = String(store.marketplace_code || '').trim().toLowerCase();
    if (['reverb', 'walmart', 'sears'].includes(c)) return c;
    const n = String(store.marketplace_name || '').trim().toLowerCase();
    if (n.includes('walmart')) return 'walmart';
    if (n.includes('sears')) return 'sears';
    if (n.includes('reverb')) return 'reverb';
    return '';
};

export const downloadSampleTemplate = (storeId = null, marketplaceKind = '') => {
    const params = new URLSearchParams();
    if (storeId) params.set('store_id', storeId);
    const kind = String(marketplaceKind || '').trim().toLowerCase();
    if (['reverb', 'walmart', 'sears'].includes(kind)) params.set('marketplace', kind);
    params.set('_cb', String(Date.now()));
    const qs = params.toString();
    const path = `/catalog/sample-template/?${qs}`;
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

/** Manual sync for large catalogs can run for hours — keep polling until Celery finishes. */
const PUSH_LISTINGS_MAX_WAIT_MS = 6 * 60 * 60 * 1000;

/**
 * Poll Celery job status until ready.
 * @param {object} options
 * @param {boolean} [options.queuedJob=false] When true, the server returned job_id (task is
 *   enqueued). Pending only means waiting in queue — never treat that as "no worker".
 * @param {function} [options.onPoll] Called each poll with { status, elapsedMs, ready }.
 */
function pollCatalogCeleryJob(
    storeId,
    jobId,
    {
        intervalMs = 3500,
        maxWaitMs = 600000,
        workerGraceMs = 10000,
        queuedJob = false,
        onPoll = null,
    } = {},
) {
    const start = Date.now();
    const MAX_POLL_FAILS = 6;
    let sawStarted = false;
    let consecutiveFails = 0;
    return new Promise((resolve, reject) => {
        const poll = () => {
            api.get(`/stores/${storeId}/catalog/jobs/${jobId}/`)
                .then((res) => {
                    consecutiveFails = 0;
                    const d = res.data;
                    const elapsedMs = Date.now() - start;
                    if (d.status !== 'pending') sawStarted = true;
                    if (typeof onPoll === 'function') {
                        onPoll({ status: d.status, elapsedMs, ready: d.ready });
                    }
                    if (d.ready) {
                        if (d.successful) return resolve(d.result);
                        return reject(new Error(d.error || 'Task failed'));
                    }
                    if (!queuedJob && !sawStarted && elapsedMs > workerGraceMs) {
                        const err = new Error('No Celery worker detected, falling back to inline.');
                        err.code = 'NO_WORKER';
                        return reject(err);
                    }
                    if (elapsedMs > maxWaitMs) {
                        const err = new Error(
                            queuedJob
                                ? 'Manual sync is still running on the server but this page stopped waiting. '
                                  + 'Check Activity for progress — do not start another Manual sync.'
                                : 'Task timed out.',
                        );
                        err.code = 'POLL_TIMEOUT';
                        return reject(err);
                    }
                    setTimeout(poll, intervalMs);
                })
                .catch((err) => {
                    const isNetErr = !err.response || err.code === 'ERR_NETWORK' || err.message === 'Network Error';
                    if (isNetErr && consecutiveFails < MAX_POLL_FAILS && Date.now() - start < maxWaitMs) {
                        consecutiveFails += 1;
                        return setTimeout(poll, intervalMs);
                    }
                    return reject(err);
                });
        };
        poll();
    });
}

function runCatalogJobPost(url, body, storeId, options = {}) {
    const {
        forbidInlineFallback = false,
        workerGraceMs = 10000,
        intervalMs = 3500,
        maxWaitMs = 600000,
        queuedJob = false,
        onPoll = null,
    } = options;

    return api.post(url, body, { timeout: 120000 })
        .then((res) => {
            if (res.data?.job_id) {
                return pollCatalogCeleryJob(storeId, res.data.job_id, {
                    intervalMs,
                    maxWaitMs,
                    workerGraceMs,
                    queuedJob: true,
                    onPoll,
                }).then((result) => ({ data: result, meta: res.data }));
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
                        ? 'Background sync worker is not running. Manual sync must run on celery_worker_sync. Ask your admin to start it, then try again.'
                        : (err.response?.data?.error ||
                              err.response?.data?.detail ||
                              err.message ||
                              'Manual sync failed. Try again in a moment.');
                const wrapped = new Error(msg);
                wrapped.response = err.response;
                wrapped.code = err.code;
                throw wrapped;
            }
            const shouldFallback = !body.run_inline && !forbidInlineFallback && transient;
            if (shouldFallback) {
                return api.post(url, { ...body, run_inline: true }, { timeout: 600000 });
            }
            throw err;
        });
}

export const getMydealTemplateStatus = (storeId) =>
    api.get(`/stores/${storeId}/mydeal/templates/status/`);

/** kind: price | inventory | zip (ZIP with one or both Mydeal CSV templates) */
export const uploadMydealTemplate = (storeId, kind, file) => {
    const form = new FormData();
    form.append('file', file);
    form.append('kind', kind);
    return api.post(`/stores/${storeId}/mydeal/templates/upload/`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
    });
};

/** type: price | inventory | both */
export const downloadMydealTemplates = (storeId, type = 'both') =>
    api.get(`/stores/${storeId}/mydeal/templates/export/`, {
        params: { type },
        responseType: 'blob',
    });

/**
 * Push scraped/synced listings to marketplace (no vendor scrape).
 * Always Celery when runInline is false — never inline (avoids Gunicorn 120s kill on large catalogs).
 * @param {object} [options]
 * @param {function} [options.onPoll] Progress callback while waiting for Celery.
 */
export const triggerCatalogPushListings = (storeId, runInline = false, options = {}) =>
    runCatalogJobPost(
        `/stores/${storeId}/catalog/push-listings/`,
        { run_inline: runInline },
        storeId,
        {
            forbidInlineFallback: !runInline,
            maxWaitMs: PUSH_LISTINGS_MAX_WAIT_MS,
            onPoll: options.onPoll,
        },
    );

/** Emergency: zero stock everywhere, deactivate store + schedule. Requires confirm: true on server. */
export const triggerCatalogCriticalZero = (storeId, runInline = false) =>
    runCatalogJobPost(
        `/stores/${storeId}/catalog/critical-zero/`,
        { confirm: true, run_inline: runInline },
        storeId,
    );

/** Zero stock for failed / needs_attention listings only; store stays active. */
export const triggerCatalogFailedZero = (storeId, runInline = false) =>
    runCatalogJobPost(
        `/stores/${storeId}/catalog/failed-zero/`,
        { confirm: true, run_inline: runInline },
        storeId,
    );
