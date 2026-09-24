import { it, expect } from 'vitest'
import { onSignOut, runSignOutTasks } from './signOutTasks'

it('does not hold sign-out on a task that never settles', async () => {
  // A backend that does not answer must not leave the student unable to
  // sign out; the page's unmount cleanup is still there as the fallback.
  const off = onSignOut(() => new Promise(() => {}))
  try {
    const started = Date.now()
    await runSignOutTasks(50)
    expect(Date.now() - started).toBeLessThan(2000)
  } finally {
    off()
  }
})

it('does not let one failing task stop the others or the sign-out', async () => {
  const ran = []
  const offs = [
    onSignOut(() => { throw new Error('sync failure') }),
    onSignOut(async () => { throw new Error('async failure') }),
    onSignOut(async () => { ran.push('ok') }),
  ]
  try {
    await expect(runSignOutTasks(1000)).resolves.not.toThrow()
    expect(ran).toEqual(['ok'])
  } finally {
    offs.forEach(off => off())
  }
})

it('forgets a task once it is unregistered', async () => {
  const ran = []
  const off = onSignOut(() => { ran.push('gone') })
  off()
  await runSignOutTasks(100)
  expect(ran).toEqual([])
})
