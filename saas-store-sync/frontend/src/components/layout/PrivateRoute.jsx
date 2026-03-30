import { Navigate, Outlet } from 'react-router-dom';
import { useContext } from 'react';
import { AuthContext } from '../../context/AuthContext';
import { WesolutionsLoading } from '../brand';
import {
    DEV_AUTH_BYPASS,
    DEV_AUTH_TOKEN_KEY,
    DEV_AUTH_TOKEN_VALUE,
} from '../../config/devAuthBypass';

const PrivateRoute = () => {
    const { user, loading } = useContext(AuthContext);

    if (loading) return <WesolutionsLoading />;

    const devSession =
        DEV_AUTH_BYPASS &&
        (localStorage.getItem(DEV_AUTH_TOKEN_KEY) === DEV_AUTH_TOKEN_VALUE ||
            localStorage.getItem('access_token') === DEV_AUTH_TOKEN_VALUE);

    if (!user && !devSession) return <Navigate to="/login" replace />;

    return <Outlet />;
};

export default PrivateRoute;
