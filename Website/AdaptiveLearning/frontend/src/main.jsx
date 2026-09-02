import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { Toaster } from 'sonner'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* The only Toaster in the app. There were two -- one here and one in
        App.jsx -- so every notification rendered twice, once top-right
        and once bottom-right. `topics.test.js`-style source check in
        `Toaster.test.jsx` keeps it that way. */}
    <Toaster richColors position="top-right" theme="system" closeButton />
    <App />
  </React.StrictMode>,
)