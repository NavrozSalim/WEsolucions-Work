import api from './api';
import { jwtDecode } from 'jwt-decode';

const storeTokens = (data) => {
    if (data?.access) {
        localStorage.setItem('access_token', data.access);
        localStorage.setItem('refresh_token', data.refresh);
    }
};

export const login = async (email, password, accountType) => {
    const payload = { email, password };
    if (accountType) payload.account_type = accountType;
    const response = await api.post('/auth/login/', payload);
    storeTokens(response.data);
    return response.data;
};

export const register = async (email, password, firstName, lastName) => {
    return await api.post('/auth/register/', {
        email,
        password,
        first_name: firstName,
        last_name: lastName,
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
        if (decoded.exp * 1000 < Date.now()) {
            logout();
            return null;
        }
        return decoded;
    } catch {
        return null;
    }
};

export const getUserProfile = async () => {
    const response = await api.get('/auth/profile/');
    return response.data;
};

export const getPricingPlans = async () => {
    const response = await api.get('/auth/pricing/');
    return response.data;
};

export const startSuperUserSignup = async (payload) => {
    const response = await api.post('/auth/super-user/signup/', payload);
    return response.data;
};

export const verifySuperUserOtp = async (email, code) => {
    const response = await api.post('/auth/super-user/verify-otp/', { email, code });
    storeTokens(response.data);
    return response.data;
};

export const getTeam = async () => {
    const response = await api.get('/auth/team/');
    return response.data;
};

export const createTeamMember = async (payload) => {
    const response = await api.post('/auth/team/', payload);
    return response.data;
};

export const updateTeamMember = async (memberId, payload) => {
    const response = await api.patch(`/auth/team/${memberId}/`, payload);
    return response.data;
};

export const deleteTeamMember = async (memberId) => {
    await api.delete(`/auth/team/${memberId}/`);
};

export const updateOrganizationPlan = async (seatLimit) => {
    const response = await api.patch('/auth/organization/plan/', { seat_limit: seatLimit });
    return response.data;
};

export const getPlatformStats = async () => {
    const response = await api.get('/auth/platform/stats/');
    return response.data;
};

export const getPlatformUsers = async (params = {}) => {
    const response = await api.get('/auth/platform/users/', { params });
    return response.data;
};

export const getPlatformOrganizations = async (params = {}) => {
    const response = await api.get('/auth/platform/organizations/', { params });
    return response.data;
};

export const getPlatformAudit = async (params = {}) => {
    const response = await api.get('/auth/platform/audit/', { params });
    return response.data;
};

export const getPlatformUserDetail = async (userId) => {
    const response = await api.get(`/auth/platform/users/${userId}/`);
    return response.data;
};

export const updatePlatformUser = async (userId, payload) => {
    const response = await api.patch(`/auth/platform/users/${userId}/`, payload);
    return response.data;
};

export const removePlatformUser = async (userId, { hard = false } = {}) => {
    const response = await api.delete(`/auth/platform/users/${userId}/`, {
        params: hard ? { hard: '1' } : undefined,
    });
    return response.data;
};

export const hasPermission = (user, key) => {
    if (!user) return false;
    if (user.is_platform_admin) return true;
    if (user.account_type === 'super_user' || user.account_type === 'standalone') return true;
    return Boolean(user.permissions?.[key]);
};

const HOME_CANDIDATES = [
    { path: '/app', permission: 'dashboard' },
    { path: '/catalog', permission: 'catalog' },
    { path: '/orders', permission: 'orders' },
    { path: '/tickets', permission: 'tickets' },
    { path: '/store-settings', permission: 'stores' },
    { path: '/team', permission: 'team' },
];

export const getDefaultAppPath = (user) => {
    if (!user) return '/login/choose';
    if (user.is_platform_admin) return '/platform';
    for (const item of HOME_CANDIDATES) {
        if (hasPermission(user, item.permission)) return item.path;
    }
    return '/app';
};
