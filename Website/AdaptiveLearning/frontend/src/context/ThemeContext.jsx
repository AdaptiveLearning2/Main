import { createContext, useContext, useState, useEffect } from 'react'
import { readPref, writePref } from '../lib/localPref'

const ThemeContext = createContext()

export function ThemeProvider({ children }) {
  // `readPref` guards against `localStorage` throwing (Safari private
  // browsing, blocked site data). This runs in the provider's own
  // `useState` initializer, which wraps every route, so an unguarded throw
  // here would crash the whole app instead of just losing the theme.
  const [dark, setDark] = useState(() => readPref('al_theme') === 'dark')

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    writePref('al_theme', dark ? 'dark' : 'light')
  }, [dark])

  return (
    <ThemeContext.Provider value={{ dark, toggleTheme: () => setDark(d => !d) }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  return useContext(ThemeContext)
}