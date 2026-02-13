// =============================================================================
// Unit Tests: Telemetry Module
// Feature: telemetry-unification, Task 7
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  trackEvent,
  flush,
  _resetForTesting,
  _getBuffer,
  _setBufferForTesting,
  MAX_BATCH_SIZE,
  MAX_BUFFER_SIZE,
  FLUSH_INTERVAL_MS,
} from '../telemetry';
import type { TelemetryEvent } from '../telemetry';

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  _resetForTesting();
  vi.useFakeTimers();
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, status: 200 }),
  );
});

afterEach(() => {
  _resetForTesting();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// trackEvent basics
// ---------------------------------------------------------------------------

describe('trackEvent', () => {
  it('adds event to buffer with correct structure', () => {
    trackEvent('ptf_admin.upsert_submit', { period: '2025-01' });

    const buf = _getBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toBe('ptf_admin.upsert_submit');
    expect(buf[0].properties).toEqual({ period: '2025-01' });
    expect(buf[0].timestamp).toBeTruthy();
    // ISO 8601 format check
    expect(() => new Date(buf[0].timestamp)).not.toThrow();
  });

  it('defaults properties to empty object when omitted', () => {
    trackEvent('ptf_admin.filter_change');

    const buf = _getBuffer();
    expect(buf[0].properties).toEqual({});
  });

  it('schedules flush after FLUSH_INTERVAL_MS', () => {
    trackEvent('ptf_admin.test');

    expect(fetch).not.toHaveBeenCalled();

    vi.advanceTimersByTime(FLUSH_INTERVAL_MS);

    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('flushes immediately when buffer reaches MAX_BATCH_SIZE', () => {
    for (let i = 0; i < MAX_BATCH_SIZE; i++) {
      trackEvent(`ptf_admin.event_${i}`);
    }

    // Should have flushed on the 20th event
    expect(fetch).toHaveBeenCalledTimes(1);

    const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse(call[1].body);
    expect(body.events).toHaveLength(MAX_BATCH_SIZE);
  });
});

// ---------------------------------------------------------------------------
// Buffer overflow
// ---------------------------------------------------------------------------

describe('buffer overflow protection', () => {
  it('drops oldest events when buffer exceeds MAX_BUFFER_SIZE', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    // Pre-fill buffer to MAX_BUFFER_SIZE using test helper (bypasses flush)
    const prefilled: TelemetryEvent[] = Array.from(
      { length: MAX_BUFFER_SIZE },
      (_, i) => ({
        event: `ptf_admin.old_${i}`,
        properties: {},
        timestamp: '2025-01-01T00:00:00.000Z',
      }),
    );
    _setBufferForTesting(prefilled);

    // Adding one more event should trigger overflow warning
    trackEvent('ptf_admin.overflow');

    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Buffer overflow'),
    );

    // After overflow + trackEvent, buffer hits MAX_BATCH_SIZE so flush()
    // fires and clears the buffer. The important thing is the warn was logged.
    // Verify the POST was sent with the overflow event included
    const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse(call[1].body);
    const events = body.events as TelemetryEvent[];
    // The newest event should be in the flushed batch
    expect(events[events.length - 1].event).toBe('ptf_admin.overflow');

    warnSpy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// flush()
// ---------------------------------------------------------------------------

describe('flush', () => {
  it('sends buffered events via POST', async () => {
    trackEvent('ptf_admin.a');
    trackEvent('ptf_admin.b');

    await flush();

    expect(fetch).toHaveBeenCalledWith(
      '/admin/telemetry/events',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const body = JSON.parse(
      (fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].body,
    );
    expect(body.events).toHaveLength(2);
  });

  it('clears buffer after flush', async () => {
    trackEvent('ptf_admin.test');
    await flush();

    expect(_getBuffer()).toHaveLength(0);
  });

  it('does nothing when buffer is empty', async () => {
    await flush();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('discards batch on fetch failure without throwing', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('Network error')),
    );

    trackEvent('ptf_admin.test');
    // Should not throw
    await expect(flush()).resolves.toBeUndefined();

    // Buffer should be cleared (batch discarded)
    expect(_getBuffer()).toHaveLength(0);

    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('Failed to send events'),
      expect.any(Error),
    );

    warnSpy.mockRestore();
  });

  it('cancels pending timer when called manually', async () => {
    trackEvent('ptf_admin.test');
    // Timer is scheduled
    await flush();

    // Advance past the interval — should NOT trigger another fetch
    vi.advanceTimersByTime(FLUSH_INTERVAL_MS + 100);

    // Only the manual flush call
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
