// Copyright (c) 2026 codeasier. MIT; see ../LICENSE.
// Adapted from the module-navigation capability filter in the predecessor.
// Module descriptors are injected here; there are no production module imports.
// This is presentation filtering only, never a substitute for server authorization.
export function visibleNavigation(items, capabilities) {
  if (!Array.isArray(items) || !capabilities || typeof capabilities !== 'object') return []
  return items.filter(item => item && typeof item.id === 'string' &&
    typeof item.capability === 'string' && item.capability.length > 0 &&
    Object.hasOwn(capabilities, item.capability) && capabilities[item.capability] === true)
}
