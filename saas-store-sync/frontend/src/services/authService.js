import api from './api';
import { jwtDecode } from 'jwt-decode';
import {
    DEV_AUTH_BYPASS,
    DEV_AUTH_TOKEN_KEY,
    DEV_AUTH_TOKEN_VALUE,
} from '../config/devAuthBypass';

const devBypassUser = () => ({
    email: 'dev@local.test',
    exp: Math.floor(Date.now() / 1000) + 86400 * 365,
});

export const login = async (email, password) => {
    const response = await api.post('/auth/login/', { email, password });
    if (response.data.access) {
        localStorage.setItem('access_token', response.data.access);
        localStorage.setItem('refresh_token', response.data.refresh);
    }
    return response.data;
};

export const register = async (email, password, firstName, lastName) => {
    return await api.post('/auth/register/', {
        email,
        password,
        first_name: firstName,
        last_name: lastName
    });
};

export const logout = () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem(DEV_AUTH_TOKEN_KEY);
};

export const getCurrentUser = () => {
    if (
        DEV_AUTH_BYPASS &&
        localStorage.getItem(DEV_AUTH_TOKEN_KEY) === DEV_AUTH_TOKEN_VALUE
    ) {
        return devBypassUser();
    }

    if (
        DEV_AUTH_BYPASS &&
        localStorage.getItem('access_token') === DEV_AUTH_TOKEN_VALUE
    ) {
        return devBypassUser();
    }

    const token = localStorage.getItem('access_token');
    if (!token) return null;

    try {
        const decoded = jwtDecode(token);
        // Checking token expiration
        if (decoded.exp * 1000 < Date.now()) {
            logout();
            return null;
        }
        return decoded;
    } catch (e) {
        return null;
    }
};
