import api from './api';

// --- Managed store listings ---
export const getListings = (storeId, params) => api.get(`/stores/${storeId}/listings/`, { params });
export const createListing = (storeId, data) => api.post(`/stores/${storeId}/listings/`, data);
export const getListing = (storeId, listingId) => api.get(`/stores/${storeId}/listings/${listingId}/`);
export const updateListing = (storeId, listingId, data) => api.put(`/stores/${storeId}/listings/${listingId}/`, data);
export const deleteListing = (storeId, listingId) => api.delete(`/stores/${storeId}/listings/${listingId}/`);

/** Push READY listings to the store's marketplace; optionally only specific ids. */
export const publishListings = (storeId, listingIds = null) =>
    api.post(`/stores/${storeId}/listings/publish/`, listingIds ? { listing_ids: listingIds } : {});

export const bulkUploadListings = (storeId, file, action = 'create') => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('action', action);
    return api.post(`/stores/${storeId}/listings/bulk-upload/`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
    });
};

export const downloadListingTemplate = (storeId, action = 'create') =>
    api.get(`/stores/${storeId}/listings/template/`, {
        params: { action },
        responseType: 'blob',
    });

/** Upload listing photo files; returns { urls: string[], photos: [...] }. */
export const uploadListingPhotos = (storeId, files) => {
    const fd = new FormData();
    const list = Array.from(files || []);
    list.forEach((file) => fd.append('photos', file));
    return api.post(`/stores/${storeId}/listings/photos/`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
    });
};

/** Reverb category catalog (uuid + full_name). Optional search `q`. */
export const getReverbCategories = (storeId, q = '') =>
    api.get(`/stores/${storeId}/listings/reverb/categories/`, {
        params: q ? { q } : {},
    });

/** Reverb condition catalog (uuid + display name). */
export const getReverbConditions = (storeId) =>
    api.get(`/stores/${storeId}/listings/reverb/conditions/`);

/** Scrape vendor URLs on managed listings → update local price/stock. */
export const scrapeListings = (storeId, listingIds = null) =>
    api.post(`/stores/${storeId}/listings/scrape/`, listingIds ? { listing_ids: listingIds } : {});

/** Live managed-listing scrape progress (processed / total). */
export const getListingScrapeProgress = (storeId) =>
    api.get(`/stores/${storeId}/listings/scrape/progress/`);

/** Push local price/stock to marketplace for already-uploaded listings. */
export const pushListingInventory = (storeId, listingIds = null) =>
    api.post(`/stores/${storeId}/listings/push-inventory/`, listingIds ? { listing_ids: listingIds } : {});

/** Reset inventory sync status to pending. scope: failed | scraped | all */
export const resetListingInventory = (storeId, scope = 'failed') =>
    api.post(`/stores/${storeId}/listings/reset-inventory/`, { scope });

/** Critical: zero stock on marketplace listings and push. */
export const criticalZeroListingInventory = (storeId) =>
    api.post(`/stores/${storeId}/listings/critical-inventory/`, { action: 'zero_inventory' });

/** Download managed inventory Excel. */
export const exportListingInventory = (storeId, syncStatus = '') =>
    api.get(`/stores/${storeId}/listings/inventory-export/`, {
        params: syncStatus ? { sync_status: syncStatus } : {},
        responseType: 'blob',
    }).then((res) => {
        const url = window.URL.createObjectURL(new Blob([res.data], {
            type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', 'managed_inventory.xlsx');
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });

/** Managed upload history. scope: 'history' (default) | 'logs' | 'all' */
export const getListingUploads = (storeId, { scope = 'history' } = {}) =>
    api.get(`/stores/${storeId}/listings/uploads/`, { params: { scope } });

/** Download failed rows from a managed Upload history entry as CSV. */
export const downloadListingUploadErrors = (storeId, uploadId, filename = '') =>
    api.get(`/stores/${storeId}/listings/uploads/${uploadId}/errors/`, { responseType: 'blob' }).then((res) => {
        const base = (filename || 'upload').replace(/\.[^.]+$/, '') || 'upload';
        const url = window.URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${base}_errors.csv`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });

/** Export all rows from a managed Upload history entry as CSV. */
export const exportListingUpload = (storeId, uploadId, filename = '') =>
    api.get(`/stores/${storeId}/listings/uploads/${uploadId}/export/`, { responseType: 'blob' }).then((res) => {
        const base = (filename || 'upload').replace(/\.[^.]+$/, '') || 'upload';
        const url = window.URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `${base}_export.csv`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });

/**
 * Delete a managed Upload history entry.
 * @param {{ deleteSystem?: boolean, deleteMarketplace?: boolean }} options
 */
export const deleteListingUpload = (storeId, uploadId, { deleteSystem = false, deleteMarketplace = false } = {}) =>
    api.delete(`/stores/${storeId}/listings/uploads/${uploadId}/`, {
        data: {
            delete_system: !!deleteSystem,
            delete_marketplace: !!deleteMarketplace,
        },
    });

// --- Orders ---
export const getOrders = (storeId, { refresh = false } = {}) =>
    api.get(`/stores/${storeId}/orders/`, { params: refresh ? { refresh: 1 } : {} });
export const createTestOrder = (storeId) => api.post(`/stores/${storeId}/orders/test/`);
export const submitOrderShipping = (storeId, orderId, data) =>
    api.post(`/stores/${storeId}/orders/${orderId}/shipping/`, data);
export const completeOrderShipping = (storeId, orderId) =>
    api.post(`/stores/${storeId}/orders/${orderId}/shipping/complete/`);
export const cancelOrder = (storeId, orderId, data = {}) =>
    api.post(`/stores/${storeId}/orders/${orderId}/cancel/`, data);
export const getOrderCancelReasons = (storeId) =>
    api.get(`/stores/${storeId}/orders/cancel-reasons/`);

/** Download store orders as Excel (.xlsx). Optional status filter (e.g. paid). */
export const exportOrdersExcel = (storeId, storeName = '', { status } = {}) => {
    const params = {};
    if (status && status !== 'all') params.status = status;
    return api.get(`/stores/${storeId}/orders/export/`, { params, responseType: 'blob' }).then((res) => {
        const safe = String(storeName || storeId).replace(/[^\w.-]+/g, '_').slice(0, 40) || 'store';
        const suffix = status && status !== 'all' ? `_${status}` : '';
        const url = window.URL.createObjectURL(new Blob([res.data], {
            type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `orders_${safe}${suffix}.xlsx`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });
};

// --- Tickets / customer messages ---
export const getTickets = (storeId, { refresh = false } = {}) =>
    api.get(`/stores/${storeId}/tickets/`, { params: refresh ? { refresh: 1 } : {} });
export const createTestTicket = (storeId) => api.post(`/stores/${storeId}/tickets/test/`);
export const replyToTicket = (storeId, ticketId, data) =>
    api.post(`/stores/${storeId}/tickets/${ticketId}/reply/`, data);

/** Download store tickets as Excel (.xlsx). */
export const exportTicketsExcel = (storeId, storeName = '') =>
    api.get(`/stores/${storeId}/tickets/export/`, { responseType: 'blob' }).then((res) => {
        const safe = String(storeName || storeId).replace(/[^\w.-]+/g, '_').slice(0, 40) || 'store';
        const url = window.URL.createObjectURL(new Blob([res.data], {
            type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `tickets_${safe}.xlsx`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    });
