import api from './api';
import { jwtDecode } from 'jwt-decode';

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
};

export const getCurrentUser = () => {
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
