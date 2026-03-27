import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// Normalize double-slash paths (e.g. //auth/callback) so React Router can match routes
if (window.location.pathname.startsWith('//')) {
    const fixed = window.location.pathname.replace(/^\/+/, '/') + window.location.search + window.location.hash;
    window.history.replaceState(null, '', fixed);
}

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <App />
    </React.StrictMode>,
)
