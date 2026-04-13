import { createContext, useState, useEffect } from 'react';
import {
    getCurrentUser,
    getUserProfile,
    login as authLogin,
    logout as authLogout,
} from '../services/authService';

export const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
    const [user, setUser] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const bootstrapAuth = async () => {
            const currentUser = getCurrentUser();
            if (!currentUser) {
                setUser(null);
                setLoading(false);
                return;
            }

            try {
                const profile = await getUserProfile();
                setUser(profile);
            } catch {
                authLogout();
                setUser(null);
            } finally {
                setLoading(false);
            }
        };

        bootstrapAuth();
    }, []);

    const login = async (email, password) => {
        await authLogin(email, password);
        try {
            const profile = await getUserProfile();
            setUser(profile);
        } catch {
            setUser(getCurrentUser());
        }
    };

    const setUserFromTokens = async () => {
        setLoading(true);
        try {
            const profile = await getUserProfile();
            setUser(profile);
        } catch {
            setUser(getCurrentUser());
        } finally {
            setLoading(false);
        }
    };

    const logout = () => {
        authLogout();
        setUser(null);
    };

    return (
        <AuthContext.Provider value={{ user, login, logout, setUserFromTokens, loading }}>
            {children}
        </AuthContext.Provider>
    );
};
