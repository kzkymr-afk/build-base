import React from 'react';

/**
 * 専門用語の隣に置く小さな「？」アイコン。ホバー/フォーカスで説明を表示する。
 * 表示専用コンポーネント（状態・ロジックは持たない）。
 */
export function TermTooltip({ label, explain }: { label: string; explain: string }) {
  return (
    <span className="term-tooltip">
      {label}
      <span className="term-tooltip-icon" tabIndex={0} aria-label={explain}>
        ?
        <span className="term-tooltip-bubble">{explain}</span>
      </span>
    </span>
  );
}
