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

export const getListingUploads = (storeId) =>
    api.get(`/stores/${storeId}/listings/uploads/`);

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
