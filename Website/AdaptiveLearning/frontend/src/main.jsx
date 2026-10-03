import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { prefetchRoute } from './routes'

// A direct load fetches its page chunk now, not after the session and role reads.
prefetchRoute(window.location.pathname)

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* Notifications mount in `App.jsx` (`ThemedToaster`), inside `ThemeProvider`. */}
    <App />
  </React.StrictMode>,
)