// The lesson page's health and status polls; a module of its own so a test can shorten them.
// Under push both answers are configuration, not live state, so they are re-read slowly.
export const PUSH_POLL_MS = 30_000
export const PULL_HEALTH_POLL_MS = 5_000
export const PULL_STATUS_POLL_MS = 3_000
