/**
 * Build href for Django Google OAuth start (/api/v1/auth/google/).
 * Handles relative VITE_API_URL (/api/v1) and absolute bases without double slashes.
 */
export function buildGoogleOAuthStartUrl() {
    const raw = (import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1').trim();
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    const qs = new URLSearchParams({ next: '/', origin }).toString();
    const path = `/api/v1/auth/google/?${qs}`;
    const base = raw.replace(/\/api\/v1\/?$/i, '').replace(/\/+$/, '');
    if (!base) {
        return path;
    }
    return `${base}${path}`;
}
