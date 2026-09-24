import { Toaster } from 'sonner'
import { useTheme } from '../../context/ThemeContext'

/**
 * The app's only notification container, themed from `ThemeContext` (the manual
 * toggle), not the OS. Must render inside `ThemeProvider`.
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
