import api from './api';
import { apiDownload } from '../utils/downloadFile';

export async function getAutoBotJobs() {
    const response = await api.get('/auto-bot/jobs/');
    return response.data;
}

export async function createAutoBotJob({ jobType, file, storeName }) {
    const body = new FormData();
    body.append('job_type', jobType);
    body.append('file', file);
    body.append('store_name', storeName || '');
    const response = await api.post('/auto-bot/jobs/', body);
    return response.data;
}

export async function cancelAutoBotJob(jobId) {
    const response = await api.post(`/auto-bot/jobs/${jobId}/cancel/`);
    return response.data;
}

export async function deleteAutoBotJob(jobId) {
    await api.delete(`/auto-bot/jobs/${jobId}/`);
}

export async function getAutoBotLaptops() {
    const response = await api.get('/auto-bot/laptops/');
    return response.data;
}

/** kind is 'input', 'done' or 'remaining'. */
export function downloadAutoBotFile(jobId, kind) {
    return apiDownload(api, `/auto-bot/jobs/${jobId}/download/${kind}/`, {
        fallbackFilename: `${kind}.xlsx`,
    });
}
