/**
 * Call from a `pagehide` handler that stopped a stream. If Back restores the page from the
 * back-forward cache, it reloads, since its state still shows the stream running.
 */
export function reloadIfRestored(hideEvent, reload = () => window.location.reload()) {
  if (!hideEvent?.persisted) return
  const onShow = (e) => {
    window.removeEventListener('pageshow', onShow)
    if (e?.persisted) reload()
  }
  window.addEventListener('pageshow', onShow)
}
