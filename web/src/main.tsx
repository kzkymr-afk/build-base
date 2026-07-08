import React from 'react';
import { createRoot } from 'react-dom/client';
import { toPng, toSvg } from 'html-to-image';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import { api } from './api';
import { DataTable as BaseDataTable } from './components/DataTable';
import { Empty, FilterBar, InlineError, MarkdownBlock, Pager } from './components/common';
import { TermTooltip } from './components/TermTooltip';
import { t } from './terminology';
import {
  APP_VERSION_FALLBACK,
  COLLAPSED_TEXT_COLUMNS,
  REVIEW_CATEGORY_ORDER,
  baseColumnLabels,
  baseColumns,
  resultHiddenColumns,
  tabGroups,
  tabs
} from './constants';
import {
  CORROBORATION_VERDICT_LABELS,
  type AlgorithmAuditFinding,
  type AlgorithmAuditFindingsResult,
  type AlgorithmAuditResult,
  type AnalysisTableData,
  type AutomationStatus,
  type CellDetail,
  type ChartData,
  type ChartKind,
  type ChartMode,
  type ChartRenderOptions,
  type ChartSeries,
  type ChartSource,
  type ChartViewMode,
  type CompanyOption,
  type ConceptListResult,
  type ConceptRow,
  type CoreCoverageCell,
  type CoreCoverageResponse,
  type CorroborationSummary,
  type DesignPreset,
  type ExportBackground,
  type ExportFontScale,
  type ExportMarginPreset,
  type ExportPresetId,
  type ExportSettings,
  type FactbookOptions,
  type FactbookStatus,
  type FactbookValidationSummary,
  type FieldDefinitionResult,
  type FieldDefinitionRow,
  type FieldDefinitionUpdateResult,
  type FieldOption,
  type GoldenSummary,
  type Job,
  type LegendPosition,
  type MappingProposal,
  type MappingProposalsResult,
  type Options,
  type Page,
  type ReconciliationGroupsResult,
  type RegressionSummary,
  type ReviewTarget,
  type Row,
  type SeriesRenderKind,
  type SeriesStyle,
  type SourceSummary,
  type Status,
  type StockMonthlyStatus,
  type StockOptions,
  type WidePage
} from './types';
import './styles.css';

const exportSizePresets = [
  { id: 'wide', label: '16:9 全幅', width: 1280, height: 720 },
  { id: 'half', label: '16:9 半幅', width: 760, height: 428 },
  { id: 'standard', label: '4:3', width: 960, height: 720 },
  { id: 'panorama', label: '横長', width: 1400, height: 560 },
] as const;

const linePatternOptions = [
  { id: 'solid', label: '実線', dasharray: '' },
  { id: 'dash', label: '破線', dasharray: '7 5' },
  { id: 'dot', label: '点線', dasharray: '2 4' },
] as const;

const defaultExportSettings: ExportSettings = {
  presetId: 'wide',
  width: 1280,
  height: 720,
  pixelRatio: 2,
  background: 'white',
  marginPreset: 'standard',
  fontScale: 'standard',
  legendPosition: 'bottom',
  directLineLabels: false,
  designPreset: 'sharp',
};

const exportMarginPresets: Record<ExportMarginPreset, { top: number; right: number; bottom: number; left: number }> = {
  compact: { top: 10, right: 16, bottom: 4, left: 4 },
  standard: { top: 18, right: 28, bottom: 8, left: 8 },
  wide: { top: 30, right: 44, bottom: 18, left: 18 },
};

const exportFontScales: Record<ExportFontScale, { tick: number; legend: number; label: number; stat: number }> = {
  small: { tick: 11, legend: 11, label: 10, stat: 14 },
  standard: { tick: 13, legend: 13, label: 12, stat: 16 },
  large: { tick: 16, legend: 15, label: 14, stat: 20 },
};

const designPresets: Record<DesignPreset, {
  label: string;
  grid: string;
  axis: string;
  tick: string;
  text: string;
  background: string;
}> = {
  sharp: {
    label: 'Sharp',
    grid: '#e8edf3',
    axis: '#bcc8d4',
    tick: '#334155',
    text: '#111827',
    background: '#ffffff',
  },
  report: {
    label: 'Report',
    grid: '#edf1f5',
    axis: '#cbd5df',
    tick: '#4b5f6d',
    text: '#1f2937',
    background: '#ffffff',
  },
  minimal: {
    label: 'Minimal',
    grid: '#f2f5f8',
    axis: '#d8e0e8',
    tick: '#475569',
    text: '#0f172a',
    background: '#ffffff',
  },
};


const reviewColumnLabels: Record<string, string> = {
  review_saved: '保存',
  applied_status: '反映状態',
  applied_value: '反映値',
  applied_at: '反映日時',
  reviewed_at: '保存日時',
  review_decision: '判定',
  corrected_value: '修正値',
  extracted_value: '抽出値',
  field_name_ja: '項目',
  reviewer_note: 'メモ',
  company_name_ja: '会社名',
  review_category: '分類',
  review_category_label: '分類'
};

const reviewListColumns = [
  'review_category_label',
  'review_saved',
  'company_name_ja',
  'fiscal_year',
  'field_name_ja',
  'extracted_value'
];

const fieldDefinitionColumnLabels: Record<string, string> = {
  field_id: '項目ID',
  field_name_ja: '項目名',
  category: 'ジャンル',
  category_label: 'ジャンル',
  target_unit: '単位',
  data_scope_required: 'スコープ',
  period_type: '期間',
  preferred_method: '抽出方法',
  synonyms_ja: '同義語',
  xbrl_tag_candidates: '有報タグ候補',
  context_filters: 'コンテキスト',
  section_keywords: 'セクション語',
  calculation_formula: '計算式',
  validation_rule_ids: '検算',
  review_threshold: '閾値',
  notes: 'メモ'
};

const fieldDefinitionListColumns = [
  'field_name_ja',
  'category_label',
  'target_unit',
  'preferred_method'
];


const stockColumnLabels: Record<string, string> = {
  listed_company_id: '上場会社ID',
  operating_company_id: '会社ID',
  operating_company_name: '会社名',
  stock_code: '証券コード',
  ticker: 'ティッカー',
  exchange: '市場',
  date: '月末日',
  month: '月',
  fiscal_year: '年度',
  open: '始値',
  high: '高値',
  low: '安値',
  close: '終値',
  adjusted_close: '調整後終値',
  volume: '出来高',
  monthly_return: '月次リターン',
  monthly_return_pct: '月次リターン%',
  dividend: '配当',
  split_factor: '分割係数',
  shares_outstanding: '発行済株式数',
  market_cap: '時価総額',
  currency: '通貨',
  source: '取得元',
  source_symbol: '取得シンボル',
  fetched_at_utc: '取得日時'
};

const stockListColumns = [
  'operating_company_name',
  'month',
  'adjusted_close',
  'monthly_return_pct',
  'volume',
  'market_cap',
  'source'
];

const factbookOrderColumnLabels: Record<string, string> = {
  company_id: '会社ID',
  company_name: '会社名',
  fiscal_year: '年度',
  fiscal_year_end: '決算日',
  period_type: '期間種別',
  period_label: '期間',
  source_company_id: '資料会社ID',
  source_doc_type: '資料種別',
  source_dataset_id: 'データセット',
  source_metric_id: '指標',
  category_type: '分類種別',
  scope: 'スコープ',
  business_scope: '事業範囲',
  use_category_raw: '元分類',
  use_category_normalized: '標準分類',
  use_category_label: '表示分類',
  order_amount: '受注高',
  unit: '単位',
  amount_million_yen: '百万円換算',
  source_url: '元URL',
  source_page: '掲載ページ',
  source_table_title: '表題',
  source_quote: '引用',
  source_file: '保存ファイル',
  extraction_status: '抽出状態',
  fetched_at_utc: '取得日時'
};

const factbookOrderListColumns = [
  'company_name',
  'fiscal_year',
  'category_type',
  'use_category_label',
  'business_scope',
  'order_amount',
  'unit',
  'source_doc_type',
  'source_quote'
];

const factbookDocumentColumnLabels: Record<string, string> = {
  source_dataset_id: 'データセット',
  company_id: '会社ID',
  company_name: '会社名',
  source_doc_type: '資料種別',
  source_metric_id: '指標',
  category_type: '分類種別',
  fiscal_year: '年度',
  period_type: '期間種別',
  period_label: '期間',
  title: '資料名',
  url: 'URL',
  file_name: 'ファイル',
  file_ext: '形式',
  parser_status: '解析状態',
  note: 'メモ',
  discovered_at_utc: '発見日時'
};

const factbookDocumentListColumns = [
  'company_name',
  'fiscal_year',
  'period_type',
  'source_doc_type',
  'title',
  'file_ext',
  'parser_status'
];

const factbookValidationColumnLabels: Record<string, string> = {
  validation_status: '状態',
  validation_message: '理由',
  source_metric_id: '指標',
  category_type: '分類種別',
  use_category_normalized: '標準分類',
  use_category_label: '表示分類',
  company_name: '会社名',
  fiscal_year: '年度',
  factbook_amount_million_yen: 'FB値(百万円)',
  yuho_field_id: '有報項目ID',
  yuho_field_name: '有報項目',
  yuho_value_million_yen: '有報値(百万円)',
  source_quote: '引用',
  count: '件数'
};

const auditListColumns = [
  'value',
  'unit_normalized',
  'data_scope',
  'source_heading',
  'source_quote',
  'extraction_method',
  'confidence'
];

function onlyExistingColumns(columns: string[], desired: string[]): string[] {
  return desired.filter((column) => columns.includes(column));
}

function appliedStatusLabel(status: unknown): string {
  const value = String(status || '').trim();
  if (value === 'applied') return '反映済み';
  if (value === 'rejected') return '除外済み';
  if (value === 'not_applicable') return '対象外';
  if (value === 'not_exported') return '未出力';
  if (value === 'not_found') return '対象なし';
  return '未反映';
}

function reviewSavedLabel(value: unknown): string {
  return String(value || '') === 'yes' ? '保存済み' : '未保存';
}

function formatConfidence(value: unknown): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return '-';
  if (num >= 0.9) return '高';
  if (num >= 0.7) return '中';
  return '低';
}

function renderClampedText(value: unknown, className = '') {
  const text = String(value ?? '');
  return <span className={`clamped-cell ${className}`.trim()} title={text}>{text}</span>;
}

function renderCellValue(column: string, value: unknown) {
  if (column === 'review_category_label') {
    return <span className="pill pill-info">{String(value || '未分類')}</span>;
  }
  if (column === 'review_saved') {
    const saved = String(value || '') === 'yes';
    return <span className={`pill ${saved ? 'pill-ok' : 'pill-muted'}`}>{reviewSavedLabel(value)}</span>;
  }
  if (column === 'applied_status') {
    const status = String(value || '').trim();
    const ok = status === 'applied' || status === 'rejected';
    return <span className={`pill ${ok ? 'pill-ok' : 'pill-warn'}`}>{appliedStatusLabel(value)}</span>;
  }
  if (COLLAPSED_TEXT_COLUMNS.has(column)) {
    return renderClampedText(value);
  }
  if (Array.isArray(value) || (typeof value === 'object' && value !== null)) {
    return renderClampedText(JSON.stringify(value));
  }
  return String(value ?? '');
}

