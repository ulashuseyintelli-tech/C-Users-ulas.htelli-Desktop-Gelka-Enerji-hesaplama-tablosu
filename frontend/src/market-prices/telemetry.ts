// =============================================================================
// PTF Admin Frontend — Telemetry Module
// =============================================================================
//
// Lightweight fire-and-forget event tracking. All UI components call trackEvent()
// instead of touching fetch/endpoint details directly.
//
// Design: buffered batching with flush interval OR max batch size trigger.
// Failures are silently logged — telemetry never breaks the UI.
// =============================================================================

/** Single telemetry event queued for delivery */
export interface TelemetryEvent {
  event: string;
  properties: Record<string, unknown>;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const TELEMETRY_ENDPOINT = '/admin/telemetry/events';

/** Flush interval in ms — events are sent at most this often */
export const FLUSH_INTERVAL_MS = 2_000;

/** When buffer reaches this size, flush immediately */
export const MAX_BATCH_SIZE = 20;

/** Hard cap — oldest events are dropped when exceeded */
export const MAX_BUFFER_SIZE = 200;

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

let eventBuffer: TelemetryEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Queue a telemetry event for batched delivery.
 *
 * - Buffer overflow → oldest events dropped + console.warn
 * - Batch size reached → immediate flush
 * - Otherwise → flush after FLUSH_INTERVAL_MS
 */
export function trackEvent(
  event: string,
  properties: Record<string, unknown> = {},
): void {
  // Buffer overflow protection: drop oldest events
  if (eventBuffer.length >= MAX_BUFFER_SIZE) {
    const dropCount = eventBuffer.length - MAX_BUFFER_SIZE + 1;
    eventBuffer = eventBuffer.slice(dropCount);
    console.warn(`[telemetry] Buffer overflow, dropped ${dropCount} oldest events`);
  }

  eventBuffer.push({
    event,
    properties,
    timestamp: new Date().toISOString(),
  });

  if (eventBuffer.length >= MAX_BATCH_SIZE) {
    flush();
  } else if (!flushTimer) {
    flushTimer = setTimeout(flush, FLUSH_INTERVAL_MS);
  }
}

/**
 * Immediately send all buffered events. Useful for tests and page unload.
 * Fire-and-forget: failed batches are discarded, no retry.
 */
export async function flush(): Promise<void> {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  if (eventBuffer.length === 0) return;

  const batch = [...eventBuffer];
  eventBuffer = [];

  try {
    await fetch(TELEMETRY_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events: batch }),
    });
  } catch (err) {
    // Fire-and-forget: discard failed batch, no retry
    console.warn('[telemetry] Failed to send events, batch discarded:', err);
  }
}

// ---------------------------------------------------------------------------
// Test helpers (not exported from barrel — tests import directly)
// ---------------------------------------------------------------------------

/** Reset internal state — for test isolation only */
export function _resetForTesting(): void {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  eventBuffer = [];
}

/** Read current buffer snapshot — for test assertions only */
export function _getBuffer(): readonly TelemetryEvent[] {
  return [...eventBuffer];
}
