import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Download, Loader2, Play, Plus, ShoppingCart, Trash2, Upload } from 'lucide-react';
import Button from '../../components/ui/Button';
import ConfirmModal from '../../components/ui/ConfirmModal';
import Toggle from '../../components/ui/Toggle';
import PageHeader from '../../components/design/PageHeader';
import Badge from '../../components/design/Badge';
import {
    cancelAutoOrderRun,
    createPurchaseAccount,
    deletePurchaseAccount,
    downloadAutoOrderFile,
    getAutoOrderRun,
    getAutoOrderRuns,
    getAutoOrderSettings,
    getPurchaseAccounts,
    setAutoOrderAutomate,
    startAutoOrderRun,
    uploadAutoOrderFile,
} from '../../services/autoOrderBotService';

const POLL_MS = 4000;
const ROW_COLUMNS = ['order_id', 'sku', 'buy_qty', 'vendor', 'vendor_url', 'stock_seen', 'status', 'vendor_order_number', 'error', 'screenshot'];
const STATUS_VARIANT = { pending: 'warning', cancelled: 'default', running: 'accent', finished: 'success', rejected: 'warning', failed: 'error' };
const ROW_VARIANT = { purchased: 'success', ready_for_payment: 'success', failed: 'error', out_of_stock: 'warning', stock_unknown: 'warning' };
const EMPTY_ACCOUNT = { name: '', vendor: '', chrome_profile: '', email: '', debug_port: '' };

const errText = (e) => e?.response?.data?.detail || e?.message || 'Request failed';

