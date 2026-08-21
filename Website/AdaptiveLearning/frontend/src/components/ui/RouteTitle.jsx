import { useLocation } from 'react-router-dom'
import useDocumentTitle from '../../hooks/useDocumentTitle'
import { titleForPath } from '../../lib/routeTitles'

/** Names the current route in the tab, the history entry and the bookmark.
 *
 * Every route used to render the one static title from index.html, so a
 * teacher with several tabs open saw identical titles.
 *
 * Renders nothing. A route added to App.jsx without an entry in the title map
 * falls back to the app name rather than inheriting the previous page's
 * title -- a stale title names the wrong page with full confidence.
 */
export default function RouteTitle() {
  const { pathname } = useLocation()
  useDocumentTitle(titleForPath(pathname) || 'AdaptiveLearning')
  return null
}
