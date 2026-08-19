import { createContext, useContext, useState, useEffect } from 'react'
import { readPref, writePref } from '../lib/localPref'

const ThemeContext = createContext()

export function ThemeProvider({ children }) {
  // Guarded, and this is the one that most needed it: `localStorage`
  // *throws* rather than returning null when storage is unavailable --
  // Safari private browsing, site data blocked, a partitioned iframe -- and
  // an unguarded read here happens inside this provider's own `useState`
  // initialiser. It wraps every route, so the throw took down the whole
  // application rather than costing one remembered theme.
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