function RowsTable({ title, rows }) {
    return (
        <div>
            <h3 className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-200">{title} ({rows.length})</h3>
            {rows.length === 0 ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">No rows.</p>
            ) : (
                <div className="max-h-96 overflow-auto rounded-lg border border-slate-200 dark:border-slate-700">
                    <table className="table-base text-xs">
                        <thead>
                            <tr>{ROW_COLUMNS.map((c) => <th key={c}>{c}</th>)}</tr>
                        </thead>
                        <tbody>
                            {rows.map((r, i) => (
                                <tr key={i}>
                                    {ROW_COLUMNS.map((c) => (
                                        <td key={c} className="max-w-[16rem] truncate" title={String(r[c] ?? '')}>
                                            {c === 'status' && r[c]
                                                ? <Badge variant={ROW_VARIANT[r[c]] || 'default'}>{r[c]}</Badge>
                                                : String(r[c] ?? '')}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

function FilePicker({ label, hint, saved, disabled, busy, inputRef, onChange }) {
    const id = `file-${label}`;
    return (
        <div className="space-y-1.5">
            <span className="block text-sm font-medium text-slate-800 dark:text-slate-200">{label} <span className="font-normal text-slate-500">(.xlsx)</span></span>
            <input ref={inputRef} id={id} type="file" accept=".xlsx" disabled={disabled || busy} className="sr-only"
                onChange={(e) => onChange(e.target.files[0] || null)} />
            <label htmlFor={id}
                className={`flex items-center gap-3 rounded-lg border border-dashed px-4 py-3 text-sm transition ${disabled
                    ? 'cursor-not-allowed opacity-50 border-slate-300 dark:border-slate-700'
                    : 'cursor-pointer border-slate-300 hover:border-accent-500 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-800/60'}`}>
                {busy ? <Loader2 className="h-4 w-4 shrink-0 animate-spin" /> : <Upload className="h-4 w-4 shrink-0 text-slate-500" />}
                <span className={`min-w-0 flex-1 truncate ${saved ? 'font-medium text-slate-900 dark:text-slate-100' : 'text-slate-500'}`}>
                    {saved ? saved.name : 'Click to choose a file'}
                </span>
                <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium dark:bg-slate-800">{saved ? 'Replace' : 'Browse'}</span>
            </label>
            {saved && <span className="block text-xs text-slate-500">Saved {new Date(saved.uploaded_at).toLocaleString()}</span>}
            {hint && <span className="block text-xs text-slate-500">{hint}</span>}
        </div>
    );
}

export default function AutoOrderBot() {
    const { storeId } = useParams();
    const [info, setInfo] = useState(null);
    const [accounts, setAccounts] = useState([]);
    const [account, setAccount] = useState('');
    const [uploading, setUploading] = useState('');
    const [run, setRun] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [confirmPlace, setConfirmPlace] = useState(false);
    const [newAccount, setNewAccount] = useState(null);
    const ordersInput = useRef(null);
    const mappingInput = useRef(null);

    const saveFile = async (kind, file) => {
        if (!file) return;
        setUploading(kind);
        setError('');
        try {
            const files = await uploadAutoOrderFile(storeId, kind, file);
            setInfo((i) => ({ ...i, files }));
        } catch (e) {
            setError(errText(e));
        } finally {
            setUploading('');
            const ref = kind === 'orders' ? ordersInput : mappingInput;
            if (ref.current) ref.current.value = '';
        }
    };

    const loadRun = useCallback(async (runId) => {
        const detail = await getAutoOrderRun(storeId, runId);
        setRun(detail);
        return detail;
    }, [storeId]);

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const [s, a, runs] = await Promise.all([
                    getAutoOrderSettings(storeId), getPurchaseAccounts(storeId), getAutoOrderRuns(storeId),
                ]);
                if (!alive) return;
                setInfo(s);
                setAccounts(a.accounts);
                if (runs.runs.length) await loadRun(runs.runs[0].id);
            } catch (e) {
                if (alive) setError(errText(e));
            }
        })();
        return () => { alive = false; };
    }, [storeId, loadRun]);

    const runId = run?.id;
    const running = run?.status === 'running' || run?.status === 'pending';
    useEffect(() => {
        if (!running) return undefined;
        const t = setInterval(() => { loadRun(runId).catch(() => {}); }, POLL_MS);
        return () => clearInterval(t);
    }, [running, runId, loadRun]);

    const cancelRun = async () => {
        try { setRun(await cancelAutoOrderRun(storeId, run.id)); } catch (e) { setError(errText(e)); }
    };

    const toggleAutomate = async (value) => {
        setError('');
        try { setInfo(await setAutoOrderAutomate(storeId, value)); } catch (e) { setError(errText(e)); }
    };

    const start = async (place) => {
        setConfirmPlace(false);
        setBusy(true);
        setError('');
        try {
            const created = await startAutoOrderRun(storeId, { account, place });
            setRun(created);
            setInfo((i) => ({ ...i, files: { ...i.files, orders: i.files.orders } }));
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    const saveAccount = async () => {
        setError('');
        try {
            const created = await createPurchaseAccount(storeId, newAccount);
            setAccounts((a) => [...a, created]);
            setAccount(created.name);
            setNewAccount(null);
        } catch (e) { setError(errText(e)); }
    };

    const removeAccount = async (a) => {
        try {
            await deletePurchaseAccount(storeId, a.id);
            setAccounts((list) => list.filter((x) => x.id !== a.id));
            if (account === a.name) setAccount('');
        } catch (e) { setError(errText(e)); }
    };

    if (!info) {
        return <div className="p-8 text-slate-600 dark:text-slate-400">{error || 'Loading Auto Order Bot…'}</div>;
    }

    const enabled = info.automate && info.supported;
    const ready = enabled && account && info.files.orders && info.files.mapping && !uploading && !busy && !running;
    const input = 'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100';
    const card = 'rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900 space-y-3';

    return (
        <div className="space-y-6">
            <Link to="/store-settings" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400">
                <ArrowLeft className="h-4 w-4" /> Store Settings
            </Link>
            <PageHeader title="Auto Order Bot" description="Buys marketplace orders from the vendor with a purchase account you own." />
            {error && <div className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-900/20 dark:text-rose-300">{error}</div>}

            {/* 1. Store */}
            <section className={card}>
                <dl className="grid gap-3 sm:grid-cols-3 text-sm">
                    <div><dt className="text-slate-500">Store</dt><dd className="font-medium text-slate-900 dark:text-slate-100">{info.store_name}</dd></div>
                    <div><dt className="text-slate-500">Marketplace</dt><dd className="font-medium text-slate-900 dark:text-slate-100">{info.marketplace_name || '—'}</dd></div>
                    <div><dt className="text-slate-500">Region</dt><dd className="font-medium text-slate-900 dark:text-slate-100">{info.region}</dd></div>
                </dl>
            </section>

            {/* 2. Automate */}
            <section className={card}>
                <label className="flex items-center gap-3 text-sm font-medium text-slate-900 dark:text-slate-100">
                    <Toggle checked={info.automate} onChange={toggleAutomate} />
                    Automate the order process
                </label>
                {!info.supported && (
                    <p className="text-sm text-amber-700 dark:text-amber-400">{info.message} Runs are disabled for this store.</p>
                )}
            </section>

            {info.supported && (
                <>
                    {/* 3. Accounts */}
                    <section className={card}>
                        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Purchase account ({info.region} vendors: {info.vendors.join(', ')})</h2>
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            One account is one vendor. The Chrome profile must already be logged in with the card saved on the vendor site. No passwords or card numbers are stored here.
                        </p>
                        {accounts.length === 0 && <p className="text-sm text-slate-500">No purchase accounts for this region yet.</p>}
                        <ul className="space-y-1">
                            {accounts.map((a) => (
                                <li key={a.id} className="flex items-center gap-3 text-sm">
                                    <label className="flex flex-1 items-center gap-2">
                                        <input type="radio" name="account" disabled={!enabled} checked={account === a.name} onChange={() => setAccount(a.name)} />
                                        <span className="font-medium">{a.name}</span>
                                        <span className="text-slate-500">{a.vendor} · {a.chrome_profile || a.user_data_dir}</span>
                                    </label>
                                    <button type="button" title="Delete account" onClick={() => removeAccount(a)} className="text-rose-600"><Trash2 className="h-4 w-4" /></button>
                                </li>
                            ))}
                        </ul>
                        {newAccount ? (
                            <div className="grid gap-2 sm:grid-cols-5">
                                <input className={input} placeholder="Name" value={newAccount.name} onChange={(e) => setNewAccount({ ...newAccount, name: e.target.value })} />
                                <select className={input} value={newAccount.vendor} onChange={(e) => setNewAccount({ ...newAccount, vendor: e.target.value })}>
                                    <option value="">Vendor…</option>
                                    {info.vendors.map((v) => <option key={v} value={v}>{v}</option>)}
                                </select>
                                <input className={input} placeholder="Chrome profile (e.g. Profile 1)" value={newAccount.chrome_profile} onChange={(e) => setNewAccount({ ...newAccount, chrome_profile: e.target.value })} />
                                <input className={input} placeholder="Account email" value={newAccount.email} onChange={(e) => setNewAccount({ ...newAccount, email: e.target.value })} />
                                <div className="flex gap-2">
                                    <Button onClick={saveAccount}>Save</Button>
                                    <Button variant="secondary" onClick={() => setNewAccount(null)}>Cancel</Button>
                                </div>
                            </div>
                        ) : (
                            <Button variant="secondary" onClick={() => setNewAccount({ ...EMPTY_ACCOUNT })}><Plus className="mr-1 h-4 w-4" />Add purchase account</Button>
                        )}
                    </section>

                    {/* 4. Files */}
                    <section className={card}>
                        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Files</h2>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <FilePicker label="Marketplace order export" saved={info.files.orders}
                                hint="Upload a new one every day. It is deleted automatically when the day ends."
                                disabled={!enabled} busy={uploading === 'orders'} inputRef={ordersInput}
                                onChange={(f) => saveFile('orders', f)} />
                            <FilePicker label="Mapping workbook" saved={info.files.mapping}
                                hint="Kept until you replace it (e.g. after uploading new listings). Headers: SKU, Vendor, Vendor URL, Vendor SKU"
                                disabled={!enabled} busy={uploading === 'mapping'} inputRef={mappingInput}
                                onChange={(f) => saveFile('mapping', f)} />
                        </div>
                    </section>

                    {/* 5. Actions */}
                    <section className={card}>
                        <div className="flex flex-wrap items-center gap-3">
                            <Button variant="secondary" className="min-w-36 justify-center" disabled={!ready} onClick={() => start(false)}>
                                {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />}Dry run
                            </Button>
                            <Button className="min-w-36 justify-center" disabled={!ready} onClick={() => setConfirmPlace(true)}>
                                <ShoppingCart className="mr-1 h-4 w-4" />Place orders
                            </Button>
                        </div>
                        <p className={`text-sm font-medium ${info.laptop_online ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}>
                            {info.laptop_online ? 'Order bot laptop is online.' : 'Order bot laptop is offline. A run waits until run_agent.bat is running on it.'}
                        </p>
                        <ul className="list-disc space-y-1 pl-5 text-xs text-slate-500 dark:text-slate-400">
                            <li><strong>Dry run</strong> fills the cart and ship-to, then stops before the final click.</li>
                            <li><strong>Place orders</strong> really buys, using the card saved on the vendor account.</li>
                            <li>Before each line the bot empties the cart of the purchase account.</li>
                        </ul>
                    </section>
                </>
            )}

            {/* 6. Latest run */}
            {run && (
                <section className={card}>
                    <div className="flex flex-wrap items-center gap-3">
                        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Latest run</h2>
                        <Badge variant={STATUS_VARIANT[run.status] || 'default'}>{run.status === 'pending' ? 'waiting for the laptop' : run.status}</Badge>
                        <span className="text-xs text-slate-500">
                            {run.place ? 'Place orders' : 'Dry run'} · {run.marketplace} · {run.account_name} · {new Date(run.started_at).toLocaleString()}
                        </span>
                        {running && <Loader2 className="h-4 w-4 animate-spin" />}
                        {running && <Button variant="secondary" onClick={cancelRun}>Cancel run</Button>}
                    </div>
                    <p className="text-sm">Bot exit code: <strong>{run.exit_code ?? (running ? 'running…' : '—')}</strong></p>
                    {run.status === 'rejected' && <p className="text-sm text-amber-700 dark:text-amber-400">The bot rejected the files before opening a browser. See the message below.</p>}
                    {run.stdout && <pre className="max-h-40 overflow-auto rounded-lg bg-slate-100 p-3 text-xs dark:bg-slate-800">{run.stdout}</pre>}
                    <div>
                        <h3 className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-200">Log (tail)</h3>
                        <pre className="max-h-64 overflow-auto rounded-lg bg-slate-100 p-3 text-xs dark:bg-slate-800">{run.log_tail || 'No log yet.'}</pre>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        {['done', 'remaining', 'log'].map((kind) => (
                            <Button key={kind} variant="secondary" onClick={() => downloadAutoOrderFile(storeId, run.id, kind).catch((e) => setError(errText(e)))}>
                                <Download className="mr-1 h-4 w-4" />{kind === 'log' ? 'Log' : `${kind[0].toUpperCase()}${kind.slice(1)} workbook`}
                            </Button>
                        ))}
                    </div>
                    <RowsTable title="Done" rows={run.done_rows || []} />
                    <RowsTable title="Remaining" rows={run.remaining_rows || []} />
                </section>
            )}

            <ConfirmModal
                open={confirmPlace}
                title="Place real orders?"
                message={`This will buy every buyable line in the file on the "${account}" account using its saved card. Only continue after a dry run looked right.`}
                confirmLabel="Place orders"
                variant="danger"
                onConfirm={() => start(true)}
                onCancel={() => setConfirmPlace(false)}
            />
        </div>
    );
}
