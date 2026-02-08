import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToastNotification } from '../ToastNotification';
import type { ToastMessage } from '../types';

describe('ToastNotification', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const makeToast = (overrides: Partial<ToastMessage> = {}): ToastMessage => ({
    id: 'toast-1',
    type: 'success',
    title: 'İşlem başarılı',
    ...overrides,
  });

  it('renders nothing when toasts array is empty', () => {
    const { container } = render(<ToastNotification toasts={[]} onDismiss={vi.fn()} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders a success toast with correct styling', () => {
    const toast = makeToast({ type: 'success' });
    render(<ToastNotification toasts={[toast]} onDismiss={vi.fn()} />);
    const alert = screen.getByRole('alert');
    expect(alert.className).toContain('bg-green-50');
    expect(alert.className).toContain('border-green-200');
    expect(alert.className).toContain('text-green-700');
  });

  it('renders an info toast with correct styling', () => {
    const toast = makeToast({ type: 'info', title: 'Bilgi' });
    render(<ToastNotification toasts={[toast]} onDismiss={vi.fn()} />);
    const alert = screen.getByRole('alert');
    expect(alert.className).toContain('bg-blue-50');
    expect(alert.className).toContain('border-blue-200');
    expect(alert.className).toContain('text-blue-700');
  });

  it('renders a warning toast with correct styling', () => {
    const toast = makeToast({ type: 'warning', title: 'Uyarı' });
    render(<ToastNotification toasts={[toast]} onDismiss={vi.fn()} />);
    const alert = screen.getByRole('alert');
    expect(alert.className).toContain('bg-amber-50');
    expect(alert.className).toContain('border-amber-200');
    expect(alert.className).toContain('text-amber-700');
  });

  it('renders an error toast with correct styling', () => {
    const toast = makeToast({ type: 'error', title: 'Hata' });
    render(<ToastNotification toasts={[toast]} onDismiss={vi.fn()} />);
    const alert = screen.getByRole('alert');
    expect(alert.className).toContain('bg-red-50');
    expect(alert.className).toContain('border-red-200');
    expect(alert.className).toContain('text-red-700');
  });

  it('displays the toast title', () => {
    const toast = makeToast({ title: 'Kayıt oluşturuldu' });
    render(<ToastNotification toasts={[toast]} onDismiss={vi.fn()} />);
    expect(screen.getByText('Kayıt oluşturuldu')).toBeInTheDocument();
  });

  it('displays error_code detail in monospace font', () => {
    const toast = makeToast({ type: 'error', detail: 'INVALID_PERIOD_FORMAT' });
    render(<ToastNotification toasts={[toast]} onDismiss={vi.fn()} />);
    const detail = screen.getByText('INVALID_PERIOD_FORMAT');
    expect(detail.className).toContain('font-mono');
  });

  it('does not render detail element when detail is undefined', () => {
    const toast = makeToast({ detail: undefined });
    render(<ToastNotification toasts={[toast]} onDismiss={vi.fn()} />);
    const alert = screen.getByRole('alert');
    const monoElements = alert.querySelectorAll('.font-mono');
    expect(monoElements).toHaveLength(0);
  });

  it('calls onDismiss when dismiss button is clicked', async () => {
    const onDismiss = vi.fn();
    const toast = makeToast({ id: 'toast-42' });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ToastNotification toasts={[toast]} onDismiss={onDismiss} />);

    await user.click(screen.getByRole('button', { name: 'Kapat' }));
    expect(onDismiss).toHaveBeenCalledWith('toast-42');
  });

  it('auto-closes after default 5 seconds', () => {
    const onDismiss = vi.fn();
    const toast = makeToast({ id: 'toast-auto' });
    render(<ToastNotification toasts={[toast]} onDismiss={onDismiss} />);

    act(() => {
      vi.advanceTimersByTime(4999);
    });
    expect(onDismiss).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onDismiss).toHaveBeenCalledWith('toast-auto');
  });

  it('auto-closes after custom duration', () => {
    const onDismiss = vi.fn();
    const toast = makeToast({ id: 'toast-custom', autoClose: 2000 });
    render(<ToastNotification toasts={[toast]} onDismiss={onDismiss} />);

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(onDismiss).toHaveBeenCalledWith('toast-custom');
  });

  it('renders multiple toasts', () => {
    const toasts: ToastMessage[] = [
      makeToast({ id: '1', title: 'First' }),
      makeToast({ id: '2', title: 'Second', type: 'error' }),
    ];
    render(<ToastNotification toasts={toasts} onDismiss={vi.fn()} />);
    expect(screen.getByText('First')).toBeInTheDocument();
    expect(screen.getByText('Second')).toBeInTheDocument();
  });
});
