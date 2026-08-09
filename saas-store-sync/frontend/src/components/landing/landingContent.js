// Centralized content + design tokens for the SellerPilot Hub landing page.
// Keeping copy here makes sections thin and reusable.

export const NAV_LINKS = [
    { label: 'Product', href: '#product' },
    { label: 'Integrations', href: '#integrations' },
    { label: 'How It Works', href: '#how-it-works' },
    { label: 'FAQ', href: '#faq' },
];

export const SALES_EMAIL = 'sales@sellerpilothub.com';
export const SALES_MAILTO = `mailto:${SALES_EMAIL}?subject=SellerPilot%20Hub%20Demo`;

// Honest integration status — do not claim availability that isn't implemented.
export const INTEGRATIONS = [
    { name: 'Amazon', status: 'available' },
    { name: 'eBay', status: 'available' },
    { name: 'Walmart', status: 'available' },
    { name: 'Reverb', status: 'available' },
    { name: 'Sears', status: 'available' },
    { name: 'Kogan', status: 'available' },
    { name: 'Google Sheets', status: 'available' },
    { name: 'Etsy', status: 'beta' },
    { name: 'HEB', status: 'beta' },
    { name: 'Vevor AU', status: 'beta' },
    { name: 'Costco AU', status: 'planned' },
];

export const STATUS_STYLES = {
    available: {
        label: 'Available',
        className: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300',
        dot: 'bg-emerald-400',
    },
    beta: {
        label: 'Beta',
        className: 'border-amber-400/30 bg-amber-400/10 text-amber-300',
        dot: 'bg-amber-400',
    },
    planned: {
        label: 'Planned',
        className: 'border-slate-400/30 bg-slate-400/10 text-slate-300',
        dot: 'bg-slate-400',
    },
};

export const PROBLEMS = [
    {
        icon: 'TrendingUp',
        title: 'Manual Price Checking',
        body: 'Supplier prices change, but your marketplace listings stay outdated.',
    },
    {
        icon: 'PackageX',
        title: 'Inventory Mistakes',
        body: 'Stock changes can create overselling, cancellations, and unhappy customers.',
    },
    {
        icon: 'Network',
        title: 'Scattered Workflows',
        body: 'Stores, spreadsheets, supplier links, and team activity become difficult to manage.',
    },
];

export const FEATURES = [
    {
        icon: 'LayoutDashboard',
        title: 'Multi-Store Dashboard',
        body: 'Manage marketplace stores and operational activity from one dashboard.',
        span: 'lg:col-span-2',
    },
    {
        icon: 'LineChart',
        title: 'Vendor Price Intelligence',
        body: 'Monitor supplier pricing and identify product cost changes.',
    },
    {
        icon: 'PackageCheck',
        title: 'Inventory Automation',
        body: 'Apply stock rules and reduce the risk of overselling.',
    },
    {
        icon: 'Link2',
        title: 'Product Mapping',
        body: 'Connect marketplace catalog rows to their correct supplier product URLs.',
    },
    {
        icon: 'RefreshCw',
        title: 'Automated Sync Jobs',
        body: 'Run scraping, ingestion, pricing, inventory, and marketplace updates in the background.',
        span: 'lg:col-span-2',
    },
    {
        icon: 'Users',
        title: 'Team Permissions',
        body: 'Create user accounts and control access to dashboard, stores, catalog, orders, tickets, and team.',
    },
    {
        icon: 'Activity',
        title: 'Sync Visibility',
        body: 'Review job status, failures, logs, activity, and audit history.',
    },
    {
        icon: 'FileSpreadsheet',
        title: 'Bulk Catalog Workflows',
        body: 'Import and manage large product catalogs through CSV and spreadsheet workflows.',
    },
];

export const WORKFLOW_STEPS = [
    {
        icon: 'Store',
        title: 'Connect your stores',
        body: 'Add marketplace stores with encrypted credentials.',
    },
    {
        icon: 'Link2',
        title: 'Import or map products',
        body: 'Upload catalogs and map rows to supplier URLs.',
    },
    {
        icon: 'SlidersHorizontal',
        title: 'Configure rules',
        body: 'Set pricing and inventory automation rules.',
    },
    {
        icon: 'RefreshCw',
        title: 'Monitor and sync',
        body: 'Let background workers push updates and flag issues.',
    },
];

export const SHOWCASE_TABS = ['Overview', 'Catalog', 'Sync Activity', 'Analytics', 'Team Access'];

export const COMPARISON = {
    without: [
        'Update marketplaces separately',
        'Check suppliers manually',
        'Depend on multiple spreadsheets',
        'Discover stock issues after orders arrive',
        'Limited visibility into team activity',
    ],
    with: [
        'Control stores from one dashboard',
        'Monitor vendor prices and stock',
        'Apply reusable automation rules',
        'Run updates through background workers',
        'Review logs, analytics, and audit history',
    ],
};

