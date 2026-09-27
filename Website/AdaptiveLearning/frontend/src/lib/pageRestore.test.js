import { describe, it, expect, vi } from 'vitest'
import { reloadIfRestored } from './pageRestore'

const page = (type, persisted) => Object.assign(new Event(type), { persisted })

describe('reloadIfRestored', () => {
  it('reloads once when Back restores a page kept in the back-forward cache', () => {
    const reload = vi.fn()
    reloadIfRestored(page('pagehide', true), reload)
    window.dispatchEvent(page('pageshow', true))
    window.dispatchEvent(page('pageshow', true))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('does nothing for a page that is really going away', () => {
    const reload = vi.fn()
    reloadIfRestored(page('pagehide', false), reload)
    window.dispatchEvent(page('pageshow', true))
    expect(reload).not.toHaveBeenCalled()
  })
})
