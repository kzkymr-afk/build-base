export type Row = Record<string, unknown>;

export type Page<T extends Row = Row> = {
  rows: T[];
  columns: string[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  review_category_counts?: Record<string, number>;
  review_category_labels?: Record<string, string>;
};

export type CellStatus = {
  status: string;
  status_label: string;
  summary: string;
  next_action: string;
  review_saved: boolean;
  applied_status: string;
  candidate_count: number;
  has_source_audit: boolean;
  resolution: string;
};

export type WidePage<T extends Row = Row> = Page<T> & {
  cell_statuses: Record<string, Record<string, CellStatus>>;
};

export type Job = {
  id: string;
  name: string;
  status: string;
  started_at: string;
  finished_at: string;
  exit_code: number | null;
  error: string;
  logs: string[];
};

export type Status = {
  app_version?: string;
  project_root: string;
  files: Record<string, boolean>;
  ai_bundle_generated_at_utc: string;
  algorithm_audit_generated_at_utc?: string;
  run_report: { summary: Record<string, string>; exists: boolean; path: string };
};

export type AutomationStatus = {
  as_of: string;
  enabled: boolean;
  target_fiscal_year: number | null;
  annual_window: {
    in_window: boolean;
    target_fiscal_year: number | null;
    window_start: string;
    window_end: string;
    next_window_start: string;
    next_window_target_fiscal_year: number | null;
    message: string;
  };
  review_gate: {
    ready: boolean;
    active_review_items: number;
    saved_unapplied_reviews: number;
    review_queue_items: number;
    resolved_reviews: number;
    blocking_reasons: string[];
    algorithm_audit?: {
      exists: boolean;
      generated_at_utc: string;
      age_days: number | null;
      max_age_days: number;
      stale: boolean;
    };
  };
  company_year_roll_forward: {
    target_fiscal_year: number | null;
    existing_rows: number;
    planned_rows: number;
    planned_company_year_ids: string[];
  };
  sources: {
    total: number;
    enabled: number;
    planned: number;
  };
};

export type StockMonthlyStatus = {
  enabled: boolean;
  provider: string;
  cadence: string;
  as_of: string;
  run_day_of_month: number;
  due: boolean;
  target_month: string;
  latest_month: string;
  last_run_at_utc: string;
  last_status: string;
  last_successful_month: string;
  last_error_count: number;
  last_ignored_listing_error_count?: number;
  price_rows: number;
  total_securities: number;
  enabled_securities: number;
  output_path: string;
  security_master_path: string;
  message: string;
};

export type CompanyOption = {
  id: string;
  name: string;
  label: string;
};

export type FieldOption = {
  id: string;
  name: string;
  category: string;
  unit: string;
  label: string;
};

export type FieldDefinitionRow = {
  field_id: string;
  field_name_ja: string;
  category: string;
  category_label?: string;
  target_unit: string;
  data_scope_required: string;
  period_type: string;
  preferred_method: string;
  xbrl_tag_candidates: string;
  context_filters: string;
  section_keywords: string;
  synonyms_ja: string;
  calculation_formula: string;
  validation_rule_ids: string;
  review_threshold: string;
  notes: string;
};

export type FieldDefinitionResult = {
  path: string;
  rows: FieldDefinitionRow[];
  columns: string[];
  editable_columns: string[];
  categories: Array<{ id: string; label: string }>;
  total: number;
  all_total: number;
};

export type FieldDefinitionUpdateResult = {
  path: string;
  field: FieldDefinitionRow;
  changed_columns: string[];
  backup_path: string;
  xlsx_written: string;
};

export type FieldPreset = {
  id: string;
  name: string;
  fields: string[];
};

export type Options = {
  companies: CompanyOption[];
  years: string[];
  period_types: string[];
  fields: FieldOption[];
  default_result_fields: string[];
  field_presets: FieldPreset[];
};

export type StockOptions = {
  companies: CompanyOption[];
  months: string[];
  fields: FieldOption[];
  default_result_fields: string[];
  field_presets: FieldPreset[];
};

export type FactbookStatus = {
  enabled: boolean;
  cadence: string;
  as_of: string;
  source_count: number;
  enabled_source_count: number;
  order_rows: number;
  parsed_order_rows: number;
  source_documents: number;
  unsupported_documents: number;
  latest_fiscal_year: string;
  last_run_at_utc: string;
  last_status: string;
  last_error_count: number;
  output_path: string;
  source_document_path: string;
  message: string;
};

export type FactbookValidationSummary = {
  status?: string;
  validated_at_utc?: string;
  rows?: number;
  comparable_rows?: number;
  incomplete_rows?: number;
  pending_rows?: number;
  status_counts?: Record<string, number>;
  by_status?: Record<string, number>;
  by_category_type?: Record<string, number>;
  by_source_metric_id?: Record<string, number>;
  by_status_category?: Record<string, Record<string, number>>;
  top_no_mapping_categories?: Row[];
  top_missing_yuho_fields?: Row[];
  pending_samples?: Row[];
  output_path?: string;
  pending_output_path?: string;
  output_exists?: boolean;
  pending_output_exists?: boolean;
};

export type FactbookOptions = {
  companies: CompanyOption[];
  years: string[];
  category_types: string[];
  fields: FieldOption[];
  default_result_fields: string[];
  field_presets: FieldPreset[];
};

export type ReviewTarget = {
  company: string;
  fiscal_year: string;
  field_id: string;
};

export type CellDetail = {
  company_year_id: string;
  company_id: string;
  fiscal_year: string;
  field_id: string;
  field_name_ja: string;
  unit: string;
  data_scope_required: string;
  preferred_method: string;
  current_value: string;
  is_blank: boolean;
  status: string;
  status_label: string;
  summary: string;
  next_action: string;
  has_source_audit: boolean;
  has_review_candidate: boolean;
  failure_reason: string;
  current: Row;
  review_state: Row;
  candidates: Row[];
  mapping_state: Row;
  similar_scope_counts: Record<string, { count: number; samples: Row[] }>;
  actions_available: Record<string, boolean>;
  wide_row: Row;
  audit_rows: Row[];
  review_rows: Row[];
  resolved_rows: Row[];
  source_chain: {
    status: string;
    fact_resolution: Row;
    corroborations: Row[];
    mappings: Row[];
    observed_items: Row[];
    review_decisions: Row[];
  };
};

export type AlgorithmAuditResult = {
  generated_at_utc: string;
  bundle_dir: string;
  summary: Record<string, unknown>;
  files: Array<{ file: string; source?: string; description: string; bytes: number }>;
  prompt: string;
  prompt_path: string;
  readme_path: string;
};

export type RegressionSummary = {
  status?: string;
  pass?: boolean;
  mode?: string;
  generated_at_utc?: string;
  golden_cell_count?: number;
  gated_golden_count?: number;
  negative_golden_count?: number;
  mismatch_count?: number;
  missing_in_actual_count?: number;
  value_mismatch_count?: number;
  negative_golden_violations?: number;
};

export type GoldenSummary = {
  status?: string;
  golden_cell_count: number;
  negative_golden_count: number;
  by_origin: Record<string, number>;
  latest_decided_at_utc?: string;
};

export type CoreCoverageCell = {
  filled_years: number;
  total_years: number;
  blank_years: number[];
  excluded_years: number[];
  recoverable_years: number[];
};

export type CoreCoverageFieldSummary = {
  filled: number;
  total: number;
  rate: number;
  recoverable: number;
};

export type CoreCoverageResponse = {
  fields: { field_id: string; field_name_ja: string }[];
  companies: string[];
  matrix: Record<string, Record<string, CoreCoverageCell>>;
  summary: Record<string, CoreCoverageFieldSummary>;
};

export type CorroborationSummary = {
  status?: string;
  cells_total?: number;
  corroborated_2plus?: number;
  corroborated_1?: number;
  corroborated_0?: number;
  conflicts?: number;
  auto_accepted_with_zero_corroboration?: number;
  extraction_method_counts?: Record<string, number>;
  validation_rule_status_counts?: Record<string, Record<string, number>>;
  notes?: string[];
};

export type MappingProposal = {
  mapping_id: string;
  action: string;
  status: string;
  decided_by: string;
  decided_by_kind: string;
  confidence: number | null;
  rationale: string;
  new_concept_proposal: { concept_name_ja?: string; category?: string; definition_ja?: string } | null;
  observed_item: {
    observed_item_id: string;
    item_kind: string;
    element_id: string;
    element_local_name: string;
    label_ja: string;
    normalized_scope: string;
    unit: string;
    taxonomy_kind: string;
    section_name: string;
    sample_values: Record<string, unknown>;
  };
  concept: { concept_id: string; concept_name_ja: string; category: string; data_scope: string; target_unit: string } | null;
  corroboration?: {
    overlap_count: number;
    match_count: number;
    match_rate: number;
    verdict: 'corroborated' | 'conflicts' | 'unverifiable' | 'weak';
    examples: { company_year_id: string; element_value: number; concept_value: number; matched: boolean }[];
  };
};

export const CORROBORATION_VERDICT_LABELS: Record<string, string> = {
  corroborated: '数値一致 ✓ 承認して安全',
  conflicts: '数値不一致 ✗ 別物の可能性大（却下推奨）',
  unverifiable: '照合不能（概念側に既存値なし）',
  weak: '部分一致（要確認）',
};

export type MappingProposalsResult = {
  total: number;
  action_counts: Record<string, number>;
  proposals: MappingProposal[];
};

export type ConceptRow = {
  concept_id: string;
  concept_name_ja: string;
  category: string;
  data_scope: string;
  target_unit: string;
  period_type: string;
  definition_ja: string;
  calculation_formula: string;
  status: string;
  merged_into_concept_id: string;
  mapping_count: number;
  confirmed_mapping_count: number;
  proposed_mapping_count: number;
};

export type ConceptListResult = {
  page: number;
  page_size: number;
  total: number;
  rows: ConceptRow[];
  status_counts: Record<string, number>;
};

export type ReconciliationGroup = {
  group_id: string;
  rule_id: string;
  item_count: number;
  company_year_count: number;
  field_count: number;
  sample_rows: Row[];
};

export type ReconciliationGroupsResult = {
  total: number;
  groups: ReconciliationGroup[];
};

export type AlgorithmAuditFinding = {
  finding_id: string;
  kind: string;
  severity: 'high' | 'medium' | 'low' | 'info';
  target: string;
  evidence: Record<string, unknown>;
  suggested_action: string;
};

export type AlgorithmAuditFindingsResult = {
  status?: string;
  generated_at_utc?: string;
  summary?: { total: number; by_kind: Record<string, number>; by_severity: Record<string, number> };
  findings?: AlgorithmAuditFinding[];
};

export type ChartData = {
  rows: Row[];
  columns: string[];
  total: number;
  omitted_rows: number;
  fields: FieldOption[];
  companies: CompanyOption[];
  years: string[];
  sources?: SourceSummary[];
};

export type AnalysisTableData = {
  rows: Row[];
  columns: string[];
  labels: Record<string, string>;
  fieldName: string;
  unit: string;
};

export type SourceSummary = {
  company_year_id: string;
  company_name: string;
  period: string;
  field_id: string;
  field_name: string;
  value: string;
  unit: string;
  data_scope: string;
  source_doc_id: string;
  source_file: string;
  source_heading: string;
  source_quote: string;
  extraction_method: string;
  confidence: string;
};

export type ChartKind = 'line' | 'bar' | 'combo' | 'scatter';
export type ChartMode = 'trend' | 'company';
export type ChartSource = 'financial' | 'stock' | 'factbook_orders';
export type ChartViewMode = 'chart' | 'table';
export type SeriesRenderKind = 'line' | 'bar';
export type ExportPresetId = 'wide' | 'half' | 'standard' | 'panorama';
export type ExportBackground = 'white' | 'transparent';
export type ExportMarginPreset = 'compact' | 'standard' | 'wide';
export type ExportFontScale = 'small' | 'standard' | 'large';
export type LegendPosition = 'none' | 'top' | 'bottom' | 'right';
export type DesignPreset = 'sharp' | 'report' | 'minimal';

export type ChartSeries = {
  key: string;
  label: string;
  fieldId: string;
  fieldName: string;
  unit: string;
  companyName?: string;
};

export type SeriesStyle = {
  color?: string;
  strokeWidth?: number;
  strokeDasharray?: string;
  renderAs?: SeriesRenderKind;
};

export type ExportSettings = {
  presetId: ExportPresetId;
  width: number;
  height: number;
  pixelRatio: 1 | 2 | 3;
  background: ExportBackground;
  marginPreset: ExportMarginPreset;
  fontScale: ExportFontScale;
  legendPosition: LegendPosition;
  directLineLabels: boolean;
  designPreset: DesignPreset;
};

export type ChartRenderOptions = {
  height: number;
  exportMode: boolean;
  exportSettings: ExportSettings;
  axisDomains?: {
    left?: [number, number];
    right?: [number, number];
    x?: [number, number];
    y?: [number, number];
  };
};
