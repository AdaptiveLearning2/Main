// The lesson page's health and status polls; a module of its own so a test can shorten them.
// Under push both answers are configuration, not live state, so they are re-read slowly.
export const PUSH_POLL_MS = 30_000
export const PULL_HEALTH_POLL_MS = 5_000
export const PULL_STATUS_POLL_MS = 3_000
// The sidecar's push status during a lesson: delivery counts, and its own recording answer.
export const PUSH_STATUS_POLL_MS = 10_000
