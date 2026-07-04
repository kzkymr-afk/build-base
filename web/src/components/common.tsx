import React from 'react';

export function Pager({
  page,
  totalPages,
  total,
  onPage,
  itemLabel = ''
}: {
  page: number;
  totalPages: number;
  total: number;
  onPage: (page: number) => void;
  itemLabel?: string;
}) {
  const prefix = itemLabel ? `${itemLabel}` : '';
  return (
    <div className="pager">
      <button className="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>{prefix}前へ</button>
      <span>{page} / {totalPages}（{total}件）</span>
      <button className="ghost" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>{prefix}次へ</button>
    </div>
  );
}

export function FilterBar({ children }: { children: React.ReactNode }) {
  return <div className="filter-bar">{children}</div>;
}

export function Empty({ message }: { message: string }) {
  return <div className="empty">{message}</div>;
}

export function InlineError({ message }: { message: string }) {
  return <div className="inline-error">{message}</div>;
}

export function MarkdownBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="panel">
      <h2>{title}</h2>
      <pre className="markdown">{content || '読み込み中です。'}</pre>
    </div>
  );
}
