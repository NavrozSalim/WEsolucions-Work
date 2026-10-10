import api from './api';
import { apiDownload, filenameFromContentDisposition, saveBlob } from '../utils/downloadFile';

export async function getDiscoveryOptions() {
    const response = await api.get('/discovery/options/');
    return response.data;
}

export async function getDiscoveryJobs() {
    const response = await api.get('/discovery/jobs/');
    return response.data;
}

export async function createDiscoveryJob({ file, marketplace, mode, rules, columns, useSample, zipCode }) {
    const body = new FormData();
    body.append('file', file);
    body.append('marketplace', marketplace);
    body.append('mode', mode);
    body.append('use_sample', useSample ? 'true' : 'false');
    body.append('zip_code', zipCode || '');
    body.append('rules', JSON.stringify(rules));
    body.append('columns', JSON.stringify(columns));
    const response = await api.post('/discovery/jobs/', body);
    return response.data;
}

export async function continueDiscoveryJob(jobId, { rules, columns, useSample }) {
    const response = await api.post(`/discovery/jobs/${jobId}/continue/`, {
        rules,
        columns,
        use_sample: useSample,
    });
    return response.data;
}

export async function downloadDiscoveryTemplate(marketplace, mode) {
    const response = await api.get('/discovery/template/', {
        params: { marketplace, mode },
        responseType: 'blob',
    });
    const filename = filenameFromContentDisposition(
        response.headers['content-disposition'],
        `${marketplace}-${mode}-template.xlsx`,
    );
    saveBlob(response.data, filename);
}

export async function getDiscoveryJob(jobId) {
    const response = await api.get(`/discovery/jobs/${jobId}/`);
    return response.data;
}

export async function getDiscoveryRows(jobId, { q = '', page = 1 } = {}) {
    const response = await api.get(`/discovery/jobs/${jobId}/rows/`, {
        params: { q, page },
    });
    return response.data;
}

export async function deleteDiscoveryJob(jobId) {
    await api.delete(`/discovery/jobs/${jobId}/`);
}

export async function cancelDiscoveryJob(jobId) {
    const response = await api.post(`/discovery/jobs/${jobId}/cancel/`);
    return response.data;
}

export async function clearDiscoveryJobs() {
    const response = await api.delete('/discovery/jobs/clear/');
    return response.data;
}

export async function downloadDiscoveryIds(jobId) {
    const response = await api.get(`/discovery/jobs/${jobId}/ids/`, {
        responseType: 'blob',
    });
    const filename = filenameFromContentDisposition(
        response.headers['content-disposition'],
        `discovery-${jobId}-ids.xlsx`,
    );
    saveBlob(response.data, filename);
}

export async function downloadDiscoveryResult(jobId, format = 'xlsx') {
    const ext = format === 'csv' ? 'csv' : 'xlsx';
    return apiDownload(api, `/discovery/jobs/${jobId}/download/`, {
        params: { format: ext },
        fallbackFilename: `discovery-${jobId}.${ext}`,
        mimeType: ext === 'csv'
            ? 'text/csv;charset=utf-8'
            : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
}
