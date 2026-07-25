import { useContext, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
    AlertTriangle,
    ArrowRight,
    CheckCircle2,
    Clock3,
    Loader2,
    Package,
    PackageX,
    ShoppingCart,
    Store,
    MessageSquare,
} from 'lucide-react';
import {
    LineChart,
    Line,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    BarChart,
    Bar,
    Cell,
    Legend,
} from 'recharts';
import { getDashboardSummary, getAnalyticsCharts, getStores } from '../../services/storeService';
import { Badge, EmptyState, KPICard, PageHeader } from '../../components/design';
import { useSidebarActivity } from '../../context/SidebarActivityContext';
import { ThemeContext } from '../../context/ThemeContext';

function catalogLink({ storeId, status } = {}) {
    const params = new URLSearchParams();
    if (storeId) params.set('store', storeId);
    if (status) params.set('status', status);
    const qs = params.toString();
    return qs ? `/catalog?${qs}` : '/catalog';
}

function storeHealth(store) {
    const failed = store.failed_count || 0;
    const attention = store.needs_attention_count || 0;
    if (failed > 0 || attention > 0) {
        return { label: 'Needs attention', variant: 'error' };
    }
    if ((store.pending_count || 0) > 0) {
        return { label: 'Pending', variant: 'warning' };
    }
    if ((store.scraped_count || 0) > 0) {
        return { label: 'Scraped', variant: 'warning' };
    }
    if ((store.product_count || 0) === 0) {
        return { label: 'Empty', variant: 'default' };
    }
    if (store.connection_status === 'error') {
        return { label: 'Connection error', variant: 'error' };
    }
    if (!store.is_active) {
        return { label: 'Inactive', variant: 'default' };
    }
    return { label: 'Healthy', variant: 'success' };
}

function formatCount(n) {
    if (n == null || Number.isNaN(n)) return '–';
    return Number(n).toLocaleString();
}

function formatRelativeTime(iso) {
    if (!iso) return '—';
    const t = new Date(iso);
    if (Number.isNaN(t.getTime())) return '—';
    const diffMs = Date.now() - t.getTime();
    if (diffMs < 0) return 'just now';
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 48) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 14) return `${days}d ago`;
    return t.toLocaleDateString();
}

function shortDate(d) {
    if (!d) return '';
    const parts = String(d).slice(5).split('-'); // MM-DD
    if (parts.length === 2) return `${parts[0]}/${parts[1]}`;
    return String(d);
}

function ChartCard({ title, description, children, empty }) {
    return (
        <div className="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
            <h3 className="text-base font-medium text-slate-900 dark:text-slate-100">{title}</h3>
            {description ? (
                <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{description}</p>
            ) : null}
            <div className="mt-4 h-56">
                {empty ? (
                    <div className="flex h-full items-center justify-center text-sm text-slate-500 dark:text-slate-400">
                        No chart data yet.
                    </div>
                ) : (
                    children
                )}
            </div>
        </div>
    );
}

