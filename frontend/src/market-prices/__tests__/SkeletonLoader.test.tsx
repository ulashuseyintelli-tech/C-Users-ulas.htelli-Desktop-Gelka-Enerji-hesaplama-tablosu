import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SkeletonLoader } from '../SkeletonLoader';

describe('SkeletonLoader', () => {
  it('renders 5 skeleton rows by default', () => {
    render(<SkeletonLoader />);
    const table = screen.getByRole('status');
    const tbody = table.querySelector('tbody');
    const rows = tbody!.querySelectorAll('tr');
    expect(rows).toHaveLength(5);
  });

  it('renders custom number of rows', () => {
    render(<SkeletonLoader rows={3} />);
    const table = screen.getByRole('status');
    const tbody = table.querySelector('tbody');
    const rows = tbody!.querySelectorAll('tr');
    expect(rows).toHaveLength(3);
  });

  it('renders 8 columns per row matching PriceListTable layout', () => {
    render(<SkeletonLoader />);
    const table = screen.getByRole('status');
    const tbody = table.querySelector('tbody');
    const firstRow = tbody!.querySelector('tr');
    const cells = firstRow!.querySelectorAll('td');
    expect(cells).toHaveLength(8);
  });

  it('renders 8 header columns', () => {
    render(<SkeletonLoader />);
    const table = screen.getByRole('status');
    const thead = table.querySelector('thead');
    const headerCells = thead!.querySelectorAll('th');
    expect(headerCells).toHaveLength(8);
  });

  it('uses animate-pulse class for shimmer effect', () => {
    render(<SkeletonLoader rows={1} />);
    const table = screen.getByRole('status');
    const pulseElements = table.querySelectorAll('.animate-pulse');
    // 8 header + 8 body cells = 16 shimmer elements
    expect(pulseElements.length).toBeGreaterThanOrEqual(16);
  });

  it('has accessible loading label', () => {
    render(<SkeletonLoader />);
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', 'Yükleniyor');
  });
});
