import { useLocation } from 'react-router-dom'
import useDocumentTitle from '../../hooks/useDocumentTitle'
import { titleForPath } from '../../lib/routeTitles'

/** Names the current route in the tab, the history entry and the bookmark.
 *
 * Every route rendered the one static title from index.html, so a teacher with
 * Live, a class and a student report open saw three identical tabs, and back
 * through history gave no clue where any entry led.
 *
 * Renders nothing. Mounted once inside the router, so a route added to App.jsx
 * without an entry in the title map falls back to the app name rather than
 * inheriting whatever the previous page set -- a stale title is worse than a
 * generic one, since it names the wrong page with full confidence.
 */
export default function RouteTitle() {
  const { pathname } = useLocation()
  useDocumentTitle(titleForPath(pathname) || 'AdaptiveLearning')
  return null
}