export default function Dashboard() {
    const theme = useContext(ThemeContext);
    const dark = theme?.dark ?? true;
    const [summary, setSummary] = useState({
        total_products: 0,
        catalog_count: 0,
        out_of_stock_count: 0,
        needs_attention_count: 0,
        pending_count: 0,
        failed_count: 0,
        scraped_count: 0,
        synced_count: 0,
        store_breakdown: [],
    });
    const [chartData, setChartData] = useState({
        out_of_stock: [],
        sync_health: [],
        sync_mix: null,
    });
    const [loading, setLoading] = useState(true);
    const [chartsLoading, setChartsLoading] = useState(true);
    const [stores, setStores] = useState([]);
    const [selectedStore, setSelectedStore] = useState('');
    const [range, setRange] = useState('30');
    const { activities } = useSidebarActivity();

    const tooltipStyle = useMemo(
        () => ({
            backgroundColor: dark ? '#0f172a' : '#ffffff',
            border: dark ? '1px solid #334155' : '1px solid #e2e8f0',
            borderRadius: '8px',
            fontSize: '12px',
            color: dark ? '#e2e8f0' : '#0f172a',
        }),
        [dark],
    );

    useEffect(() => {
        getStores()
            .then((res) => setStores(Array.isArray(res.data) ? res.data : res.data?.results || []))
            .catch(() => {});
    }, []);

    useEffect(() => {
        setLoading(true);
        const params = selectedStore ? { store_id: selectedStore } : undefined;
        getDashboardSummary(params)
            .then((res) => setSummary(res.data || {}))
            .catch(() => {})
            .finally(() => setLoading(false));
    }, [selectedStore]);

    useEffect(() => {
        setChartsLoading(true);
        const params = { range };
        if (selectedStore) params.store_id = selectedStore;
        getAnalyticsCharts(params)
            .then((res) => {
                const d = res.data || {};
                setChartData({
                    out_of_stock: d.out_of_stock || [],
                    sync_health: d.sync_health || [],
                    sync_mix: d.sync_mix || null,
                });
            })
            .catch(() => setChartData({ out_of_stock: [], sync_health: [], sync_mix: null }))
            .finally(() => setChartsLoading(false));
    }, [range, selectedStore]);

    const catalogCount = summary.catalog_count ?? summary.total_products ?? 0;
    const outOfStock = summary.out_of_stock_count ?? 0;
    const needsAttention = summary.needs_attention_count ?? 0;
    const pending = summary.pending_count ?? 0;
    const failed = summary.failed_count ?? 0;
    const synced = summary.synced_count ?? 0;
    const scraped = summary.scraped_count ?? 0;
    const storeBreakdown = summary.store_breakdown || [];

    const attentionItems = useMemo(() => {
        return [...storeBreakdown]
            .map((s) => {
                const issues = [];
                if ((s.failed_count || 0) > 0) {
                    issues.push({
                        key: 'failed',
                        label: `${formatCount(s.failed_count)} failed`,
                        status: 'failed',
                        count: s.failed_count,
                    });
                }
                if ((s.needs_attention_count || 0) > 0) {
                    issues.push({
                        key: 'needs_attention',
                        label: `${formatCount(s.needs_attention_count)} needs attention`,
                        status: 'needs_attention',
                        count: s.needs_attention_count,
                    });
                }
                if ((s.pending_count || 0) > 0) {
                    issues.push({
                        key: 'pending',
                        label: `${formatCount(s.pending_count)} pending`,
                        status: 'pending',
                        count: s.pending_count,
                    });
                }
                const weight =
                    (s.failed_count || 0) * 1000 +
                    (s.needs_attention_count || 0) * 100 +
                    (s.pending_count || 0);
                return { ...s, issues, weight };
            })
            .filter((s) => s.issues.length > 0)
            .sort((a, b) => b.weight - a.weight);
    }, [storeBreakdown]);

    const liveJobs = useMemo(
        () => Object.values(activities || {}).sort((a, b) => a.id.localeCompare(b.id)),
        [activities],
    );

    const syncMixBars = useMemo(() => {
        const mix = chartData.sync_mix;
        if (!mix) return [];
        return [
            { key: 'synced', label: 'Synced', value: mix.synced || 0, fill: '#10b981' },
            { key: 'pending', label: 'Pending', value: mix.pending || 0, fill: '#f59e0b' },
            { key: 'scraped', label: 'Scraped', value: mix.scraped || 0, fill: '#38bdf8' },
            { key: 'needs_attention', label: 'Attention', value: mix.needs_attention || 0, fill: '#f43f5e' },
            { key: 'failed', label: 'Failed', value: mix.failed || 0, fill: '#e11d48' },
        ].filter((x) => x.value > 0 || (mix.total || 0) === 0);
    }, [chartData.sync_mix]);

    const storeParam = selectedStore || undefined;
    const dash = (v) => (loading ? '–' : formatCount(v));
    const axisColor = dark ? '#94a3b8' : '#64748b';
    const gridColor = dark ? '#334155' : '#e2e8f0';

    const kpis = [
        {
            label: 'Needs attention',
            value: dash(needsAttention + failed),
            sub: failed > 0 ? `${formatCount(failed)} failed · open in catalog` : 'Failed or blocked listings',
            icon: AlertTriangle,
            tone: needsAttention + failed > 0 ? 'error' : 'default',
            to: catalogLink({
                storeId: storeParam,
                status: failed > needsAttention ? 'failed' : 'needs_attention',
            }),
        },
        {
            label: 'Pending',
            value: dash(pending),
            sub: scraped > 0 ? `${formatCount(scraped)} scraped waiting` : 'Waiting to scrape or sync',
            icon: Clock3,
            tone: pending > 0 ? 'warning' : 'default',
            to: catalogLink({ storeId: storeParam, status: 'pending' }),
        },
        {
            label: 'Out of stock',
            value: dash(outOfStock),
            sub: 'Zero or missing stock',
            icon: PackageX,
            tone: outOfStock > 0 ? 'warning' : 'default',
            to: catalogLink({ storeId: storeParam }),
        },
        {
            label: 'Active listings',
            value: dash(catalogCount),
            sub: synced > 0 ? `${formatCount(synced)} synced` : 'Catalog products',
            icon: Package,
            tone: 'accent',
            to: catalogLink({ storeId: storeParam }),
        },
    ];

    return (
        <div className="space-y-6">
            <PageHeader
                title="Dashboard"
                description="Counts, trends, and store detail in one place."
                actions={
                    <div className="flex flex-wrap items-center gap-2">
                        <select
                            value={selectedStore}
                            onChange={(e) => setSelectedStore(e.target.value)}
                            className="min-w-[160px] rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300"
                        >
                            <option value="">All stores</option>
                            {stores.map((s) => (
                                <option key={s.id} value={s.id}>
                                    {s.name}
                                </option>
                            ))}
                        </select>
                        <div className="flex gap-1">
                            {['7', '30'].map((r) => (
                                <button
                                    key={r}
                                    type="button"
                                    onClick={() => setRange(r)}
                                    className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                                        range === r
                                            ? 'bg-accent-600 text-white dark:bg-accent-500'
                                            : 'text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800'
                                    }`}
                                >
                                    {r} days
                                </button>
                            ))}
                        </div>
                    </div>
                }
            />

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {kpis.map((item) => (
                    <KPICard key={item.label} {...item} />
                ))}
            </div>

            <div className="grid gap-6 lg:grid-cols-3">
                <ChartCard
                    title="Sync health trend"
                    description={`Pending, attention, and failed over the last ${range} days.`}
                    empty={!chartsLoading && !(chartData.sync_health?.length > 0)}
                >
                    {chartsLoading ? (
                        <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Loading…
                        </div>
                    ) : (
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartData.sync_health}>
                                <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                                <XAxis
                                    dataKey="date"
                                    tick={{ fontSize: 11, fill: axisColor }}
                                    stroke={axisColor}
                                    tickFormatter={shortDate}
                                />
                                <YAxis tick={{ fontSize: 11, fill: axisColor }} stroke={axisColor} />
                                <Tooltip contentStyle={tooltipStyle} labelFormatter={shortDate} />
                                <Legend wrapperStyle={{ fontSize: 12 }} />
                                <Line
                                    type="monotone"
                                    dataKey="pending"
                                    name="Pending"
                                    stroke="#f59e0b"
                                    strokeWidth={2}
                                    dot={false}
                                />
                                <Line
                                    type="monotone"
                                    dataKey="needs_attention"
                                    name="Attention"
                                    stroke="#f43f5e"
                                    strokeWidth={2}
                                    dot={false}
                                />
                                <Line
                                    type="monotone"
                                    dataKey="failed"
                                    name="Failed"
                                    stroke="#e11d48"
                                    strokeWidth={2}
                                    dot={false}
                                />
                            </LineChart>
                        </ResponsiveContainer>
                    )}
                </ChartCard>

                <ChartCard
                    title="Out-of-stock trend"
                    description={`Inventory risk over the last ${range} days.`}
                    empty={!chartsLoading && !(chartData.out_of_stock?.length > 0)}
                >
                    {chartsLoading ? (
                        <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Loading…
                        </div>
                    ) : (
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartData.out_of_stock}>
                                <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                                <XAxis
                                    dataKey="date"
                                    tick={{ fontSize: 11, fill: axisColor }}
                                    stroke={axisColor}
                                    tickFormatter={shortDate}
                                />
                                <YAxis tick={{ fontSize: 11, fill: axisColor }} stroke={axisColor} />
                                <Tooltip contentStyle={tooltipStyle} labelFormatter={shortDate} />
                                <Line
                                    type="monotone"
                                    dataKey="count"
                                    name="Out of stock"
                                    stroke="#2563eb"
                                    strokeWidth={2}
                                    dot={{ r: 2, fill: '#2563eb' }}
                                />
                            </LineChart>
                        </ResponsiveContainer>
                    )}
                </ChartCard>

                <ChartCard
                    title="Listing mix"
                    description="Current status breakdown across this view."
                    empty={!chartsLoading && syncMixBars.length === 0}
                >
                    {chartsLoading ? (
                        <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Loading…
                        </div>
                    ) : (
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={syncMixBars} layout="vertical" margin={{ left: 8, right: 12 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke={gridColor} horizontal={false} />
                                <XAxis type="number" tick={{ fontSize: 11, fill: axisColor }} stroke={axisColor} />
                                <YAxis
                                    type="category"
                                    dataKey="label"
                                    width={72}
                                    tick={{ fontSize: 11, fill: axisColor }}
                                    stroke={axisColor}
                                />
                                <Tooltip contentStyle={tooltipStyle} />
                                <Bar dataKey="value" name="Listings" radius={[0, 4, 4, 0]}>
                                    {syncMixBars.map((entry) => (
                                        <Cell key={entry.key} fill={entry.fill} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    )}
                </ChartCard>
            </div>

            <div className="grid gap-6 lg:grid-cols-3">
                <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900 lg:col-span-2">
                    <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                        <div>
                            <h2 className="text-base font-medium text-slate-900 dark:text-slate-100">
                                Needs attention now
                            </h2>
                            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                                Stores with failed, blocked, or pending listings.
                            </p>
                        </div>
                        <Link
                            to={catalogLink({ storeId: storeParam })}
                            className="inline-flex items-center gap-1 text-sm font-medium text-accent-600 hover:text-accent-500 dark:text-accent-400"
                        >
                            Open catalog
                            <ArrowRight className="h-3.5 w-3.5" />
                        </Link>
                    </div>

                    {loading ? (
                        <div className="flex items-center justify-center gap-2 px-5 py-16 text-sm text-slate-500 dark:text-slate-400">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Loading…
                        </div>
                    ) : attentionItems.length === 0 ? (
                        <EmptyState
                            icon={CheckCircle2}
                            title="All stores look healthy"
                            description="No failed, needs-attention, or pending listings in this view."
                        />
                    ) : (
                        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                            {attentionItems.map((s) => {
                                const primary = s.issues[0];
                                return (
                                    <li key={s.store_id}>
                                        <Link
                                            to={catalogLink({
                                                storeId: s.store_id,
                                                status: primary?.status,
                                            })}
                                            className="flex items-center gap-4 px-5 py-3.5 transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/40"
                                        >
                                            <div className="min-w-0 flex-1">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                                                        {s.store_name}
                                                    </p>
                                                    {s.marketplace_name && (
                                                        <span className="text-xs text-slate-400 dark:text-slate-500">
                                                            {s.marketplace_name}
                                                        </span>
                                                    )}
                                                </div>
                                                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                                    {s.issues.map((i) => i.label).join(' · ')}
                                                    {' · '}
                                                    {formatCount(s.product_count)} total
                                                    {' · '}
                                                    {formatCount(s.out_of_stock_count)} OOS
                                                </p>
                                            </div>
                                            <Badge
                                                variant={
                                                    primary?.status === 'pending' ? 'warning' : 'error'
                                                }
                                            >
                                                {primary?.status === 'pending'
                                                    ? 'Pending'
                                                    : 'Needs attention'}
                                            </Badge>
                                            <ArrowRight className="h-4 w-4 shrink-0 text-slate-400" />
                                        </Link>
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </div>

                <div className="space-y-6">
                    <div className="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
                        <h3 className="text-base font-medium text-slate-900 dark:text-slate-100">
                            Live jobs
                        </h3>
                        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                            Scrapes and syncs running in this session.
                        </p>
                        <div className="mt-4 space-y-3">
                            {liveJobs.length === 0 ? (
                                <p className="text-sm text-slate-500 dark:text-slate-400">
                                    No background jobs running.
                                </p>
                            ) : (
                                liveJobs.map((job) => (
                                    <div
                                        key={job.id}
                                        className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-800/50"
                                    >
                                        <div className="flex items-start gap-2">
                                            <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-accent-500" />
                                            <div className="min-w-0 flex-1">
                                                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                                                    {job.title}
                                                </p>
                                                {job.description ? (
                                                    <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                                                        {job.description}
                                                    </p>
                                                ) : null}
                                                {job.progress != null && job.progress > 0 ? (
                                                    <div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                                                        <div
                                                            className="h-full rounded-full bg-accent-500 transition-all"
                                                            style={{
                                                                width: `${Math.min(100, job.progress)}%`,
                                                            }}
                                                        />
                                                    </div>
                                                ) : null}
                                            </div>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>

                    <div className="rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
                        <h3 className="text-base font-medium text-slate-900 dark:text-slate-100">
                            Quick actions
                        </h3>
                        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                            Jump into daily ops.
                        </p>
                        <div className="mt-4 space-y-1">
                            {[
                                {
                                    to: catalogLink({ storeId: storeParam }),
                                    icon: Package,
                                    label: 'Open catalog',
                                },
                                { to: '/orders', icon: ShoppingCart, label: 'View orders' },
                                { to: '/tickets', icon: MessageSquare, label: 'Open tickets' },
                                { to: '/store-settings', icon: Store, label: 'Manage stores' },
                            ].map((a) => (
                                <Link
                                    key={a.to + a.label}
                                    to={a.to}
                                    className="flex items-center gap-3 rounded-md px-2 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/60"
                                >
                                    <a.icon className="h-4 w-4 text-slate-400" />
                                    <span className="flex-1 font-medium">{a.label}</span>
                                    <ArrowRight className="h-3.5 w-3.5 text-slate-400" />
                                </Link>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
                <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                    <h2 className="text-base font-medium text-slate-900 dark:text-slate-100">
                        Store scoreboard
                    </h2>
                    <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                        Every count you need per store — open a row for full catalog detail.
                    </p>
                </div>
                {loading ? (
                    <div className="flex items-center justify-center gap-2 px-5 py-12 text-sm text-slate-500">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading…
                    </div>
                ) : storeBreakdown.length === 0 ? (
                    <EmptyState
                        icon={Store}
                        title="No stores yet"
                        description="Connect a marketplace store to start syncing listings."
                        action={
                            <Link
                                to="/store-settings"
                                className="inline-flex items-center rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-500"
                            >
                                Add store
                            </Link>
                        }
                    />
                ) : (
                    <div className="overflow-x-auto">
                        <table className="table-base">
                            <thead>
                                <tr>
                                    <th>Store</th>
                                    <th>Status</th>
                                    <th className="text-right">Listings</th>
                                    <th className="text-right">Synced</th>
                                    <th className="text-right">Pending</th>
                                    <th className="text-right">Scraped</th>
                                    <th className="text-right">Attention</th>
                                    <th className="text-right">Failed</th>
                                    <th className="text-right">OOS</th>
                                    <th>Last sync</th>
                                    <th />
                                </tr>
                            </thead>
                            <tbody>
                                {storeBreakdown.map((s) => {
                                    const health = storeHealth(s);
                                    return (
                                        <tr key={s.store_id}>
                                            <td>
                                                <div className="font-medium text-slate-900 dark:text-slate-100">
                                                    {s.store_name}
                                                </div>
                                                {s.marketplace_name ? (
                                                    <div className="text-xs text-slate-500 dark:text-slate-400">
                                                        {s.marketplace_name}
                                                    </div>
                                                ) : null}
                                            </td>
                                            <td>
                                                <Badge variant={health.variant}>{health.label}</Badge>
                                            </td>
                                            <td className="text-right tabular-nums">
                                                {formatCount(s.product_count)}
                                            </td>
                                            <td className="text-right tabular-nums text-emerald-600 dark:text-emerald-400">
                                                {formatCount(s.synced_count)}
                                            </td>
                                            <td className="text-right tabular-nums text-amber-600 dark:text-amber-400">
                                                {formatCount(s.pending_count)}
                                            </td>
                                            <td className="text-right tabular-nums text-sky-600 dark:text-sky-400">
                                                {formatCount(s.scraped_count)}
                                            </td>
                                            <td className="text-right tabular-nums text-rose-600 dark:text-rose-400">
                                                {formatCount(s.needs_attention_count)}
                                            </td>
                                            <td className="text-right tabular-nums text-rose-600 dark:text-rose-400">
                                                {formatCount(s.failed_count)}
                                            </td>
                                            <td className="text-right tabular-nums text-slate-700 dark:text-slate-300">
                                                {formatCount(s.out_of_stock_count)}
                                            </td>
                                            <td className="whitespace-nowrap text-sm text-slate-500 dark:text-slate-400">
                                                {formatRelativeTime(s.last_sync_at)}
                                            </td>
                                            <td className="text-right">
                                                <div className="flex items-center justify-end gap-3">
                                                    {(s.needs_attention_count || 0) + (s.failed_count || 0) > 0 ? (
                                                        <Link
                                                            to={catalogLink({
                                                                storeId: s.store_id,
                                                                status:
                                                                    (s.failed_count || 0) >
                                                                    (s.needs_attention_count || 0)
                                                                        ? 'failed'
                                                                        : 'needs_attention',
                                                            })}
                                                            className="text-sm font-medium text-rose-600 hover:text-rose-500 dark:text-rose-400"
                                                        >
                                                            Issues
                                                        </Link>
                                                    ) : null}
                                                    <Link
                                                        to={catalogLink({ storeId: s.store_id })}
                                                        className="inline-flex items-center gap-1 text-sm font-medium text-accent-600 hover:text-accent-500 dark:text-accent-400"
                                                    >
                                                        Open
                                                        <ArrowRight className="h-3.5 w-3.5" />
                                                    </Link>
                                                </div>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
