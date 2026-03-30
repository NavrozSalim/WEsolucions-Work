import api from './api';

export const getStores = () => api.get('/stores/');
export const createStore = (data) => api.post('/stores/', data);
export const getMarketplaces = () => api.get('/marketplaces/');
export const getVendors = () => api.get('/vendors/');
export const getStore = (id) => api.get(`/stores/${id}/`);
export const updateStore = (id, data) => api.patch(`/stores/${id}/`, data);
export const updateStoreActive = (id, isActive) => api.patch(`/stores/${id}/`, { is_active: isActive });
export const deleteStore = (id) => api.delete(`/stores/${id}/`);

export const validateStore = (id) => api.post(`/stores/${id}/validate/`);

export const getSyncSchedule = (storeId) => api.get(`/stores/${storeId}/sync/schedule/`);
export const updateSyncSchedule = (storeId, data) => api.put(`/stores/${storeId}/sync/schedule/`, data);
export const createSyncSchedule = (storeId, data) => api.post(`/stores/${storeId}/sync/schedule/`, data);

export const triggerStoreUpdate = (storeId, runInline = true) =>
    api.post(`/stores/${storeId}/sync/update/`, { run_inline: runInline });

/** Poll Celery job until run_store_update finishes (or timeout). */
export const getStoreSyncJobStatus = (storeId, jobId) =>
    api.get(`/stores/${storeId}/sync/jobs/${jobId}/`);

export function pollStoreUpdateJob(storeId, jobId, options = {}) {
    const intervalMs = options.intervalMs ?? 1500;
    const maxWaitMs = options.maxWaitMs ?? 180000;
    const start = Date.now();

    return new Promise((resolve, reject) => {
        const poll = () => {
            getStoreSyncJobStatus(storeId, jobId)
                .then((res) => {
                    const d = res.data;
                    if (d.ready) {
                        if (d.successful) resolve(d.result);
                        else reject(new Error(d.error || 'Update task failed'));
                        return;
                    }
                    if (Date.now() - start > maxWaitMs) {
                        reject(
                            new Error(
                                'Timed out waiting for update. Start a Celery worker: celery -A core worker'
                            )
                        );
                        return;
                    }
                    setTimeout(poll, intervalMs);
                })
                .catch(reject);
        };
        poll();
    });
}

/** Human-readable summary for run_store_update task result. */
export function formatStoreUpdateResult(r) {
    if (!r || typeof r !== 'object') return { message: 'Update finished.', variant: 'info' };

    if (r.skipped && r.reason === 'not_connected') {
        return { message: 'Update skipped: store not connected.', variant: 'error' };
    }
    if (r.skipped && r.reason === 'store_not_found') {
        return { message: r.hint || 'Store not found.', variant: 'error' };
    }

    const processed = r.listings_processed;
    const scraped = r.scraped;
    const pushed = r.pushed;
    const parts = [];
    if (processed != null) parts.push(`${processed} listing(s) processed`);
    if (scraped != null) parts.push(`${scraped} scraped OK`);
    if (pushed != null) parts.push(`${pushed} pushed to marketplace`);
    if (r.push_failed) parts.push(`${r.push_failed} push failed`);
    if (r.push_skipped) parts.push(`${r.push_skipped} not pushed (missing Reverb listing ID)`);

    let message = parts.length ? parts.join(' · ') : 'Update finished.';
    if (r.hint) message += ` — ${r.hint}`;
    if (r.error_summary && r.error_summary !== r.hint && !message.includes(r.error_summary)) {
        message += ` — ${r.error_summary}`;
    }
    if (Array.isArray(r.push_errors) && r.push_errors.length) {
        const first = r.push_errors[0];
        const errHint = [first.sku, first.error].filter(Boolean).join(': ');
        if (errHint) message += ` (${errHint.slice(0, 140)}${errHint.length > 140 ? '…' : ''})`;
    }
    if (r.store_is_active === false) {
        message +=
            ' Tip: Store “Active” is off — turn it on for scheduled daily sync; manual Update still ran.';
    }

    const procN = Number(processed ?? 0);
    const scrapedN = Number(scraped ?? 0);
    const pushedN = Number(pushed ?? 0);
    const nothingDone = procN === 0 && scrapedN === 0 && pushedN === 0 && !r.push_failed;

    let variant = 'success';
    if (r.push_failed > 0) variant = 'error';
    else if (nothingDone || (procN > 0 && scrapedN === 0)) variant = 'info';
    else if (r.error_summary || r.push_skipped > 0 || r.hint) variant = 'info';

    return { message, variant };
}

export const getDashboardSummary = (params) => api.get('/analytics/dashboard/', { params });
export const getAnalyticsCharts = (params) => api.get('/analytics/charts/', { params });
