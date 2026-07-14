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

// --- Tickets / customer messages ---
export const getTickets = (storeId, { refresh = false } = {}) =>
    api.get(`/stores/${storeId}/tickets/`, { params: refresh ? { refresh: 1 } : {} });
export const createTestTicket = (storeId) => api.post(`/stores/${storeId}/tickets/test/`);
export const replyToTicket = (storeId, ticketId, data) =>
    api.post(`/stores/${storeId}/tickets/${ticketId}/reply/`, data);
