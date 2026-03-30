import axios from 'axios';
import {
    DEV_AUTH_BYPASS,
    DEV_AUTH_TOKEN_KEY,
    DEV_AUTH_TOKEN_VALUE,
} from '../config/devAuthBypass';

const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1',
});

// Request interceptor to add JWT token
api.interceptors.request.use(
    (config) => {
        const token = localStorage.getItem('access_token');
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => Promise.reject(error)
);

// Response interceptor to handle token refresh
api.interceptors.response.use(
    (response) => response,
    async (error) => {
        const originalRequest = error.config;
        const devBypassTokenPresent =
            DEV_AUTH_BYPASS &&
            (localStorage.getItem(DEV_AUTH_TOKEN_KEY) === DEV_AUTH_TOKEN_VALUE ||
                localStorage.getItem('access_token') === DEV_AUTH_TOKEN_VALUE);

        if (devBypassTokenPresent) {
            // In dev bypass mode, avoid refresh/login redirect loops on fake tokens.
            return Promise.reject(error);
        }

        if (error.response?.status === 401 && !originalRequest._retry) {
            originalRequest._retry = true;
            try {
                const refreshToken = localStorage.getItem('refresh_token');
                if (!refreshToken) {
                    throw new Error('No refresh token available');
                }
                const response = await axios.post(`${api.defaults.baseURL}/auth/refresh/`, {
                    refresh: refreshToken,
                });

                const { access } = response.data;
                localStorage.setItem('access_token', access);

                // Retry the original request with the new token
                originalRequest.headers.Authorization = `Bearer ${access}`;
                return api(originalRequest);
            } catch (err) {
                // If refresh fails, log out user
                localStorage.removeItem('access_token');
                localStorage.removeItem('refresh_token');
                window.location.href = '/login';
                return Promise.reject(err);
            }
        }
        return Promise.reject(error);
    }
);

export default api;
