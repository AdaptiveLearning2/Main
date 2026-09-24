import { createContext, useContext, useState, useEffect } from 'react'
import { readPref, writePref } from '../lib/localPref'

const ThemeContext = createContext()

export function ThemeProvider({ children }) {
  // `readPref` guards a throwing `localStorage`; unguarded, this would crash every route.
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