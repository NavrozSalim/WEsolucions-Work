import api from './api';
import { apiDownload } from '../utils/downloadFile';

const base = (storeId) => `/stores/${storeId}/auto-order-bot`;

export const getAutoOrderSettings = (storeId) => api.get(`${base(storeId)}/`).then((r) => r.data);
export const setAutoOrderAutomate = (storeId, automate) =>
    api.patch(`${base(storeId)}/`, { automate }).then((r) => r.data);

export const getPurchaseAccounts = (storeId) => api.get(`${base(storeId)}/accounts/`).then((r) => r.data);
export const createPurchaseAccount = (storeId, data) =>
    api.post(`${base(storeId)}/accounts/`, data).then((r) => r.data);
export const deletePurchaseAccount = (storeId, accountId) => api.delete(`${base(storeId)}/accounts/${accountId}/`);

export const getAutoOrderRuns = (storeId) => api.get(`${base(storeId)}/runs/`).then((r) => r.data);
export const getAutoOrderRun = (storeId, runId) => api.get(`${base(storeId)}/runs/${runId}/`).then((r) => r.data);

/** kind is 'orders' or 'mapping'. Replaces the saved file. */
export function uploadAutoOrderFile(storeId, kind, file) {
    const body = new FormData();
    body.append('file', file);
    return api.post(`${base(storeId)}/files/${kind}/`, body).then((r) => r.data);
}

export function startAutoOrderRun(storeId, { account, place }) {
    const body = new FormData();
    body.append('account', account);
    body.append('place', place ? 'true' : 'false');
    return api.post(`${base(storeId)}/runs/`, body).then((r) => r.data);
}

export const cancelAutoOrderRun = (storeId, runId) =>
    api.post(`${base(storeId)}/runs/${runId}/cancel/`).then((r) => r.data);

/** kind is 'done', 'remaining' or 'log'. */
export function downloadAutoOrderFile(storeId, runId, kind) {
    return apiDownload(api, `${base(storeId)}/runs/${runId}/download/${kind}/`, {
        fallbackFilename: kind === 'log' ? 'log.txt' : `${kind}.xlsx`,
    });
}
