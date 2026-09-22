import { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import { exchangeTemuCode, getTemuAuthorizeUrl } from '../../services/storeService';

export const TEMU_DEFAULT_BASE_URL = 'https://openapi-b-global.temu.com';
const TEMU_AU_SELLER_CENTER = 'https://au.seller.temu.com/open-platform/client-manage';

export const emptyTemuFields = () => ({
    temu_region: 'au',
    temu_base_url: '',
    temu_app_key: '',
    temu_app_secret: '',
    temu_access_token: '',
    temu_mall_id: '',
    temu_auth_code: '',
    temu_redirect_uri: '',
});

/** Payload for create/update. Blank secrets are dropped so a PATCH keeps the stored value. */
export function buildTemuPayload(form, { includeBlankSecrets = false } = {}) {
    const payload = {
        temu_region: 'au',
        temu_base_url: form.temu_base_url?.trim() || '',
        temu_mall_id: form.temu_mall_id?.trim() || '',
    };
    for (const key of ['temu_app_key', 'temu_app_secret', 'temu_access_token']) {
        const value = form[key]?.trim() || '';
        if (value || includeBlankSecrets) payload[key] = value;
    }
    return payload;
}

export function validateTemuFields(form, { requireSecrets = true } = {}) {
    const errs = [];
    if (requireSecrets) {
        if (!form.temu_app_key?.trim()) errs.push('Temu App Key is required');
        if (!form.temu_app_secret?.trim()) errs.push('Temu App Secret is required');
        if (!form.temu_access_token?.trim()) {
            errs.push('Temu Access Token is required — paste it, or use Connect Temu');
        }
    }
    return errs;
}

/**
 * Temu Partner Open API connection fields (AU / Global router only).
 *
 * Two ways to connect:
 *  - paste the Access Token shown by AU Seller Center after self-authorizing
 *  - Connect Temu: open the authorization URL, then exchange the returned code
 *
 * @param {'create'|'edit'} mode - edit leaves secrets blank to keep the stored value
 */
export default function TemuConnectionFields({ form, setForm, mode = 'edit' }) {
    const isCreate = mode === 'create';
    const [authUrl, setAuthUrl] = useState('');
    const [busy, setBusy] = useState('');
    const [message, setMessage] = useState('');
    const [ok, setOk] = useState(null);

    const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));

    const handleBuildUrl = () => {
        const appKey = form.temu_app_key?.trim();
        const redirect = form.temu_redirect_uri?.trim();
        if (!appKey) {
            setOk(false);
            setMessage('Enter the App Key first.');
            return;
        }
        if (!redirect) {
            setOk(false);
            setMessage('Enter the Redirect URI configured on your Temu app.');
            return;
        }
        setBusy('url');
        setMessage('');
        setOk(null);
        getTemuAuthorizeUrl({ temu_app_key: appKey, redirect_uri: redirect })
            .then((res) => {
                setAuthUrl(res.data?.url || '');
                setOk(true);
                setMessage(res.data?.message || 'Open the link, approve permissions, then paste the code.');
            })
            .catch((err) => {
                setOk(false);
                setMessage(err.response?.data?.message || 'Could not build the authorization URL.');
            })
            .finally(() => setBusy(''));
    };

    const handleExchange = () => {
        const appKey = form.temu_app_key?.trim();
        const appSecret = form.temu_app_secret?.trim();
        const code = form.temu_auth_code?.trim();
        if (!appKey || !appSecret || !code) {
            setOk(false);
            setMessage('App Key, App Secret, and the authorization code are all required.');
            return;
        }
        setBusy('exchange');
        setMessage('');
        setOk(null);
        exchangeTemuCode({
            temu_app_key: appKey,
            temu_app_secret: appSecret,
            code,
            temu_base_url: form.temu_base_url?.trim() || '',
        })
            .then((res) => {
                const token = res.data?.temu_access_token || '';
                const mallId = res.data?.temu_mall_id || '';
                setForm((f) => ({
                    ...f,
                    temu_access_token: token || f.temu_access_token,
                    temu_mall_id: mallId || f.temu_mall_id,
                    temu_auth_code: '',
                }));
                setOk(true);
                setMessage(
                    mallId
                        ? `Access token saved for mall ${mallId}.`
                        : res.data?.message || 'Access token saved.'
                );
            })
            .catch((err) => {
                setOk(false);
                setMessage(err.response?.data?.message || 'Temu rejected this authorization code.');
            })
            .finally(() => setBusy(''));
    };

    return (
        <div className="space-y-3 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
            <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">Temu connection</p>
                <span className="shrink-0 rounded-full bg-slate-100 dark:bg-slate-800 px-2 py-0.5 text-xs text-slate-600 dark:text-slate-300">
                    AU / Global
                </span>
            </div>

            <p className="text-xs text-slate-500 dark:text-slate-400">
                Create a self-developed app in{' '}
                <a
                    className="text-accent-600 dark:text-accent-400 underline"
                    href={TEMU_AU_SELLER_CENTER}
                    target="_blank"
                    rel="noreferrer"
                >
                    AU Seller Center → Open Platform
                </a>
                , then copy its App Key and App Secret. Requests go to{' '}
                <code className="text-[11px]">{TEMU_DEFAULT_BASE_URL}</code>.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Input
                    label="App Key"
                    placeholder={isCreate ? 'From the Temu app details' : 'Leave blank to keep current'}
                    value={form.temu_app_key || ''}
                    onChange={set('temu_app_key')}
                    required={isCreate}
                />
                <Input
                    label="App Secret"
                    type="password"
                    placeholder={isCreate ? 'From the Temu app details' : 'Leave blank to keep current'}
                    value={form.temu_app_secret || ''}
                    onChange={set('temu_app_secret')}
                    required={isCreate}
                />
                <div className="sm:col-span-2">
                    <Input
                        label="Access Token"
                        type="password"
                        placeholder={
                            isCreate
                                ? 'Paste from Seller Center, or use Connect Temu below'
                                : 'Leave blank to keep current'
                        }
                        value={form.temu_access_token || ''}
                        onChange={set('temu_access_token')}
                    />
                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                        The token belongs to one Temu mall. Authorization Management shows it after you
                        approve the app permissions.
                    </p>
                </div>
                <Input
                    label="Mall ID (Optional)"
                    placeholder="Filled automatically after Connect Temu"
                    value={form.temu_mall_id || ''}
                    onChange={set('temu_mall_id')}
                />
                <Input
                    label="Router URL (Optional)"
                    placeholder={TEMU_DEFAULT_BASE_URL}
                    value={form.temu_base_url || ''}
                    onChange={set('temu_base_url')}
                />
            </div>

            <div className="space-y-3 rounded-md border border-slate-100 dark:border-slate-700/80 bg-slate-50/60 dark:bg-slate-800/40 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Connect Temu (authorization callback)
                </p>
                <Input
                    label="Redirect URI"
                    placeholder="Must match redirect_url on your Temu app"
                    value={form.temu_redirect_uri || ''}
                    onChange={set('temu_redirect_uri')}
                />
                <div className="flex flex-wrap items-center gap-2">
                    <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={handleBuildUrl}
                        disabled={busy === 'url'}
                    >
                        {busy === 'url' ? 'Building…' : 'Get authorization link'}
                    </Button>
                    {authUrl && (
                        <a
                            className="inline-flex items-center gap-1 text-sm text-accent-600 dark:text-accent-400 underline"
                            href={authUrl}
                            target="_blank"
                            rel="noreferrer"
                        >
                            Open Temu authorization
                            <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                    )}
                </div>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                    <div className="flex-1">
                        <Input
                            label="Authorization code"
                            placeholder="code=… value from the redirect URL"
                            value={form.temu_auth_code || ''}
                            onChange={set('temu_auth_code')}
                        />
                    </div>
                    <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={handleExchange}
                        disabled={busy === 'exchange' || !form.temu_auth_code?.trim()}
                    >
                        {busy === 'exchange' ? 'Exchanging…' : 'Exchange for token'}
                    </Button>
                </div>
                {message && (
                    <p
                        className={`text-sm ${
                            ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
                        }`}
                    >
                        {message}
                    </p>
                )}
            </div>

            <p className="text-xs text-slate-500 dark:text-slate-400">
                {isCreate
                    ? 'Connection is checked with bg.open.accesstoken.info.get, then a one-product query. Authorize goods, stock, order, and logistics scopes.'
                    : 'App Secret and Access Token are write-only. Leave them blank to keep the stored values.'}
            </p>
        </div>
    );
}
