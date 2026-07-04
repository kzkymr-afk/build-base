export const tabs = [
  ['run', '実行'],
  ['results', '結果'],
  ['fields', '項目整理'],
  ['stocks', '株価'],
  ['factbooks', 'ファクトブック'],
  ['charts', 'グラフ'],
  ['audit', '根拠'],
  ['review', 'レビュー'],
  ['mapping_review', 'マッピングレビュー'],
  ['algorithm_audit_findings', 'アルゴリズム監査'],
  ['ai', 'AI分析'],
  ['report', 'レポート']
] as const;

export const APP_VERSION_FALLBACK = '0.17.2';
export const COLLAPSED_TEXT_COLUMNS = new Set(['source_quote', 'quotes']);
export const REVIEW_CATEGORY_ORDER = ['missing', 'new_candidate', 'validation_issue', 'scope_warning', 'warning_candidate', 'saved_unapplied', 'recurrent', 'resolved_done'];

export const baseColumns = new Set([
  'company_year_id',
  'fiscal_year',
  'fiscal_year_end',
  'operating_company_id',
  'operating_company_name',
  'reporting_entity_id',
  'data_scope_allowed',
  'analysis_treatment'
]);

export const resultHiddenColumns = new Set([
  'company_year_id',
  'operating_company_id',
  'reporting_entity_id'
]);

export const baseColumnLabels: Record<string, string> = {
  company_year_id: '会社年度',
  fiscal_year: '年度',
  fiscal_year_end: '決算日',
  operating_company_id: '会社ID',
  operating_company_name: '会社名',
  reporting_entity_id: '開示主体',
  data_scope_allowed: '対象スコープ',
  analysis_treatment: '分析上の扱い'
};
