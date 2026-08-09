import { Navigate, Outlet } from 'react-router-dom';
import { useContext } from 'react';
import { AuthContext } from '../../context/AuthContext';
import { SellerPilotHubLoading } from '../brand';

/** Requires authenticated platform staff (master admin). */
const PlatformRoute = () => {
    const { user, loading } = useContext(AuthContext);

    if (loading) return <SellerPilotHubLoading />;
    if (!user) return <Navigate to="/login/master" replace />;
    if (!user.is_platform_admin) return <Navigate to="/login/choose" replace />;

    return <Outlet />;
};

export default PlatformRoute;
