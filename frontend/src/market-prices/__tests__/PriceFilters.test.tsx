import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PriceFilters } from '../PriceFilters';
import type { FilterState } from '../types';

describe('PriceFilters', () => {
  const defaultFilters: FilterState = {
    status: 'all',
    fromPeriod: '',
    toPeriod: '',
  };

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders status dropdown with correct options', () => {
    render(<PriceFilters filters={defaultFilters} onFilterChange={vi.fn()} />);
    const select = screen.getByLabelText('Durum');
    expect(select).toBeInTheDocument();

    const options = select.querySelectorAll('option');
    expect(options).toHaveLength(3);
    expect(options[0].textContent).toBe('Tümü');
    expect(options[1].textContent).toBe('Ön Değer');
    expect(options[2].textContent).toBe('Kesinleşmiş');
  });

  it('renders from_period and to_period month inputs', () => {
    render(<PriceFilters filters={defaultFilters} onFilterChange={vi.fn()} />);
    expect(screen.getByLabelText('Başlangıç Dönemi')).toHaveAttribute('type', 'month');
    expect(screen.getByLabelText('Bitiş Dönemi')).toHaveAttribute('type', 'month');
  });

  it('reflects current filter values', () => {
    const filters: FilterState = {
      status: 'final',
      fromPeriod: '2024-01',
      toPeriod: '2025-06',
    };
    render(<PriceFilters filters={filters} onFilterChange={vi.fn()} />);

    expect(screen.getByLabelText('Durum')).toHaveValue('final');
    expect(screen.getByLabelText('Başlangıç Dönemi')).toHaveValue('2024-01');
    expect(screen.getByLabelText('Bitiş Dönemi')).toHaveValue('2025-06');
  });

  it('debounces status change by 300ms', async () => {
    const onFilterChange = vi.fn();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<PriceFilters filters={defaultFilters} onFilterChange={onFilterChange} />);

    await user.selectOptions(screen.getByLabelText('Durum'), 'final');

    // Not called immediately
    expect(onFilterChange).not.toHaveBeenCalled();

    // Called after 300ms
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(onFilterChange).toHaveBeenCalledWith({ status: 'final' });
  });

  it('debounces from_period change by 300ms', async () => {
    const onFilterChange = vi.fn();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<PriceFilters filters={defaultFilters} onFilterChange={onFilterChange} />);

    const input = screen.getByLabelText('Başlangıç Dönemi');
    await user.clear(input);
    await user.type(input, '2024-01');

    // Should not be called during typing (debounce resets)
    expect(onFilterChange).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(300);
    });
    // After debounce, the last change fires
    expect(onFilterChange).toHaveBeenCalled();
  });

  it('debounces to_period change by 300ms', async () => {
    const onFilterChange = vi.fn();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<PriceFilters filters={defaultFilters} onFilterChange={onFilterChange} />);

    const input = screen.getByLabelText('Bitiş Dönemi');
    await user.clear(input);
    await user.type(input, '2025-06');

    expect(onFilterChange).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(onFilterChange).toHaveBeenCalled();
  });

  it('cancels previous debounce when a new change occurs', async () => {
    const onFilterChange = vi.fn();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<PriceFilters filters={defaultFilters} onFilterChange={onFilterChange} />);

    // First change
    await user.selectOptions(screen.getByLabelText('Durum'), 'provisional');

    // Advance 200ms (not yet fired)
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(onFilterChange).not.toHaveBeenCalled();

    // Second change before debounce fires
    await user.selectOptions(screen.getByLabelText('Durum'), 'final');

    // Advance another 300ms
    act(() => {
      vi.advanceTimersByTime(300);
    });

    // Only the last change should fire
    expect(onFilterChange).toHaveBeenCalledTimes(1);
    expect(onFilterChange).toHaveBeenCalledWith({ status: 'final' });
  });
});