export const PERMISSION_MODULES = [
    { key: 'dashboard', label: 'Dashboard', enabled: true },
    { key: 'stores', label: 'Stores', enabled: true },
    { key: 'catalog', label: 'Catalog', enabled: true },
    { key: 'orders', label: 'Orders', enabled: true },
    { key: 'tickets', label: 'Tickets', enabled: false },
    { key: 'team', label: 'Team', enabled: false },
];

export const PRICING_PLANS = [
    {
        seats: 5,
        price: 0,
        priceLabel: 'Free',
        tagline: 'For getting started',
        cta: 'Start Free',
        highlight: false,
        features: ['5 user accounts', 'All core modules', 'Background sync jobs', 'Community support'],
    },
    {
        seats: 10,
        price: 10,
        priceLabel: '$10',
        tagline: 'For growing teams',
        cta: 'Choose Plan',
        highlight: true,
        features: ['10 user accounts', 'Everything in Free', 'Granular team permissions', 'Audit visibility'],
    },
    {
        seats: 15,
        price: 20,
        priceLabel: '$20',
        tagline: 'For busy operations',
        cta: 'Choose Plan',
        highlight: false,
        features: ['15 user accounts', 'Everything in $10', 'Priority sync queues', 'Email support'],
    },
    {
        seats: 20,
        price: 30,
        priceLabel: '$30',
        tagline: 'For larger stores',
        cta: 'Start Setup',
        highlight: false,
        features: ['20 user accounts', 'Everything in $20', 'Bulk catalog workflows', 'Onboarding help'],
    },
];

export const SECURITY_POINTS = [
    {
        icon: 'Lock',
        title: 'Encrypted credentials',
        body: 'Marketplace API tokens are encrypted at rest with a Fernet key.',
    },
    {
        icon: 'Building2',
        title: 'Organization-based access',
        body: 'Members belong to an organization and share only its store data.',
    },
    {
        icon: 'ShieldCheck',
        title: 'Module permissions',
        body: 'Super Users grant access per module — dashboard, stores, catalog, and more.',
    },
    {
        icon: 'ScrollText',
        title: 'Audit visibility',
        body: 'Key actions are recorded so you can review who changed what.',
    },
    {
        icon: 'Server',
        title: 'Background job processing',
        body: 'Heavy work runs on Celery workers, isolated from the request path.',
    },
    {
        icon: 'HeartPulse',
        title: 'Health & readiness',
        body: 'Health and readiness endpoints support orchestration and monitoring.',
    },
];

export const FAQS = [
    {
        q: 'Which marketplaces can I connect?',
        a: 'Core marketplaces like Amazon, eBay, Walmart, Reverb, Sears, and Kogan are available today, plus Google Sheets workflows. Etsy, HEB, and Vevor AU are in beta, and more are planned. Availability is labeled honestly in the Integrations section.',
    },
    {
        q: 'How does product mapping work?',
        a: 'You map each marketplace catalog row to its correct supplier product URL. SellerPilot Hub then monitors that supplier source for price and stock changes.',
    },
    {
        q: 'Can SellerPilot Hub update prices automatically?',
        a: 'Yes. You configure pricing rules per store, and background jobs apply them and push updates to your marketplaces.',
    },
    {
        q: 'Can it prevent overselling?',
        a: 'Inventory automation applies stock rules and syncs availability, which reduces the risk of overselling. It depends on timely supplier data and marketplace sync windows.',
    },
    {
        q: 'Can I invite team members?',
        a: 'A Super User selects a seat plan and creates user accounts, then assigns per-module permissions for dashboard, stores, catalog, orders, tickets, and team.',
    },
    {
        q: 'How are marketplace credentials protected?',
        a: 'Credentials are encrypted at rest using a Fernet key, and access is scoped to your organization with module-level permissions.',
    },
    {
        q: 'Do I need technical knowledge?',
        a: 'No. You connect stores, map products, and configure rules through the dashboard. CSV and spreadsheet workflows help with bulk catalogs.',
    },
    {
        q: 'What happens when a sync fails?',
        a: 'Failed jobs appear in sync visibility with status, logs, and activity so you can investigate and re-run them.',
    },
    {
        q: 'Is there a free plan?',
        a: 'Yes. The first five user accounts are free. Each additional pack of five seats is $10.',
    },
];

export const FOOTER_COLUMNS = [
    {
        title: 'Product',
        links: [
            { label: 'Product', href: '#product' },
            { label: 'Integrations', href: '#integrations' },
            { label: 'How It Works', href: '#how-it-works' },
        ],
    },
    {
        title: 'Resources',
        links: [
            { label: 'Documentation', href: '#' },
            { label: 'Status', href: '#' },
            { label: 'FAQ', href: '#faq' },
            { label: 'Contact', href: SALES_MAILTO },
        ],
    },
    {
        title: 'Legal',
        links: [
            { label: 'Privacy', href: '#' },
            { label: 'Terms', href: '#' },
        ],
    },
];
