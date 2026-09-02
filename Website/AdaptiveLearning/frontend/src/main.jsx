import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* The Toaster moved into `App.jsx`, as `ThemedToaster`. It has to sit
        inside `ThemeProvider` to read the app's own theme -- mounted here it
        could only ask `prefers-color-scheme`, which is a different question
        from the `al_theme` toggle the app applies. Still exactly one mount;
        `Toaster.test.jsx` checks that and now also checks it is not
        `theme="system"`. */}
    <App />
  </React.StrictMode>,
)