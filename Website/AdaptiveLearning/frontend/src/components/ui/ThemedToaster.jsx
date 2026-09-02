import { Toaster } from 'sonner'
import { useTheme } from '../../context/ThemeContext'

/**
 * The one notification container, wired to the theme the app actually uses.
 *
 * It was mounted in `main.jsx` asking sonner for its "system" theme, which
 * resolves from `prefers-color-scheme` — and this app's theme is not that.
 * (Written out rather than quoted as a prop, because `Toaster.test.jsx`
 * greps this file for that literal and a comment would satisfy the grep.
 * `Heatmap.jsx` carries the same warning about the word "chart".)
 * `ThemeContext`
 * holds a manual toggle in `al_theme` and applies it as a `dark` class on the
 * root element, so a student whose OS is light and who has chosen dark got
 * light toasts over a dark page, and the reverse for the other pairing. The
 * two agree only by coincidence of the OS matching the toggle.
 *
 * That could not be fixed where it was: `main.jsx` renders the Toaster
 * *outside* `<App />`, and `ThemeProvider` is inside it, so there was no
 * theme to read. Hence a component — it moves the mount inside the provider
 * without adding a second one, which `Toaster.test.jsx` still checks.
 *
 * An explicit "light"/"dark" rather than passing the class through: sonner
 * resolves "system" itself and has no notion of our root class, so telling it
 * the resolved value is the only way the two cannot disagree.
 */
export default function ThemedToaster() {
  const { dark } = useTheme()
  return (
    <Toaster
      richColors
      position="top-right"
      theme={dark ? 'dark' : 'light'}
      closeButton
    />
  )
}
