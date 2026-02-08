import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge } from '../StatusBadge';

describe('StatusBadge', () => {
  it('renders "Ön Değer" for provisional status', () => {
    render(<StatusBadge status="provisional" />);
    expect(screen.getByText('Ön Değer')).toBeInTheDocument();
  });

  it('renders "Kesinleşmiş" for final status', () => {
    render(<StatusBadge status="final" />);
    expect(screen.getByText('Kesinleşmiş')).toBeInTheDocument();
  });

  it('applies amber styling for provisional status', () => {
    render(<StatusBadge status="provisional" />);
    const badge = screen.getByText('Ön Değer');
    expect(badge.className).toContain('bg-amber-100');
    expect(badge.className).toContain('text-amber-700');
  });

  it('applies green styling for final status', () => {
    render(<StatusBadge status="final" />);
    const badge = screen.getByText('Kesinleşmiş');
    expect(badge.className).toContain('bg-green-100');
    expect(badge.className).toContain('text-green-700');
  });

  it('renders as a span element', () => {
    render(<StatusBadge status="provisional" />);
    const badge = screen.getByText('Ön Değer');
    expect(badge.tagName).toBe('SPAN');
  });
});
