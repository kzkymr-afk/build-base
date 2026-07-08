import React from 'react';
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable
} from '@tanstack/react-table';
import { Empty } from './common';
import { type CellStatus, type Row } from '../types';

export function DataTable({
  data,
  columns,
  baseColumns,
  columnLabels = {},
  onCellClick,
  onRowClick,
  selectedRowKey = '',
  selectedRowKeys,
  getRowKey,
  selectableRows = false,
  onRowSelectionToggle,
  compact = false,
  markEmptyCells = false,
  cellStatuses,
  clampAllCells = false,
  highlightStatus = '',
  formatCellText,
  renderCellValue,
  renderClampedText
}: {
  data: Row[];
  columns: string[];
  baseColumns: Set<string>;
  columnLabels?: Record<string, string>;
  onCellClick?: (row: Row, column: string) => void;
  onRowClick?: (row: Row) => void;
  selectedRowKey?: string;
  selectedRowKeys?: Set<string>;
  getRowKey?: (row: Row) => string;
  selectableRows?: boolean;
  onRowSelectionToggle?: (row: Row) => void;
  compact?: boolean;
  markEmptyCells?: boolean;
  cellStatuses?: Record<string, Record<string, CellStatus>>;
  clampAllCells?: boolean;
  highlightStatus?: string;
  formatCellText?: (column: string, value: unknown) => string;
  renderCellValue: (column: string, value: unknown) => React.ReactNode;
  renderClampedText: (value: unknown, className?: string) => React.ReactNode;
}) {
  const defs = React.useMemo<ColumnDef<Row>[]>(() => {
    const valueColumns: ColumnDef<Row>[] = columns.map((column): ColumnDef<Row> => ({
      accessorKey: column,
      header: columnLabels[column] || column,
      cell: (info) => {
        const rawValue = info.getValue();
        const value = String(rawValue ?? '');
        const companyYearId = String(info.row.original.company_year_id || '');
        const status = companyYearId ? cellStatuses?.[companyYearId]?.[column] : undefined;
        if (status && !baseColumns.has(column)) {
          return (
            <span className="status-cell">
              <span className={value.trim() === '' ? 'empty-cell' : 'numeric-value'}>{value.trim() === '' ? '空欄' : renderCellValue(column, rawValue)}</span>
              <span className={`cell-status-dot status-${status.status}`} title={status.summary || status.status_label}>{status.status_label}</span>
            </span>
          );
        }
        if (markEmptyCells && !baseColumns.has(column) && value.trim() === '') {
          return <span className="empty-cell">空欄</span>;
        }
        if (clampAllCells && column !== 'review_saved' && column !== 'applied_status') {
          return renderClampedText(formatCellText ? formatCellText(column, rawValue) : rawValue);
        }
        return renderCellValue(column, rawValue);
      }
    }));
    if (!selectableRows) {
      return valueColumns;
    }
    const selectionColumn: ColumnDef<Row> = {
      id: '__select',
      header: '',
      cell: (info) => {
        const row = info.row.original;
        const key = getRowKey?.(row) || '';
        return (
          <input
            type="checkbox"
            aria-label="選択"
            checked={Boolean(key && selectedRowKeys?.has(key))}
            onChange={(event) => {
              event.stopPropagation();
              onRowSelectionToggle?.(row);
            }}
            onClick={(event) => event.stopPropagation()}
          />
        );
      }
    };
    return [
      selectionColumn,
      ...valueColumns
    ];
  }, [columns, columnLabels, markEmptyCells, cellStatuses, baseColumns, clampAllCells, formatCellText, renderCellValue, renderClampedText, selectableRows, selectedRowKeys, getRowKey, onRowSelectionToggle]);
  const table = useReactTable({ data, columns: defs, getCoreRowModel: getCoreRowModel() });
  if (!data.length) return <Empty message="該当する行がありません。" />;
  return (
    <div className={`table-wrap ${compact ? 'compact' : ''} ${clampAllCells ? 'clamp-all-cells' : ''}`}>
      <table>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className={[
                onRowClick ? 'row-clickable' : '',
                selectedRowKey && getRowKey?.(row.original) === selectedRowKey ? 'row-selected' : '',
                selectedRowKeys?.has(getRowKey?.(row.original) || '') ? 'row-selected' : ''
              ].filter(Boolean).join(' ')}
              onClick={() => onRowClick?.(row.original)}
            >
              {row.getVisibleCells().map((cell) => {
                const interactiveCell = Boolean(onCellClick && !baseColumns.has(cell.column.id));
                const status = String(row.original.company_year_id || '')
                  ? cellStatuses?.[String(row.original.company_year_id || '')]?.[cell.column.id]
                  : undefined;
                const statusMatchesFilter = Boolean(highlightStatus && status?.status === highlightStatus);
                const openCell = () => {
                  if (interactiveCell) {
                    onCellClick?.(row.original, cell.column.id);
                  }
                };
                return (
                  <td
                    key={cell.id}
                    className={[
                      interactiveCell ? 'cell-clickable' : '',
                      statusMatchesFilter ? 'cell-status-match' : ''
                    ].filter(Boolean).join(' ')}
                    title={interactiveCell ? `セル作業を開く: ${columnLabels[cell.column.id] || cell.column.id}${status?.status_label ? `（${status.status_label}）` : ''}` : undefined}
                    role={interactiveCell ? 'button' : undefined}
                    tabIndex={interactiveCell ? 0 : undefined}
                    onClick={(event) => {
                      if (interactiveCell) {
                        event.stopPropagation();
                        openCell();
                      }
                    }}
                    onKeyDown={(event) => {
                      if (!interactiveCell) return;
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        event.stopPropagation();
                        openCell();
                      }
                    }}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
