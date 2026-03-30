/**
 * Fake Google login without backend (local UI testing only).
 * On in `npm run dev`, or set VITE_DEV_AUTH_BYPASS=true for preview/prod builds.
 */
export const DEV_AUTH_BYPASS =
    import.meta.env.DEV === true || import.meta.env.VITE_DEV_AUTH_BYPASS === 'true';

export const DEV_AUTH_TOKEN_KEY = 'token';
export const DEV_AUTH_TOKEN_VALUE = 'dev-token';