function useOptions() {
  const [options, setOptions] = React.useState<Options | null>(null);
  const [error, setError] = React.useState('');

  const refresh = React.useCallback(() => {
    api<Options>('/api/options').then(setOptions).catch((err) => setError(String(err)));
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  const fieldLabels = React.useMemo(() => {
    const labels: Record<string, string> = { ...baseColumnLabels };
    for (const field of options?.fields || []) {
      labels[field.id] = field.name || field.id;
    }
    return labels;
  }, [options]);

  return { options, fieldLabels, error, refresh };
}

function useStockOptions() {
  const [options, setOptions] = React.useState<StockOptions | null>(null);
  const [error, setError] = React.useState('');

  const refresh = React.useCallback(() => {
    api<StockOptions>('/api/market/stock/options').then(setOptions).catch((err) => setError(String(err)));
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  return { options, error, refresh };
}

function useFactbookOptions() {
  const [options, setOptions] = React.useState<FactbookOptions | null>(null);
  const [error, setError] = React.useState('');

  const refresh = React.useCallback(() => {
    api<FactbookOptions>('/api/company-factbooks/options').then(setOptions).catch((err) => setError(String(err)));
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  return { options, error, refresh };
}

type ResultsFilterTarget = { company: string; fiscal_year?: string };

function App() {
  const [tab, setTab] = React.useState<(typeof tabs)[number][0]>('home');
  const [theme, setTheme] = React.useState<'dark' | 'light'>(() => {
    const saved = window.localStorage.getItem('buildbase-theme');
    return saved === 'light' ? 'light' : 'dark';
  });
  const [status, setStatus] = React.useState<Status | null>(null);
  const [job, setJob] = React.useState<Job | null>(null);
  const [dataRefreshToken, setDataRefreshToken] = React.useState(0);
  const [error, setError] = React.useState('');
  const [auditTarget, setAuditTarget] = React.useState<{ company_year_id: string; field_id: string } | null>(null);
  const [reviewTarget, setReviewTarget] = React.useState<ReviewTarget | null>(null);
  const [resultsFilterTarget, setResultsFilterTarget] = React.useState<ResultsFilterTarget | null>(null);
  const completedJobRef = React.useRef('');

  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem('buildbase-theme', theme);
  }, [theme]);

  const refreshStatus = React.useCallback(() => {
    api<Status>('/api/status').then(setStatus).catch((err) => setError(String(err)));
  }, []);

  const refreshJob = React.useCallback(() => {
    api<Job>('/api/jobs/current').then(setJob).catch((err) => setError(String(err)));
  }, []);

  React.useEffect(() => {
    refreshStatus();
    refreshJob();
  }, [refreshStatus, refreshJob]);

  React.useEffect(() => {
    if (job?.status !== 'running') return;
    const timer = window.setInterval(refreshJob, 1500);
    return () => window.clearInterval(timer);
  }, [job?.status, refreshJob]);

  React.useEffect(() => {
    if (!job?.id || job.status === 'running' || completedJobRef.current === job.id) return;
    completedJobRef.current = job.id;
    refreshStatus();
    if (job.status === 'succeeded') {
      setDataRefreshToken((value) => value + 1);
    }
  }, [job?.id, job?.status, refreshStatus]);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">B</span>
          <div>
            <strong>BuildBase</strong>
            <small>Construction Data Workbench</small>
          </div>
        </div>
        <nav className="nav-groups">
          {tabGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <span>{group.label}</span>
              {group.items.map((itemKey) => {
                const tabItem = tabs.find(([key]) => key === itemKey);
                if (!tabItem) return null;
                const [key, label] = tabItem;
                return (
                  <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>
                    {label}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="status-box">
          <span className={`dot ${job?.status === 'running' ? 'running' : job?.status === 'failed' ? 'failed' : 'ok'}`} />
          <span>{job?.status || 'idle'}</span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <h1>{tabs.find(([key]) => key === tab)?.[1]}</h1>
            <p>{status?.project_root || 'プロジェクト状態を読み込み中'}</p>
          </div>
          <div className="topbar-actions">
            <button className="ghost theme-toggle" onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}>
              {theme === 'dark' ? 'Light' : 'Dark'}
            </button>
            <button className="ghost" onClick={() => { refreshStatus(); refreshJob(); }}>
              全体再読込
            </button>
          </div>
        </header>
        {error && (
          <div className="alert">
            <span>{error}</span>
            <button onClick={() => setError('')}>閉じる</button>
          </div>
        )}
        {tab === 'home' && (
          <HomeDashboardPanel
            status={status}
            job={job}
            onJob={setJob}
            onError={setError}
            refreshToken={dataRefreshToken}
            onNavigateToResults={(target) => { setResultsFilterTarget(target); setTab('results'); }}
          />
        )}
        {tab === 'run' && <RunPanel job={job} onJob={setJob} onError={setError} onRefreshStatus={refreshStatus} status={status} />}
        {tab === 'results' && (
          <ResultsPanel
            refreshToken={dataRefreshToken}
            job={job}
            onJob={setJob}
            onAudit={(target) => { setAuditTarget(target); setTab('audit'); }}
            onReview={(target) => { setReviewTarget(target); setTab('review'); }}
            initialFilter={resultsFilterTarget}
          />
        )}
        {tab === 'fields' && <FieldAdminPanel onUpdated={() => setDataRefreshToken((value) => value + 1)} />}
        {tab === 'concepts' && <ConceptManagementPanel onError={setError} refreshToken={dataRefreshToken} />}
        {tab === 'stocks' && <StocksPanel refreshToken={dataRefreshToken} />}
        {tab === 'factbooks' && <FactbooksPanel refreshToken={dataRefreshToken} job={job} onJob={setJob} onError={setError} />}
        {tab === 'charts' && <ChartsPanel refreshToken={dataRefreshToken} />}
        {tab === 'audit' && <AuditPanel initialTarget={auditTarget} refreshToken={dataRefreshToken} />}
        {tab === 'review' && <ReviewPanel initialTarget={reviewTarget} job={job} onJob={setJob} onError={setError} refreshToken={dataRefreshToken} />}
        {tab === 'reconciliation' && <ReconciliationPanel onError={setError} refreshToken={dataRefreshToken} />}
        {tab === 'mapping_review' && <MappingReviewPanel onError={setError} refreshToken={dataRefreshToken} />}
        {tab === 'algorithm_audit_findings' && <AlgorithmAuditFindingsPanel onError={setError} refreshToken={dataRefreshToken} />}
        {tab === 'ai' && <AiPanel onError={setError} />}
        {tab === 'report' && <ReportPanel status={status} refreshToken={dataRefreshToken} job={job} onJob={setJob} onError={setError} />}
      </main>
      <div className="version-badge" aria-label="アプリバージョン">
        v{status?.app_version || APP_VERSION_FALLBACK}
      </div>
    </div>
  );
}

function HomeDashboardPanel({
  status,
  job,
  onJob,
  onError,
  refreshToken,
  onNavigateToResults
}: {
  status: Status | null;
  job: Job | null;
  onJob: (job: Job) => void;
  onError: (message: string) => void;
  refreshToken: number;
  onNavigateToResults: (target: ResultsFilterTarget) => void;
}) {
  const [regression, setRegression] = React.useState<RegressionSummary | null>(null);
  const [goldenSummary, setGoldenSummary] = React.useState<GoldenSummary | null>(null);
  const [automation, setAutomation] = React.useState<AutomationStatus | null>(null);
  const [error, setError] = React.useState('');
  const jobRunning = job?.status === 'running';

  const refresh = React.useCallback(() => {
    api<RegressionSummary>('/api/regression/summary').then(setRegression).catch((err) => setError(String(err)));
    api<GoldenSummary>('/api/golden/summary').then(setGoldenSummary).catch((err) => setError(String(err)));
    api<AutomationStatus>('/api/automation/status').then(setAutomation).catch((err) => setError(String(err)));
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh, refreshToken]);

  async function startJob(path: string, body?: Record<string, unknown>) {
    if (jobRunning) {
      onError('ジョブ実行中です。作業ログの完了を待ってから次の操作を実行してください。');
      return;
    }
    try {
      const next = await api<Job>(path, {
        method: 'POST',
        body: body ? JSON.stringify(body) : undefined
      });
      onJob(next);
      window.setTimeout(refresh, 1200);
    } catch (err) {
      onError(String(err));
    }
  }

  const regressionPass = regression?.pass === true;
  const regressionBuilt = regression?.status !== 'not_built' && regression != null;
  const files = status?.files || {};

  return (
    <section className="stack dashboard-home">
      <CoreCoverageMatrixPanel refreshToken={refreshToken} onNavigateToResults={onNavigateToResults} />
      <div className="dashboard-grid">
        <div className="panel metric-panel">
          <small><TermTooltip label={t('golden')} explain="人がレビューして正しいと確定した数値の件数です。この値は自動処理で上書きされません。" /></small>
          <strong>{goldenSummary?.golden_cell_count ?? '-'}</strong>
          <span>除外扱い {goldenSummary?.negative_golden_count ?? '-'}</span>
        </div>
        <div className="panel metric-panel">
          <small>Regression</small>
          <strong className={regressionPass ? 'text-ok' : regressionBuilt ? 'text-warn' : ''}>
            {regressionBuilt ? regressionPass ? 'PASS' : 'CHECK' : '未実行'}
          </strong>
          <span>{regression?.mode || 'light/full'}</span>
        </div>
        <div className="panel metric-panel">
          <small>レビュー残</small>
          <strong>{automation?.review_gate.active_review_items ?? '-'}</strong>
          <span>未反映 {automation?.review_gate.saved_unapplied_reviews ?? '-'}</span>
        </div>
        <div className="panel metric-panel">
          <small>監査</small>
          <strong>{files.algorithm_audit ? 'あり' : '未生成'}</strong>
          <span>{status?.algorithm_audit_generated_at_utc || '-'}</span>
        </div>
      </div>
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>品質ゲート</h2>
            <p className="muted">「確定値」の凍結保存と、既存の確定値が崩れていないかの回帰チェックをここから実行します。</p>
          </div>
          <span className={`badge ${regressionPass ? 'succeeded' : 'pending'}`}>
            {regressionPass ? '回帰OK' : '確認待ち'}
          </span>
        </div>
        {error && <InlineError message={error} />}
        <div className="toolbar">
          <button onClick={() => startJob('/api/jobs/regression-check', { mode: 'light' })} disabled={jobRunning}>回帰チェック light</button>
          <button className="secondary" onClick={() => startJob('/api/jobs/regression-check', { mode: 'full' })} disabled={jobRunning}>回帰チェック full</button>
          <button className="ghost" onClick={() => startJob('/api/jobs/golden-freeze')} disabled={jobRunning}>確定値として凍結保存</button>
          <button className="ghost" onClick={refresh}>状態再読込</button>
        </div>
        <div className="detail-grid">
          <div>
            <small>mismatch</small>
            <strong>{regression?.mismatch_count ?? '-'}</strong>
          </div>
          <div>
            <small>missing</small>
            <strong>{regression?.missing_in_actual_count ?? '-'}</strong>
          </div>
          <div>
            <small>negative violation</small>
            <strong>{regression?.negative_golden_violations ?? '-'}</strong>
          </div>
          <div>
            <small>generated</small>
            <strong>{regression?.generated_at_utc || '-'}</strong>
          </div>
        </div>
      </div>
      <CorroborationSummaryPanel job={job} onJob={onJob} onError={onError} jobRunning={jobRunning} refreshToken={refreshToken} />
      <ReviewTerminal job={job} />
    </section>
  );
}

function coverageCellStatus(cell: CoreCoverageCell): 'full' | 'partial' | 'empty' | 'excluded' {
  if (cell.total_years === 0) return 'excluded';
  if (cell.filled_years >= cell.total_years) return 'full';
  if (cell.filled_years > 0) return 'partial';
  return 'empty';
}

function CoreCoverageMatrixPanel({
  refreshToken,
  onNavigateToResults
}: {
  refreshToken: number;
  onNavigateToResults: (target: ResultsFilterTarget) => void;
}) {
  const [coverage, setCoverage] = React.useState<CoreCoverageResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [localError, setLocalError] = React.useState('');

  const refresh = React.useCallback(() => {
    setLoading(true);
    api<CoreCoverageResponse>('/api/coverage/core')
      .then((result) => { setCoverage(result); setLocalError(''); })
      .catch((err) => setLocalError(String(err)))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh, refreshToken]);

  function handleCellClick(companyId: string, cell: CoreCoverageCell) {
    const yearForJump = cell.blank_years[0] ?? cell.recoverable_years[0];
    onNavigateToResults({ company: companyId, fiscal_year: yearForJump ? String(yearForJump) : undefined });
  }

  return (
    <div className="panel core-coverage-panel">
      <div className="panel-head">
        <div>
          <h2>主要項目の充足マップ</h2>
          <p className="muted">会社×主要項目の入力状況です。空欄セルをクリックすると該当会社の結果一覧に移動します。</p>
        </div>
        <button className="ghost" onClick={refresh} disabled={loading}>再読込</button>
      </div>
      {localError && <InlineError message={localError} />}
      {!coverage ? (
        <Empty message={loading ? '読み込み中です。' : 'データがありません。'} />
      ) : (
        <>
          <div className="coverage-legend">
            <span className="coverage-swatch full">全年度そろっている</span>
            <span className="coverage-swatch partial">一部だけ入っている</span>
            <span className="coverage-swatch empty">ほとんど空欄</span>
            <span className="coverage-swatch excluded">対象外</span>
            <span className="coverage-swatch recoverable">自動で埋められそう</span>
          </div>
          <div className="coverage-table-scroll">
            <table className="coverage-table">
              <thead>
                <tr>
                  <th>会社</th>
                  {coverage.fields.map((field) => {
                    const summary = coverage.summary[field.field_id];
                    return (
                      <th key={field.field_id}>
                        <div className="coverage-field-header">
                          <span>{field.field_name_ja || field.field_id}</span>
                          {summary && (
                            <small>
                              {summary.filled}/{summary.total}
                              {summary.recoverable > 0 ? ` (回復候補${summary.recoverable})` : ''}
                            </small>
                          )}
                        </div>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {coverage.companies.map((companyId) => (
                  <tr key={companyId}>
                    <th scope="row">{companyId}</th>
                    {coverage.fields.map((field) => {
                      const cell = coverage.matrix[field.field_id]?.[companyId];
                      if (!cell) {
                        return <td key={field.field_id} className="coverage-cell excluded">-</td>;
                      }
                      const cellStatus = coverageCellStatus(cell);
                      const hasRecoverable = cell.recoverable_years.length > 0;
                      const clickable = cellStatus !== 'excluded';
                      return (
                        <td
                          key={field.field_id}
                          className={`coverage-cell ${cellStatus}${hasRecoverable ? ' recoverable' : ''}${clickable ? ' clickable' : ''}`}
                          onClick={clickable ? () => handleCellClick(companyId, cell) : undefined}
                          title={
                            cellStatus === 'excluded'
                              ? '対象外の会社・年度です'
                              : `充足 ${cell.filled_years}/${cell.total_years} 年度${hasRecoverable ? ` / 自動回復候補 ${cell.recoverable_years.length}年度` : ''}`
                          }
                        >
                          {cellStatus === 'excluded' ? '対象外' : `${cell.filled_years}/${cell.total_years}`}
                          {hasRecoverable && <span className="coverage-badge">自動回復</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function RunPanel({ job, onJob, onError, onRefreshStatus, status }: {
  job: Job | null;
  onJob: (job: Job) => void;
  onError: (message: string) => void;
  onRefreshStatus: () => void;
  status: Status | null;
}) {
  const [automation, setAutomation] = React.useState<AutomationStatus | null>(null);
  const [stockStatus, setStockStatus] = React.useState<StockMonthlyStatus | null>(null);
  const [automationLoading, setAutomationLoading] = React.useState(false);
  const [stockLoading, setStockLoading] = React.useState(false);
  const [fiscalYear, setFiscalYear] = React.useState('');
  const [forceAnnual, setForceAnnual] = React.useState(false);
  const jobRunning = job?.status === 'running';

  const refreshAutomation = React.useCallback((yearOverride?: string) => {
    setAutomationLoading(true);
    const params = new URLSearchParams();
    const selectedYear = yearOverride ?? fiscalYear;
    if (selectedYear.trim()) {
      params.set('fiscal_year', selectedYear.trim());
    }
    const path = `/api/automation/status${params.toString() ? `?${params}` : ''}`;
    api<AutomationStatus>(path)
      .then((next) => {
        setAutomation(next);
        if (!fiscalYear && next.target_fiscal_year) {
          setFiscalYear(String(next.target_fiscal_year));
        }
      })
      .catch((err) => onError(String(err)))
      .finally(() => setAutomationLoading(false));
  }, [fiscalYear, onError]);

  React.useEffect(() => {
    refreshAutomation();
  }, []);

  const refreshStockStatus = React.useCallback(() => {
    setStockLoading(true);
    api<StockMonthlyStatus>('/api/market/stock/status')
      .then(setStockStatus)
      .catch((err) => onError(String(err)))
      .finally(() => setStockLoading(false));
  }, [onError]);

  React.useEffect(() => {
    refreshStockStatus();
  }, [refreshStockStatus]);

  React.useEffect(() => {
    if (!job?.id || job.status === 'running') return;
    refreshStockStatus();
  }, [job?.id, job?.status, refreshStockStatus]);

  async function start(path: string) {
    if (jobRunning) {
      onError('ジョブ実行中です。作業ログの完了を待ってから次の操作を実行してください。');
      return;
    }
    try {
      const next = await api<Job>(path, { method: 'POST' });
      onJob(next);
      window.setTimeout(onRefreshStatus, 800);
    } catch (err) {
      onError(String(err));
    }
  }

  async function startAnnual(dryRun: boolean) {
    if (jobRunning) {
      onError('ジョブ実行中です。作業ログの完了を待ってから次の操作を実行してください。');
      return;
    }
    const year = fiscalYear.trim() ? Number(fiscalYear.trim()) : undefined;
    if (year !== undefined && !Number.isFinite(year)) {
      onError('年度には数値を入力してください。');
      return;
    }
    try {
      const next = await api<Job>('/api/jobs/annual-refresh', {
        method: 'POST',
        body: JSON.stringify({ fiscal_year: year, force: forceAnnual, dry_run: dryRun })
      });
      onJob(next);
      window.setTimeout(() => {
        onRefreshStatus();
        refreshAutomation(fiscalYear);
      }, 800);
    } catch (err) {
      onError(String(err));
    }
  }

  async function startStock(dryRun: boolean) {
    if (jobRunning) {
      onError('ジョブ実行中です。作業ログの完了を待ってから次の操作を実行してください。');
      return;
    }
    try {
      const next = await api<Job>('/api/jobs/stock-refresh', {
        method: 'POST',
        body: JSON.stringify({ force: true, dry_run: dryRun })
      });
      onJob(next);
      window.setTimeout(() => {
        onRefreshStatus();
        refreshStockStatus();
      }, 800);
    } catch (err) {
      onError(String(err));
    }
  }

  return (
    <section className="stack">
      <div className="toolbar">
        <button onClick={() => start('/api/jobs/run-all')} disabled={jobRunning}>完成データを更新</button>
        <button onClick={() => start('/api/jobs/reextract-with-review')} disabled={jobRunning}>保存済みレビューで再抽出</button>
        <button onClick={() => start('/api/jobs/report')} disabled={jobRunning}>レポート更新</button>
        <button onClick={() => start('/api/jobs/algorithm-audit')} disabled={jobRunning}>抽出ロジックを点検</button>
        <button onClick={() => start('/api/jobs/apply-review')} disabled={jobRunning}>レビュー結果を反映</button>
      </div>
      {jobRunning && <p className="hint action-lock">ジョブ実行中です。完了まで新しい実行操作はできません。</p>}
      <AnnualAutomationPanel
        automation={automation}
        loading={automationLoading}
        fiscalYear={fiscalYear}
        forceAnnual={forceAnnual}
        onFiscalYear={(value) => {
          setFiscalYear(value);
          refreshAutomation(value);
        }}
        onForceAnnual={setForceAnnual}
        onRefresh={() => refreshAutomation()}
        onDryRun={() => startAnnual(true)}
        onRun={() => startAnnual(false)}
        jobRunning={jobRunning}
      />
      <StockAutomationPanel
        status={stockStatus}
        loading={stockLoading}
        onRefresh={refreshStockStatus}
        onDryRun={() => startStock(true)}
        onRun={() => startStock(false)}
        jobRunning={jobRunning}
      />
      <FileHealth status={status} />
      <JobLog job={job} />
    </section>
  );
}

function AnnualAutomationPanel({
  automation,
  loading,
  fiscalYear,
  forceAnnual,
  onFiscalYear,
  onForceAnnual,
  onRefresh,
  onDryRun,
  onRun,
  jobRunning
}: {
  automation: AutomationStatus | null;
  loading: boolean;
  fiscalYear: string;
  forceAnnual: boolean;
  onFiscalYear: (value: string) => void;
  onForceAnnual: (value: boolean) => void;
  onRefresh: () => void;
  onDryRun: () => void;
  onRun: () => void;
  jobRunning: boolean;
}) {
  const ready = automation?.review_gate.ready;
  const targetYear = automation?.target_fiscal_year ?? automation?.annual_window.target_fiscal_year ?? '';
  const blocking = automation?.review_gate.blocking_reasons || [];
  const algorithmAudit = automation?.review_gate.algorithm_audit;
  return (
    <div className="panel automation-panel">
      <div className="panel-head">
        <div>
          <h2>年次自動取得</h2>
          <p className="muted">{automation?.annual_window.message || '自動化状態を読み込み中です。'}</p>
        </div>
        <span className={`badge ${ready ? 'succeeded' : 'failed'}`}>{ready ? 'レビュー完了' : 'レビュー未完了'}</span>
      </div>
      <div className="automation-grid">
        <div className="metric">
          <small>対象年度</small>
          <strong>{targetYear || '-'}</strong>
        </div>
        <div className="metric">
          <small>未対応レビュー</small>
          <strong className={ready ? 'text-ok' : 'text-warn'}>{automation?.review_gate.active_review_items ?? '-'}</strong>
        </div>
        <div className="metric">
          <small>未反映レビュー</small>
          <strong>{automation?.review_gate.saved_unapplied_reviews ?? '-'}</strong>
        </div>
        <div className="metric">
          <small>年度行追加予定</small>
          <strong>{automation?.company_year_roll_forward.planned_rows ?? '-'}</strong>
        </div>
        <div className="metric">
          <small>将来ソース</small>
          <strong>{automation ? `${automation.sources.enabled}/${automation.sources.total} active` : '-'}</strong>
        </div>
        <div className="metric">
          <small>AI監査</small>
          <strong className={algorithmAudit?.exists && !algorithmAudit?.stale ? 'text-ok' : 'text-warn'}>
            {algorithmAudit?.exists ? `${algorithmAudit.age_days ?? '-'}日` : '未生成'}
          </strong>
        </div>
      </div>
      {blocking.length > 0 && (
        <p className="hint">停止理由: {blocking.join(' / ')}</p>
      )}
      <div className="toolbar annual-toolbar">
        <label className="filter-field small">
          <span>年度</span>
          <input value={fiscalYear} onChange={(e) => onFiscalYear(e.target.value)} placeholder="2025" />
        </label>
        <label className="check-field">
          <input type="checkbox" checked={forceAnnual} onChange={(e) => onForceAnnual(e.target.checked)} />
          <span>レビュー未完了でも取得</span>
        </label>
        <button className="ghost" disabled={loading} onClick={onRefresh}>年次状態更新</button>
        <button className="secondary" onClick={onDryRun} disabled={jobRunning}>年次ドライラン</button>
        <button disabled={jobRunning || (!forceAnnual && ready === false)} onClick={onRun}>年次取得を実行</button>
      </div>
    </div>
  );
}

function StockAutomationPanel({
  status,
  loading,
  onRefresh,
  onDryRun,
  onRun,
  jobRunning
}: {
  status: StockMonthlyStatus | null;
  loading: boolean;
  onRefresh: () => void;
  onDryRun: () => void;
  onRun: () => void;
  jobRunning: boolean;
}) {
  return (
    <div className="panel automation-panel">
      <div className="panel-head">
        <div>
          <h2>株価月次取得</h2>
          <p className="muted">{status?.message || '株価取得状態を読み込み中です。'}</p>
        </div>
        <span className={`badge ${status?.due ? 'failed' : 'succeeded'}`}>{status?.due ? '更新必要' : '更新済み'}</span>
      </div>
      <div className="automation-grid">
        <div className="metric">
          <small>取得元</small>
          <strong>{status?.provider || '-'}</strong>
        </div>
        <div className="metric">
          <small>対象月</small>
          <strong>{status?.target_month || '-'}</strong>
        </div>
        <div className="metric">
          <small>最新月</small>
          <strong className={status?.due ? 'text-warn' : 'text-ok'}>{status?.latest_month || '-'}</strong>
        </div>
        <div className="metric">
          <small>月次行数</small>
          <strong>{status?.price_rows ?? '-'}</strong>
        </div>
        <div className="metric">
          <small>対象銘柄</small>
          <strong>{status ? `${status.enabled_securities}/${status.total_securities}` : '-'}</strong>
        </div>
        <div className="metric">
          <small>前回エラー</small>
          <strong className={status?.last_error_count ? 'text-warn' : 'text-ok'}>
            {status?.last_error_count ?? '-'}
            {status?.last_ignored_listing_error_count ? `（対象外${status.last_ignored_listing_error_count}）` : ''}
          </strong>
        </div>
      </div>
      <div className="toolbar annual-toolbar">
        <button className="ghost" disabled={loading} onClick={onRefresh}>株価状態更新</button>
        <button className="secondary" onClick={onDryRun} disabled={jobRunning}>株価ドライラン</button>
        <button onClick={onRun} disabled={jobRunning}>今すぐ月次株価取得</button>
      </div>
      <p className="hint">
        Webアプリ起動中は毎月{status?.run_day_of_month || 5}日以降に自動チェックし、前月末までの月次OHLC・調整後終値・出来高を保存します。
      </p>
    </div>
  );
}

function XbrlFactStorePanel({
  status,
  onRun,
  jobRunning
}: {
  status: Status | null;
  onRun: () => void;
  jobRunning: boolean;
}) {
  const files = status?.files || {};
  const manifestReady = Boolean(files.xbrl_fact_store_manifest);
  const factsReady = Boolean(files.xbrl_fact_store_facts_json);
  const contextReady = Boolean(files.xbrl_fact_store_context_json);
  const digestReady = Boolean(files.xbrl_fact_store_digest);
  const ready = manifestReady && factsReady && contextReady;
  const message = status
    ? ready ? '有報データの整理は完了しています。' : '有報データはまだ整理されていません。'
    : '状態を読み込み中です。';

  return (
    <div className="panel automation-panel">
      <div className="panel-head">
        <div>
          <h2>有報データ整理</h2>
          <p className="muted">{message}</p>
        </div>
        <span className={`badge ${ready ? 'succeeded' : 'pending'}`}>{ready ? '生成済み' : status ? '未生成' : '確認中'}</span>
      </div>
      <div className="automation-grid">
        <div className="metric">
          <small>manifest</small>
          <strong className={manifestReady ? 'text-ok' : 'text-warn'}>{manifestReady ? 'あり' : 'なし'}</strong>
        </div>
        <div className="metric">
          <small>有報の全データ</small>
          <strong className={factsReady ? 'text-ok' : 'text-warn'}>{factsReady ? 'あり' : 'なし'}</strong>
        </div>
        <div className="metric">
          <small>年度・会社情報</small>
          <strong className={contextReady ? 'text-ok' : 'text-warn'}>{contextReady ? 'あり' : 'なし'}</strong>
        </div>
        <div className="metric">
          <small>確認用メモ</small>
          <strong className={digestReady ? 'text-ok' : 'text-warn'}>{digestReady ? 'あり' : 'なし'}</strong>
        </div>
      </div>
      <div className="toolbar annual-toolbar">
        <button onClick={onRun} disabled={jobRunning}>有報データを整理</button>
      </div>
    </div>
  );
}

function FileHealth({ status }: { status: Status | null }) {
  if (!status) return <Empty message="状態を読み込み中です。" />;
  return (
    <details className="panel technical-details">
      <summary>詳細ファイル状態</summary>
      <div className="grid health-grid">
        {Object.entries(status.files).map(([key, ok]) => (
          <div className="metric" key={key}>
            <small>{key}</small>
            <strong className={ok ? 'text-ok' : 'text-warn'}>{ok ? 'あり' : 'なし'}</strong>
          </div>
        ))}
        <div className="metric">
          <small>AI bundle</small>
          <strong>{status.ai_bundle_generated_at_utc || '未生成'}</strong>
        </div>
        <div className="metric">
          <small>Algorithm audit</small>
          <strong>{status.algorithm_audit_generated_at_utc || '未生成'}</strong>
        </div>
      </div>
    </details>
  );
}

function JobLog({ job }: { job: Job | null }) {
  if (!job?.id) return <Empty message="まだジョブは実行されていません。" />;
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>{job.name}</h2>
        <span className={`badge ${job.status}`}>{job.status}</span>
      </div>
      {job.error && <p className="error-text">{job.error}</p>}
      <pre className="log">{job.logs.join('\n') || 'ログ待機中...'}</pre>
    </div>
  );
}

function ReviewTerminal({ job }: { job: Job | null }) {
  const logRef = React.useRef<HTMLPreElement | null>(null);

  React.useEffect(() => {
    const node = logRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [job?.id, job?.status, job?.logs.length]);

  return (
    <div className="panel review-terminal-panel">
      <div className="panel-head">
        <div>
          <h2>作業ログ</h2>
          <p className="muted">再取得、最終反映の進捗をここで確認できます。</p>
        </div>
        <span className={`badge ${job?.status || ''}`}>{job?.status || 'idle'}</span>
      </div>
      {job?.error && <p className="error-text">{job.error}</p>}
      <pre className="log review-terminal-log" ref={logRef}>
        {job?.id ? job.logs.join('\n') || 'ログ待機中...' : 'まだジョブは実行されていません。'}
      </pre>
    </div>
  );
}

function ResultsPanel({
  refreshToken,
  job,
  onJob,
  onAudit,
  onReview,
  initialFilter
}: {
  refreshToken: number;
  job: Job | null;
  onJob: (job: Job) => void;
  onAudit: (target: { company_year_id: string; field_id: string }) => void;
  onReview: (target: ReviewTarget) => void;
  initialFilter?: ResultsFilterTarget | null;
}) {
  const [page, setPage] = React.useState(1);
  const [company, setCompany] = React.useState('');
  const [year, setYear] = React.useState('');
  const [periodType, setPeriodType] = React.useState('annual');
  const [preset, setPreset] = React.useState('all');

  React.useEffect(() => {
    if (!initialFilter) return;
    setCompany(initialFilter.company);
    if (initialFilter.fiscal_year) {
      setYear(initialFilter.fiscal_year);
    }
    setPage(1);
  }, [initialFilter]);
  const [data, setData] = React.useState<WidePage | null>(null);
  const [dataReloadToken, setDataReloadToken] = React.useState(0);
  const [error, setError] = React.useState('');
  const [cellDetail, setCellDetail] = React.useState<CellDetail | null>(null);
  const [cellLoading, setCellLoading] = React.useState(false);
  const [cellError, setCellError] = React.useState('');
  const { options, fieldLabels, error: optionsError } = useOptions();

  React.useEffect(() => {
    if (!year && options?.years.length) {
      setYear(options.years[options.years.length - 1]);
    }
  }, [options, year]);

  const selectedFields = React.useMemo(() => {
    if (!options) return '';
    const selected = options.field_presets.find((item) => item.id === preset);
    return (selected?.fields || options.default_result_fields).join(',');
  }, [options, preset]);

  React.useEffect(() => {
    if (!options) return;
    const params = new URLSearchParams({ page: String(page), page_size: '50', company, fiscal_year: year, period_type: periodType, fields: selectedFields });
    api<WidePage>(`/api/datasets/wide?${params}`).then(setData).catch((err) => setError(String(err)));
  }, [options, page, company, year, periodType, selectedFields, refreshToken, dataReloadToken]);

  function resetPage(next: () => void) {
    setPage(1);
    setCellDetail(null);
    setCellError('');
    next();
  }

  async function loadCellDetail(companyYearId: string, fieldId: string) {
    const params = new URLSearchParams({ company_year_id: companyYearId, field_id: fieldId });
    setCellLoading(true);
    setCellError('');
    try {
      const detail = await api<CellDetail>(`/api/datasets/cell-detail?${params}`);
      setCellDetail(detail);
    } catch (err) {
      setCellError(String(err));
    } finally {
      setCellLoading(false);
    }
  }

  async function openCellDetail(row: Row, column: string) {
    if (baseColumns.has(column)) return;
    const companyYearId = String(row.company_year_id || '');
    if (!companyYearId) return;
    await loadCellDetail(companyYearId, column);
  }

  function refreshCellWorkbench() {
    setDataReloadToken((value) => value + 1);
    if (cellDetail) {
      void loadCellDetail(cellDetail.company_year_id, cellDetail.field_id);
    }
  }

  return (
    <section className="stack">
      <FilterBar>
        <label className="filter-field">
          <span>会社</span>
          <select value={company} onChange={(e) => resetPage(() => setCompany(e.target.value))}>
            <option value="">全社</option>
            {(options?.companies || []).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>
        <label className="filter-field small">
          <span>年度</span>
          <select value={year} onChange={(e) => resetPage(() => setYear(e.target.value))}>
            <option value="">全年度</option>
            {(options?.years || []).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <label className="filter-field small">
          <span>期間</span>
          <select value={periodType} onChange={(e) => resetPage(() => setPeriodType(e.target.value))}>
            {(options?.period_types || ['annual']).map((item) => (
              <option key={item} value={item}>{periodTypeLabel(item)}</option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>指標</span>
          <select value={preset} onChange={(e) => resetPage(() => setPreset(e.target.value))}>
            {(options?.field_presets || [{ id: 'core', name: '主要指標', fields: [] }]).map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
      </FilterBar>
      <p className="hint">初期表示は最新年度の全指標です。数値セルや空欄セルをクリックすると、根拠・レビュー候補・次の操作を確認できます。</p>
      {optionsError && <InlineError message={optionsError} />}
      {error && <InlineError message={error} />}
      <CellDetailPanel
        detail={cellDetail}
        loading={cellLoading}
        error={cellError}
        onAudit={onAudit}
        onReview={onReview}
        job={job}
        onJob={onJob}
        onChanged={refreshCellWorkbench}
      />
      {data ? (
        <>
          <DataTable
            data={data.rows}
            columns={data.columns.filter((column) => !resultHiddenColumns.has(column))}
            columnLabels={fieldLabels}
            markEmptyCells
            cellStatuses={data.cell_statuses}
            compact
            onCellClick={openCellDetail}
          />
          <Pager page={data.page} totalPages={data.total_pages} total={data.total} onPage={setPage} />
        </>
      ) : (
        <Empty message="結果データを読み込み中です。" />
      )}
    </section>
  );
}

function ConceptManagementPanel({ onError, refreshToken }: { onError: (message: string) => void; refreshToken: number }) {
  const [data, setData] = React.useState<ConceptListResult | null>(null);
  const [status, setStatus] = React.useState('');
  const [search, setSearch] = React.useState('');
  const [page, setPage] = React.useState(1);
  const [selectedId, setSelectedId] = React.useState('');
  const [draft, setDraft] = React.useState<Partial<ConceptRow>>({});
  const [targetId, setTargetId] = React.useState('');
  const [splitDraft, setSplitDraft] = React.useState({ concept_id: '', concept_name_ja: '', category: '', data_scope: '', target_unit: '', period_type: '', definition_ja: '' });
  const [message, setMessage] = React.useState('');

  const load = React.useCallback(() => {
    const params = new URLSearchParams({ page: String(page), page_size: '100', status, search });
    api<ConceptListResult>(`/api/concepts?${params.toString()}`)
      .then((result) => {
        setData(result);
        if (!selectedId && result.rows.length) {
          setSelectedId(result.rows[0].concept_id);
          setDraft(result.rows[0]);
        }
      })
      .catch((err) => onError(String(err)));
  }, [onError, page, search, selectedId, status]);

  React.useEffect(() => {
    load();
  }, [load, refreshToken]);

  const selected = data?.rows.find((row) => row.concept_id === selectedId) || null;

  function selectConcept(row: ConceptRow) {
    setSelectedId(row.concept_id);
    setDraft(row);
    setTargetId(row.merged_into_concept_id || '');
    setMessage('');
  }

  async function saveConcept() {
    if (!selectedId) return;
    try {
      await api(`/api/concepts/${encodeURIComponent(selectedId)}`, {
        method: 'POST',
        body: JSON.stringify({
          updates: {
            concept_name_ja: draft.concept_name_ja,
            category: draft.category,
            data_scope: draft.data_scope,
            target_unit: draft.target_unit,
            period_type: draft.period_type,
            definition_ja: draft.definition_ja,
            calculation_formula: draft.calculation_formula,
            status: draft.status,
            merged_into_concept_id: draft.merged_into_concept_id,
          },
        }),
      });
      setMessage('保存しました');
      load();
    } catch (err) {
      onError(String(err));
    }
  }

  async function mergeConcept() {
    if (!selectedId || !targetId) return;
    try {
      const result = await api<{ mappings_retargeted: number }>('/api/concepts/merge', {
        method: 'POST',
        body: JSON.stringify({ source_concept_id: selectedId, target_concept_id: targetId }),
      });
      setMessage(`統合しました: 対応付け ${result.mappings_retargeted}件を移動`);
      load();
    } catch (err) {
      onError(String(err));
    }
  }

  async function splitConcept() {
    if (!selectedId || !splitDraft.concept_name_ja) return;
    try {
      await api('/api/concepts/split', {
        method: 'POST',
        body: JSON.stringify({ source_concept_id: selectedId, new_concepts: [splitDraft] }),
      });
      setMessage('分割先の項目を作成しました');
      setSplitDraft({ concept_id: '', concept_name_ja: '', category: '', data_scope: '', target_unit: '', period_type: '', definition_ja: '' });
      load();
    } catch (err) {
      onError(String(err));
    }
  }

  return (
    <section className="stack">
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>表の項目の管理</h2>
            <p className="muted">最終的な表に出てくる「項目（{t('concept')}）」の一覧です。名称や定義の修正、同じ意味の項目の統合、項目の分割をここで行います。</p>
          </div>
          <span className="badge">total {data?.total ?? '-'}</span>
        </div>
        <div className="toolbar">
          <input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="項目ID・名称・カテゴリ検索" />
          <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
            <option value="">全status</option>
            <option value="active">active</option>
            <option value="merged">merged</option>
            <option value="retired">retired</option>
          </select>
          <button className="ghost" onClick={load}>再読込</button>
        </div>
        {message && <div className="notice">{message}</div>}
        <div className="concept-layout">
          <div className="table-wrap compact-table">
            <table>
              <thead>
                <tr>
                  <th>concept_id</th>
                  <th>名称</th>
                  <th>status</th>
                  <th>map</th>
                  <th>単位</th>
                </tr>
              </thead>
              <tbody>
                {(data?.rows || []).map((row) => (
                  <tr key={row.concept_id} className={row.concept_id === selectedId ? 'selected-row' : ''} onClick={() => selectConcept(row)}>
                    <td className="mono">{row.concept_id}</td>
                    <td>{row.concept_name_ja}</td>
                    <td><span className={`badge ${row.status === 'active' ? 'succeeded' : 'pending'}`}>{row.status}</span></td>
                    <td className="mono">{row.mapping_count}</td>
                    <td>{row.target_unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager
              page={page}
              totalPages={Math.max(1, Math.ceil((data?.total || 0) / (data?.page_size || 100)))}
              total={data?.total || 0}
              onPage={setPage}
            />
          </div>
          <div className="concept-editor">
            {selected ? (
              <>
                <h3>{selected.concept_id}</h3>
                <label>名称<input value={draft.concept_name_ja || ''} onChange={(event) => setDraft({ ...draft, concept_name_ja: event.target.value })} /></label>
                <label>カテゴリ<input value={draft.category || ''} onChange={(event) => setDraft({ ...draft, category: event.target.value })} /></label>
                <div className="inline-fields">
                  <label>scope<input value={draft.data_scope || ''} onChange={(event) => setDraft({ ...draft, data_scope: event.target.value })} /></label>
                  <label>unit<input value={draft.target_unit || ''} onChange={(event) => setDraft({ ...draft, target_unit: event.target.value })} /></label>
                </div>
                <div className="inline-fields">
                  <label>period<input value={draft.period_type || ''} onChange={(event) => setDraft({ ...draft, period_type: event.target.value })} /></label>
                  <label>status
                    <select value={draft.status || 'active'} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>
                      <option value="active">active</option>
                      <option value="merged">merged</option>
                      <option value="retired">retired</option>
                    </select>
                  </label>
                </div>
                <label>定義<textarea rows={4} value={draft.definition_ja || ''} onChange={(event) => setDraft({ ...draft, definition_ja: event.target.value })} /></label>
                <label>計算式<textarea rows={3} value={draft.calculation_formula || ''} onChange={(event) => setDraft({ ...draft, calculation_formula: event.target.value })} /></label>
                <button onClick={saveConcept}>保存</button>
                <hr />
                <h3>項目を統合</h3>
                <div className="toolbar">
                  <input value={targetId} onChange={(event) => setTargetId(event.target.value)} placeholder="統合先の項目ID" />
                  <button className="secondary" onClick={mergeConcept} disabled={!targetId || targetId === selectedId}>統合</button>
                </div>
                <h3>分割先を作成</h3>
                <input value={splitDraft.concept_id} onChange={(event) => setSplitDraft({ ...splitDraft, concept_id: event.target.value })} placeholder="新しい項目ID（空なら自動）" />
                <input value={splitDraft.concept_name_ja} onChange={(event) => setSplitDraft({ ...splitDraft, concept_name_ja: event.target.value })} placeholder="新しい項目名" />
                <div className="inline-fields">
                  <input value={splitDraft.category} onChange={(event) => setSplitDraft({ ...splitDraft, category: event.target.value })} placeholder="category" />
                  <input value={splitDraft.target_unit} onChange={(event) => setSplitDraft({ ...splitDraft, target_unit: event.target.value })} placeholder="target_unit" />
                </div>
                <textarea rows={3} value={splitDraft.definition_ja} onChange={(event) => setSplitDraft({ ...splitDraft, definition_ja: event.target.value })} placeholder="定義" />
                <button className="ghost" onClick={splitConcept} disabled={!splitDraft.concept_name_ja}>分割先作成</button>
              </>
            ) : (
              <Empty message="項目がありません" />
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function FieldAdminPanel({ onUpdated }: { onUpdated: () => void }) {
  const [category, setCategory] = React.useState('');
  const [search, setSearch] = React.useState('');
  const [data, setData] = React.useState<FieldDefinitionResult | null>(null);
  const [selected, setSelected] = React.useState<FieldDefinitionRow | null>(null);
  const [draft, setDraft] = React.useState<Partial<FieldDefinitionRow>>({});
  const [message, setMessage] = React.useState('');
  const [error, setError] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [termSynonyms, setTermSynonyms] = React.useState('');
  const [termXbrlTags, setTermXbrlTags] = React.useState('');
  const [termSections, setTermSections] = React.useState('');
  const [termNote, setTermNote] = React.useState('');

  const refresh = React.useCallback(() => {
    const params = new URLSearchParams({ category, search });
    api<FieldDefinitionResult>(`/api/field-definitions?${params}`)
      .then((result) => {
        setData(result);
        setError('');
        setSelected((current) => {
          if (!current) return result.rows[0] || null;
          return result.rows.find((row) => row.field_id === current.field_id) || result.rows[0] || null;
        });
      })
      .catch((err) => setError(String(err)));
  }, [category, search]);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  React.useEffect(() => {
    if (!selected) {
      setDraft({});
      return;
    }
    setDraft({ ...selected });
    setTermSynonyms('');
    setTermXbrlTags('');
    setTermSections('');
    setTermNote('');
  }, [selected]);

  const columns = fieldDefinitionListColumns;
  const selectedKey = selected?.field_id || '';
  const selectedFieldId = selected?.field_id || '';

  function setDraftValue(key: keyof FieldDefinitionRow, value: string) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function saveField() {
    if (!selectedFieldId) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const updates = {
        field_name_ja: draft.field_name_ja || '',
        category: draft.category || '',
        target_unit: draft.target_unit || '',
        data_scope_required: draft.data_scope_required || '',
        period_type: draft.period_type || '',
        preferred_method: draft.preferred_method || '',
        xbrl_tag_candidates: draft.xbrl_tag_candidates || '',
        context_filters: draft.context_filters || '',
        section_keywords: draft.section_keywords || '',
        synonyms_ja: draft.synonyms_ja || '',
        calculation_formula: draft.calculation_formula || '',
        validation_rule_ids: draft.validation_rule_ids || '',
        review_threshold: draft.review_threshold || '',
        notes: draft.notes || '',
      };
      const result = await api<FieldDefinitionUpdateResult>(`/api/field-definitions/${encodeURIComponent(selectedFieldId)}`, {
        method: 'POST',
        body: JSON.stringify({ updates })
      });
      setMessage(result.changed_columns.length ? `保存しました: ${result.changed_columns.join(', ')}。バックアップ: ${result.backup_path}` : '変更はありません。');
      onUpdated();
      refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function appendTerms() {
    if (!selectedFieldId) return;
    const synonyms = splitTerms(termSynonyms);
    const xbrlTags = splitTerms(termXbrlTags);
    const sectionKeywords = splitTerms(termSections);
    if (!synonyms.length && !xbrlTags.length && !sectionKeywords.length && !termNote.trim()) {
      setError('追加する同義語・XBRL候補・セクション語・メモのいずれかを入力してください。');
      return;
    }
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const result = await api<FieldDefinitionUpdateResult>(`/api/field-definitions/${encodeURIComponent(selectedFieldId)}/terms`, {
        method: 'POST',
        body: JSON.stringify({ synonyms, xbrl_tags: xbrlTags, section_keywords: sectionKeywords, note: termNote })
      });
      setMessage(result.changed_columns.length ? `追加しました: ${result.changed_columns.join(', ')}。` : '既存候補と同じため変更はありません。');
      onUpdated();
      refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="stack">
      <FilterBar>
        <label className="filter-field">
          <span>ジャンル</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">全ジャンル</option>
            {(data?.categories || []).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>検索</span>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="完成工事総利益、売上総利益、GrossProfit..." />
        </label>
        <button className="ghost" type="button" onClick={refresh}>項目再読込</button>
      </FilterBar>
      <p className="hint">項目名、同義語、有報タグ候補、セクション語をここで整理します。項目IDは最終データの主キーなので変更しません。</p>
      {error && <InlineError message={error} />}
      {message && <div className="alert success-alert">{message}</div>}
      <div className="field-admin-layout">
        <div className="panel">
          <div className="panel-head">
            <div>
              <h2>項目一覧</h2>
              <p className="muted">{data ? `${data.total} / ${data.all_total}項目` : '読み込み中'}</p>
            </div>
          </div>
          {data ? (
            <DataTable
              data={data.rows as unknown as Row[]}
              columns={columns}
              columnLabels={fieldDefinitionColumnLabels}
              onRowClick={(row) => setSelected(row as FieldDefinitionRow)}
              selectedRowKey={selectedKey}
              getRowKey={(row) => String(row.field_id || '')}
              compact
              clampAllCells
            />
          ) : (
            <Empty message="項目定義を読み込み中です。" />
          )}
        </div>
        <div className="panel field-editor">
          {selected ? (
            <>
              <div className="panel-head">
                <div>
                  <h2>{selected.field_name_ja || selected.field_id}</h2>
                  <p className="muted">項目ID: {selected.field_id}</p>
                </div>
                <span className="badge pending">{selected.category_label || selected.category || '未分類'}</span>
              </div>
              <div className="field-editor-grid">
                <label>
                  <span>項目名</span>
                  <input value={draft.field_name_ja || ''} onChange={(e) => setDraftValue('field_name_ja', e.target.value)} />
                </label>
                <label>
                  <span>ジャンル</span>
                  <input value={draft.category || ''} onChange={(e) => setDraftValue('category', e.target.value)} />
                </label>
                <label>
                  <span>単位</span>
                  <input value={draft.target_unit || ''} onChange={(e) => setDraftValue('target_unit', e.target.value)} />
                </label>
                <label>
                  <span>スコープ</span>
                  <select value={draft.data_scope_required || ''} onChange={(e) => setDraftValue('data_scope_required', e.target.value)}>
                    <option value="">未設定</option>
                    <option value="consolidated">consolidated</option>
                    <option value="standalone">standalone</option>
                    <option value="segment">segment</option>
                    <option value="permit_entity">permit_entity</option>
                  </select>
                </label>
                <label>
                  <span>期間</span>
                  <select value={draft.period_type || ''} onChange={(e) => setDraftValue('period_type', e.target.value)}>
                    <option value="">未設定</option>
                    <option value="current_year">current_year</option>
                    <option value="period_end">period_end</option>
                    <option value="duration">duration</option>
                  </select>
                </label>
                <label>
                  <span>抽出方法</span>
                  <select value={draft.preferred_method || ''} onChange={(e) => setDraftValue('preferred_method', e.target.value)}>
                    <option value="">未設定</option>
                    <option value="XBRL_CSV">XBRL_CSV</option>
                    <option value="LOCAL_TABLE">LOCAL_TABLE</option>
                    <option value="MANUAL_OBSIDIAN">MANUAL_OBSIDIAN</option>
                    <option value="CALCULATED">CALCULATED</option>
                  </select>
                </label>
              </div>
              <label>
                <span>同義語</span>
                <textarea value={draft.synonyms_ja || ''} onChange={(e) => setDraftValue('synonyms_ja', e.target.value)} rows={3} />
              </label>
              <label>
                <span>有報タグ候補</span>
                <textarea value={draft.xbrl_tag_candidates || ''} onChange={(e) => setDraftValue('xbrl_tag_candidates', e.target.value)} rows={3} />
              </label>
              <label>
                <span>セクション語</span>
                <textarea value={draft.section_keywords || ''} onChange={(e) => setDraftValue('section_keywords', e.target.value)} rows={3} />
              </label>
              <label>
                <span>コンテキスト</span>
                <input value={draft.context_filters || ''} onChange={(e) => setDraftValue('context_filters', e.target.value)} />
              </label>
              <label>
                <span>メモ</span>
                <textarea value={draft.notes || ''} onChange={(e) => setDraftValue('notes', e.target.value)} rows={3} />
              </label>
              <div className="toolbar">
                <button type="button" onClick={saveField} disabled={saving}>{saving ? '保存中...' : '項目定義を保存'}</button>
              </div>
              <div className="field-term-adder">
                <h3>表記ゆれ・タグを追加</h3>
                <div className="field-editor-grid">
                  <label>
                    <span>追加する同義語</span>
                    <input value={termSynonyms} onChange={(e) => setTermSynonyms(e.target.value)} placeholder="完成工事総利益;売上総利益" />
                  </label>
                  <label>
                    <span>追加する有報タグ候補</span>
                    <input value={termXbrlTags} onChange={(e) => setTermXbrlTags(e.target.value)} placeholder="GrossProfitOnCompletedConstructionContracts" />
                  </label>
                  <label>
                    <span>追加するセクション語</span>
                    <input value={termSections} onChange={(e) => setTermSections(e.target.value)} placeholder="完成工事;売上総利益" />
                  </label>
                  <label>
                    <span>追加メモ</span>
                    <input value={termNote} onChange={(e) => setTermNote(e.target.value)} placeholder="会社別表記ゆれとして追加" />
                  </label>
                </div>
                <button type="button" className="secondary" onClick={appendTerms} disabled={saving}>追加内容を保存</button>
              </div>
            </>
          ) : (
            <Empty message="編集する項目を選択してください。" />
          )}
        </div>
      </div>
    </section>
  );
}

function CellDetailPanel({
  detail,
  loading,
  error,
  onAudit,
  onReview,
  job,
  onJob,
  onChanged
}: {
  detail: CellDetail | null;
  loading: boolean;
  error: string;
  onAudit: (target: { company_year_id: string; field_id: string }) => void;
  onReview: (target: ReviewTarget) => void;
  job: Job | null;
  onJob: (job: Job) => void;
  onChanged: () => void;
}) {
  const [decision, setDecision] = React.useState('correct');
  const [correctedValue, setCorrectedValue] = React.useState('');
  const [note, setNote] = React.useState('');
  const [fieldNameDraft, setFieldNameDraft] = React.useState('');
  const [similarScope, setSimilarScope] = React.useState('cell_only');
  const [similarPreview, setSimilarPreview] = React.useState<Row[] | null>(null);
  const [similarCount, setSimilarCount] = React.useState(0);
  const [busy, setBusy] = React.useState('');
  const [message, setMessage] = React.useState('');
  const [panelError, setPanelError] = React.useState('');
  const [inferredSuggestion, setInferredSuggestion] = React.useState<Row | null>(null);
  const [expandPreview, setExpandPreview] = React.useState<Row[] | null>(null);
  const [expandTargetCount, setExpandTargetCount] = React.useState(0);

  React.useEffect(() => {
    if (!detail) return;
    const candidateValue = String(detail.candidates?.[0]?.value || detail.review_rows?.[0]?.extracted_value || detail.current_value || '');
    setDecision(candidateValue ? 'accept' : 'correct');
    setCorrectedValue(candidateValue);
    setNote(String(detail.review_state?.reviewer_note || ''));
    setFieldNameDraft(detail.field_name_ja);
    setSimilarScope('cell_only');
    setSimilarPreview(null);
    setSimilarCount(0);
    setMessage('');
    setPanelError('');
    setInferredSuggestion(null);
    setExpandPreview(null);
    setExpandTargetCount(0);
  }, [detail?.company_year_id, detail?.field_id]);

  if (loading) {
    return <div className="panel cell-detail"><p className="muted">セル詳細を読み込み中です。</p></div>;
  }
  if (error) {
    return <InlineError message={error} />;
  }
  if (!detail) {
    return null;
  }

  const reviewColumns = ['extracted_value', 'review_reason', 'validation_status', 'confidence', 'source_quote'];
  const resolvedColumns = ['review_decision', 'corrected_value', 'applied_status', 'applied_value', 'applied_at', 'extracted_value', 'reviewer_note', 'reviewed_at'];
  const auditColumns = ['value', 'unit_normalized', 'data_scope', 'source_heading', 'source_quote', 'validation_status', 'confidence'];
  const chain = detail.source_chain;
  const factRows = chain?.fact_resolution && Object.keys(chain.fact_resolution).length ? [chain.fact_resolution] : [];
  const canOpenReview = detail.review_rows.length > 0 || detail.resolved_rows.length > 0;
  const displayValue = detail.is_blank ? '空欄' : detail.current_value;
  const jobRunning = job?.status === 'running';
  const reviewState = detail.review_state || {};
  const mappingRows = (detail.mapping_state?.mappings as Row[] | undefined) || [];
  const proposedMappings = mappingRows.filter((row) => String(row.status || '') === 'proposed');

  async function saveReview(nextDecision = decision, nextValue = correctedValue) {
    if (!detail) return;
    if (jobRunning) {
      setPanelError('ジョブ実行中です。完了後に保存してください。');
      return;
    }
    if (nextDecision === 'correct' && !String(nextValue).trim()) {
      setPanelError('correct で保存するには修正値を入力してください。');
      return;
    }
    if (nextDecision === 'accept' && !detail.candidates.some((candidate) => String(candidate.value || '').trim())) {
      setPanelError('accept で保存するには候補値が必要です。手入力の場合は correct を選んでください。');
      return;
    }
    setBusy('review');
    setPanelError('');
    setExpandPreview(null);
    setExpandTargetCount(0);
    try {
      const result = await api<{ changed: number; total: number; inferred_source_suggestion?: Row | null }>(`/api/cells/${encodeURIComponent(detail.company_year_id)}/${encodeURIComponent(detail.field_id)}/review`, {
        method: 'POST',
        body: JSON.stringify({
          review_decision: nextDecision,
          corrected_value: nextDecision === 'correct' ? nextValue : '',
          reviewer_note: note,
          reviewer: 'web_cell_workbench'
        })
      });
      setMessage(`セルレビューを保存しました: ${result.changed}件 / resolved合計 ${result.total}件。最終表へ出すにはレビュー反映が必要です。`);
      setInferredSuggestion(result.inferred_source_suggestion || null);
      onChanged();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setBusy('');
    }
  }

  async function saveFieldName() {
    if (!detail) return;
    const nextName = fieldNameDraft.trim();
    if (!nextName || nextName === detail.field_name_ja) return;
    setBusy('field-name');
    setPanelError('');
    try {
      await api(`/api/cells/${encodeURIComponent(detail.company_year_id)}/${encodeURIComponent(detail.field_id)}/field-name`, {
        method: 'POST',
        body: JSON.stringify({ field_name_ja: nextName })
      });
      setMessage(`項目名を更新しました: ${detail.field_id} → ${nextName}`);
      onChanged();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setBusy('');
    }
  }

  async function applyReviewJob() {
    if (jobRunning) return;
    setBusy('apply-review');
    setPanelError('');
    try {
      const next = await api<Job>('/api/jobs/apply-review', { method: 'POST' });
      onJob(next);
      setMessage('保存済みレビューを最終データへ反映するジョブを開始しました。');
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setBusy('');
    }
  }

  async function decideMapping(mappingId: string, nextDecision: 'confirm' | 'reject') {
    if (!detail) return;
    setBusy(mappingId);
    setPanelError('');
    try {
      await api(`/api/cells/${encodeURIComponent(detail.company_year_id)}/${encodeURIComponent(detail.field_id)}/mapping`, {
        method: 'POST',
        body: JSON.stringify({ mapping_id: mappingId, decision: nextDecision, reviewer: 'web_cell_workbench' })
      });
      setMessage(nextDecision === 'confirm' ? '対応付けの提案を承認しました。' : '対応付けの提案を却下しました。');
      onChanged();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setBusy('');
    }
  }

  async function previewSimilar() {
    if (!detail) return;
    setBusy('similar-preview');
    setPanelError('');
    try {
      const result = await api<{ target_count: number; targets: Row[] }>(`/api/cells/${encodeURIComponent(detail.company_year_id)}/${encodeURIComponent(detail.field_id)}/apply-similar`, {
        method: 'POST',
        body: JSON.stringify({
          scope: similarScope,
          review_decision: decision,
          corrected_value: correctedValue,
          reviewer_note: note,
          preview: true
        })
      });
      setSimilarCount(result.target_count);
      setSimilarPreview(result.targets);
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setBusy('');
    }
  }

  async function applySimilar() {
    if (!detail || !similarPreview) return;
    const ok = window.confirm(`${similarCount}セルへ同じレビュー判断を保存します。最終表への反映は別途レビュー反映が必要です。実行しますか？`);
    if (!ok) return;
    setBusy('similar-apply');
    setPanelError('');
    try {
      const result = await api<{ changed: number; target_count: number }>(`/api/cells/${encodeURIComponent(detail.company_year_id)}/${encodeURIComponent(detail.field_id)}/apply-similar`, {
        method: 'POST',
        body: JSON.stringify({
          scope: similarScope,
          review_decision: decision,
          corrected_value: correctedValue,
          reviewer_note: note,
          preview: false
        })
      });
      setMessage(`同種セルへ保存しました: ${result.changed}件 / 対象 ${result.target_count}セル。`);
      onChanged();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setBusy('');
    }
  }

  async function previewExpandToYears() {
    if (!detail) return;
    setBusy('expand-preview');
    setPanelError('');
    try {
      const result = await api<{ target_count: number; targets: Row[] }>(`/api/cells/${encodeURIComponent(detail.company_year_id)}/${encodeURIComponent(detail.field_id)}/expand-to-years`, {
        method: 'POST',
        body: JSON.stringify({ preview: true, reviewer: 'web_cell_workbench' })
      });
      setExpandTargetCount(result.target_count);
      setExpandPreview(result.targets);
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setBusy('');
    }
  }

  async function applyExpandToYears() {
    if (!detail || !expandPreview) return;
    const ok = window.confirm(`他の${expandTargetCount}年度に同じ表から取得した値を反映します。実行しますか？`);
    if (!ok) return;
    setBusy('expand-apply');
    setPanelError('');
    try {
      const result = await api<{ changed: number; target_count: number }>(`/api/cells/${encodeURIComponent(detail.company_year_id)}/${encodeURIComponent(detail.field_id)}/expand-to-years`, {
        method: 'POST',
        body: JSON.stringify({ preview: false, reviewer: 'web_cell_workbench' })
      });
      setMessage(`他の年度に反映しました: ${result.changed}件 / 対象 ${result.target_count}年度。最終表へ出すにはレビュー反映が必要です。`);
      setExpandPreview(null);
      setExpandTargetCount(0);
      setInferredSuggestion(null);
      onChanged();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="panel cell-detail cell-workbench">
      <div className="panel-head">
        <div>
          <h2>Cell Workbench: {detail.field_name_ja}</h2>
          <p className="muted">{detail.company_year_id} / {detail.field_id}</p>
        </div>
        <span className={`badge status-${detail.status}`}>{detail.status_label}</span>
      </div>

      <div className="detail-grid">
        <div>
          <small>最終表の現在値</small>
          <strong className={detail.is_blank ? 'blank-value' : ''}>{displayValue}</strong>
        </div>
        <div>
          <small>保存状態</small>
          <strong>{reviewState.saved ? `保存済み ${String(reviewState.reviewed_at || '')}` : '未保存'}</strong>
        </div>
        <div>
          <small>反映状態</small>
          <strong>{appliedStatusLabel(reviewState.applied_status)}{reviewState.applied_value ? `: ${String(reviewState.applied_value)}` : ''}</strong>
        </div>
        <div>
          <small>単位</small>
          <strong>{detail.unit || '-'}</strong>
        </div>
        <div>
          <small>必要スコープ</small>
          <strong>{detail.data_scope_required || '-'}</strong>
        </div>
        <div>
          <small>抽出方式</small>
          <strong>{detail.preferred_method || '-'}</strong>
        </div>
      </div>

      <div className="detail-section">
        <h3>判定</h3>
        <p>{detail.summary}</p>
      </div>
      <div className="detail-section">
        <h3>次の操作</h3>
        <p>{detail.next_action}</p>
      </div>
      {message && <div className="inline-success">{message}</div>}
      {panelError && <InlineError message={panelError} />}

      <div className="workbench-grid">
        <div className="workbench-card">
          <h3>セル値を保存</h3>
          <label>判定</label>
          <select value={decision} onChange={(e) => setDecision(e.target.value)}>
            <option value="accept">候補値を採用</option>
            <option value="correct">手入力で修正</option>
            <option value="reject">候補を却下</option>
            <option value="not_applicable">このセルは対象外</option>
          </select>
          <label>修正値</label>
          <input value={correctedValue} onChange={(e) => setCorrectedValue(e.target.value)} disabled={decision !== 'correct'} />
          <label>メモ</label>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
          <div className="toolbar">
            <button type="button" onClick={() => saveReview()} disabled={busy === 'review' || jobRunning}>
              {busy === 'review' ? '保存中...' : '保存'}
            </button>
            <button type="button" className="secondary" onClick={applyReviewJob} disabled={busy === 'apply-review' || jobRunning || !reviewState.saved}>
              {busy === 'apply-review' ? '起動中...' : '最終表へ反映'}
            </button>
          </div>
          <p className="hint">保存は review_resolved.csv に残ります。最終表へ出すには反映ジョブが必要です。</p>
        </div>

        <div className="workbench-card">
          <h3>候補から採用</h3>
          {detail.candidates.length ? (
            <div className="candidate-list">
              {detail.candidates.slice(0, 4).map((candidate, index) => (
                <button
                  type="button"
                  className="candidate-pick"
                  key={`${candidate.candidate_id || index}`}
                  onClick={() => {
                    const value = String(candidate.value || '');
                    setDecision('correct');
                    setCorrectedValue(value);
                    void saveReview('correct', value);
                  }}
                  disabled={busy === 'review' || jobRunning}
                >
                  <strong>{String(candidate.value || '-')}</strong>
                  <span>{String(candidate.source || '')} / {String(candidate.reason || '')}</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">候補値はありません。必要なら手入力で保存します。</p>
          )}
        </div>

        {inferredSuggestion && (
          <div className="workbench-card">
            <h3>📌 出典を特定しました</h3>
            <p>
              {String(inferredSuggestion.section_name || '有報の表')} の「{String(inferredSuggestion.role || '該当項目')}」欄
              （確からしさ {formatConfidence(inferredSuggestion.confidence)}）から見つかりました。
            </p>
            {Number(inferredSuggestion.expandable_year_count || 0) > 0 ? (
              <p className="muted">この会社の他の年度（未入力 {String(inferredSuggestion.expandable_year_count)}件）にも同じ表から取得できる可能性があります。</p>
            ) : (
              <p className="muted">この会社に未入力の他年度はありません。</p>
            )}
            <div className="toolbar">
              <button type="button" className="secondary" onClick={previewExpandToYears} disabled={busy === 'expand-preview' || jobRunning}>
                {busy === 'expand-preview' ? '確認中...' : '他の年度に展開（プレビュー）'}
              </button>
              <button type="button" onClick={applyExpandToYears} disabled={!expandPreview || expandTargetCount === 0 || busy === 'expand-apply' || jobRunning}>
                {busy === 'expand-apply' ? '反映中...' : '反映する'}
              </button>
            </div>
            {expandPreview && (
              <div className="similar-preview">
                <p className="muted">展開できる年度 {expandTargetCount}件。</p>
                <MiniRows title="展開予定の年度と値" rows={expandPreview} columns={['company_year_id', 'field_id', 'value', 'unit']} emptyMessage="展開できる年度はありません（値が確定できませんでした）。" />
              </div>
            )}
          </div>
        )}

        <div className="workbench-card">
          <h3>項目名を修正</h3>
          <input value={fieldNameDraft} onChange={(e) => setFieldNameDraft(e.target.value)} />
          <button type="button" className="secondary" onClick={saveFieldName} disabled={busy === 'field-name' || !fieldNameDraft.trim() || fieldNameDraft.trim() === detail.field_name_ja}>
            {busy === 'field-name' ? '更新中...' : '項目名を更新'}
          </button>
          <p className="hint">値の判定ではなく、項目名の表示を直します。</p>
        </div>

        <div className="workbench-card">
          <h3>同種セルへ適用</h3>
          <select value={similarScope} onChange={(e) => { setSimilarScope(e.target.value); setSimilarPreview(null); }}>
            <option value="cell_only">このセルのみ</option>
            <option value="same_company_all_years">同じ会社の全年度</option>
            <option value="same_field_all_companies">同じ項目の全社全年度</option>
          </select>
          <div className="toolbar">
            <button type="button" className="secondary" onClick={previewSimilar} disabled={busy === 'similar-preview'}>
              {busy === 'similar-preview' ? '確認中...' : '対象をプレビュー'}
            </button>
            <button type="button" onClick={applySimilar} disabled={!similarPreview || busy === 'similar-apply' || jobRunning}>
              {busy === 'similar-apply' ? '適用中...' : 'プレビュー対象へ保存'}
            </button>
          </div>
          {similarPreview && (
            <div className="similar-preview">
              <p className="muted">対象 {similarCount}セル。先頭 {similarPreview.length}件を表示。</p>
              <MiniRows title="適用対象プレビュー" rows={similarPreview} columns={['company_year_id', 'fiscal_year', 'field_id', 'current_value']} emptyMessage="対象はありません。" />
            </div>
          )}
        </div>
      </div>

      {proposedMappings.length > 0 && (
        <div className="detail-section">
          <h3>項目対応候補</h3>
          <div className="mapping-action-list">
            {proposedMappings.slice(0, 5).map((mapping) => {
              const mappingId = String(mapping.mapping_id || '');
              return (
                <div className="mapping-action" key={mappingId}>
                  <div>
                    <strong>{String(mapping.action || '')} → {String(mapping.concept_id || '')}</strong>
                    <span>{String(mapping.observed_item_id || '')} / confidence {String(mapping.confidence || '-')}</span>
                  </div>
                  <div className="toolbar">
                    <button type="button" className="secondary" onClick={() => decideMapping(mappingId, 'confirm')} disabled={busy === mappingId}>承認</button>
                    <button type="button" className="ghost" onClick={() => decideMapping(mappingId, 'reject')} disabled={busy === mappingId}>却下</button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
      <div className="toolbar">
        <button
          className="ghost"
          disabled={!detail.has_source_audit}
          onClick={() => onAudit({ company_year_id: detail.company_year_id, field_id: detail.field_id })}
        >
          根拠タブで開く
        </button>
        <button
          className="ghost"
          disabled={!canOpenReview}
          onClick={() => onReview({ company: detail.company_id, fiscal_year: detail.fiscal_year, field_id: detail.field_id })}
        >
          レビューで開く
        </button>
      </div>

      <MiniRows title="レビュー候補" rows={detail.review_rows} columns={reviewColumns} emptyMessage="レビュー候補はありません。" />
      <MiniRows title="保存済みレビュー" rows={detail.resolved_rows} columns={resolvedColumns} emptyMessage="保存済みレビューはありません。" />
      <MiniRows title="根拠" rows={detail.audit_rows} columns={auditColumns} emptyMessage="該当する根拠データはありません。" />
      <MiniRows
        title={`${t('source_chain')}: 確定した数値`}
        rows={factRows}
        columns={['value', 'resolution', 'corroboration_count', 'conflict_count', 'buckets', 'sources', 'decided_at_utc']}
        emptyMessage="確定した数値の記録はありません。"
      />
      <MiniRows
        title={`${t('source_chain')}: ${t('observed')}`}
        rows={chain?.observed_items || []}
        columns={['observed_item_id', 'item_kind', 'element_id', 'label_ja', 'normalized_scope', 'unit', 'source']}
        emptyMessage="対応する有報の項目はありません。"
      />
      <MiniRows
        title={`${t('source_chain')}: ${t('mapping')}`}
        rows={chain?.mappings || []}
        columns={['mapping_id', 'observed_item_id', 'concept_id', 'action', 'status', 'decided_by', 'confidence']}
        emptyMessage="対応する項目の対応付けはありません。"
      />
      <MiniRows
        title={`${t('source_chain')}: ${t('corroboration')}`}
        rows={chain?.corroborations || []}
        columns={['check_kind', 'check_ref', 'matched', 'primary_value', 'other_value', 'difference', 'restatement_suspected', 'detail']}
        emptyMessage="照合の記録はありません。"
      />
    </div>
  );
}

function StocksPanel({ refreshToken }: { refreshToken: number }) {
  const [page, setPage] = React.useState(1);
  const [company, setCompany] = React.useState('');
  const [month, setMonth] = React.useState('');
  const [data, setData] = React.useState<Page | null>(null);
  const [status, setStatus] = React.useState<StockMonthlyStatus | null>(null);
  const [error, setError] = React.useState('');
  const { options: stockOptions, error: optionsError, refresh: refreshStockOptions } = useStockOptions();

  React.useEffect(() => {
    if (!month && stockOptions?.months.length) {
      setMonth(stockOptions.months[stockOptions.months.length - 1]);
    }
  }, [stockOptions, month]);

  const loadStatus = React.useCallback(() => {
    api<StockMonthlyStatus>('/api/market/stock/status').then(setStatus).catch((err) => setError(String(err)));
  }, []);

  React.useEffect(() => {
    loadStatus();
    refreshStockOptions();
  }, [loadStatus, refreshStockOptions, refreshToken]);

  React.useEffect(() => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: '50',
      company,
      month,
    });
    api<Page>(`/api/market/stock/monthly?${params}`)
      .then((next) => {
        setData(next);
        setError('');
      })
      .catch((err) => setError(String(err)));
  }, [page, company, month, refreshToken]);

  function resetPage(next: () => void) {
    setPage(1);
    next();
  }

  return (
    <section className="stack">
      <div className="panel automation-panel">
        <div className="panel-head">
          <div>
            <h2>株価月次データ</h2>
            <p className="muted">{status?.message || '株価取得状態を読み込み中です。'}</p>
          </div>
          <span className={`badge ${status?.due ? 'failed' : status?.last_error_count ? 'running' : 'succeeded'}`}>
            {status?.due ? '更新必要' : status?.last_error_count ? '一部エラー' : '更新済み'}
          </span>
        </div>
        <div className="automation-grid">
          <div className="metric">
            <small>最新月</small>
            <strong>{status?.latest_month || '-'}</strong>
          </div>
          <div className="metric">
            <small>行数</small>
            <strong>{status?.price_rows ?? '-'}</strong>
          </div>
          <div className="metric">
            <small>対象銘柄</small>
            <strong>{status ? `${status.enabled_securities}/${status.total_securities}` : '-'}</strong>
          </div>
          <div className="metric">
            <small>前回エラー</small>
            <strong className={status?.last_error_count ? 'text-warn' : 'text-ok'}>
              {status?.last_error_count ?? '-'}
              {status?.last_ignored_listing_error_count ? `（対象外${status.last_ignored_listing_error_count}）` : ''}
            </strong>
          </div>
          <div className="metric">
            <small>取得元</small>
            <strong>{status?.provider || '-'}</strong>
          </div>
        </div>
      </div>
      <FilterBar>
        <label className="filter-field">
          <span>会社</span>
          <select value={company} onChange={(e) => resetPage(() => setCompany(e.target.value))}>
            <option value="">全社</option>
            {(stockOptions?.companies || []).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>
        <label className="filter-field small">
          <span>月</span>
          <select value={month} onChange={(e) => resetPage(() => setMonth(e.target.value))}>
            <option value="">全期間</option>
            {(stockOptions?.months || []).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <button className="ghost" onClick={() => { loadStatus(); refreshStockOptions(); }}>
          株価再読込
        </button>
      </FilterBar>
      <p className="hint">折れ線グラフにする場合は、グラフタブでデータ種別を「株価月次」に切り替えてください。</p>
      {optionsError && <InlineError message={optionsError} />}
      {error && <InlineError message={error} />}
      {data ? (
        <>
          <DataTable
            data={data.rows}
            columns={onlyExistingColumns(data.columns, stockListColumns)}
            columnLabels={stockColumnLabels}
            clampAllCells
          />
          <Pager page={data.page} totalPages={data.total_pages} total={data.total} onPage={setPage} />
        </>
      ) : (
        <Empty message="株価データを読み込み中です。" />
      )}
    </section>
  );
}

function FactbooksPanel({
  refreshToken,
  job,
  onJob,
  onError
}: {
  refreshToken: number;
  job: Job | null;
  onJob: (job: Job) => void;
  onError: (message: string) => void;
}) {
  const [page, setPage] = React.useState(1);
  const [documentPage, setDocumentPage] = React.useState(1);
  const [company, setCompany] = React.useState('');
  const [fiscalYear, setFiscalYear] = React.useState('');
  const [categoryType, setCategoryType] = React.useState('');
  const [search, setSearch] = React.useState('');
  const [orders, setOrders] = React.useState<Page | null>(null);
  const [documents, setDocuments] = React.useState<Page | null>(null);
  const [status, setStatus] = React.useState<FactbookStatus | null>(null);
  const [validation, setValidation] = React.useState<FactbookValidationSummary | null>(null);
  const [error, setError] = React.useState('');
  const { options, error: optionsError, refresh: refreshOptions } = useFactbookOptions();
  const jobRunning = job?.status === 'running';

  const loadStatus = React.useCallback(() => {
    api<FactbookStatus>('/api/company-factbooks/status').then(setStatus).catch((err) => setError(String(err)));
    api<FactbookValidationSummary>('/api/company-factbooks/validation-summary')
      .then(setValidation)
      .catch((err) => setError(String(err)));
  }, []);

  React.useEffect(() => {
    loadStatus();
    refreshOptions();
  }, [loadStatus, refreshOptions, refreshToken]);

  React.useEffect(() => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: '50',
      company,
      fiscal_year: fiscalYear,
      category_type: categoryType,
      search,
    });
    api<Page>(`/api/company-factbooks/orders?${params}`)
      .then((next) => {
        setOrders(next);
        setError('');
      })
      .catch((err) => setError(String(err)));
  }, [page, company, fiscalYear, categoryType, search, refreshToken]);

  React.useEffect(() => {
    const params = new URLSearchParams({
      page: String(documentPage),
      page_size: '50',
      company,
      fiscal_year: fiscalYear,
      search,
    });
    api<Page>(`/api/company-factbooks/documents?${params}`)
      .then((next) => {
        setDocuments(next);
        setError('');
      })
      .catch((err) => setError(String(err)));
  }, [documentPage, company, fiscalYear, search, refreshToken]);

  function resetPage(next: () => void) {
    setPage(1);
    setDocumentPage(1);
    next();
  }

  async function startRefresh(dryRun = false) {
    if (jobRunning) {
      onError('ジョブ実行中です。作業ログの完了を待ってから次の操作を実行してください。');
      return;
    }
    try {
      const next = await api<Job>('/api/jobs/factbook-refresh', {
        method: 'POST',
        body: JSON.stringify({ force: true, dry_run: dryRun }),
      });
      onJob(next);
      window.setTimeout(() => {
        loadStatus();
        refreshOptions();
      }, 800);
    } catch (err) {
      onError(String(err));
    }
  }

  return (
    <section className="stack">
      <div className="panel automation-panel">
        <div className="panel-head">
          <div>
            <h2>ファクトブック受注カテゴリ</h2>
            <p className="muted">{status?.message || 'ファクトブック取得状態を読み込み中です。'}</p>
          </div>
          <span className={`badge ${status?.last_error_count ? 'running' : status?.order_rows ? 'succeeded' : 'pending'}`}>
            {status?.last_error_count ? '一部エラー' : status?.order_rows ? 'データあり' : '未取得'}
          </span>
        </div>
        <div className="automation-grid">
          <div className="metric">
            <small>最新年度</small>
            <strong>{status?.latest_fiscal_year || '-'}</strong>
          </div>
          <div className="metric">
            <small>抽出行</small>
            <strong>{status?.order_rows ?? '-'}</strong>
          </div>
          <div className="metric">
            <small>資料候補</small>
            <strong>{status?.source_documents ?? '-'}</strong>
          </div>
          <div className="metric">
            <small>未解析資料</small>
            <strong className={status?.unsupported_documents ? 'text-warn' : 'text-ok'}>{status?.unsupported_documents ?? '-'}</strong>
          </div>
          <div className="metric">
            <small>対象ソース</small>
            <strong>{status ? `${status.enabled_source_count}/${status.source_count}` : '-'}</strong>
          </div>
        </div>
        <div className="inline-actions">
          <button onClick={() => startRefresh(false)} disabled={jobRunning}>公式ソースを取得</button>
          <button className="ghost" onClick={() => startRefresh(true)} disabled={jobRunning}>取得ドライラン</button>
          <button className="ghost" onClick={() => { loadStatus(); refreshOptions(); }} disabled={jobRunning}>ファクトブック再読込</button>
        </div>
      </div>
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>有報照合ステータス</h2>
            <p className="muted">
              {validation?.validated_at_utc ? `最終検証: ${validation.validated_at_utc}` : '照合結果を読み込み中です。'}
            </p>
          </div>
          <span className={`badge ${validation?.status === 'completed' ? 'succeeded' : validation?.status === 'mismatch' ? 'failed' : 'pending'}`}>
            {validation?.status || '未検証'}
          </span>
        </div>
        <div className="automation-grid">
          <div className="metric"><small>検証行</small><strong>{validation?.rows ?? '-'}</strong></div>
          <div className="metric"><small>比較可能</small><strong>{validation?.comparable_rows ?? '-'}</strong></div>
          <div className="metric"><small>未完了</small><strong className={validation?.incomplete_rows ? 'text-warn' : 'text-ok'}>{validation?.incomplete_rows ?? '-'}</strong></div>
          <div className="metric"><small>pending</small><strong>{validation?.pending_rows ?? '-'}</strong></div>
          {Object.entries(validation?.by_status || {}).slice(0, 4).map(([key, value]) => (
            <div className="metric" key={key}><small>{key}</small><strong>{value}</strong></div>
          ))}
        </div>
        <p className="hint">`no_mapping` は有報側の同粒度フィールド未定義、`missing_yuho_value` はフィールド定義はあるが完成表の値が空欄です。用途別受注を無理に総額へ寄せる対応付けは行いません。</p>
        <div className="grid two">
          <div>
            <h3>未対応付け上位</h3>
            <DataTable
              data={validation?.top_no_mapping_categories || []}
              columns={['category_type', 'use_category_normalized', 'use_category_label', 'source_metric_id', 'count']}
              columnLabels={factbookValidationColumnLabels}
              clampAllCells
            />
          </div>
          <div>
            <h3>有報値欠損上位</h3>
            <DataTable
              data={validation?.top_missing_yuho_fields || []}
              columns={['yuho_field_id', 'yuho_field_name', 'category_type', 'source_metric_id', 'count']}
              columnLabels={factbookValidationColumnLabels}
              clampAllCells
            />
          </div>
        </div>
        <h3>pendingサンプル</h3>
        <DataTable
          data={validation?.pending_samples || []}
          columns={['company_name', 'fiscal_year', 'validation_status', 'validation_message', 'source_metric_id', 'category_type', 'use_category_label', 'factbook_amount_million_yen', 'yuho_field_id', 'source_quote']}
          columnLabels={factbookValidationColumnLabels}
          clampAllCells
        />
      </div>
      <FilterBar>
        <label className="filter-field">
          <span>会社</span>
          <select value={company} onChange={(e) => resetPage(() => setCompany(e.target.value))}>
            <option value="">全社</option>
            {(options?.companies || []).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>
        <label className="filter-field small">
          <span>年度</span>
          <select value={fiscalYear} onChange={(e) => resetPage(() => setFiscalYear(e.target.value))}>
            <option value="">全年度</option>
            {(options?.years || []).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <label className="filter-field small">
          <span>分類</span>
          <select value={categoryType} onChange={(e) => resetPage(() => setCategoryType(e.target.value))}>
            <option value="">全分類</option>
            {(options?.category_types || []).map((item) => (
              <option key={item} value={item}>{factbookCategoryTypeLabel(item)}</option>
            ))}
          </select>
        </label>
        <input value={search} onChange={(e) => resetPage(() => setSearch(e.target.value))} placeholder="分類・資料名・URLを検索" />
      </FilterBar>
      <p className="hint">用途別は `category_type=use`、清水の建築/土木等の区分は `business_scope` として分けて保存します。グラフタブではデータ種別を「ファクトブック受注」に切り替えてください。</p>
      {optionsError && <InlineError message={optionsError} />}
      {error && <InlineError message={error} />}
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>抽出済みカテゴリ</h2>
            <p className="muted">{orders ? `${orders.total}件` : '読み込み中です。'}</p>
          </div>
        </div>
        {orders ? (
          <>
            <DataTable
              data={orders.rows}
              columns={onlyExistingColumns(orders.columns, factbookOrderListColumns)}
              columnLabels={factbookOrderColumnLabels}
              clampAllCells
            />
            <Pager page={orders.page} totalPages={orders.total_pages} total={orders.total} onPage={setPage} itemLabel="カテゴリ" />
          </>
        ) : (
          <Empty message="ファクトブック由来データを読み込み中です。" />
        )}
      </div>
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>発見済み資料</h2>
            <p className="muted">{documents ? `${documents.total}件` : '読み込み中です。'}</p>
          </div>
        </div>
        {documents ? (
          <>
            <DataTable
              data={documents.rows}
              columns={onlyExistingColumns(documents.columns, factbookDocumentListColumns)}
              columnLabels={factbookDocumentColumnLabels}
              clampAllCells
            />
            <Pager page={documents.page} totalPages={documents.total_pages} total={documents.total} onPage={setDocumentPage} itemLabel="資料" />
          </>
        ) : (
          <Empty message="公式資料候補を読み込み中です。" />
        )}
      </div>
    </section>
  );
}

function AuditPanel({
  initialTarget,
  refreshToken
}: {
  initialTarget: { company_year_id: string; field_id: string } | null;
  refreshToken: number;
}) {
  const [page, setPage] = React.useState(1);
  const [companyYearId, setCompanyYearId] = React.useState(initialTarget?.company_year_id || '');
  const [fieldId, setFieldId] = React.useState(initialTarget?.field_id || '');
  const [search, setSearch] = React.useState('');
  const [data, setData] = React.useState<Page | null>(null);

  React.useEffect(() => {
    if (initialTarget) {
      setCompanyYearId(initialTarget.company_year_id);
      setFieldId(initialTarget.field_id);
      setPage(1);
    }
  }, [initialTarget]);

  React.useEffect(() => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: '50',
      company_year_id: companyYearId,
      field_id: fieldId,
      search
    });
    api<Page>(`/api/datasets/audit?${params}`).then(setData);
  }, [page, companyYearId, fieldId, search, refreshToken]);

  return (
    <section className="stack">
      <FilterBar>
        <input value={companyYearId} onChange={(e) => setCompanyYearId(e.target.value)} placeholder="company_year_id" />
        <input value={fieldId} onChange={(e) => setFieldId(e.target.value)} placeholder="field_id" />
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="根拠・見出しを検索" />
      </FilterBar>
      {data ? (
        <>
          <DataTable
            data={data.rows}
            columns={onlyExistingColumns(data.columns, auditListColumns)}
            columnLabels={{ ...baseColumnLabels, ...sourceSummaryColumnLabels }}
            clampAllCells
          />
          <Pager page={data.page} totalPages={data.total_pages} total={data.total} onPage={setPage} />
        </>
      ) : (
        <Empty message="根拠データを読み込み中です。" />
      )}
    </section>
  );
}

function ReviewPanel({
  initialTarget,
  job,
  onJob,
  onError,
  refreshToken
}: {
  initialTarget: ReviewTarget | null;
  job: Job | null;
  onJob: (job: Job) => void;
  onError: (message: string) => void;
  refreshToken: number;
}) {
  const reviewStatusOptions = [
    { value: 'active', label: '未対応' },
    { value: '', label: '全レビュー' },
    { value: 'saved', label: '保存済み' },
    { value: 'unsaved', label: '未保存' }
  ];
  const [page, setPage] = React.useState(1);
  const [company, setCompany] = React.useState('');
  const [year, setYear] = React.useState('');
  const [fieldId, setFieldId] = React.useState('');
  const [search, setSearch] = React.useState('');
  const [reviewStatus, setReviewStatus] = React.useState('active');
  const [reviewCategory, setReviewCategory] = React.useState('');
  const [data, setData] = React.useState<Page | null>(null);
  const [selected, setSelected] = React.useState<Row | null>(null);
  const [decision, setDecision] = React.useState('accept');
  const [correctedValue, setCorrectedValue] = React.useState('');
  const [fieldNameDraft, setFieldNameDraft] = React.useState('');
  const [note, setNote] = React.useState('');
  const [notApplicableScope, setNotApplicableScope] = React.useState('from_selected_year');
  const [message, setMessage] = React.useState('');
  const [panelError, setPanelError] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [savingFieldName, setSavingFieldName] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [applying, setApplying] = React.useState(false);
  const [automation, setAutomation] = React.useState<AutomationStatus | null>(null);
  const [automationError, setAutomationError] = React.useState('');  const editorRef = React.useRef<HTMLElement | null>(null);
  const autoSelectedTargetRef = React.useRef('');
  const { options, fieldLabels, refresh: refreshOptions } = useOptions();
  const jobRunning = job?.status === 'running';

  React.useEffect(() => {
    if (!initialTarget) return;
    setPage(1);
    setCompany(initialTarget.company);
    setYear(initialTarget.fiscal_year);
    setFieldId(initialTarget.field_id);
    setSearch('');
    setReviewStatus('');
    setReviewCategory('');
    autoSelectedTargetRef.current = '';
  }, [initialTarget]);

  const loadReviewQueue = React.useCallback(() => {
    let active = true;
    setData(null);
    const params = new URLSearchParams({
      page: String(page),
      page_size: '50',
      company,
      fiscal_year: year,
      field_id: fieldId,
      search,
      review_status: reviewStatus,
      review_category: reviewCategory
    });
    api<Page>(`/api/reviews/queue?${params}`)
      .then((next) => {
        if (active) setData(next);
      })
      .catch((err) => {
        if (active) onError(String(err));
      });
    return () => {
      active = false;
    };
  }, [page, company, year, fieldId, search, reviewStatus, reviewCategory, onError, refreshToken]);

  React.useEffect(() => loadReviewQueue(), [loadReviewQueue]);

  const loadAutomationStatus = React.useCallback(() => {
    api<AutomationStatus>('/api/automation/status')
      .then((next) => {
        setAutomation(next);
        setAutomationError('');
      })
      .catch((err) => setAutomationError(String(err)));
  }, [refreshToken]);

  React.useEffect(() => {
    loadAutomationStatus();
  }, [loadAutomationStatus]);

  const selectedRowKey = selected ? reviewRowKey(selected) : '';

  React.useEffect(() => {
    if (!selectedRowKey || !data?.rows.length) return;
    const refreshed = data.rows.find((row) => reviewRowKey(row) === selectedRowKey);
    if (refreshed && refreshed !== selected) {
      setSelected(refreshed);
    }
  }, [data, selected, selectedRowKey]);

  function pick(row: Row) {
    const extractedValue = String(row.extracted_value || '').trim();
    const existingDecision = String(row.review_decision || '').trim();
    setSelected(row);
    setDecision(existingDecision || (extractedValue ? 'accept' : 'correct'));
    setCorrectedValue(String(row.corrected_value || row.extracted_value || ''));
    setFieldNameDraft(String(row.field_name_ja || ''));
    setNote(String(row.reviewer_note || ''));
    setMessage(row.review_saved === 'yes' ? '保存済みレビューを読み込みました。修正して保存すると上書きされます。' : '');
    setPanelError('');
    window.setTimeout(() => {
      editorRef.current?.scrollIntoView({ behavior: 'smooth', block: window.innerWidth <= 900 ? 'start' : 'nearest' });
    }, 0);
  }

  React.useEffect(() => {
    if (!initialTarget || !data?.rows.length) return;
    const targetRequestKey = `${initialTarget.company}::${initialTarget.fiscal_year}::${initialTarget.field_id}`;
    if (autoSelectedTargetRef.current === targetRequestKey) return;
    const target = data.rows.find(
      (row) =>
        String(row.company_year_id || '').startsWith(`${initialTarget.company}_`) &&
        String(row.fiscal_year || '') === String(initialTarget.fiscal_year || '') &&
        String(row.field_id || '') === String(initialTarget.field_id || '')
    );
    if (target) {
      autoSelectedTargetRef.current = targetRequestKey;
      pick(target);
    }
  }, [data, initialTarget]);

  async function save() {
    if (!selected) return;
    if (jobRunning) {
      setPanelError('ジョブ実行中です。作業ログの完了を待ってから保存してください。');
      return;
    }
    const extractedValue = String(selected.extracted_value || '').trim();
    if (decision === 'accept' && !extractedValue) {
      setPanelError('抽出値が空のため accept では保存できません。correct を選び、修正値を入力してください。');
      return;
    }
    if (decision === 'correct' && !correctedValue.trim()) {
      setPanelError('correct で保存するには修正値を入力してください。');
      return;
    }
    setSaving(true);
    setPanelError('');
    try {
      const payload = {
        company_year_id: selected.company_year_id,
        field_id: selected.field_id,
        review_decision: decision,
        corrected_value: correctedValue,
        reviewer_note: note
      };
      const result = await api<{ changed: number; total: number; path: string }>('/api/reviews/resolved', {
        method: 'POST',
        body: JSON.stringify({ reviews: [payload] })
      });
      setMessage(`保存しました: ${result.changed}件 / resolved合計 ${result.total}件。`);
      loadReviewQueue();
      loadAutomationStatus();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function saveFieldName() {
    if (!selected) return;
    if (jobRunning) {
      setPanelError('ジョブ実行中です。作業ログの完了を待ってから項目名を更新してください。');
      return;
    }
    const fieldIdValue = String(selected.field_id || '').trim();
    const nextName = fieldNameDraft.trim();
    const currentName = String(selected.field_name_ja || '').trim();
    if (!fieldIdValue || !nextName || nextName === currentName) return;
    setSavingFieldName(true);
    setPanelError('');
    try {
      await api(`/api/field-definitions/${encodeURIComponent(fieldIdValue)}`, {
        method: 'POST',
        body: JSON.stringify({ updates: { field_name_ja: nextName } })
      });
      try {
        await api(`/api/concepts/${encodeURIComponent(fieldIdValue)}`, {
          method: 'POST',
          body: JSON.stringify({ updates: { concept_name_ja: nextName } })
        });
      } catch {
        // field_definitionだけに存在する項目もあるため、概念同期の失敗は表示名更新自体を妨げない。
      }
      setSelected({ ...selected, field_name_ja: nextName });
      refreshOptions();
      loadReviewQueue();
      setMessage(`項目名を更新しました: ${fieldIdValue} → ${nextName}`);
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setSavingFieldName(false);
    }
  }

  async function applyReview() {
    if (jobRunning) {
      setPanelError('ジョブ実行中です。作業ログの完了を待ってから最終反映してください。');
      return;
    }
    setApplying(true);
    setPanelError('');
    try {
      const next = await api<Job>('/api/jobs/apply-review', { method: 'POST' });
      onJob(next);
      setMessage('保存済みレビュー全体を最終データへ一括反映するジョブを開始しました。');
      window.setTimeout(loadAutomationStatus, 1000);
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setApplying(false);
    }
  }

  async function markSelectedCompanyFieldNotApplicable() {
    if (!selected || !selectedCompanyId) return;
    if (jobRunning) {
      setPanelError('ジョブ実行中です。作業ログの完了を待ってから対象外設定してください。');
      return;
    }
    const fieldIdValue = String(selected.field_id || '');
    const fieldLabel = String(selected.field_name_ja || fieldIdValue);
    const fiscalYearValue = yearFromReviewRow(selected);
    const range = notApplicableRange(notApplicableScope, fiscalYearValue);
    const defaultReason = `${selectedCompanyId} / ${fieldLabel} は同業比較の分析項目として対象外`;
    const ok = window.confirm(`${selectedCompanyId} の${range.label}について「${fieldLabel}」を対象外として保存します。よろしいですか？`);
    if (!ok) return;
    setSaving(true);
    setPanelError('');
    try {
      const result = await api<{
        changed: number;
        total: number;
        marked: number;
        replaced_exclusions?: number;
        stale_not_applicable_deleted?: number;
      }>('/api/reviews/not-applicable', {
        method: 'POST',
        body: JSON.stringify({
          company_id: selectedCompanyId,
          field_id: fieldIdValue,
          start_year: range.startYear,
          end_year: range.endYear,
          note: note || defaultReason
        })
      });
      setDecision('not_applicable');
      setCorrectedValue('');
      const cleanupMessage = [
        result.replaced_exclusions ? `既存設定${result.replaced_exclusions}件を置換` : '',
        result.stale_not_applicable_deleted ? `範囲外の古い対象外${result.stale_not_applicable_deleted}件を解除` : ''
      ].filter(Boolean).join(' / ');
      setMessage(`${selectedCompanyId} / ${fieldIdValue} / ${range.label} を対象外として ${result.marked}件保存しました。${cleanupMessage ? `${cleanupMessage}。` : ''}最終データへ反映するには、上部の一括反映を実行してください。`);
      loadReviewQueue();
      loadAutomationStatus();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function deleteReview() {
    if (!selected || !isEditingSavedReview) return;
    if (jobRunning) {
      setPanelError('ジョブ実行中です。作業ログの完了を待ってから削除してください。');
      return;
    }
    const ok = window.confirm('この保存済みレビューを削除します。review_queue.csv は残ります。');
    if (!ok) return;
    setDeleting(true);
    setPanelError('');
    try {
      const payload = {
        company_year_id: selected.company_year_id,
        field_id: selected.field_id
      };
      const result = await api<{ deleted: number; total: number; path: string }>('/api/reviews/resolved/delete', {
        method: 'POST',
        body: JSON.stringify({ reviews: [payload] })
      });
      setSelected(null);
      setDecision('accept');
      setCorrectedValue('');
      setNote('');
      setMessage(`削除しました: ${result.deleted}件 / resolved残り ${result.total}件`);
      loadReviewQueue();
      loadAutomationStatus();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setDeleting(false);
    }
  }

  const extractedValue = String(selected?.extracted_value || '').trim();
  const isEditingSavedReview = selected?.review_saved === 'yes';
  const selectedCompanyId = selected ? companyIdFromCompanyYear(String(selected.company_year_id || '')) : '';
  const selectedFiscalYear = selected ? yearFromReviewRow(selected) : null;
  const canSave = Boolean(selected) && !saving && !(decision === 'correct' && !correctedValue.trim()) && !(decision === 'accept' && !extractedValue);
  const savedUnappliedReviews = automation?.review_gate.saved_unapplied_reviews ?? 0;
  const activeReviewItems = automation?.review_gate.active_review_items ?? data?.total ?? 0;

  return (
    <section className="review-layout">
      <div className="stack">
        <ReviewWorkflowGuide savedUnappliedReviews={savedUnappliedReviews} activeReviewItems={activeReviewItems} />
        <ReviewTerminal job={job} />
        {jobRunning && <p className="hint action-lock">ジョブ実行中です。完了まで保存・候補反映・再取得・最終反映はできません。</p>}
        <div className="filter-bar review-filter-bar">
          <label className="filter-field">
            <span>会社</span>
            <select value={company} onChange={(e) => { setPage(1); setCompany(e.target.value); }}>
              <option value="">全社</option>
              {(options?.companies || []).map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
          </label>
          <label className="filter-field small">
            <span>年度</span>
            <select value={year} onChange={(e) => { setPage(1); setYear(e.target.value); }}>
              <option value="">全年度</option>
              {(options?.years || []).map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </label>
          <label className="filter-field">
            <span>項目</span>
            <select value={fieldId} onChange={(e) => { setPage(1); setFieldId(e.target.value); }}>
              <option value="">全項目</option>
              {(options?.fields || []).map((item) => (
                <option key={item.id} value={item.id}>{item.name || item.id}</option>
              ))}
            </select>
          </label>
          <div className="filter-field review-status-filter">
            <span>レビュー状態</span>
            <div className="segmented-control review-status-control">
              {reviewStatusOptions.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={reviewStatus === item.value ? 'active' : ''}
                  onClick={() => {
                    setPage(1);
                    setReviewStatus(item.value);
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <label className="filter-field review-search-field">
            <span>検索</span>
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="会社・項目・理由を検索" />
          </label>
        </div>
        <ReviewCategorySummary
          data={data}
          currentCategory={reviewCategory}
          onCategory={(category) => {
            setPage(1);
            setReviewCategory((current) => current === category ? '' : category);
          }}
        />
        <div className="panel review-batch-panel">
          <div>
            <h2>3. 最終データへ一括反映</h2>
            <p className="muted">保存済みレビュー全体を final_master、分析データ、レポートへ反映します。レビューと再取得結果を確認してから押します。</p>
          </div>
          <div className="review-batch-metrics">
            <div className="metric compact">
              <small>未反映レビュー</small>
              <strong className={savedUnappliedReviews ? 'text-warn' : 'text-ok'}>{automation ? savedUnappliedReviews : '-'}</strong>
            </div>
            <div className="metric compact">
              <small>未対応レビュー</small>
              <strong>{automation ? activeReviewItems : '-'}</strong>
            </div>
          </div>
          <button
            type="button"
            className="secondary"
            onClick={applyReview}
            disabled={jobRunning || applying || (automation !== null && savedUnappliedReviews === 0)}
          >
            {applying ? '一括反映中...' : '最終データへ一括反映'}
          </button>
          {automationError && <InlineError message={automationError} />}
        </div>
        {data ? (
          <div className="panel review-queue-panel">
            <div className="panel-head">
              <h2>レビュー一覧</h2>
              <p className="muted">行をクリックすると右側のレビュー編集欄に読み込みます。</p>
            </div>
            <DataTable
              data={data.rows}
              columns={onlyExistingColumns(data.columns, reviewListColumns)}
              columnLabels={{ ...fieldLabels, ...reviewColumnLabels }}
              onRowClick={pick}
              selectedRowKey={selectedRowKey}
              getRowKey={reviewRowKey}
              compact
              clampAllCells
            />
            <Pager page={data.page} totalPages={data.total_pages} total={data.total} onPage={setPage} />
          </div>
        ) : (
          <Empty message="レビューキューを読み込み中です。" />
        )}
      </div>
      <aside className="editor" ref={editorRef}>
        <h2>1. レビュー編集</h2>
        {selected ? (
          <>
            <dl>
              <dt>対象</dt>
              <dd>{String(selected.company_year_id)} / {String(selected.field_id)}</dd>
              <dt>項目</dt>
              <dd>{String(selected.field_name_ja || '')}</dd>
              <dt>抽出値</dt>
              <dd>{String(selected.extracted_value || '')}</dd>
              <dt>理由</dt>
              <dd>{String(selected.review_reason || '')}</dd>
              <dt>保存状態</dt>
              <dd>{isEditingSavedReview ? `保存済み ${String(selected.reviewed_at || '')}` : '未保存'}</dd>
              <dt>反映状態</dt>
              <dd>{appliedStatusLabel(selected.applied_status)}{selected.applied_at ? ` ${String(selected.applied_at)}` : ''}</dd>
              <dt>反映値</dt>
              <dd>{String(selected.applied_value || '-')}</dd>
            </dl>
            <div className="field-name-editor">
              <label>項目名</label>
              <input value={fieldNameDraft} onChange={(e) => setFieldNameDraft(e.target.value)} />
              <button
                type="button"
                className="secondary"
                onClick={saveFieldName}
                disabled={savingFieldName || jobRunning || !fieldNameDraft.trim() || fieldNameDraft.trim() === String(selected.field_name_ja || '').trim()}
              >
                {savingFieldName ? '項目名更新中...' : '項目名を更新'}
              </button>
              <p className="hint">値ではなく項目名が誤っている場合はここで直します。項目の名称表示を更新し、抽出値やレビュー判定は変更しません。</p>
            </div>
            {isEditingSavedReview && <p className="hint">この行は保存済みレビューです。内容を直して保存すると同じ会社年度・項目を上書きします。</p>}
            <label>判定</label>
            <select value={decision} onChange={(e) => setDecision(e.target.value)}>
              <option value="accept">accept</option>
              <option value="correct">correct</option>
              <option value="reject">reject</option>
              <option value="not_applicable">not_applicable（対象外）</option>
            </select>
            <label>修正値</label>
            <input value={correctedValue} onChange={(e) => setCorrectedValue(e.target.value)} disabled={decision !== 'correct'} />
            <label>メモ（任意）</label>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={5} />
            <p className="hint">値が正しいなら accept/correct、値が誤りなら reject、項目を採用しないなら not_applicable を使ってください。項目名だけが誤っている場合は上の「項目名を更新」で直します。</p>
            {decision === 'correct' && !correctedValue.trim() && <p className="hint">修正値を入力すると保存できます。</p>}
            {decision === 'accept' && !extractedValue && <p className="hint">抽出値が空の行は correct で修正値を入力してください。</p>}
            {decision === 'not_applicable' && <p className="hint">対象外は値を作らず、レビュー済みとして扱います。この会社・年度では項目を採用しない場合に使ってください。項目名の誤りは上の入力欄で修正します。</p>}
            {selectedCompanyId && (
              <div className="not-applicable-scope">
                <label>会社・項目対象外の範囲</label>
                <select value={notApplicableScope} onChange={(e) => setNotApplicableScope(e.target.value)} disabled={saving || jobRunning}>
                  <option value="from_selected_year">{selectedFiscalYear ? `${selectedFiscalYear}年度以降` : '選択年度以降'}</option>
                  <option value="after_selected_year">{selectedFiscalYear ? `${selectedFiscalYear + 1}年度以降（当年を含まない）` : '選択翌年度以降'}</option>
                  <option value="selected_year">{selectedFiscalYear ? `${selectedFiscalYear}年度のみ` : '選択年度のみ'}</option>
                  <option value="until_selected_year">{selectedFiscalYear ? `${selectedFiscalYear}年度以前` : '選択年度以前'}</option>
                  <option value="before_selected_year">{selectedFiscalYear ? `${selectedFiscalYear - 1}年度以前（当年を含まない）` : '選択前年度以前'}</option>
                  <option value="all_years">全年度</option>
                </select>
                <p className="hint">選択中の会社・項目について、指定範囲のレビュー候補をまとめて not_applicable にします。子会社化後だけ項目が消える場合や、この会社では比較対象外にする場合に使ってください。</p>
              </div>
            )}
            {panelError && <InlineError message={panelError} />}
            <div className="toolbar">
              <button type="button" onClick={save} disabled={!canSave || jobRunning}>{saving ? '保存中...' : isEditingSavedReview ? '1. 上書き保存' : '1. 保存'}</button>
              {selectedCompanyId && (
                <button type="button" className="secondary" onClick={markSelectedCompanyFieldNotApplicable} disabled={saving || jobRunning}>
                  会社・項目を範囲指定で対象外
                </button>
              )}
              {isEditingSavedReview && (
                <button type="button" className="danger" onClick={deleteReview} disabled={deleting || jobRunning}>{deleting ? '削除中...' : 'レビュー削除'}</button>
              )}
            </div>
            {message && <p className="success-text">{message}</p>}
          </>
        ) : (
          <Empty message="左の表から行を選んでください。" />
        )}
      </aside>
    </section>
  );
}

function ReviewWorkflowGuide({
  savedUnappliedReviews,
  activeReviewItems
}: {
  savedUnappliedReviews: number;
  activeReviewItems: number;
}) {
  const steps = [
    {
      title: '1. レビューして保存',
      body: '怪しい値・空欄を確認し、accept / correct / reject / 対象外で保存します。',
      meta: `${activeReviewItems}件 未対応`
    },
    {
      title: '2. 候補を反映して再取得',
      body: '現在の抽出設定で再取得し、保存済みレビューを再適用します。',
      meta: '再取得'
    },
    {
      title: '3. 最終データへ一括反映',
      body: '保存済みレビューを final_master、分析データ、レポートへ反映します。',
      meta: `${savedUnappliedReviews}件 未反映`
    }
  ];

  return (
    <div className="panel review-workflow-panel">
      <div className="panel-head">
        <div>
          <h2>レビュー作業の流れ</h2>
          <p className="muted">通常は 1 から 3 の順に進めます。セル値修正は保存済みレビューとして残り、最終反映で完成表へ反映されます。</p>
        </div>
      </div>
      <div className="review-workflow-steps">
        {steps.map((step) => (
          <div className="workflow-step" key={step.title}>
            <strong>{step.title}</strong>
            <span>{step.body}</span>
            <small>{step.meta}</small>
          </div>
        ))}
      </div>
    </div>
  );
}

function ReviewCategorySummary({
  data,
  currentCategory,
  onCategory
}: {
  data: Page | null;
  currentCategory: string;
  onCategory: (category: string) => void;
}) {
  const counts = data?.review_category_counts || {};
  const labels = data?.review_category_labels || {};
  const visible = REVIEW_CATEGORY_ORDER.filter((key) => counts[key] || key !== 'resolved_done');
  if (!data) return null;
  return (
    <div className="panel review-category-panel">
      <div className="panel-head">
        <div>
          <h2>レビュー内訳</h2>
          <p className="muted">未対応が増えた時は、まずこの内訳で「未取得」「検算」「スコープ」「警告」のどれが残っているかを見ます。</p>
        </div>
      </div>
      <div className="review-category-grid">
        {visible.map((key) => (
          <button
            type="button"
            className={`review-category-card ${key} ${currentCategory === key ? 'active' : ''}`.trim()}
            key={key}
            onClick={() => onCategory(key)}
          >
            <small>{labels[key] || key}</small>
            <strong>{counts[key] || 0}</strong>
          </button>
        ))}
      </div>
    </div>
  );
}

function AiPanel({ onError }: { onError: (message: string) => void }) {
  const [theme, setTheme] = React.useState('建設会社の営業戦略・収益性・受注構造を比較分析してください。');
  const [company, setCompany] = React.useState('');
  const [year, setYear] = React.useState('');
  const [field, setField] = React.useState('');
  const [extra, setExtra] = React.useState('');
  const [prompt, setPrompt] = React.useState('');
  const [references, setReferences] = React.useState<string[]>([]);
  const [auditResult, setAuditResult] = React.useState<AlgorithmAuditResult | null>(null);
  const [auditPrompt, setAuditPrompt] = React.useState('');
  const [auditLoading, setAuditLoading] = React.useState(false);
  const [auditError, setAuditError] = React.useState('');
  const { options } = useOptions();

  async function generate() {
    try {
      const result = await api<{ prompt: string; references: string[] }>('/api/ai/prompt', {
          method: 'POST',
          body: JSON.stringify({
            theme,
          companies: company ? [company] : [],
          fiscal_years: year ? [year] : [],
          fields: field ? [field] : [],
          extra_instruction: extra
        })
      });
      setPrompt(result.prompt);
      setReferences(result.references);
    } catch (err) {
      onError(String(err));
    }
  }

  async function generateAlgorithmAudit() {
    setAuditLoading(true);
    setAuditError('');
    try {
      const result = await api<AlgorithmAuditResult>('/api/algorithm-audit/build', { method: 'POST' });
      setAuditResult(result);
      setAuditPrompt(result.prompt);
    } catch (err) {
      const message = String(err);
      setAuditError(message);
      onError(message);
    } finally {
      setAuditLoading(false);
    }
  }

  const riskCount = auditResult?.summary?.risk_flags ?? '-';

  return (
    <section className="ai-layout">
      <div className="stack">
        <div className="panel form-panel">
          <h2>データ分析プロンプト</h2>
          <label>分析目的</label>
          <textarea value={theme} onChange={(e) => setTheme(e.target.value)} rows={5} />
          <label>会社</label>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            <option value="">全社</option>
            {(options?.companies || []).map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
          <label>年度</label>
          <select value={year} onChange={(e) => setYear(e.target.value)}>
            <option value="">全年度</option>
            {(options?.years || []).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
          <label>指標</label>
          <select value={field} onChange={(e) => setField(e.target.value)}>
            <option value="">指定なし</option>
            {(options?.fields || []).map((item) => (
              <option key={item.id} value={item.id}>{item.name || item.id}</option>
            ))}
          </select>
          <label>追加指示</label>
          <textarea value={extra} onChange={(e) => setExtra(e.target.value)} rows={4} />
          <button onClick={generate}>プロンプト生成</button>
        </div>
        <div className="panel form-panel">
          <h2>アルゴリズム監査</h2>
          <p className="hint">レビュー由来ルール、抽出設定、主要コードをAIに監査させるためのローカルパックを生成します。</p>
          <button onClick={generateAlgorithmAudit} disabled={auditLoading}>
            {auditLoading ? '生成中...' : '監査パック生成'}
          </button>
          {auditError && <InlineError message={auditError} />}
          {auditResult && (
            <dl className="candidate-detail">
              <dt>出力先</dt>
              <dd>{auditResult.bundle_dir}</dd>
              <dt>生成日時</dt>
              <dd>{auditResult.generated_at_utc}</dd>
              <dt>赤旗</dt>
              <dd>{String(riskCount)}件</dd>
              <dt>主なファイル</dt>
              <dd>{auditResult.files.slice(0, 6).map((item) => item.file).join(', ')}</dd>
            </dl>
          )}
        </div>
      </div>
      <div className="stack">
        <div className="panel">
          <div className="panel-head">
            <h2>生成プロンプト</h2>
            <button className="ghost" onClick={() => navigator.clipboard.writeText(prompt)}>分析プロンプトをコピー</button>
          </div>
          {references.length > 0 && <p className="muted">参照: {references.join(', ')}</p>}
          <pre className="prompt">{prompt || '条件を入力してプロンプトを生成してください。'}</pre>
        </div>
        <div className="panel">
          <div className="panel-head">
            <h2>アルゴリズム監査プロンプト</h2>
            <button className="ghost" onClick={() => navigator.clipboard.writeText(auditPrompt)} disabled={!auditPrompt}>監査プロンプトをコピー</button>
          </div>
          <pre className="prompt">{auditPrompt || '監査パックを生成すると、AIに渡す監査プロンプトが表示されます。'}</pre>
        </div>
      </div>
    </section>
  );
}

function ChartsPanel({ refreshToken }: { refreshToken: number }) {
  const { options, error: optionsError } = useOptions();
  const { options: stockOptions, error: stockOptionsError } = useStockOptions();
  const { options: factbookOptions, error: factbookOptionsError } = useFactbookOptions();
  const exportRef = React.useRef<HTMLDivElement>(null);
  const [chartSource, setChartSource] = React.useState<ChartSource>('financial');
  const [periodType, setPeriodType] = React.useState('annual');
  const [viewMode, setViewMode] = React.useState<ChartViewMode>('chart');
  const [chartKind, setChartKind] = React.useState<ChartKind>('line');
  const [mode, setMode] = React.useState<ChartMode>('trend');
  const [selectedCompanies, setSelectedCompanies] = React.useState<string[]>([]);
  const [selectedYears, setSelectedYears] = React.useState<string[]>([]);
  const [selectedFields, setSelectedFields] = React.useState<string[]>([]);
  const [rightAxisFields, setRightAxisFields] = React.useState<string[]>([]);
  const [showValueLabels, setShowValueLabels] = React.useState(false);
  const [editMode, setEditMode] = React.useState(false);
  const [selectedSeriesKey, setSelectedSeriesKey] = React.useState('');
  const [seriesStyles, setSeriesStyles] = React.useState<Record<string, SeriesStyle>>({});
  const [exportSettings, setExportSettings] = React.useState<ExportSettings>(defaultExportSettings);
  const [exportMessage, setExportMessage] = React.useState('');
  const [exportError, setExportError] = React.useState('');
  const [showCorrelation, setShowCorrelation] = React.useState(false);
  const [scatterX, setScatterX] = React.useState('');
  const [scatterY, setScatterY] = React.useState('');
  const [axisOverrides, setAxisOverrides] = React.useState<Record<string, string>>({});
  const [data, setData] = React.useState<ChartData | null>(null);
  const [error, setError] = React.useState('');

  const activeOptions = chartSource === 'stock' ? stockOptions : chartSource === 'factbook_orders' ? factbookOptions : options;
  const activePeriods = chartSource === 'stock' ? stockOptions?.months || [] : chartSource === 'factbook_orders' ? factbookOptions?.years || [] : options?.years || [];
  const activeFields = activeOptions?.fields || [];
  const activeCompanies = activeOptions?.companies || [];

  React.useEffect(() => {
    if (!activeOptions) return;
    const companyIds = new Set(activeCompanies.map((item) => item.id));
    const periodIds = new Set(activePeriods);
    const fieldIds = new Set(activeFields.map((item) => item.id));
    setSelectedCompanies((current) => current.filter((id) => companyIds.has(id)));
    setSelectedYears((current) => current.filter((id) => periodIds.has(id)));
    setSelectedFields((current) => current.filter((id) => fieldIds.has(id)));
    setScatterX((current) => current && fieldIds.has(current) ? current : '');
    setScatterY((current) => current && fieldIds.has(current) ? current : '');
    setRightAxisFields((current) => current.filter((fieldId) => fieldIds.has(fieldId)));
    setSelectedSeriesKey('');
  }, [activeOptions, activeCompanies, activePeriods, activeFields, chartSource]);

  React.useEffect(() => {
    setRightAxisFields((current) => current.filter((fieldId) => selectedFields.includes(fieldId)));
  }, [selectedFields]);

  React.useEffect(() => {
    if (viewMode !== 'table') return;
    setSelectedFields((current) => current.slice(0, 1));
    setRightAxisFields([]);
  }, [viewMode]);

  const queryFields = React.useMemo(() => {
    if (viewMode === 'table') {
      return selectedFields.slice(0, 1);
    }
    if (chartKind === 'scatter') {
      return [scatterX, scatterY].filter(Boolean);
    }
    return selectedFields;
  }, [chartKind, scatterX, scatterY, selectedFields, viewMode]);

  const requiredFieldCount = viewMode === 'chart' && chartKind === 'scatter' ? 2 : 1;
  const hasRequiredSelections = selectedCompanies.length > 0 && selectedYears.length > 0 && queryFields.length >= requiredFieldCount;

  React.useEffect(() => {
    if (!activeOptions) return;
    if (!hasRequiredSelections) {
      setData(null);
      setError('');
      return;
    }
    const params = new URLSearchParams({
      source: chartSource,
      companies: selectedCompanies.join(','),
      fiscal_years: selectedYears.join(','),
      period_type: periodType,
      fields: queryFields.join(','),
      max_rows: '5000'
    });
    api<ChartData>(`/api/charts/data?${params}`)
      .then((next) => {
        setData(next);
        setError('');
      })
      .catch((err) => setError(String(err)));
  }, [activeOptions, chartSource, selectedCompanies, selectedYears, periodType, queryFields, refreshToken, hasRequiredSelections]);

  if (!options || (chartSource === 'stock' && !stockOptions) || (chartSource === 'factbook_orders' && !factbookOptions)) {
    return <Empty message="グラフ設定を読み込み中です。" />;
  }

  const fieldsById = new Map(activeFields.map((field) => [field.id, field]));
  const fieldChoices = activeFields.filter((field) => chartFieldEligible(field));
  const selectedFieldOptions = selectedFields.map((fieldId) => fieldsById.get(fieldId)).filter(Boolean) as FieldOption[];
  const chartRows = data?.rows || [];
  const analysisTable = buildAnalysisTableRows(chartRows, queryFields, fieldsById, chartSource, selectedYears, activeCompanies);
  const tableField = queryFields[0] ? fieldsById.get(queryFields[0]) : undefined;
  const trend = buildTrendChartRows(chartRows, selectedFields, fieldsById, selectedCompanies.length);
  const companyBars = buildCompanyChartRows(chartRows, selectedFields, fieldsById, selectedYears.length);
  const scatter = buildScatterRows(chartRows, scatterX, scatterY);
  const fullSeries = mode === 'trend' ? trend.series : companyBars.series;
  const series = fullSeries.slice(0, 18);
  const hasSeriesOverflow = fullSeries.length > series.length;
  const selectedRightAxisFields = rightAxisFields.filter((fieldId) => selectedFields.includes(fieldId));
  const selectedSeries = series.find((item) => item.key === selectedSeriesKey) || series[0] || null;
  const selectedSeriesIndex = selectedSeries ? Math.max(0, series.findIndex((item) => item.key === selectedSeries.key)) : 0;
  const selectedSeriesStyle = selectedSeries ? seriesStyles[selectedSeries.key] || {} : {};
  const selectedSeriesRenderKind = getSeriesRenderKind(chartKind, selectedSeriesIndex, selectedSeriesStyle);
  const activeAxisRanges: { left?: [number, number]; right?: [number, number]; x?: [number, number]; y?: [number, number] } = chartKind === 'scatter'
    ? {
        x: niceAxisRange(scatter.map((row) => numericValue(row.x)).filter((value): value is number => value != null), { includeZero: false }),
        y: niceAxisRange(scatter.map((row) => numericValue(row.y)).filter((value): value is number => value != null), { includeZero: false }),
      }
    : chartAxisRanges({
        kind: chartKind,
        rows: mode === 'company' ? companyBars.rows : trend.rows,
        series,
        rightAxisFields: selectedRightAxisFields,
        seriesStyles,
      });
  const axisDomains = axisDomainsFromOverrides(axisOverrides, activeAxisRanges);
  const previewRenderOptions: ChartRenderOptions = {
    height: 420,
    exportMode: false,
    exportSettings,
    axisDomains,
  };
  const exportRenderOptions: ChartRenderOptions = {
    height: Math.max(260, exportSettings.height - 34),
    exportMode: true,
    exportSettings,
    axisDomains,
  };

  function updateSelectedSeriesStyle(patch: SeriesStyle) {
    if (!selectedSeries) return;
    setSeriesStyles((current) => ({
      ...current,
      [selectedSeries.key]: {
        ...(current[selectedSeries.key] || {}),
        ...patch,
      },
    }));
  }

  function resetSelectedSeriesStyle() {
    if (!selectedSeries) return;
    setSeriesStyles((current) => {
      const next = { ...current };
      delete next[selectedSeries.key];
      return next;
    });
  }

  function updateExportSettings(patch: Partial<ExportSettings>) {
    setExportSettings((current) => ({ ...current, ...patch }));
  }

  function updateAxisOverride(key: string, value: string) {
    setAxisOverrides((current) => ({ ...current, [key]: value }));
  }

  function fillAxisOverridesFromAuto() {
    setAxisOverrides(axisOverridesFromAuto(activeAxisRanges));
  }

  function clearAxisOverrides() {
    setAxisOverrides({});
  }

  function applyExportPreset(presetId: ExportPresetId) {
    const preset = exportSizePresets.find((item) => item.id === presetId) || exportSizePresets[0];
    setExportSettings((current) => ({
      ...current,
      presetId,
      width: preset.width,
      height: preset.height,
    }));
  }

  function applyDesignPreset(designPreset: DesignPreset) {
    setExportSettings((current) => ({
      ...current,
      designPreset,
      legendPosition: designPreset === 'minimal' ? 'none' : 'bottom',
      directLineLabels: designPreset === 'minimal' ? chartKind === 'line' || chartKind === 'combo' : current.directLineLabels,
      marginPreset: designPreset === 'minimal' ? 'compact' : designPreset === 'report' ? 'wide' : 'standard',
      fontScale: designPreset === 'report' ? 'large' : current.fontScale,
    }));
  }

  function resetChartSelections(nextSource = chartSource) {
    const sourceOptions = nextSource === 'stock' ? stockOptions : nextSource === 'factbook_orders' ? factbookOptions : options;
    if (!sourceOptions) return;
    setSelectedCompanies([]);
    setSelectedYears([]);
    setSelectedFields([]);
    setRightAxisFields([]);
    setScatterX('');
    setScatterY('');
    setSelectedSeriesKey('');
    setAxisOverrides({});
    setData(null);
  }

  function renderActiveChart(options: ChartRenderOptions) {
    if (!hasRequiredSelections) {
      return <Empty message={chartSelectionMessage(chartSource, viewMode, chartKind)} />;
    }
    if (chartKind === 'scatter') {
      return (
        <ScatterChartBlock
          rows={scatter}
          xLabel={fieldsById.get(scatterX)?.name || scatterX}
          yLabel={fieldsById.get(scatterY)?.name || scatterY}
          showCorrelation={showCorrelation}
          renderOptions={options}
        />
      );
    }
    const chartData = mode === 'company'
      ? { rows: companyBars.rows, xKey: 'label' }
      : { rows: trend.rows, xKey: 'fiscal_year' };
    return (
      <BarOrLineChartBlock
        kind={chartKind}
        rows={chartData.rows}
        xKey={chartData.xKey}
        series={series}
        rightAxisFields={selectedRightAxisFields}
        showValueLabels={showValueLabels}
        seriesStyles={seriesStyles}
        renderOptions={options}
      />
    );
  }

  async function copyTableCsv() {
    if (!analysisTable.rows.length) return;
    setExportMessage('');
    setExportError('');
    try {
      await navigator.clipboard.writeText(toCsv(analysisTable.rows, analysisTable.columns, analysisTable.labels));
      setExportMessage('表をCSVとしてコピーしました。');
    } catch (err) {
      setExportError(String(err));
    }
  }

  function exportTableCsv() {
    if (!analysisTable.rows.length) return;
    setExportMessage('');
    setExportError('');
    downloadText(toCsv(analysisTable.rows, analysisTable.columns, analysisTable.labels), chartExportFileName('csv'));
    setExportMessage('表CSVを書き出しました。');
  }

  async function exportPng(copyToClipboard = false) {
    if (!exportRef.current) return;
    setExportMessage('');
    setExportError('');
    try {
      const backgroundColor = exportSettings.background === 'white' ? '#ffffff' : undefined;
      const dataUrl = await toPng(exportRef.current, {
        pixelRatio: exportSettings.pixelRatio,
        backgroundColor,
        cacheBust: true,
      });
      if (copyToClipboard) {
        if (!navigator.clipboard || typeof ClipboardItem === 'undefined') {
          throw new Error('このブラウザでは画像のクリップボードコピーに対応していません。PNG保存を使ってください。');
        }
        const blob = await fetch(dataUrl).then((response) => response.blob());
        await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
        setExportMessage('PNGをクリップボードにコピーしました。');
      } else {
        downloadDataUrl(dataUrl, chartExportFileName('png'));
        setExportMessage('PNGを書き出しました。');
      }
    } catch (err) {
      setExportError(String(err));
    }
  }

  async function exportSvg() {
    if (!exportRef.current) return;
    setExportMessage('');
    setExportError('');
    try {
      const backgroundColor = exportSettings.background === 'white' ? '#ffffff' : undefined;
      const dataUrl = await toSvg(exportRef.current, {
        backgroundColor,
        cacheBust: true,
      });
      downloadDataUrl(dataUrl, chartExportFileName('svg'));
      setExportMessage('SVGを書き出しました。');
    } catch (err) {
      setExportError(String(err));
    }
  }

  return (
    <section className="stack">
      <div className="filter-bar chart-filter-bar">
        <label className="filter-field small">
          <span>データ</span>
          <select value={chartSource} onChange={(e) => {
            const nextSource = e.target.value as ChartSource;
            setChartSource(nextSource);
            resetChartSelections(nextSource);
          }}>
            <option value="financial">有報データ</option>
            <option value="stock">株価月次</option>
            <option value="factbook_orders">ファクトブック受注</option>
          </select>
        </label>
        <label className="filter-field small">
          <span>出力</span>
          <select value={viewMode} onChange={(e) => setViewMode(e.target.value as ChartViewMode)}>
            <option value="chart">グラフ</option>
            <option value="table">表</option>
          </select>
        </label>
        {chartSource === 'financial' && (
          <label className="filter-field small">
            <span>期間</span>
            <select value={periodType} onChange={(e) => setPeriodType(e.target.value)}>
              {(options?.period_types || ['annual']).map((item) => (
                <option key={item} value={item}>{periodTypeLabel(item)}</option>
              ))}
            </select>
          </label>
        )}
        {viewMode === 'chart' && (
          <label className="filter-field small">
            <span>種類</span>
            <select value={chartKind} onChange={(e) => setChartKind(e.target.value as ChartKind)}>
              <option value="line">折れ線</option>
              <option value="bar">棒</option>
              <option value="combo">複合</option>
              <option value="scatter">散布図</option>
            </select>
          </label>
        )}
        {viewMode === 'chart' && chartKind !== 'scatter' && (
          <label className="filter-field">
            <span>比較軸</span>
            <select value={mode} onChange={(e) => setMode(e.target.value as ChartMode)}>
              <option value="trend">年度推移（会社・項目を系列化）</option>
              <option value="company">会社比較（横軸を会社/年度）</option>
            </select>
          </label>
        )}
        <button className="ghost" onClick={() => {
          resetChartSelections();
        }}>
          初期化
        </button>
      </div>
      {optionsError && <InlineError message={optionsError} />}
      {stockOptionsError && <InlineError message={stockOptionsError} />}
      {factbookOptionsError && <InlineError message={factbookOptionsError} />}
      {error && <InlineError message={error} />}
      <div className="chart-workbench">
        <div className="panel chart-controls">
          <ChoiceGroup
            title="会社名"
            items={activeCompanies}
            selected={selectedCompanies}
            onToggle={(id) => setSelectedCompanies(toggleValue(selectedCompanies, id))}
            onAll={() => setSelectedCompanies(activeCompanies.map((item) => item.id))}
            onClear={() => setSelectedCompanies([])}
            allLabel="会社すべて"
            clearLabel="会社解除"
          />
          <ChoiceGroup
            title={chartSource === 'stock' ? '月' : '年度'}
            items={activePeriods.map((period) => ({ id: period, label: period, name: period }))}
            selected={selectedYears}
            onToggle={(id) => setSelectedYears(toggleValue(selectedYears, id))}
            onAll={() => setSelectedYears(activePeriods)}
            onClear={() => setSelectedYears([])}
            allLabel={chartSource === 'stock' ? '月すべて' : '年度すべて'}
            clearLabel={chartSource === 'stock' ? '月解除' : '年度解除'}
            compact
          />
          {viewMode === 'chart' && (
            <div className="chart-style-section">
              <div className="choice-head">
                <h3>表示設定</h3>
              </div>
              {chartKind !== 'scatter' ? (
                <>
                  <label className="check-field chart-label-toggle">
                    <input
                      type="checkbox"
                      checked={showValueLabels}
                      onChange={(event) => setShowValueLabels(event.target.checked)}
                    />
                    <span>値ラベルを表示</span>
                  </label>
                  <label className="check-field chart-label-toggle">
                    <input
                      type="checkbox"
                      checked={editMode}
                      onChange={(event) => setEditMode(event.target.checked)}
                    />
                    <span>編集モード</span>
                  </label>
                </>
              ) : (
                <label className="check-field chart-label-toggle">
                  <input
                    type="checkbox"
                    checked={showCorrelation}
                    onChange={(event) => setShowCorrelation(event.target.checked)}
                  />
                  <span>相関係数を表示</span>
                </label>
              )}
              {chartKind !== 'scatter' && editMode && selectedSeries && (
                <div className="series-editor">
                  <label className="filter-field">
                    <span>系列</span>
                    <select value={selectedSeries.key} onChange={(event) => setSelectedSeriesKey(event.target.value)}>
                      {series.map((item) => (
                        <option key={item.key} value={item.key}>{item.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="filter-field color-field">
                    <span>色</span>
                    <input
                      type="color"
                      value={selectedSeriesStyle.color || chartColors[selectedSeriesIndex % chartColors.length]}
                      onChange={(event) => updateSelectedSeriesStyle({ color: event.target.value })}
                    />
                  </label>
                  {chartKind === 'combo' && (
                    <label className="filter-field">
                      <span>表示形式</span>
                      <select
                        value={selectedSeriesRenderKind}
                        onChange={(event) => updateSelectedSeriesStyle({ renderAs: event.target.value as SeriesRenderKind })}
                      >
                        <option value="bar">棒</option>
                        <option value="line">線</option>
                      </select>
                    </label>
                  )}
                  {selectedSeriesRenderKind === 'line' && (
                    <>
                      <label className="filter-field">
                        <span>太さ {selectedSeriesStyle.strokeWidth || 2.2}</span>
                        <input
                          type="range"
                          min="1"
                          max="6"
                          step="0.2"
                          value={selectedSeriesStyle.strokeWidth || 2.2}
                          onChange={(event) => updateSelectedSeriesStyle({ strokeWidth: Number(event.target.value) })}
                        />
                      </label>
                      <label className="filter-field">
                        <span>線種</span>
                        <select
                          value={linePatternOptions.find((item) => item.dasharray === (selectedSeriesStyle.strokeDasharray || ''))?.id || 'solid'}
                          onChange={(event) => {
                            const selected = linePatternOptions.find((item) => item.id === event.target.value) || linePatternOptions[0];
                            updateSelectedSeriesStyle({ strokeDasharray: selected.dasharray });
                          }}
                        >
                          {linePatternOptions.map((item) => (
                            <option key={item.id} value={item.id}>{item.label}</option>
                          ))}
                        </select>
                      </label>
                    </>
                  )}
                  <button type="button" className="ghost" onClick={resetSelectedSeriesStyle}>選択系列を初期化</button>
                </div>
              )}
            </div>
          )}
          {viewMode === 'chart' && hasRequiredSelections && (
            <div className="chart-style-section axis-range-section">
              <div className="choice-head">
                <h3>縦軸レンジ</h3>
                <button type="button" className="ghost" onClick={clearAxisOverrides}>自動に戻す</button>
              </div>
              <p className="hint">空欄ならデータから自動設定します。数値を入れるとその値で固定します。</p>
              {chartKind === 'scatter' ? (
                <>
                  <AxisRangeInputs
                    title="X軸"
                    minKey="xMin"
                    maxKey="xMax"
                    range={activeAxisRanges.x}
                    overrides={axisOverrides}
                    onChange={updateAxisOverride}
                  />
                  <AxisRangeInputs
                    title="Y軸"
                    minKey="yMin"
                    maxKey="yMax"
                    range={activeAxisRanges.y}
                    overrides={axisOverrides}
                    onChange={updateAxisOverride}
                  />
                </>
              ) : (
                <>
                  <AxisRangeInputs
                    title="左軸"
                    minKey="leftMin"
                    maxKey="leftMax"
                    range={activeAxisRanges.left}
                    overrides={axisOverrides}
                    onChange={updateAxisOverride}
                  />
                  {selectedRightAxisFields.length > 0 && (
                    <AxisRangeInputs
                      title="右軸"
                      minKey="rightMin"
                      maxKey="rightMax"
                      range={activeAxisRanges.right}
                      overrides={axisOverrides}
                      onChange={updateAxisOverride}
                    />
                  )}
                </>
              )}
              <button type="button" className="secondary" onClick={fillAxisOverridesFromAuto}>自動値を入力して微調整</button>
            </div>
          )}
          {viewMode === 'chart' && (
            <div className="chart-style-section export-settings-panel">
            <div className="choice-head">
              <h3>PPT書き出し</h3>
            </div>
            <label className="filter-field">
              <span>サイズ</span>
              <select value={exportSettings.presetId} onChange={(event) => applyExportPreset(event.target.value as ExportPresetId)}>
                {exportSizePresets.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </label>
            <div className="export-size-grid">
              <label className="filter-field">
                <span>幅px</span>
                <input
                  type="number"
                  min="480"
                  max="2400"
                  step="10"
                  value={exportSettings.width}
                  onChange={(event) => updateExportSettings({ width: clampNumber(event.target.value, 480, 2400, exportSettings.width) })}
                />
              </label>
              <label className="filter-field">
                <span>高さpx</span>
                <input
                  type="number"
                  min="300"
                  max="1600"
                  step="10"
                  value={exportSettings.height}
                  onChange={(event) => updateExportSettings({ height: clampNumber(event.target.value, 300, 1600, exportSettings.height) })}
                />
              </label>
            </div>
            <div className="export-size-grid">
              <label className="filter-field">
                <span>解像度</span>
                <select value={exportSettings.pixelRatio} onChange={(event) => updateExportSettings({ pixelRatio: Number(event.target.value) as 1 | 2 | 3 })}>
                  <option value={1}>1x</option>
                  <option value={2}>2x</option>
                  <option value={3}>3x</option>
                </select>
              </label>
              <label className="filter-field">
                <span>背景</span>
                <select value={exportSettings.background} onChange={(event) => updateExportSettings({ background: event.target.value as ExportBackground })}>
                  <option value="white">白</option>
                  <option value="transparent">透明</option>
                </select>
              </label>
            </div>
            <label className="filter-field">
              <span>デザイン</span>
              <select value={exportSettings.designPreset} onChange={(event) => applyDesignPreset(event.target.value as DesignPreset)}>
                {Object.entries(designPresets).map(([id, preset]) => (
                  <option key={id} value={id}>{preset.label}</option>
                ))}
              </select>
            </label>
            <div className="export-size-grid">
              <label className="filter-field">
                <span>余白</span>
                <select value={exportSettings.marginPreset} onChange={(event) => updateExportSettings({ marginPreset: event.target.value as ExportMarginPreset })}>
                  <option value="compact">小</option>
                  <option value="standard">標準</option>
                  <option value="wide">広め</option>
                </select>
              </label>
              <label className="filter-field">
                <span>文字</span>
                <select value={exportSettings.fontScale} onChange={(event) => updateExportSettings({ fontScale: event.target.value as ExportFontScale })}>
                  <option value="small">小</option>
                  <option value="standard">標準</option>
                  <option value="large">大</option>
                </select>
              </label>
            </div>
            <label className="filter-field">
              <span>凡例</span>
              <select value={exportSettings.legendPosition} onChange={(event) => updateExportSettings({ legendPosition: event.target.value as LegendPosition })}>
                <option value="bottom">下</option>
                <option value="top">上</option>
                <option value="right">右</option>
                <option value="none">非表示</option>
              </select>
            </label>
            {(chartKind === 'line' || chartKind === 'combo') && (
              <label className="check-field chart-label-toggle">
                <input
                  type="checkbox"
                  checked={exportSettings.directLineLabels}
                  onChange={(event) => updateExportSettings({ directLineLabels: event.target.checked })}
                />
                <span>線の右端に系列名</span>
              </label>
            )}
            <div className="export-actions">
              <button type="button" onClick={() => exportPng(false)} disabled={!hasRequiredSelections || !chartRows.length}>PNG保存</button>
              <button type="button" className="secondary" onClick={() => exportPng(true)} disabled={!hasRequiredSelections || !chartRows.length}>PNGコピー</button>
              <button type="button" className="ghost" onClick={exportSvg} disabled={!hasRequiredSelections || !chartRows.length}>SVG保存</button>
            </div>
            {exportMessage && <p className="success-text">{exportMessage}</p>}
            {exportError && <InlineError message={exportError} />}
          </div>
          )}
          {viewMode === 'table' && (
            <div className="chart-style-section export-settings-panel">
              <div className="choice-head">
                <h3>表書き出し</h3>
              </div>
              <div className="export-actions table-export-actions">
                <button type="button" onClick={exportTableCsv} disabled={!analysisTable.rows.length}>CSV保存</button>
                <button type="button" className="secondary" onClick={copyTableCsv} disabled={!analysisTable.rows.length}>CSVコピー</button>
              </div>
              {exportMessage && <p className="success-text">{exportMessage}</p>}
              {exportError && <InlineError message={exportError} />}
            </div>
          )}
          {viewMode === 'chart' && chartKind === 'scatter' ? (
            <div className="choice-section">
              <h3>散布図の軸</h3>
              <label className="filter-field">
                <span>X軸</span>
                <select value={scatterX} onChange={(e) => setScatterX(e.target.value)}>
                  <option value="">未選択</option>
                  {fieldOptionGroups(fieldChoices).map((group) => (
                    <optgroup key={group.key} label={group.label}>
                      {group.items.map((field) => <option key={field.id} value={field.id}>{field.label}</option>)}
                    </optgroup>
                  ))}
                </select>
              </label>
              <label className="filter-field">
                <span>Y軸</span>
                <select value={scatterY} onChange={(e) => setScatterY(e.target.value)}>
                  <option value="">未選択</option>
                  {fieldOptionGroups(fieldChoices).map((group) => (
                    <optgroup key={group.key} label={group.label}>
                      {group.items.map((field) => <option key={field.id} value={field.id}>{field.label}</option>)}
                    </optgroup>
                  ))}
                </select>
              </label>
            </div>
          ) : (
            <ChoiceGroup
              title="項目"
              items={fieldChoices.map((field) => ({ id: field.id, label: field.label, name: field.name, category: field.category }))}
              selected={viewMode === 'table' ? selectedFields.slice(0, 1) : selectedFields}
              onToggle={(id) => setSelectedFields(viewMode === 'table' ? toggleSingleValue(selectedFields, id) : toggleValue(selectedFields, id))}
              onAll={() => setSelectedFields(viewMode === 'table' ? (fieldChoices[0] ? [fieldChoices[0].id] : []) : fieldChoices.slice(0, 8).map((item) => item.id))}
              onClear={() => setSelectedFields([])}
              allLabel={viewMode === 'table' ? '先頭項目' : '項目候補'}
              clearLabel="項目解除"
              groupByCategory
            />
          )}
          {viewMode === 'chart' && chartKind !== 'scatter' && selectedFieldOptions.length > 1 && (
            <ChoiceGroup
              title="右軸"
              items={selectedFieldOptions.map((field) => ({ id: field.id, label: field.label, name: field.name }))}
              selected={selectedRightAxisFields}
              onToggle={(id) => setRightAxisFields(toggleValue(selectedRightAxisFields, id))}
              onAll={() => setRightAxisFields(selectedFields)}
              onClear={() => setRightAxisFields([])}
              allLabel="右軸すべて"
              clearLabel="右軸解除"
              compact
            />
          )}
        </div>
        <div className="panel chart-main-panel">
          <div className="panel-head">
            <div>
              <h2>{viewMode === 'table' ? `分析表${tableField ? `: ${tableField.name}` : ''}` : chartTitle(chartKind, mode, chartSource)}</h2>
              <p className="muted">
                {hasRequiredSelections ? (data ? `${data.total}行を読み込み / ${queryFields.length}項目` : 'データを読み込み中です。') : chartSelectionMessage(chartSource, viewMode, chartKind)}
                {data?.omitted_rows ? ` / ${data.omitted_rows}行は上限超過で省略` : ''}
                {chartSourceLabel(chartSource)}
              </p>
            </div>
            {viewMode === 'chart' && hasSeriesOverflow && <span className="badge running">系列を18件に制限</span>}
          </div>
          {viewMode === 'table' ? (
            <AnalysisTableBlock table={analysisTable} emptyMessage={chartSelectionMessage(chartSource, viewMode, chartKind)} />
          ) : (
            renderActiveChart(previewRenderOptions)
          )}
          <div className="chart-meta">
            <span>会社 {selectedCompanies.length || '未選択'}</span>
            <span>{chartSource === 'stock' ? '月' : '年度'} {selectedYears.length || '未選択'}</span>
            <span>項目 {viewMode === 'chart' && chartKind === 'scatter' ? queryFields.length || '未選択' : queryFields.length || '未選択'}</span>
            {viewMode === 'chart' && chartKind !== 'scatter' && <span>右軸 {selectedRightAxisFields.length || 'なし'}</span>}
          </div>
          {selectedFields.length > 1 && viewMode === 'chart' && chartKind !== 'scatter' && (
            <p className="hint">単位が違う項目を同じ軸に載せると見え方が歪みます。比較しにくい場合は項目を絞ってください。</p>
          )}
          <SourceSummaryPanel sources={data?.sources || []} />
        </div>
      </div>
      {viewMode === 'chart' && (
        <ChartExportFrame exportRef={exportRef} settings={exportSettings}>
          {renderActiveChart(exportRenderOptions)}
        </ChartExportFrame>
      )}
    </section>
  );
}

function ChartExportFrame({
  exportRef,
  settings,
  children,
}: {
  exportRef: React.RefObject<HTMLDivElement | null>;
  settings: ExportSettings;
  children: React.ReactNode;
}) {
  const background = settings.background === 'white' ? '#ffffff' : 'transparent';
  return (
    <div className="chart-export-stage" aria-hidden="true">
      <div
        ref={exportRef}
        className={`chart-export-frame chart-export-${settings.designPreset}`}
        style={{
          width: settings.width,
          height: settings.height,
          background,
        }}
      >
        {children}
      </div>
    </div>
  );
}

function AnalysisTableBlock({ table, emptyMessage }: { table: AnalysisTableData; emptyMessage: string }) {
  if (!table.columns.length) {
    return <Empty message={emptyMessage} />;
  }
  if (!table.rows.length) {
    return <Empty message="表にできる数値データがありません。" />;
  }
  return (
    <div className="analysis-table">
      <div className="analysis-table-legend">
        <span>単位</span>
        <strong>{table.unit || '-'}</strong>
      </div>
      <DataTable
        data={table.rows}
        columns={table.columns}
        columnLabels={table.labels}
        compact
        markEmptyCells
      />
    </div>
  );
}

function SourceSummaryPanel({ sources }: { sources: SourceSummary[] }) {
  const rows: Row[] = sources.slice(0, 40).map((source) => ({ ...source }));
  const columns = [
    'company_name',
    'period',
    'field_name',
    'value',
    'unit',
    'data_scope',
    'source_file',
    'source_heading',
    'source_quote',
    'extraction_method',
    'confidence'
  ].filter((column) => rows.some((row) => String(row[column] ?? '').trim()));
  return (
    <div className="source-summary">
      <div className="source-summary-head">
        <h3>出典</h3>
        <span>{sources.length ? `${sources.length}件` : '未表示'}</span>
      </div>
      {rows.length && columns.length ? (
        <DataTable
          data={rows}
          columns={columns}
          columnLabels={sourceSummaryColumnLabels}
          compact
          clampAllCells
        />
      ) : (
        <p className="muted">出典行はありません。</p>
      )}
    </div>
  );
}

function ChoiceGroup({
  title,
  items,
  selected,
  onToggle,
  onAll,
  onClear,
  compact = false,
  groupByCategory = false,
  allLabel = '選択',
  clearLabel = '解除'
}: {
  title: string;
  items: Array<{ id: string; label: string; name?: string; category?: string }>;
  selected: string[];
  onToggle: (id: string) => void;
  onAll: () => void;
  onClear: () => void;
  compact?: boolean;
  groupByCategory?: boolean;
  allLabel?: string;
  clearLabel?: string;
}) {
  const groups = groupByCategory ? choiceItemGroups(items) : [{ key: 'all', label: '', items }];
  return (
    <div className="choice-section">
      <div className="choice-head">
        <h3>{title}</h3>
        <div>
          <button className="ghost" type="button" onClick={onAll}>{allLabel}</button>
          <button className="ghost" type="button" onClick={onClear}>{clearLabel}</button>
        </div>
      </div>
      {groups.map((group) => (
        <div className={groupByCategory ? 'choice-category' : ''} key={group.key}>
          {groupByCategory && (
            <div className="choice-category-head">
              <span>{group.label}</span>
              <small>{group.items.length}</small>
            </div>
          )}
          <div className={`choice-grid ${compact ? 'compact-choice' : ''}`}>
            {group.items.map((item) => {
              const active = selected.includes(item.id);
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`choice-chip ${active ? 'active' : ''}`}
                  onClick={() => onToggle(item.id)}
                  title={item.label}
                >
                  <span>{item.name || item.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function AxisRangeInputs({
  title,
  minKey,
  maxKey,
  range,
  overrides,
  onChange,
}: {
  title: string;
  minKey: string;
  maxKey: string;
  range?: [number, number];
  overrides: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  const autoLabel = range ? `${formatAxisInput(range[0])} - ${formatAxisInput(range[1])}` : '自動値なし';
  return (
    <div className="axis-range-group">
      <div className="axis-range-head">
        <strong>{title}</strong>
        <span>auto {autoLabel}</span>
      </div>
      <label className="filter-field">
        <span>最小値</span>
        <input
          inputMode="decimal"
          value={overrides[minKey] || ''}
          placeholder={range ? formatAxisInput(range[0]) : 'auto'}
          onChange={(event) => onChange(minKey, event.target.value)}
        />
      </label>
      <label className="filter-field">
        <span>最大値</span>
        <input
          inputMode="decimal"
          value={overrides[maxKey] || ''}
          placeholder={range ? formatAxisInput(range[1]) : 'auto'}
          onChange={(event) => onChange(maxKey, event.target.value)}
        />
      </label>
    </div>
  );
}

function BarOrLineChartBlock({ kind, rows, xKey, series, rightAxisFields, showValueLabels, seriesStyles, renderOptions }: {
  kind: Exclude<ChartKind, 'scatter'>;
  rows: Row[];
  xKey: string;
  series: ChartSeries[];
  rightAxisFields: string[];
  showValueLabels: boolean;
  seriesStyles: Record<string, SeriesStyle>;
  renderOptions: ChartRenderOptions;
}) {
  if (!rows.length || !series.length) {
    return <Empty message="グラフ化できる数値データがありません。" />;
  }
  const ChartComponent = kind === 'bar' ? BarChart : kind === 'combo' ? ComposedChart : LineChart;
  const rightAxisSet = new Set(rightAxisFields);
  const hasRightAxis = series.some((item) => rightAxisSet.has(item.fieldId));
  const seriesByKey = new Map(series.map((item) => [item.key, item]));
  const font = exportFontScales[renderOptions.exportSettings.fontScale];
  const design = designPresets[renderOptions.exportSettings.designPreset];
  const margin = chartMargin(renderOptions.exportSettings, hasRightAxis);
  const legendProps = chartLegendProps(renderOptions.exportSettings, font.legend);
  const lastIndexes = lastValueIndexes(rows, series);
  return (
    <div className={`chart-canvas ${renderOptions.exportMode ? 'chart-canvas-export' : ''} chart-design-${renderOptions.exportSettings.designPreset}`}>
      <ResponsiveContainer width="100%" height={renderOptions.height}>
        <ChartComponent data={rows} margin={margin}>
          <CartesianGrid stroke={design.grid} vertical={false} />
          <XAxis
            dataKey={xKey}
            axisLine={{ stroke: design.axis }}
            tick={{ fill: design.tick, fontSize: font.tick, fontWeight: 650 }}
            tickLine={false}
            tickMargin={10}
          />
          <YAxis
            yAxisId="left"
            domain={renderOptions.axisDomains?.left}
            axisLine={false}
            tick={{ fill: design.tick, fontSize: font.tick, fontWeight: 650 }}
            tickFormatter={formatAxisTick}
            tickLine={false}
            width={font.tick >= 16 ? 70 : 58}
          />
          {hasRightAxis && (
            <YAxis
              yAxisId="right"
              orientation="right"
              domain={renderOptions.axisDomains?.right}
              axisLine={false}
              tick={{ fill: design.tick, fontSize: font.tick, fontWeight: 650 }}
              tickFormatter={formatAxisTick}
              tickLine={false}
              width={font.tick >= 16 ? 70 : 58}
            />
          )}
          <Tooltip content={<ChartTooltip seriesByKey={seriesByKey} />} />
          {legendProps && (
            <Legend
              iconSize={10}
              {...legendProps}
              formatter={(value) => seriesByKey.get(String(value))?.label || String(value)}
            />
          )}
          {series.map((item, index) => {
            const style = seriesStyles[item.key] || {};
            const color = style.color || chartColors[index % chartColors.length];
            const renderKind = getSeriesRenderKind(kind, index, style);
            const strokeWidth = style.strokeWidth || 2.2;
            const strokeDasharray = style.strokeDasharray || undefined;
            const showDirectLabel = renderKind === 'line' && renderOptions.exportSettings.directLineLabels;
            return renderKind === 'bar' ? (
              <Bar
                key={item.key}
                dataKey={item.key}
                name={item.label}
                yAxisId={rightAxisSet.has(item.fieldId) ? 'right' : 'left'}
                fill={color}
                radius={[3, 3, 0, 0]}
                maxBarSize={28}
                isAnimationActive={false}
              >
                {showValueLabels && (
                  <LabelList
                    className="chart-value-label"
                    dataKey={item.key}
                    position="top"
                    formatter={formatValueLabel}
                  />
                )}
              </Bar>
            ) : (
              <Line
                key={item.key}
                type="linear"
                dataKey={item.key}
                name={item.label}
                yAxisId={rightAxisSet.has(item.fieldId) ? 'right' : 'left'}
                stroke={color}
                strokeWidth={strokeWidth}
                strokeDasharray={strokeDasharray}
                dot={showValueLabels ? renderLinePointWithLabel : { r: 3.2, fill: '#ffffff', strokeWidth: 1.8 }}
                activeDot={{ r: 5.2, strokeWidth: 2 }}
                connectNulls={false}
                isAnimationActive={false}
              >
                {showDirectLabel && (
                  <LabelList
                    dataKey={item.key}
                    content={(props) => renderLineEndLabel(props, item, lastIndexes[item.key], color, font.label)}
                  />
                )}
              </Line>
            );
          })}
        </ChartComponent>
      </ResponsiveContainer>
    </div>
  );
}

function renderLinePointWithLabel(props: {
  cx?: number;
  cy?: number;
  stroke?: string;
  value?: unknown;
}) {
  const cx = Number(props.cx);
  const cy = Number(props.cy);
  if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
  const label = formatValueLabel(props.value);
  return (
    <g>
      <circle cx={cx} cy={cy} r={3.2} fill="#ffffff" stroke={props.stroke || '#111827'} strokeWidth={1.8} />
      {label && (
        <text className="chart-value-label" x={cx} y={cy - 10} textAnchor="middle">
          {label}
        </text>
      )}
    </g>
  );
}

function renderLineEndLabel(
  props: { x?: string | number; y?: string | number; index?: number },
  series: ChartSeries,
  lastIndex: number | undefined,
  color: string,
  fontSize: number,
) {
  if (lastIndex == null || props.index !== lastIndex) return null;
  const x = Number(props.x);
  const y = Number(props.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return (
    <text
      className="chart-line-end-label"
      x={x + 8}
      y={y + 4}
      fill={color}
      fontSize={fontSize}
      textAnchor="start"
    >
      {series.companyName || series.label}
    </text>
  );
}

function ScatterChartBlock({ rows, xLabel, yLabel, showCorrelation, renderOptions }: {
  rows: Row[];
  xLabel: string;
  yLabel: string;
  showCorrelation: boolean;
  renderOptions: ChartRenderOptions;
}) {
  if (!rows.length) {
    return <Empty message="散布図にできる数値ペアがありません。" />;
  }
  const correlation = pearsonCorrelation(rows);
  const font = exportFontScales[renderOptions.exportSettings.fontScale];
  const design = designPresets[renderOptions.exportSettings.designPreset];
  const margin = chartMargin(renderOptions.exportSettings, false);
  const legendProps = chartLegendProps(renderOptions.exportSettings, font.legend);
  return (
    <div className={`chart-canvas ${renderOptions.exportMode ? 'chart-canvas-export' : ''} chart-design-${renderOptions.exportSettings.designPreset}`}>
      {showCorrelation && correlation != null && (
        <div className="chart-stat">
          <span>相関係数</span>
          <strong style={{ fontSize: font.stat }}>{correlation.toFixed(3)}</strong>
        </div>
      )}
      <ResponsiveContainer width="100%" height={renderOptions.height}>
        <ScatterChart margin={margin}>
          <CartesianGrid stroke={design.grid} vertical={false} />
          <XAxis
            type="number"
            dataKey="x"
            name={xLabel}
            domain={renderOptions.axisDomains?.x}
            axisLine={{ stroke: design.axis }}
            tick={{ fill: design.tick, fontSize: font.tick, fontWeight: 650 }}
            tickFormatter={formatAxisTick}
            tickLine={false}
            tickMargin={10}
          />
          <YAxis
            type="number"
            dataKey="y"
            name={yLabel}
            domain={renderOptions.axisDomains?.y}
            axisLine={false}
            tick={{ fill: design.tick, fontSize: font.tick, fontWeight: 650 }}
            tickFormatter={formatAxisTick}
            tickLine={false}
            width={font.tick >= 16 ? 70 : 58}
          />
          <Tooltip cursor={{ stroke: '#9aa8b5', strokeDasharray: '3 3' }} content={<ScatterTooltip xLabel={xLabel} yLabel={yLabel} />} />
          {legendProps && <Legend {...legendProps} />}
          <Scatter name={`${xLabel} x ${yLabel}`} data={rows} fill="#111827" isAnimationActive={false} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

function ChartTooltip({
  active,
  label,
  payload,
  seriesByKey
}: {
  active?: boolean;
  label?: unknown;
  payload?: Array<{ dataKey?: string | number; value?: unknown; color?: string; payload?: Row }>;
  seriesByKey: Map<string, ChartSeries>;
}) {
  if (!active || !payload?.length) return null;
  const visiblePayload = payload.filter((item) => numericValue(item.value) != null);
  if (!visiblePayload.length) return null;
  return (
    <div className="chart-tooltip">
      <strong>{String(label || '')}</strong>
      {visiblePayload.map((item) => {
        const key = String(item.dataKey || '');
        const series = seriesByKey.get(key);
        return (
          <div className="chart-tooltip-row" key={key}>
            <span className="chart-tooltip-swatch" style={{ background: item.color || '#111827' }} />
            <span>
              <b>{series?.companyName || series?.label || key}</b>
              {series?.companyName && <small>{series.fieldName}</small>}
            </span>
            <em>{formatChartValue(item.value, series?.unit)}</em>
          </div>
        );
      })}
    </div>
  );
}

function ScatterTooltip({
  active,
  payload,
  xLabel,
  yLabel
}: {
  active?: boolean;
  payload?: Array<{ payload?: Row }>;
  xLabel: string;
  yLabel: string;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload || {};
  return (
    <div className="chart-tooltip">
      <strong>{String(point.company || '')} {String(point.fiscal_year || '')}</strong>
      <div className="chart-tooltip-row simple">
        <span>{xLabel}</span>
        <em>{formatChartValue(point.x, '')}</em>
      </div>
      <div className="chart-tooltip-row simple">
        <span>{yLabel}</span>
        <em>{formatChartValue(point.y, '')}</em>
      </div>
    </div>
  );
}

function ReportPanel({ status, refreshToken, job, onJob, onError }: {
  status: Status | null;
  refreshToken: number;
  job: Job | null;
  onJob: (job: Job) => void;
  onError: (message: string) => void;
}) {
  const [report, setReport] = React.useState('');
  const [coverage, setCoverage] = React.useState('');
  const jobRunning = job?.status === 'running';

  React.useEffect(() => {
    api<{ content: string }>('/api/markdown/run_report').then((data) => setReport(data.content));
    api<{ content: string }>('/api/markdown/field_coverage').then((data) => setCoverage(data.content));
  }, [refreshToken]);

  const chartData = Object.entries(status?.run_report.summary || {})
    .map(([name, raw]) => ({ name, value: Number(String(raw).replace(/[^0-9.-]/g, '')) }))
    .filter((row) => Number.isFinite(row.value));

  return (
    <section className="stack">
      <div className="panel chart-panel">
        <h2>実行サマリー</h2>
        {chartData.length ? (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis />
              <Tooltip />
              <Bar dataKey="value" fill="#2f6f73" />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <Empty message="サマリー数値がありません。" />
        )}
      </div>
      <CorroborationSummaryPanel job={job} onJob={onJob} onError={onError} jobRunning={jobRunning} refreshToken={refreshToken} />
      <MarkdownBlock title="Run Report" content={report} />
      <MarkdownBlock title="Field Coverage" content={coverage} />
    </section>
  );
}

function corroborationPercent(part: number | undefined, total: number | undefined): string {
  if (!total || part == null) return '-';
  return `${((part / total) * 100).toFixed(1)}%`;
}

function ReconciliationPanel({ onError, refreshToken }: { onError: (message: string) => void; refreshToken: number }) {
  const [data, setData] = React.useState<ReconciliationGroupsResult | null>(null);
  const [selectedGroupId, setSelectedGroupId] = React.useState('');
  const [note, setNote] = React.useState('');
  const [message, setMessage] = React.useState('');
  const [saving, setSaving] = React.useState(false);

  const load = React.useCallback(() => {
    api<ReconciliationGroupsResult>('/api/reconciliation/groups')
      .then((result) => {
        setData(result);
        if (!selectedGroupId && result.groups.length) {
          setSelectedGroupId(result.groups[0].group_id);
        }
      })
      .catch((err) => onError(String(err)));
  }, [onError, selectedGroupId]);

  React.useEffect(() => {
    load();
  }, [load, refreshToken]);

  const selected = data?.groups.find((group) => group.group_id === selectedGroupId) || null;

  async function acceptGroup() {
    if (!selected) return;
    setSaving(true);
    setMessage('');
    try {
      const result = await api<{ applied_items: number; total: number }>('/api/reconciliation/apply', {
        method: 'POST',
        body: JSON.stringify({
          group_id: selected.group_id,
          decision: 'accept',
          reviewer_note: note || `checked reconciliation group: ${selected.rule_id}`,
          reviewer: 'web',
        }),
      });
      setMessage(`保存しました: ${result.applied_items}件 / resolved合計 ${result.total}件`);
      load();
    } catch (err) {
      onError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="stack">
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>照合グループの確認</h2>
            <p className="muted">同じルールで見つかった「数値の食い違い」をグループごとにまとめて表示しています。内容を確認してまとめて承認すると、通常のレビュー保存と同じ扱いで反映されます。</p>
          </div>
          <span className="badge">groups {data?.total ?? '-'}</span>
        </div>
        {message && <div className="notice">{message}</div>}
        <div className="reconciliation-layout">
          <div className="table-wrap compact-table">
            <table>
              <thead>
                <tr>
                  <th>group</th>
                  <th>items</th>
                  <th>company_year</th>
                  <th>fields</th>
                </tr>
              </thead>
              <tbody>
                {(data?.groups || []).map((group) => (
                  <tr key={group.group_id} className={group.group_id === selectedGroupId ? 'selected-row' : ''} onClick={() => setSelectedGroupId(group.group_id)}>
                    <td className="mono">{group.rule_id}</td>
                    <td className="mono">{group.item_count}</td>
                    <td className="mono">{group.company_year_count}</td>
                    <td className="mono">{group.field_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="concept-editor">
            {selected ? (
              <>
                <h3>{selected.rule_id}</h3>
                <div className="detail-grid">
                  <div><small>items</small><strong>{selected.item_count}</strong></div>
                  <div><small>company_year</small><strong>{selected.company_year_count}</strong></div>
                  <div><small>fields</small><strong>{selected.field_count}</strong></div>
                </div>
                <label>メモ<textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="判断メモ" /></label>
                <button onClick={acceptGroup} disabled={saving}>グループをaccept保存</button>
                <MiniRows
                  title="サンプル"
                  rows={selected.sample_rows}
                  columns={['company_year_id', 'field_id', 'field_name_ja', 'existing_value', 'extracted_value', 'review_reason']}
                  emptyMessage="サンプル行はありません。"
                />
              </>
            ) : (
              <Empty message="照合グループはありません。" />
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

const MAPPING_ACTION_LABELS: Record<string, string> = {
  map: '既存の項目に対応付ける',
  different_scope: '範囲が違うため対応不可',
  ignore: '対応不要',
  new_concept: '新しい項目として追加'
};

function MappingReviewPanel({ onError, refreshToken }: { onError: (message: string) => void; refreshToken: number }) {
  const [actionFilter, setActionFilter] = React.useState('');
  const [kindFilter, setKindFilter] = React.useState('');
  const [verdictFilter, setVerdictFilter] = React.useState('');
  const [data, setData] = React.useState<MappingProposalsResult | null>(null);
  const [conflictSummary, setConflictSummary] = React.useState<Record<string, number> | null>(null);
  const [busyId, setBusyId] = React.useState('');
  const [message, setMessage] = React.useState('');

  const load = React.useCallback(() => {
    const params = new URLSearchParams();
    if (actionFilter) params.set('action', actionFilter);
    if (kindFilter) params.set('decided_by_kind', kindFilter);
    if (verdictFilter) params.set('verdict', verdictFilter);
    api<MappingProposalsResult>(`/api/mappings/proposals?${params.toString()}`)
      .then(setData)
      .catch((err) => onError(String(err)));
  }, [actionFilter, kindFilter, verdictFilter, onError]);

  async function bulkRejectConflicts() {
    if (!window.confirm('数値照合で明確に不一致（一致率10%以下）のmap提案を一括却下します。既存の確定判断は変更しません。実行しますか？')) return;
    setMessage('一括却下を実行中...');
    try {
      const r = await api<{ rejected: number; candidates: number }>('/api/mappings/bulk-reject-conflicts', {
        method: 'POST',
        body: JSON.stringify({ reviewer: 'web_ui' }),
      });
      setMessage(`一括却下しました: ${r.rejected}件（候補${r.candidates}件）`);
      load();
    } catch (err) {
      onError(String(err));
      setMessage('');
    }
  }

  React.useEffect(() => { load(); }, [load, refreshToken]);

  React.useEffect(() => {
    api<Record<string, number>>('/api/mappings/conflict-summary')
      .then(setConflictSummary)
      .catch(() => setConflictSummary(null));
  }, [refreshToken]);

  async function decide(mappingId: string, decision: 'confirm' | 'reject') {
    if (decision === 'reject' && !window.confirm('この提案を却下します。よろしいですか？')) {
      return;
    }
    setBusyId(mappingId);
    setMessage('');
    try {
      await api(`/api/mappings/${encodeURIComponent(mappingId)}/${decision}`, {
        method: 'POST',
        body: JSON.stringify({ reviewer: 'web_ui' })
      });
      setMessage(decision === 'confirm' ? '承認しました。' : '却下しました。');
      load();
    } catch (err) {
      onError(String(err));
    } finally {
      setBusyId('');
    }
  }

  const counts = data?.action_counts || {};

  return (
    <section className="stack">
      <div className="panel automation-panel">
        <div className="panel-head">
          <div>
            <h2>対応付けの確認</h2>
            <p className="muted">
              システムが見つけた「有報の項目」と「表の項目」の<TermTooltip label={t('mapping')} explain="有報に出てくる項目名を、最終的な表の項目に結びつけること。" />の提案（{t('proposed')}）です。内容を確認して承認・却下してください。承認済み・却下済みの判断が変わることはありません。
            </p>
          </div>
        </div>
        <div className="automation-grid">
          <div className="metric"><small>提案合計</small><strong>{data?.total ?? '-'}</strong></div>
          <div className="metric"><small>対応付ける</small><strong>{counts.map ?? 0}</strong></div>
          <div className="metric"><small>範囲が違う</small><strong>{counts.different_scope ?? 0}</strong></div>
          <div className="metric"><small>対応不要</small><strong>{counts.ignore ?? 0}</strong></div>
          <div className="metric"><small>新しい項目にする</small><strong>{counts.new_concept ?? 0}</strong></div>
        </div>
        <div className="toolbar">
          <select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
            <option value="">提案の種類: すべて</option>
            <option value="map">対応付ける</option>
            <option value="different_scope">範囲が違う</option>
            <option value="ignore">対応不要</option>
            <option value="new_concept">新しい項目にする</option>
          </select>
          <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
            <option value="">判断主体: すべて</option>
            <option value="ai">AI提案</option>
            <option value="deterministic">{t('deterministic')}</option>
          </select>
          <select value={verdictFilter} onChange={(e) => setVerdictFilter(e.target.value)}>
            <option value="">数値照合: すべて</option>
            <option value="corroborated">一致（承認向き）</option>
            <option value="conflicts">不一致（却下向き）</option>
            <option value="unverifiable">照合不能</option>
            <option value="weak">部分一致</option>
          </select>
          <button type="button" className="secondary danger-action" onClick={bulkRejectConflicts}>
            矛盾を一括却下（一致率10%以下のmap）
          </button>
        </div>
      </div>
      {conflictSummary && (
        <div className="panel automation-panel">
          <div className="panel-head">
            <div>
              <h2>セル判定状況（参考）</h2>
              <p className="muted">各セルの判定結果の内訳です（矛盾件数の把握用。上の対応付け提案とは別の集計です）。</p>
            </div>
          </div>
          <div className="automation-grid">
            {Object.entries(conflictSummary).map(([key, value]) => (
              <div className="metric" key={key}><small>{key || '(未設定)'}</small><strong>{value}</strong></div>
            ))}
          </div>
        </div>
      )}
      {message && <p className="hint">{message}</p>}
      <div className="stack">
        {(data?.proposals || []).map((p) => (
          <div className="panel" key={p.mapping_id}>
            <div className="panel-head">
              <div>
                <h3>{p.observed_item.label_ja || p.observed_item.element_local_name || p.observed_item.observed_item_id}</h3>
                <p className="muted">
                  {p.observed_item.element_id} / scope={p.observed_item.normalized_scope || '-'} / unit={p.observed_item.unit || '-'} / {p.observed_item.taxonomy_kind}
                </p>
              </div>
              <span className={`badge ${p.decided_by_kind === 'ai' ? 'pending' : 'succeeded'}`}>
                {p.decided_by_kind === 'ai' ? 'AI提案' : '決定的一致'}{p.confidence != null ? `(${p.confidence})` : ''}
              </span>
            </div>
            <div className="grid">
              <div>
                <small>提案の種類</small>
                <strong>{MAPPING_ACTION_LABELS[p.action] || p.action}</strong>
              </div>
              <div>
                <small>対応付け先の項目</small>
                <strong>{p.concept?.concept_name_ja || p.new_concept_proposal?.concept_name_ja || '(なし/新しい項目)'}</strong>
              </div>
            </div>
            {p.corroboration && (
              <div className={`callout corroboration-${p.corroboration.verdict}`}>
                <strong>
                  {t('corroboration')}: {CORROBORATION_VERDICT_LABELS[p.corroboration.verdict] || p.corroboration.verdict}
                  {p.corroboration.overlap_count > 0
                    ? ` — 一致 ${p.corroboration.match_count}/${p.corroboration.overlap_count}（${Math.round((p.corroboration.match_rate || 0) * 100)}%）`
                    : ''}
                </strong>
                {(p.corroboration.examples || []).slice(0, 3).map((ex, idx) => (
                  <div className="muted" key={idx} style={{ fontSize: '0.85em' }}>
                    {ex.company_year_id}: 有報の値={Number(ex.element_value).toLocaleString()} vs 表の値={Number(ex.concept_value).toLocaleString()} {ex.matched ? '✓' : '✗'}
                  </div>
                ))}
              </div>
            )}
            <p className="hint">根拠: {p.rationale || '(記録なし)'}</p>
            <div className="toolbar">
              <button onClick={() => decide(p.mapping_id, 'confirm')} disabled={busyId === p.mapping_id}>承認</button>
              <button className="ghost" onClick={() => decide(p.mapping_id, 'reject')} disabled={busyId === p.mapping_id}>却下</button>
            </div>
          </div>
        ))}
        {data && data.proposals.length === 0 && <Empty message="該当する提案はありません。" />}
      </div>
    </section>
  );
}

const ALGORITHM_AUDIT_KIND_OPTIONS = [
  'duplicate_tag',
  'contradictory_mapping',
  'low_coverage_concept',
  'orphan_concept',
  'unconfirmed_concept',
  'review_section_debt',
  'review_section_debt_summary'
] as const;

function AlgorithmAuditFindingsPanel({ onError, refreshToken }: { onError: (message: string) => void; refreshToken: number }) {
  const [kindFilter, setKindFilter] = React.useState('');
  const [severityFilter, setSeverityFilter] = React.useState('');
  const [data, setData] = React.useState<AlgorithmAuditFindingsResult | null>(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(() => {
    api<AlgorithmAuditFindingsResult>('/api/algorithm-audit/findings')
      .then(setData)
      .catch((err) => onError(String(err)));
  }, [onError]);

  React.useEffect(() => { load(); }, [load, refreshToken]);

  async function rebuild() {
    setBusy(true);
    try {
      await api('/api/jobs/algorithm-audit-findings', { method: 'POST' });
      window.setTimeout(load, 1500);
    } catch (err) {
      onError(String(err));
    } finally {
      setBusy(false);
    }
  }

  const findings = (data?.findings || []).filter((f) =>
    (!kindFilter || f.kind === kindFilter) && (!severityFilter || f.severity === severityFilter)
  );

  return (
    <section className="stack">
      <div className="panel automation-panel">
        <div className="panel-head">
          <div>
            <h2>アルゴリズム監査の指摘一覧</h2>
            <p className="muted">仕組みが自動で見つけた気になる点（重複タグ／矛盾する対応付け／カバレッジ不足／孤立した項目／レビュー残骸など）の一覧です。ここでは修正は行いません。</p>
          </div>
          <button onClick={rebuild} disabled={busy}>{busy ? '生成中…' : '再生成'}</button>
        </div>
        {data?.status === 'not_built' && <p className="hint">まだ生成されていません。「再生成」を実行してください。</p>}
        {data?.summary && (
          <div className="automation-grid">
            <div className="metric"><small>件数合計</small><strong>{data.summary.total}</strong></div>
            {Object.entries(data.summary.by_kind).map(([k, v]) => (
              <div className="metric" key={k}><small>{k}</small><strong>{v}</strong></div>
            ))}
          </div>
        )}
        <div className="toolbar">
          <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
            <option value="">kind: すべて</option>
            {ALGORITHM_AUDIT_KIND_OPTIONS.map((kind) => (
              <option key={kind} value={kind}>{kind}</option>
            ))}
          </select>
          <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
            <option value="">severity: すべて</option>
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
            <option value="info">info</option>
          </select>
        </div>
      </div>
      <div className="stack">
        {findings.map((f) => (
          <div className="panel" key={f.finding_id}>
            <div className="panel-head">
              <div>
                <h3>{f.target}</h3>
                <p className="muted">{f.kind}</p>
              </div>
              <span className={`badge ${f.severity === 'high' ? 'pending' : 'succeeded'}`}>{f.severity}</span>
            </div>
            <pre className="hint">{JSON.stringify(f.evidence, null, 2)}</pre>
            <p className="hint">提案: {f.suggested_action}</p>
          </div>
        ))}
        {data && data.status !== 'not_built' && findings.length === 0 && <Empty message="該当するfindingsはありません。" />}
      </div>
    </section>
  );
}

function CorroborationSummaryPanel({ job, onJob, onError, jobRunning, refreshToken }: {
  job: Job | null;
  onJob: (job: Job) => void;
  onError: (message: string) => void;
  jobRunning: boolean;
  refreshToken: number;
}) {
  const [summary, setSummary] = React.useState<CorroborationSummary | null>(null);
  const [loading, setLoading] = React.useState(false);

  const loadSummary = React.useCallback(() => {
    setLoading(true);
    api<CorroborationSummary>('/api/corroboration/summary')
      .then(setSummary)
      .catch((err) => onError(String(err)))
      .finally(() => setLoading(false));
  }, [onError]);

  React.useEffect(() => {
    loadSummary();
  }, [loadSummary, refreshToken]);

  React.useEffect(() => {
    if (!job?.id || job.status === 'running') return;
    loadSummary();
  }, [job?.id, job?.status, loadSummary]);

  async function startRebuild() {
    if (jobRunning) {
      onError('ジョブ実行中です。作業ログの完了を待ってから次の操作を実行してください。');
      return;
    }
    try {
      const next = await api<Job>('/api/jobs/corroboration-report', { method: 'POST' });
      onJob(next);
      window.setTimeout(loadSummary, 800);
    } catch (err) {
      onError(String(err));
    }
  }

  const built = Boolean(summary && summary.status !== 'not_built');
  const message = !summary
    ? (loading ? '証拠照合レポートを読み込み中です。' : '証拠照合レポートを読み込み中です。')
    : built
      ? '証拠照合レポートは生成済みです。'
      : '証拠照合レポートはまだ生成されていません。';

  const methodCounts = Object.entries(summary?.extraction_method_counts || {});
  const ruleStatusEntries = Object.entries(summary?.validation_rule_status_counts || {});
  const notes = summary?.notes || [];

  return (
    <div className="panel automation-panel">
      <div className="panel-head">
        <div>
          <h2>証拠照合レポート</h2>
          <p className="muted">{message}</p>
        </div>
        <span className={`badge ${built ? 'succeeded' : 'pending'}`}>{built ? '生成済み' : summary ? '未生成' : '確認中'}</span>
      </div>
      {built && (
        <>
          <div className="automation-grid">
            <div className="metric">
              <small>総セル数</small>
              <strong>{summary?.cells_total ?? '-'}</strong>
            </div>
            <div className="metric">
              <small>照合2件以上（自動確定候補）</small>
              <strong className="text-ok">{summary?.corroborated_2plus ?? '-'}</strong>
              <small>{corroborationPercent(summary?.corroborated_2plus, summary?.cells_total)}</small>
            </div>
            <div className="metric">
              <small>照合1件</small>
              <strong>{summary?.corroborated_1 ?? '-'}</strong>
            </div>
            <div className="metric">
              <small>照合0件</small>
              <strong className="text-warn">{summary?.corroborated_0 ?? '-'}</strong>
              <small>{corroborationPercent(summary?.corroborated_0, summary?.cells_total)}</small>
            </div>
            <div className="metric">
              <small>矛盾</small>
              <strong className="text-warn">{summary?.conflicts ?? '-'}</strong>
              <small>{corroborationPercent(summary?.conflicts, summary?.cells_total)}</small>
            </div>
            <div className="metric">
              <small>自動確定かつ照合0件</small>
              <strong className="text-warn">{summary?.auto_accepted_with_zero_corroboration ?? '-'}</strong>
            </div>
          </div>
          {methodCounts.length > 0 && (
            <div className="detail-section">
              <h3>抽出方法別の件数</h3>
              <div className="mini-table-wrap">
                <table className="mini-table summary-mini-table">
                  <thead>
                    <tr>
                      <th>抽出方法</th>
                      <th>件数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {methodCounts.map(([method, count]) => (
                      <tr key={method}>
                        <td>{method}</td>
                        <td>{count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {ruleStatusEntries.length > 0 && (
            <div className="detail-section">
              <h3>検証ルール別の判定内訳</h3>
              <div className="mini-table-wrap">
                <table className="mini-table summary-mini-table">
                  <thead>
                    <tr>
                      <th>検証ルール</th>
                      <th>fail</th>
                      <th>pass</th>
                      <th>not_applicable</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ruleStatusEntries.map(([rule, counts]) => (
                      <tr key={rule}>
                        <td>{rule}</td>
                        <td className={counts.fail ? 'text-warn' : ''}>{counts.fail ?? 0}</td>
                        <td className="text-ok">{counts.pass ?? 0}</td>
                        <td>{counts.not_applicable ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {notes.length > 0 && (
            <div className="detail-section">
              <h3>注意事項</h3>
              <ul className="note-list">
                {notes.map((note, index) => (
                  <li key={index}>{note}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {jobRunning && <p className="hint action-lock">ジョブ実行中です。完了まで新しい実行操作はできません。</p>}
      <div className="toolbar annual-toolbar">
        <button onClick={startRebuild} disabled={jobRunning}>照合レポートを再生成</button>
      </div>
    </div>
  );
}

function reviewRowKey(row: Row): string {
  return `${String(row.company_year_id || '')}::${String(row.field_id || '')}`;
}

function companyIdFromCompanyYear(companyYearId: string): string {
  const index = companyYearId.lastIndexOf('_');
  return index > 0 ? companyYearId.slice(0, index) : '';
}

function yearFromReviewRow(row: Row): number | null {
  const direct = Number(row.fiscal_year);
  if (Number.isFinite(direct)) return direct;
  const companyYearId = String(row.company_year_id || '');
  const index = companyYearId.lastIndexOf('_');
  if (index < 0) return null;
  const fromId = Number(companyYearId.slice(index + 1));
  return Number.isFinite(fromId) ? fromId : null;
}

function notApplicableRange(scope: string, fiscalYear: number | null): { startYear: number | null; endYear: number | null; label: string } {
  if (fiscalYear == null) return { startYear: null, endYear: null, label: '全年度' };
  if (scope === 'selected_year') return { startYear: fiscalYear, endYear: fiscalYear, label: `${fiscalYear}年度のみ` };
  if (scope === 'until_selected_year') return { startYear: null, endYear: fiscalYear, label: `${fiscalYear}年度以前` };
  if (scope === 'after_selected_year') return { startYear: fiscalYear + 1, endYear: null, label: `${fiscalYear + 1}年度以降` };
  if (scope === 'before_selected_year') return { startYear: null, endYear: fiscalYear - 1, label: `${fiscalYear - 1}年度以前` };
  if (scope === 'all_years') return { startYear: null, endYear: null, label: '全年度' };
  return { startYear: fiscalYear, endYear: null, label: `${fiscalYear}年度以降` };
}

function DataTable(props: Omit<React.ComponentProps<typeof BaseDataTable>, 'baseColumns' | 'renderCellValue' | 'renderClampedText'>) {
  return (
    <BaseDataTable
      {...props}
      baseColumns={baseColumns}
      renderCellValue={renderCellValue}
      renderClampedText={renderClampedText}
    />
  );
}

function MiniRows({ title, rows, columns, emptyMessage }: {
  title: string;
  rows: Row[];
  columns: string[];
  emptyMessage: string;
}) {
  return (
    <div className="detail-section">
      <h3>{title}</h3>
      {rows.length ? (
        <div className="mini-table-wrap">
          <table className="mini-table">
            <thead>
              <tr>
                {columns.map((column) => <th key={column}>{miniColumnLabels[column] || column}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`${title}-${rowIndex}`}>
                  {columns.map((column) => <td key={column}>{row[column] == null || row[column] === '' ? '-' : renderCellValue(column, row[column])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">{emptyMessage}</p>
      )}
    </div>
  );
}

const miniColumnLabels: Record<string, string> = {
  value: '値',
  unit_normalized: '単位',
  data_scope: 'スコープ',
  source_heading: '見出し',
  source_quote: '引用',
  validation_status: '検証',
  confidence: '信頼度',
  extracted_value: '抽出値',
  review_reason: '理由',
  review_decision: '判定',
  corrected_value: '修正値',
  reviewer_note: 'メモ',
  reviewed_at: '保存日時',
  applied_status: '反映状態',
  applied_value: '反映値',
  applied_at: '反映日時',
  resolution: '確定した数値',
  corroboration_count: '数値照合の件数',
  conflict_count: '不一致の件数',
  observed_item_id: '有報の項目ID',
  item_kind: '種類',
  element_id: '要素ID',
  label_ja: '名称',
  normalized_scope: 'スコープ',
  source: '出典',
  mapping_id: '対応付けID',
  concept_id: '表の項目ID',
  action: '種類',
  status: '状態',
  decided_by: '判断者',
  check_kind: '照合の種類',
  check_ref: '照合対象',
  matched: '一致',
  primary_value: '主値',
  other_value: '比較値',
  difference: '差分',
  restatement_suspected: '訂正の疑い',
  detail: '詳細'
};

const sourceSummaryColumnLabels: Record<string, string> = {
  company_name: '会社',
  period: '年度/月',
  field_name: '項目',
  value: '値',
  unit: '単位',
  unit_normalized: '単位',
  data_scope: 'スコープ',
  source_file: '出典ファイル',
  source_heading: '見出し',
  source_quote: '引用',
  extraction_method: '抽出方法',
  confidence: '信頼度'
};

function split(value: string) {
  return value.split(',').map((part) => part.trim()).filter(Boolean);
}

function splitTerms(value: string) {
  return value.split(/[;\n,]+/).map((part) => part.trim()).filter(Boolean);
}

function firstTerm(value: string): string {
  return splitTerms(value)[0] || '';
}

function mappingStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    unmapped: '未判断',
    candidate: '確認中',
    accepted: '採用',
    separate: '別管理',
    rejected: '使わない'
  };
  return labels[value] || value || '未判断';
}

function clampNumber(value: string, min: number, max: number, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

function chartExportFileName(extension: 'png' | 'svg' | 'csv'): string {
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
    String(now.getHours()).padStart(2, '0'),
    String(now.getMinutes()).padStart(2, '0'),
  ].join('');
  return `yuho-chart-${stamp}.${extension}`;
}

function downloadDataUrl(dataUrl: string, fileName: string) {
  const link = document.createElement('a');
  link.href = dataUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function downloadText(text: string, fileName: string) {
  const blob = new Blob(['\ufeff', text], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

const chartColors = [
  '#0f5f66',
  '#d97917',
  '#2563eb',
  '#be123c',
  '#15803d',
  '#7c3aed',
  '#b45309',
  '#0e7490',
  '#64748b',
  '#111827'
];

function chartFieldEligible(field: FieldOption): boolean {
  const id = field.id.toLowerCase();
  const name = field.name.toLowerCase();
  return !id.includes('note') && !name.includes('注記');
}

function fieldCategoryLabel(category: string): string {
  const labels: Record<string, string> = {
    performance: '財務・収益',
    orders: '受注・完成・繰越',
    segment_orders: 'セグメント受注',
    expense: '費用',
    assets: '資産・資本',
    financial_position: '財政状態',
    human_capital: '人員・技術者',
    people: '人員・技術者',
    cost: '原価',
    construction: '完成工事',
    segment: 'セグメント',
    derived_ratio: '比率・派生',
    market_price: '株価',
    market_volume: '出来高',
    market_return: 'リターン',
    market_event: '配当・分割',
    market_value: '時価総額',
    use: '用途別',
    business_scope: '建築/土木等',
    company_factbook: 'ファクトブック',
  };
  return labels[category] || category || '未分類';
}

function choiceItemGroups(items: Array<{ id: string; label: string; name?: string; category?: string }>) {
  const groups: Array<{ key: string; label: string; items: Array<{ id: string; label: string; name?: string; category?: string }> }> = [];
  const byKey = new Map<string, { key: string; label: string; items: Array<{ id: string; label: string; name?: string; category?: string }> }>();
  for (const item of items) {
    const key = item.category || 'uncategorized';
    let group = byKey.get(key);
    if (!group) {
      group = { key, label: fieldCategoryLabel(item.category || ''), items: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}

function fieldOptionGroups(fields: FieldOption[]) {
  return choiceItemGroups(fields.map((field) => ({ ...field, category: field.category }))).map((group) => ({
    key: group.key,
    label: group.label,
    items: group.items as FieldOption[],
  }));
}

function chartSelectionMessage(source: ChartSource, viewMode: ChartViewMode, kind: ChartKind): string {
  const period = source === 'stock' ? '月' : '年度';
  if (viewMode === 'chart' && kind === 'scatter') {
    return `会社、${period}、X軸、Y軸を選択してください。`;
  }
  return `会社、${period}、項目を選択してください。`;
}

function buildAnalysisTableRows(
  rows: Row[],
  fields: string[],
  fieldsById: Map<string, FieldOption>,
  source: ChartSource,
  selectedPeriods: string[],
  companies: CompanyOption[],
): AnalysisTableData {
  const fieldId = fields[0] || '';
  const field = fieldsById.get(fieldId);
  const periodColumns = selectedPeriods.slice().sort(comparePeriod);
  const columns = fieldId ? ['company', ...periodColumns] : [];
  const labels: Record<string, string> = {
    company: '会社名',
  };
  for (const period of periodColumns) {
    labels[period] = `${period}${source === 'stock' ? '' : '年度'}`;
  }
  const companyOrder = new Map(companies.map((company, index) => [company.id, index]));
  const byCompany = new Map<string, Row>();
  for (const row of rows) {
    const companyId = String(row.operating_company_id || '');
    const companyName = String(row.operating_company_name || companyId);
    const period = String(source === 'stock' ? row.month || row.fiscal_year || '' : row.fiscal_year || '');
    if (!companyId || !period || !periodColumns.includes(period)) {
      continue;
    }
    const out = byCompany.get(companyId) || { company: companyName, __company_order: companyOrder.get(companyId) ?? byCompany.size };
    const rawValue = row[`${fieldId}__raw`];
    out[period] = formatTableValue(String(rawValue ?? '').trim() ? rawValue : row[fieldId]);
    byCompany.set(companyId, out);
  }
  const tableRows = Array.from(byCompany.values())
    .sort((a, b) => Number(a.__company_order ?? 0) - Number(b.__company_order ?? 0))
    .map((row) => {
      const copied = { ...row };
      delete copied.__company_order;
      return copied;
    });
  return { rows: tableRows, columns, labels, fieldName: field?.name || fieldId, unit: field?.unit || '' };
}

function formatTableValue(value: unknown): string {
  if (value == null) return '';
  const number = tableNumericValue(value);
  if (number == null) return String(value);
  return new Intl.NumberFormat('ja-JP', { maximumFractionDigits: 4 }).format(number);
}

function tableNumericValue(value: unknown): number | null {
  if (value == null || value === '') return null;
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }
  const text = String(value).trim();
  if (!text) return null;
  const normalized = text
    .replace(/,/g, '')
    .replace(/％/g, '%')
    .replace(/−/g, '-')
    .replace(/△/g, '-')
    .replace(/▲/g, '-')
    .replace(/%/g, '');
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

function toCsv(rows: Row[], columns: string[], labels: Record<string, string>): string {
  const lines = [columns.map((column) => csvCell(labels[column] || column)).join(',')];
  for (const row of rows) {
    lines.push(columns.map((column) => csvCell(row[column])).join(','));
  }
  return lines.join('\n');
}

function csvCell(value: unknown): string {
  const text = String(value ?? '');
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

function toggleValue(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

function toggleSingleValue(values: string[], value: string): string[] {
  return values[0] === value ? [] : [value];
}

function getSeriesRenderKind(kind: ChartKind, index: number, style: SeriesStyle = {}): SeriesRenderKind {
  if (kind === 'bar') return 'bar';
  if (kind === 'combo') return style.renderAs || (index === 0 ? 'bar' : 'line');
  return 'line';
}

function chartAxisRanges({
  kind,
  rows,
  series,
  rightAxisFields,
  seriesStyles,
}: {
  kind: Exclude<ChartKind, 'scatter'>;
  rows: Row[];
  series: ChartSeries[];
  rightAxisFields: string[];
  seriesStyles: Record<string, SeriesStyle>;
}): { left?: [number, number]; right?: [number, number] } {
  const rightAxisSet = new Set(rightAxisFields);
  const leftValues: number[] = [];
  const rightValues: number[] = [];
  let leftHasBar = kind === 'bar';
  let rightHasBar = kind === 'bar';
  for (const [index, item] of series.entries()) {
    const target = rightAxisSet.has(item.fieldId) ? rightValues : leftValues;
    const renderKind = getSeriesRenderKind(kind, index, seriesStyles[item.key] || {});
    if (renderKind === 'bar') {
      if (rightAxisSet.has(item.fieldId)) rightHasBar = true;
      else leftHasBar = true;
    }
    for (const row of rows) {
      const value = numericValue(row[item.key]);
      if (value != null) target.push(value);
    }
  }
  return {
    left: niceAxisRange(leftValues, { includeZero: leftHasBar }),
    right: niceAxisRange(rightValues, { includeZero: rightHasBar }),
  };
}

function axisDomainsFromOverrides(
  overrides: Record<string, string>,
  autoRanges: { left?: [number, number]; right?: [number, number]; x?: [number, number]; y?: [number, number] },
): { left?: [number, number]; right?: [number, number]; x?: [number, number]; y?: [number, number] } {
  return {
    left: axisDomain(overrides.leftMin, overrides.leftMax, autoRanges.left),
    right: axisDomain(overrides.rightMin, overrides.rightMax, autoRanges.right),
    x: axisDomain(overrides.xMin, overrides.xMax, autoRanges.x),
    y: axisDomain(overrides.yMin, overrides.yMax, autoRanges.y),
  };
}

function axisOverridesFromAuto(
  autoRanges: { left?: [number, number]; right?: [number, number]; x?: [number, number]; y?: [number, number] },
): Record<string, string> {
  return {
    leftMin: autoRanges.left ? formatAxisInput(autoRanges.left[0]) : '',
    leftMax: autoRanges.left ? formatAxisInput(autoRanges.left[1]) : '',
    rightMin: autoRanges.right ? formatAxisInput(autoRanges.right[0]) : '',
    rightMax: autoRanges.right ? formatAxisInput(autoRanges.right[1]) : '',
    xMin: autoRanges.x ? formatAxisInput(autoRanges.x[0]) : '',
    xMax: autoRanges.x ? formatAxisInput(autoRanges.x[1]) : '',
    yMin: autoRanges.y ? formatAxisInput(autoRanges.y[0]) : '',
    yMax: autoRanges.y ? formatAxisInput(autoRanges.y[1]) : '',
  };
}

function axisDomain(minText: string | undefined, maxText: string | undefined, autoRange?: [number, number]): [number, number] | undefined {
  const manualMin = parseAxisNumber(minText);
  const manualMax = parseAxisNumber(maxText);
  const min = manualMin ?? autoRange?.[0];
  const max = manualMax ?? autoRange?.[1];
  if (min == null || max == null || min >= max) return autoRange;
  return [min, max];
}

function parseAxisNumber(value: unknown): number | null {
  const text = String(value ?? '').replace(/,/g, '').trim();
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function niceAxisRange(values: number[], { includeZero }: { includeZero: boolean }): [number, number] | undefined {
  const finite = values.filter((value) => Number.isFinite(value));
  if (!finite.length) return undefined;
  let min = Math.min(...finite);
  let max = Math.max(...finite);
  if (includeZero) {
    if (min > 0) min = 0;
    if (max < 0) max = 0;
  }
  if (min === max) {
    const spread = Math.max(Math.abs(max) * 0.1, 1);
    min -= spread;
    max += spread;
  }
  const span = max - min;
  const padding = span * 0.08;
  return [niceFloor(min - padding), niceCeil(max + padding)];
}

function niceFloor(value: number): number {
  const step = niceStep(value);
  return Math.floor(value / step) * step;
}

function niceCeil(value: number): number {
  const step = niceStep(value);
  return Math.ceil(value / step) * step;
}

function niceStep(value: number): number {
  const abs = Math.abs(value);
  if (abs === 0) return 1;
  const exponent = Math.floor(Math.log10(abs));
  const base = 10 ** Math.max(exponent - 1, -6);
  const scaled = abs / base;
  if (scaled <= 20) return base;
  if (scaled <= 50) return base * 2;
  if (scaled <= 100) return base * 5;
  return base * 10;
}

function formatAxisInput(value: number): string {
  if (Math.abs(value) >= 1000) return String(Math.round(value));
  if (Math.abs(value) >= 10) return Number(value.toFixed(1)).toString();
  if (Math.abs(value) >= 1) return Number(value.toFixed(2)).toString();
  return Number(value.toFixed(4)).toString();
}

function chartMargin(settings: ExportSettings, hasRightAxis: boolean) {
  const base = exportMarginPresets[settings.marginPreset];
  const directLabelSpace = settings.directLineLabels ? 96 : 0;
  const legendRightSpace = settings.legendPosition === 'right' ? 118 : 0;
  return {
    top: base.top,
    right: base.right + (hasRightAxis ? 30 : 0) + directLabelSpace + legendRightSpace,
    bottom: base.bottom,
    left: base.left,
  };
}

function chartLegendProps(settings: ExportSettings, fontSize: number): Record<string, unknown> | null {
  if (settings.legendPosition === 'none') return null;
  const baseStyle = {
    color: designPresets[settings.designPreset].text,
    fontSize,
    fontWeight: 700,
  };
  if (settings.legendPosition === 'right') {
    return {
      align: 'right',
      verticalAlign: 'middle',
      layout: 'vertical',
      wrapperStyle: { ...baseStyle, paddingLeft: 14, lineHeight: '1.5' },
    };
  }
  return {
    align: 'center',
    verticalAlign: settings.legendPosition,
    layout: 'horizontal',
    wrapperStyle: {
      ...baseStyle,
      paddingTop: settings.legendPosition === 'bottom' ? 12 : 0,
      paddingBottom: settings.legendPosition === 'top' ? 12 : 0,
    },
  };
}

function lastValueIndexes(rows: Row[], series: ChartSeries[]): Record<string, number> {
  const indexes: Record<string, number> = {};
  for (const item of series) {
    for (let index = rows.length - 1; index >= 0; index -= 1) {
      if (numericValue(rows[index]?.[item.key]) != null) {
        indexes[item.key] = index;
        break;
      }
    }
  }
  return indexes;
}

function chartTitle(kind: ChartKind, mode: ChartMode, source: ChartSource = 'financial'): string {
  if (kind === 'scatter') return '散布図';
  if (mode === 'company') {
    const prefix = source === 'stock' ? '会社・月比較' : source === 'factbook_orders' ? '受注カテゴリ会社比較' : '会社比較';
    if (kind === 'bar') return `${prefix} 棒グラフ`;
    if (kind === 'combo') return `${prefix} 複合グラフ`;
    return `${prefix} 折れ線`;
  }
  const prefix = source === 'stock' ? '月次推移' : source === 'factbook_orders' ? '受注カテゴリ年度推移' : '年度推移';
  if (kind === 'bar') return `${prefix} 棒グラフ`;
  if (kind === 'combo') return `${prefix} 複合グラフ`;
  return `${prefix} 折れ線`;
}

function chartSourceLabel(source: ChartSource): string {
  if (source === 'stock') return ' / 月次株価';
  if (source === 'factbook_orders') return ' / ファクトブック受注';
  return ' / 有報';
}

function periodTypeLabel(periodType: string): string {
  if (periodType === 'annual') return '年次';
  if (periodType === 'semiannual_h1') return '半期';
  return periodType || '年次';
}

function factbookCategoryTypeLabel(value: string): string {
  if (value === 'use') return '用途別';
  if (value === 'business_scope') return '建築/土木等';
  return value || '未分類';
}

function buildTrendChartRows(
  rows: Row[],
  fields: string[],
  fieldsById: Map<string, FieldOption>,
  companyCount: number,
): { rows: Row[]; series: ChartSeries[] } {
  const byYear = new Map<string, Row>();
  const seriesByKey = new Map<string, ChartSeries>();
  for (const row of rows) {
    const fiscalYear = String(row.fiscal_year || '');
    if (!fiscalYear) continue;
    const point = byYear.get(fiscalYear) || { fiscal_year: fiscalYear };
    byYear.set(fiscalYear, point);
    for (const fieldId of fields) {
      const value = numericValue(row[fieldId]);
      if (value == null) continue;
      const field = fieldsById.get(fieldId);
      const companyName = String(row.operating_company_name || row.operating_company_id || '');
      const key = chartSeriesKey('trend', String(row.operating_company_id || companyName), fieldId);
      point[key] = value;
      if (!seriesByKey.has(key)) {
        seriesByKey.set(key, {
          key,
          label: trendSeriesLabel(row, field, fields.length, companyCount),
          fieldId,
          fieldName: field?.name || fieldId,
          unit: field?.unit || '',
          companyName,
        });
      }
    }
  }
  return {
    rows: Array.from(byYear.values()).sort((a, b) => comparePeriod(a.fiscal_year, b.fiscal_year)),
    series: Array.from(seriesByKey.values()),
  };
}

function buildCompanyChartRows(
  rows: Row[],
  fields: string[],
  fieldsById: Map<string, FieldOption>,
  yearCount: number,
): { rows: Row[]; series: ChartSeries[] } {
  const out: Row[] = [];
  const seriesByKey = new Map<string, ChartSeries>();
  for (const row of rows) {
    const company = String(row.operating_company_name || row.operating_company_id || '');
    const year = String(row.fiscal_year || '');
    const point: Row = {
      label: yearCount > 1 ? `${company} ${year}` : company,
      company,
      fiscal_year: year,
    };
    let hasValue = false;
    for (const fieldId of fields) {
      const value = numericValue(row[fieldId]);
      if (value == null) continue;
      const field = fieldsById.get(fieldId);
      const key = chartSeriesKey('field', '', fieldId);
      point[key] = value;
      if (!seriesByKey.has(key)) {
        seriesByKey.set(key, {
          key,
          label: field?.name || fieldId,
          fieldId,
          fieldName: field?.name || fieldId,
          unit: field?.unit || '',
        });
      }
      hasValue = true;
    }
    if (hasValue) out.push(point);
  }
  return { rows: out, series: Array.from(seriesByKey.values()) };
}

function buildScatterRows(rows: Row[], xField: string, yField: string): Row[] {
  const out: Row[] = [];
  for (const row of rows) {
    const x = numericValue(row[xField]);
    const y = numericValue(row[yField]);
    if (x == null || y == null) continue;
    out.push({
      x,
      y,
      name: `${row.operating_company_name || row.operating_company_id || ''} ${row.fiscal_year || ''}`,
      company: row.operating_company_name || row.operating_company_id || '',
      fiscal_year: row.fiscal_year || '',
    });
  }
  return out;
}

function trendSeriesLabel(row: Row, field: FieldOption | undefined, fieldCount: number, companyCount: number): string {
  const company = String(row.operating_company_name || row.operating_company_id || '');
  const fieldName = field?.name || field?.id || '';
  if (fieldCount <= 1) return company || fieldName;
  if (companyCount <= 1) return fieldName;
  return `${company} / ${fieldName}`;
}

function chartSeriesKey(prefix: string, companyId: string, fieldId: string): string {
  return [prefix, companyId, fieldId].map((value) => value.replace(/[^a-zA-Z0-9_]+/g, '_')).join('__');
}

function comparePeriod(a: unknown, b: unknown): number {
  const left = String(a || '');
  const right = String(b || '');
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
    return leftNumber - rightNumber;
  }
  return left.localeCompare(right);
}

function numericValue(value: unknown): number | null {
  if (value == null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function pearsonCorrelation(rows: Row[]): number | null {
  const points = rows
    .map((row) => ({ x: numericValue(row.x), y: numericValue(row.y) }))
    .filter((point): point is { x: number; y: number } => point.x != null && point.y != null);
  if (points.length < 2) return null;
  const meanX = points.reduce((sum, point) => sum + point.x, 0) / points.length;
  const meanY = points.reduce((sum, point) => sum + point.y, 0) / points.length;
  let covariance = 0;
  let varianceX = 0;
  let varianceY = 0;
  for (const point of points) {
    const dx = point.x - meanX;
    const dy = point.y - meanY;
    covariance += dx * dy;
    varianceX += dx * dx;
    varianceY += dy * dy;
  }
  if (varianceX === 0 || varianceY === 0) return null;
  return covariance / Math.sqrt(varianceX * varianceY);
}

function formatAxisTick(value: unknown): string {
  const number = numericValue(value);
  if (number == null) return '';
  const abs = Math.abs(number);
  if (abs >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(number / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`;
  if (abs !== 0 && abs < 1) return number.toFixed(2);
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function formatChartValue(value: unknown, unit = ''): string {
  const number = numericValue(value);
  if (number == null) return '-';
  const maximumFractionDigits = unit === '%' ? 2 : Math.abs(number) >= 100 ? 0 : 2;
  const text = new Intl.NumberFormat('ja-JP', { maximumFractionDigits }).format(number);
  return unit ? `${text}${unit}` : text;
}

function formatValueLabel(value: unknown): string {
  const number = numericValue(value);
  if (number == null) return '';
  const abs = Math.abs(number);
  if (abs >= 1_000_000) return `${(number / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 1_000) return `${(number / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`;
  if (abs !== 0 && abs < 1) return number.toFixed(2);
  if (abs >= 100) return number.toFixed(0);
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

createRoot(document.getElementById('root')!).render(<App />);
