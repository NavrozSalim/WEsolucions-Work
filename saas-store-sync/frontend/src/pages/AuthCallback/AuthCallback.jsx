import { useEffect } from 'react';
import { WesolutionsLoading } from '../../components/brand';

/**
 * Handles OAuth callback (e.g. Google). Backend redirects here with tokens in query.
 * Stores tokens, then does full-page redirect so AuthProvider reads token on fresh load.
 */
export default function AuthCallback() {
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const access = params.get('access');
        const refresh = params.get('refresh');
        const nextPath = params.get('next') || '/';

        if (access) {
            localStorage.setItem('access_token', access);
            if (refresh) localStorage.setItem('refresh_token', refresh);
            // Sanitize nextPath - only allow relative paths to prevent open redirect
            const safePath = nextPath.startsWith('/') && !nextPath.startsWith('//') ? nextPath : '/';
            // Small delay so tokens are persisted before redirect (fixes Safari/Chrome timing)
            setTimeout(() => {
                window.location.replace(safePath);
            }, 50);
        } else {
            // No tokens - backend likely redirected to /login?error=xxx instead of here
            window.location.replace('/login');
        }
    }, []);

    return <WesolutionsLoading />;
}
