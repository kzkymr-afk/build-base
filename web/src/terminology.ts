// 表示文字列の日本語平易化辞書（BuildBase S3a）。
//
// ここは表示直前の文字列変換のみを担う。型定義・APIキー・DB/CSVカラム名・
// ロジック上のステータス比較（例: status === 'proposed'）は一切変更しない。
// 新しい表示語彙を追加する場合はここに追記し、JSX側は t() 経由で参照すること。
export const TERM_JA: Record<string, string> = {
  golden: '確定値',
  source_chain: '根拠',
  observed: '有報の項目',
  concept: '表の項目',
  mapping: '対応付け',
  corroboration: '数値照合',
  needs_reconciliation: '検算不一致',
  needs_review: '要確認',
  single_source: '単一ソース',
  auto_confirmed: '自動確定',
  proposed: '提案中',
  confirmed: '確定済み',
  rejected: '却下済み',
  restatement: '訂正の疑い',
  deterministic: '自動一致',
  unverifiable: '照合不能',
};

/** 用語辞書に基づく表示文字列変換。未登録キーはそのまま返す。 */
export function t(key: string): string {
  return TERM_JA[key] ?? key;
}
