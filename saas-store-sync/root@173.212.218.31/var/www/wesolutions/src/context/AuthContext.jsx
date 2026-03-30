import { createContext, useState, useEffect } from 'react';
import { getCurrentUser, login as authLogin, logout as authLogout } from '../services/authService';

export const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
    const [user, setUser] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const currentUser = getCurrentUser();
        setUser(currentUser);
        setLoading(false);
    }, []);

    const login = async (email, password) => {
        await authLogin(email, password);
        setUser(getCurrentUser());
    };

    const setUserFromTokens = () => {
        setUser(getCurrentUser());
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
