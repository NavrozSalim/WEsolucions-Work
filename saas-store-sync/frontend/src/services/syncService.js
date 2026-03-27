import api from './api';

export const triggerSync = (storeId) => api.post(`/stores/${storeId}/sync/manual/`);
