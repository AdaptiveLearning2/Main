// The sidecar the page calls when its env names none. `pagesHeaders.js` builds `connect-src` from
// this same value, so the policy allows the loopback call the bundle makes.
export const DEFAULT_SIDECAR_URL = 'http://127.0.0.1:8001'
