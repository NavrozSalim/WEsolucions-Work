import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useContext } from 'react';
import { AuthContext } from '../../context/AuthContext';
import { getDefaultAppPath, hasPermission } from '../../services/authService';
import { SellerPilotHubLoading } from '../brand';

/**
 * Requires auth + a product permission key (e.g. "catalog", "team").
 */
export default function PermissionRoute({ permission }) {
    const { user, loading } = useContext(AuthContext);
    const location = useLocation();

    if (loading) return <SellerPilotHubLoading />;
    if (!user) return <Navigate to="/login/choose" replace />;
    if (permission && !hasPermission(user, permission)) {
        const fallback = getDefaultAppPath(user);
        if (fallback === location.pathname) {
            return <Navigate to="/" replace />;
        }
        return <Navigate to={fallback} replace />;
    }

    return <Outlet />;
}
