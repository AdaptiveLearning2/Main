import { useLocation } from 'react-router-dom'
import useDocumentTitle from '../../hooks/useDocumentTitle'
import { titleForPath } from '../../lib/routeTitles'

/**
 * Sets the tab title for the current route. Renders nothing; an unmapped route
 * falls back to the app name, never the previous page's title.
 */
export default function RouteTitle() {
  const { pathname } = useLocation()
  useDocumentTitle(titleForPath(pathname) || 'AdaptiveLearning')
  return null
}
