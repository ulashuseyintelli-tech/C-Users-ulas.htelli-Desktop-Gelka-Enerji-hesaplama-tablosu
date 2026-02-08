import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PriceListTable } from '../PriceListTable';
import type { MarketPriceRecord, PaginationState } from '../types';

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

function makeRecord(overrides: Partial<MarketPriceRecord> = {}): MarketPriceRecord {
  return {
    period: '2025-01',
    ptf_tl_per_mwh: 2508.8,
    status: 'provisional',
    price_type: 'PTF',
    captured_at: '2025-01-15T10:30:00Z',
    updated_at: '2025-01-15T10:30:00Z',
    updated_by: 'admin',
    source: 'epias_manual',
    source_note: '',
    change_reason: '',
    is_locked: false,
    yekdem_tl_per_mwh: 0,
    ...overrides,
  };
}

const defaultPagination: PaginationState = {
  page: 1,
  pageSize: 20,
  total: 50,
};

const defaultProps = {
  data: [makeRecord()],
  loading: false,
  pagination: defaultPagination,
  sortBy: 'period',
  sortOrder: 'desc' as const,
  onSort: vi.fn(),
  onPageChange: vi.fn(),
  onPageSizeChange: vi.fn(),
  onEdit: vi.fn(),
  onClearFilters: vi.fn(),
  isEmpty: false,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PriceListTable', () => {
  // ---- Rendering with data ----

  describe('rendering with data', () => {
    it('renders all column headers', () => {
      render(<PriceListTable {...defaultProps} />);

      expect(screen.getByText('Dönem')).toBeInTheDocument();
      expect(screen.getByText('PTF (TL/MWh)')).toBeInTheDocument();
      expect(screen.getByText('Durum')).toBeInTheDocument();
      expect(screen.getByText('Güncelleme')).toBeInTheDocument();
      expect(screen.getByText('Kaynak')).toBeInTheDocument();
      expect(screen.getByText('Güncelleyen')).toBeInTheDocument();
      expect(screen.getByText('Değişiklik Nedeni')).toBeInTheDocument();
      expect(screen.getByText('İşlem')).toBeInTheDocument();
    });

    it('renders period value', () => {
      render(<PriceListTable {...defaultProps} />);
      expect(screen.getByText('2025-01')).toBeInTheDocument();
    });

    it('renders price in Turkish locale format', () => {
      render(<PriceListTable {...defaultProps} />);
      expect(screen.getByText('2.508,80')).toBeInTheDocument();
    });

    it('renders status badge', () => {
      render(<PriceListTable {...defaultProps} />);
      expect(screen.getByText('Ön Değer')).toBeInTheDocument();
    });

    it('renders final status badge', () => {
      const data = [makeRecord({ status: 'final' })];
      render(<PriceListTable {...defaultProps} data={data} />);
      expect(screen.getByText('Kesinleşmiş')).toBeInTheDocument();
    });

    it('renders formatted datetime in Europe/Istanbul timezone', () => {
      render(<PriceListTable {...defaultProps} />);
      // 2025-01-15T10:30:00Z → Europe/Istanbul is UTC+3 → 13:30
      expect(screen.getByText('15.01.2025 13:30')).toBeInTheDocument();
    });

    it('renders source value', () => {
      render(<PriceListTable {...defaultProps} />);
      expect(screen.getByText('epias_manual')).toBeInTheDocument();
    });

    it('renders updated_by value', () => {
      render(<PriceListTable {...defaultProps} />);
      expect(screen.getByText('admin')).toBeInTheDocument();
    });

    it('renders edit button for each row', () => {
      render(<PriceListTable {...defaultProps} />);
      expect(screen.getByRole('button', { name: /düzenle/i })).toBeInTheDocument();
    });

    it('truncates long change_reason with ellipsis', () => {
      const longReason = 'Bu çok uzun bir değişiklik nedeni açıklamasıdır ve kesilmeli';
      const data = [makeRecord({ change_reason: longReason })];
      render(<PriceListTable {...defaultProps} data={data} />);

      // Should be truncated to 30 chars + ellipsis
      const truncated = longReason.slice(0, 30) + '…';
      expect(screen.getByText(truncated)).toBeInTheDocument();
    });

    it('shows dash for empty change_reason', () => {
      const data = [makeRecord({ change_reason: '' })];
      render(<PriceListTable {...defaultProps} data={data} />);
      expect(screen.getByText('—')).toBeInTheDocument();
    });

    it('renders multiple rows', () => {
      const data = [
        makeRecord({ period: '2025-01' }),
        makeRecord({ period: '2025-02', ptf_tl_per_mwh: 1234.56 }),
      ];
      render(<PriceListTable {...defaultProps} data={data} />);

      expect(screen.getByText('2025-01')).toBeInTheDocument();
      expect(screen.getByText('2025-02')).toBeInTheDocument();
      expect(screen.getByText('1.234,56')).toBeInTheDocument();
    });
  });

  // ---- Loading state ----

  describe('loading state', () => {
    it('renders SkeletonLoader when loading is true', () => {
      render(<PriceListTable {...defaultProps} loading={true} />);
      expect(screen.getByRole('status', { name: /yükleniyor/i })).toBeInTheDocument();
    });

    it('does not render table when loading', () => {
      render(<PriceListTable {...defaultProps} loading={true} />);
      expect(screen.queryByText('Dönem')).not.toBeInTheDocument();
    });
  });

  // ---- Empty state ----

  describe('empty state', () => {
    it('renders empty state when isEmpty is true and data is empty', () => {
      render(<PriceListTable {...defaultProps} data={[]} isEmpty={true} />);
      expect(screen.getByTestId('empty-state')).toBeInTheDocument();
      expect(screen.getByText('Kayıt bulunamadı')).toBeInTheDocument();
    });

    it('renders "Filtreleri Temizle" button in empty state', () => {
      render(<PriceListTable {...defaultProps} data={[]} isEmpty={true} />);
      expect(screen.getByRole('button', { name: 'Filtreleri Temizle' })).toBeInTheDocument();
    });

    it('calls onClearFilters when "Filtreleri Temizle" is clicked', async () => {
      const onClearFilters = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} data={[]} isEmpty={true} onClearFilters={onClearFilters} />);

      await user.click(screen.getByRole('button', { name: 'Filtreleri Temizle' }));
      expect(onClearFilters).toHaveBeenCalledTimes(1);
    });

    it('does not render table in empty state', () => {
      render(<PriceListTable {...defaultProps} data={[]} isEmpty={true} />);
      expect(screen.queryByText('Dönem')).not.toBeInTheDocument();
    });
  });

  // ---- Sort ----

  describe('sort', () => {
    it('calls onSort when a sortable column header is clicked', async () => {
      const onSort = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} onSort={onSort} />);

      await user.click(screen.getByText('PTF (TL/MWh)'));
      expect(onSort).toHaveBeenCalledWith('ptf_tl_per_mwh');
    });

    it('calls onSort with period when Dönem header is clicked', async () => {
      const onSort = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} onSort={onSort} />);

      await user.click(screen.getByText('Dönem'));
      expect(onSort).toHaveBeenCalledWith('period');
    });

    it('does not call onSort when a non-sortable column is clicked', async () => {
      const onSort = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} onSort={onSort} />);

      await user.click(screen.getByText('Kaynak'));
      expect(onSort).not.toHaveBeenCalled();
    });

    it('shows descending sort indicator on active column', () => {
      render(<PriceListTable {...defaultProps} sortBy="period" sortOrder="desc" />);
      expect(screen.getByTestId('sort-indicator-period')).toHaveTextContent('▼');
    });

    it('shows ascending sort indicator on active column', () => {
      render(<PriceListTable {...defaultProps} sortBy="period" sortOrder="asc" />);
      expect(screen.getByTestId('sort-indicator-period')).toHaveTextContent('▲');
    });

    it('sets aria-sort on active sorted column', () => {
      const { container } = render(<PriceListTable {...defaultProps} sortBy="period" sortOrder="desc" />);
      const headers = container.querySelectorAll('th');
      // First column is "Dönem" (period)
      expect(headers[0]).toHaveAttribute('aria-sort', 'descending');
    });
  });

  // ---- Pagination ----

  describe('pagination', () => {
    it('renders pagination controls', () => {
      render(<PriceListTable {...defaultProps} />);
      expect(screen.getByTestId('pagination-controls')).toBeInTheDocument();
    });

    it('displays current page and total pages', () => {
      render(<PriceListTable {...defaultProps} pagination={{ page: 2, pageSize: 20, total: 50 }} />);
      expect(screen.getByTestId('page-info')).toHaveTextContent('Sayfa 2 / 3');
    });

    it('calls onPageChange with next page when next button is clicked', async () => {
      const onPageChange = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} pagination={{ page: 1, pageSize: 20, total: 50 }} onPageChange={onPageChange} />);

      await user.click(screen.getByRole('button', { name: 'Sonraki sayfa' }));
      expect(onPageChange).toHaveBeenCalledWith(2);
    });

    it('calls onPageChange with previous page when prev button is clicked', async () => {
      const onPageChange = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} pagination={{ page: 2, pageSize: 20, total: 50 }} onPageChange={onPageChange} />);

      await user.click(screen.getByRole('button', { name: 'Önceki sayfa' }));
      expect(onPageChange).toHaveBeenCalledWith(1);
    });

    it('calls onPageChange(1) when first page button is clicked', async () => {
      const onPageChange = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} pagination={{ page: 3, pageSize: 20, total: 50 }} onPageChange={onPageChange} />);

      await user.click(screen.getByRole('button', { name: 'İlk sayfa' }));
      expect(onPageChange).toHaveBeenCalledWith(1);
    });

    it('calls onPageChange with last page when last page button is clicked', async () => {
      const onPageChange = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} pagination={{ page: 1, pageSize: 20, total: 50 }} onPageChange={onPageChange} />);

      await user.click(screen.getByRole('button', { name: 'Son sayfa' }));
      expect(onPageChange).toHaveBeenCalledWith(3);
    });

    it('disables prev/first buttons on first page', () => {
      render(<PriceListTable {...defaultProps} pagination={{ page: 1, pageSize: 20, total: 50 }} />);
      expect(screen.getByRole('button', { name: 'İlk sayfa' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Önceki sayfa' })).toBeDisabled();
    });

    it('disables next/last buttons on last page', () => {
      render(<PriceListTable {...defaultProps} pagination={{ page: 3, pageSize: 20, total: 50 }} />);
      expect(screen.getByRole('button', { name: 'Sonraki sayfa' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Son sayfa' })).toBeDisabled();
    });

    it('renders page size selector with correct options', () => {
      render(<PriceListTable {...defaultProps} />);
      const select = screen.getByLabelText('Sayfa boyutu');
      const options = within(select).getAllByRole('option');
      expect(options).toHaveLength(3);
      expect(options[0]).toHaveTextContent('10');
      expect(options[1]).toHaveTextContent('20');
      expect(options[2]).toHaveTextContent('50');
    });

    it('calls onPageSizeChange when page size is changed', async () => {
      const onPageSizeChange = vi.fn();
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} onPageSizeChange={onPageSizeChange} />);

      await user.selectOptions(screen.getByLabelText('Sayfa boyutu'), '50');
      expect(onPageSizeChange).toHaveBeenCalledWith(50);
    });

    it('reflects current page size in selector', () => {
      render(<PriceListTable {...defaultProps} pagination={{ page: 1, pageSize: 50, total: 100 }} />);
      expect(screen.getByLabelText('Sayfa boyutu')).toHaveValue('50');
    });
  });

  // ---- Edit action ----

  describe('edit action', () => {
    it('calls onEdit with the record when edit button is clicked', async () => {
      const onEdit = vi.fn();
      const record = makeRecord({ period: '2025-03' });
      const user = userEvent.setup();
      render(<PriceListTable {...defaultProps} data={[record]} onEdit={onEdit} />);

      await user.click(screen.getByRole('button', { name: /2025-03 düzenle/i }));
      expect(onEdit).toHaveBeenCalledWith(record);
    });

    it('renders edit button with correct aria-label per row', () => {
      const data = [
        makeRecord({ period: '2025-01' }),
        makeRecord({ period: '2025-02' }),
      ];
      render(<PriceListTable {...defaultProps} data={data} />);

      expect(screen.getByRole('button', { name: '2025-01 düzenle' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '2025-02 düzenle' })).toBeInTheDocument();
    });
  });
});
