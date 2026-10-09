import { useCallback } from 'react'
import { apiFetch } from '../lib/api'
import useAdminResource from './useAdminResource'

/** One admin GET, re-read every `pollMs`; `loadError` keeps the failed read's status. */
export default function useAdminRead(path, pollMs) {
  const load = useCallback(() => apiFetch(path), [path])
  return useAdminResource({ load, pollMs })
}
