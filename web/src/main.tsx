import React from 'react';
import { createRoot } from 'react-dom/client';
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
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable
} from '@tanstack/react-table';
import './styles.css';

type Row = Record<string, unknown>;

type Page<T extends Row = Row> = {
  rows: T[];
  columns: string[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  status_counts?: RuleCandidateStatusCounts;
};

type Job = {
  id: string;
  name: string;
  status: string;
  started_at: string;
  finished_at: string;
  exit_code: number | null;
  error: string;
  logs: string[];
};

type Status = {
  app_version?: string;
  project_root: string;
  files: Record<string, boolean>;
  ai_bundle_generated_at_utc: string;
  algorithm_audit_generated_at_utc?: string;
  run_report: { summary: Record<string, string>; exists: boolean; path: string };
};

type AutomationStatus = {
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

type CompanyOption = {
  id: string;
  name: string;
  label: string;
};

type FieldOption = {
  id: string;
  name: string;
  category: string;
  unit: string;
  label: string;
};

type FieldPreset = {
  id: string;
  name: string;
  fields: string[];
};

type Options = {
  companies: CompanyOption[];
  years: string[];
  fields: FieldOption[];
  default_result_fields: string[];
  field_presets: FieldPreset[];
};

type ReviewTarget = {
  company: string;
  fiscal_year: string;
  field_id: string;
};

type CellDetail = {
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
  wide_row: Row;
  audit_rows: Row[];
  review_rows: Row[];
  resolved_rows: Row[];
};

type RuleCandidateApplyResult = {
  applied_candidates: number;
  updated_fields: Array<{ field_id: string; columns: string[] }>;
  updated_sections: string[];
  backups: string[];
  warnings: string[];
};

type RuleCandidateStatusCounts = {
  active: number;
  applied: number;
  all: number;
};

type RuleCandidateGenerateResult = {
  path: string;
  total: number;
  all_total?: number;
  applied_total?: number;
  status_counts?: RuleCandidateStatusCounts;
  rows: Row[];
};

type AlgorithmAuditResult = {
  generated_at_utc: string;
  bundle_dir: string;
  summary: Record<string, unknown>;
  files: Array<{ file: string; source?: string; description: string; bytes: number }>;
  prompt: string;
  prompt_path: string;
  readme_path: string;
};

type ChartData = {
  rows: Row[];
  columns: string[];
  total: number;
  omitted_rows: number;
  fields: FieldOption[];
  companies: CompanyOption[];
  years: string[];
};

type ChartKind = 'line' | 'bar' | 'combo' | 'scatter';
type ChartMode = 'trend' | 'company';
type SeriesRenderKind = 'line' | 'bar';

type ChartSeries = {
  key: string;
  label: string;
  fieldId: string;
  fieldName: string;
  unit: string;
  companyName?: string;
};

type SeriesStyle = {
  color?: string;
  strokeWidth?: number;
  strokeDasharray?: string;
  renderAs?: SeriesRenderKind;
};

const chartAspectOptions = [
  { id: '16:9', label: '16:9 横長', ratio: 16 / 9 },
  { id: '3:2', label: '3:2 標準', ratio: 3 / 2 },
  { id: '4:3', label: '4:3 高め', ratio: 4 / 3 },
  { id: '1:1', label: '1:1 正方形', ratio: 1 },
] as const;

const linePatternOptions = [
  { id: 'solid', label: '実線', dasharray: '' },
  { id: 'dash', label: '破線', dasharray: '7 5' },
  { id: 'dot', label: '点線', dasharray: '2 4' },
] as const;

const ruleCandidateLabels: Record<string, string> = {
  field_id: 'field_id',
  field_name_ja: '項目',
  evidence_count: '証跡数',
  company_year_ids: '会社年度',
  proposed_xbrl_tags: 'XBRLタグ候補',
  proposed_section_keywords: 'セクション候補',
  proposed_tables: '表候補',
  proposed_row_labels: '行ラベル候補',
  proposed_scope: 'スコープ',
  proposed_unit: '単位',
  generality: '汎用性',
  quotes: '引用',
  reviewed_value_examples: '値の例',
  learning_source: '学習元',
  confidence: '信頼度',
  inference_notes: '推定メモ',
  candidate_status: '候補状態',
  candidate_applied_at: '候補反映日時',
  needs_manual_check: '要確認',
  recommended_action: '反映先候補'
};

const reviewColumnLabels: Record<string, string> = {
  review_saved: '保存',
  applied_status: '反映状態',
  applied_value: '反映値',
  applied_at: '反映日時',
  reviewed_at: '保存日時',
  review_decision: '判定',
  corrected_value: '修正値',
  reviewer_note: 'メモ',
  company_name_ja: '会社名'
};

const reviewPriorityColumns = [
  'review_saved',
  'applied_status',
  'applied_value',
  'applied_at',
  'company_year_id',
  'company_name_ja',
  'fiscal_year',
  'field_id',
  'field_name_ja',
  'extracted_value',
  'review_decision',
  'corrected_value',
  'review_reason',
  'reviewer_note',
  'reviewed_at'
];

const tabs = [
  ['run', '実行'],
  ['results', '結果'],
  ['charts', 'グラフ'],
  ['audit', '根拠'],
  ['review', 'レビュー'],
  ['ai', 'AI分析'],
  ['report', 'レポート']
] as const;

const APP_VERSION_FALLBACK = '0.11.0';

const baseColumns = new Set([
  'company_year_id',
  'fiscal_year',
  'fiscal_year_end',
  'operating_company_id',
  'operating_company_name',
  'reporting_entity_id',
  'data_scope_allowed',
  'analysis_treatment'
]);

const baseColumnLabels: Record<string, string> = {
  company_year_id: '会社年度',
  fiscal_year: '年度',
  fiscal_year_end: '決算日',
  operating_company_id: '会社ID',
  operating_company_name: '会社名',
  reporting_entity_id: '開示主体',
  data_scope_allowed: '対象スコープ',
  analysis_treatment: '分析上の扱い'
};

function withPriorityColumns(columns: string[], priority: string[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const column of priority) {
    if (columns.includes(column) && !seen.has(column)) {
      ordered.push(column);
      seen.add(column);
    }
  }
  for (const column of columns) {
    if (!seen.has(column)) {
      ordered.push(column);
      seen.add(column);
    }
  }
  return ordered;
}

function appliedStatusLabel(status: unknown): string {
  const value = String(status || '').trim();
  if (value === 'applied') return '反映済み';
  if (value === 'rejected') return '除外済み';
  if (value === 'not_exported') return '未出力';
  if (value === 'not_found') return '対象なし';
  return '未反映';
}

function reviewSavedLabel(value: unknown): string {
  return String(value || '') === 'yes' ? '保存済み' : '未保存';
}

function candidateStatusLabel(status: unknown): string {
  return String(status || '').trim() === 'applied' ? '対応済み' : '未対応';
}

function formatRuleCandidateGenerateMessage(result: RuleCandidateGenerateResult): string {
  const active = result.status_counts?.active ?? result.total ?? 0;
  const applied = result.status_counts?.applied ?? result.applied_total ?? 0;
  const all = result.status_counts?.all ?? result.all_total ?? result.total ?? 0;
  if (all === 0) {
    return '候補を更新しました: 全候補0件。accept/correctで保存された正しい値と、元の根拠テキストがそろうと候補化できます。';
  }
  if (active === 0 && applied > 0) {
    return `候補を更新しました: 未対応0件 / 対応済み${applied}件 / 全候補${all}件。新規対応は不要です。対応済みタブで履歴を確認できます。`;
  }
  return `候補を更新しました: 未対応${active}件 / 対応済み${applied}件 / 全候補${all}件。`;
}

function renderCellValue(column: string, value: unknown) {
  if (column === 'review_saved') {
    const saved = String(value || '') === 'yes';
    return <span className={`pill ${saved ? 'pill-ok' : 'pill-muted'}`}>{reviewSavedLabel(value)}</span>;
  }
  if (column === 'applied_status') {
    const status = String(value || '').trim();
    const ok = status === 'applied' || status === 'rejected';
    return <span className={`pill ${ok ? 'pill-ok' : 'pill-warn'}`}>{appliedStatusLabel(value)}</span>;
  }
  if (column === 'candidate_status') {
    const applied = String(value || '').trim() === 'applied';
    return <span className={`pill ${applied ? 'pill-ok' : 'pill-warn'}`}>{candidateStatusLabel(value)}</span>;
  }
  return String(value ?? '');
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

function useOptions() {
  const [options, setOptions] = React.useState<Options | null>(null);
  const [error, setError] = React.useState('');

  React.useEffect(() => {
    api<Options>('/api/options').then(setOptions).catch((err) => setError(String(err)));
  }, []);

  const fieldLabels = React.useMemo(() => {
    const labels: Record<string, string> = { ...baseColumnLabels };
    for (const field of options?.fields || []) {
      labels[field.id] = field.name || field.id;
    }
    return labels;
  }, [options]);

  return { options, fieldLabels, error };
}

function App() {
  const [tab, setTab] = React.useState<(typeof tabs)[number][0]>('run');
  const [status, setStatus] = React.useState<Status | null>(null);
  const [job, setJob] = React.useState<Job | null>(null);
  const [dataRefreshToken, setDataRefreshToken] = React.useState(0);
  const [error, setError] = React.useState('');
  const [auditTarget, setAuditTarget] = React.useState<{ company_year_id: string; field_id: string } | null>(null);
  const [reviewTarget, setReviewTarget] = React.useState<ReviewTarget | null>(null);
  const completedJobRef = React.useRef('');

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
        <nav>
          {tabs.map(([key, label]) => (
            <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>
              {label}
            </button>
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
          <button className="ghost" onClick={() => { refreshStatus(); refreshJob(); }}>
            再読込
          </button>
        </header>
        {error && (
          <div className="alert">
            <span>{error}</span>
            <button onClick={() => setError('')}>閉じる</button>
          </div>
        )}
        {tab === 'run' && <RunPanel job={job} onJob={setJob} onError={setError} onRefreshStatus={refreshStatus} status={status} />}
        {tab === 'results' && (
          <ResultsPanel
            refreshToken={dataRefreshToken}
            onAudit={(target) => { setAuditTarget(target); setTab('audit'); }}
            onReview={(target) => { setReviewTarget(target); setTab('review'); }}
          />
        )}
        {tab === 'charts' && <ChartsPanel refreshToken={dataRefreshToken} />}
        {tab === 'audit' && <AuditPanel initialTarget={auditTarget} refreshToken={dataRefreshToken} />}
        {tab === 'review' && <ReviewPanel initialTarget={reviewTarget} onJob={setJob} onError={setError} refreshToken={dataRefreshToken} />}
        {tab === 'ai' && <AiPanel onError={setError} />}
        {tab === 'report' && <ReportPanel status={status} refreshToken={dataRefreshToken} />}
      </main>
      <div className="version-badge" aria-label="アプリバージョン">
        v{status?.app_version || APP_VERSION_FALLBACK}
      </div>
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
  const [automationLoading, setAutomationLoading] = React.useState(false);
  const [fiscalYear, setFiscalYear] = React.useState('');
  const [forceAnnual, setForceAnnual] = React.useState(false);

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

  async function start(path: string) {
    try {
      const next = await api<Job>(path, { method: 'POST' });
      onJob(next);
      window.setTimeout(onRefreshStatus, 800);
    } catch (err) {
      onError(String(err));
    }
  }

  async function startAnnual(dryRun: boolean) {
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

  return (
    <section className="stack">
      <div className="toolbar">
        <button onClick={() => start('/api/jobs/run-all')}>一括実行</button>
        <button onClick={() => start('/api/jobs/reextract-with-review')}>レビュー学習後に再取得</button>
        <button onClick={() => start('/api/jobs/report')}>レポート再生成</button>
        <button onClick={() => start('/api/jobs/algorithm-audit')}>アルゴリズム監査パック生成</button>
        <button onClick={() => start('/api/jobs/apply-review')}>保存値を最終結果に反映</button>
      </div>
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
  onRun
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
}) {
  const ready = automation?.review_gate.ready;
  const targetYear = automation?.target_fiscal_year ?? automation?.annual_window.target_fiscal_year ?? '';
  const blocking = automation?.review_gate.blocking_reasons || [];
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
        <button className="ghost" disabled={loading} onClick={onRefresh}>状態更新</button>
        <button className="secondary" onClick={onDryRun}>ドライラン</button>
        <button disabled={!forceAnnual && ready === false} onClick={onRun}>年次取得を実行</button>
      </div>
    </div>
  );
}

function FileHealth({ status }: { status: Status | null }) {
  if (!status) return <Empty message="状態を読み込み中です。" />;
  return (
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

function ResultsPanel({
  refreshToken,
  onAudit,
  onReview
}: {
  refreshToken: number;
  onAudit: (target: { company_year_id: string; field_id: string }) => void;
  onReview: (target: ReviewTarget) => void;
}) {
  const [page, setPage] = React.useState(1);
  const [company, setCompany] = React.useState('');
  const [year, setYear] = React.useState('');
  const [preset, setPreset] = React.useState('core');
  const [data, setData] = React.useState<Page | null>(null);
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
    const params = new URLSearchParams({ page: String(page), page_size: '50', company, fiscal_year: year, fields: selectedFields });
    api<Page>(`/api/datasets/wide?${params}`).then(setData).catch((err) => setError(String(err)));
  }, [page, company, year, selectedFields, refreshToken]);

  function resetPage(next: () => void) {
    setPage(1);
    setCellDetail(null);
    setCellError('');
    next();
  }

  async function openCellDetail(row: Row, column: string) {
    if (baseColumns.has(column)) return;
    const companyYearId = String(row.company_year_id || '');
    if (!companyYearId) return;
    const params = new URLSearchParams({ company_year_id: companyYearId, field_id: column });
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
        <label className="filter-field">
          <span>指標</span>
          <select value={preset} onChange={(e) => resetPage(() => setPreset(e.target.value))}>
            {(options?.field_presets || [{ id: 'core', name: '主要指標', fields: [] }]).map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
      </FilterBar>
      <p className="hint">初期表示は最新年度の主要指標です。数値セルや空欄セルをクリックすると、根拠・レビュー候補・次の操作を確認できます。</p>
      {optionsError && <InlineError message={optionsError} />}
      {error && <InlineError message={error} />}
      <CellDetailPanel
        detail={cellDetail}
        loading={cellLoading}
        error={cellError}
        onAudit={onAudit}
        onReview={onReview}
      />
      {data ? (
        <>
          <DataTable
            data={data.rows}
            columns={data.columns}
            columnLabels={fieldLabels}
            markEmptyCells
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

function CellDetailPanel({
  detail,
  loading,
  error,
  onAudit,
  onReview
}: {
  detail: CellDetail | null;
  loading: boolean;
  error: string;
  onAudit: (target: { company_year_id: string; field_id: string }) => void;
  onReview: (target: ReviewTarget) => void;
}) {
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
  const canOpenReview = detail.review_rows.length > 0 || detail.resolved_rows.length > 0;
  const displayValue = detail.is_blank ? '空欄' : detail.current_value;

  return (
    <div className="panel cell-detail">
      <div className="panel-head">
        <div>
          <h2>{detail.field_name_ja}</h2>
          <p className="muted">{detail.company_year_id} / {detail.field_id}</p>
        </div>
        <span className={`badge status-${detail.status}`}>{detail.status_label}</span>
      </div>

      <div className="detail-grid">
        <div>
          <small>現在値</small>
          <strong className={detail.is_blank ? 'blank-value' : ''}>{displayValue}</strong>
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
      <MiniRows title="根拠" rows={detail.audit_rows} columns={auditColumns} emptyMessage="source_audit.csv に該当行はありません。" />
    </div>
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
          <DataTable data={data.rows} columns={data.columns} />
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
  onJob,
  onError,
  refreshToken
}: {
  initialTarget: ReviewTarget | null;
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
  const [data, setData] = React.useState<Page | null>(null);
  const [selected, setSelected] = React.useState<Row | null>(null);
  const [decision, setDecision] = React.useState('accept');
  const [correctedValue, setCorrectedValue] = React.useState('');
  const [note, setNote] = React.useState('');
  const [message, setMessage] = React.useState('');
  const [panelError, setPanelError] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [applying, setApplying] = React.useState(false);
  const [ruleCandidates, setRuleCandidates] = React.useState<Page | null>(null);
  const [ruleCandidateStatus, setRuleCandidateStatus] = React.useState('active');
  const [ruleMessage, setRuleMessage] = React.useState('');
  const [ruleError, setRuleError] = React.useState('');
  const [ruleLoading, setRuleLoading] = React.useState(false);
  const editorRef = React.useRef<HTMLElement | null>(null);
  const autoSelectedTargetRef = React.useRef('');
  const { options, fieldLabels } = useOptions();

  React.useEffect(() => {
    if (!initialTarget) return;
    setPage(1);
    setCompany(initialTarget.company);
    setYear(initialTarget.fiscal_year);
    setFieldId(initialTarget.field_id);
    setSearch('');
    setReviewStatus('');
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
      review_status: reviewStatus
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
  }, [page, company, year, fieldId, search, reviewStatus, onError, refreshToken]);

  React.useEffect(() => loadReviewQueue(), [loadReviewQueue]);

  const loadRuleCandidates = React.useCallback((statusOverride?: string) => {
    const status = statusOverride ?? ruleCandidateStatus;
    api<Page>(`/api/reviews/rule-candidates?page=1&page_size=100&candidate_status=${status}`)
      .then(setRuleCandidates)
      .catch((err) => setRuleError(String(err)));
  }, [ruleCandidateStatus]);

  React.useEffect(() => {
    loadRuleCandidates();
  }, [loadRuleCandidates, refreshToken]);

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
      try {
        const candidateResult = await api<RuleCandidateGenerateResult>('/api/reviews/rule-candidates/generate', { method: 'POST' });
        setRuleMessage(formatRuleCandidateGenerateMessage(candidateResult));
        loadRuleCandidates();
        setMessage(`保存しました: ${result.changed}件 / resolved合計 ${result.total}件。正しい値から学習候補も更新しました。`);
      } catch (candidateErr) {
        setRuleError(String(candidateErr));
        setMessage(`保存しました: ${result.changed}件 / resolved合計 ${result.total}件。学習候補の更新は失敗しました。`);
      }
      loadReviewQueue();
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function applyReview() {
    setApplying(true);
    setPanelError('');
    try {
      const next = await api<Job>('/api/jobs/apply-review', { method: 'POST' });
      onJob(next);
      setMessage('保存済みレビューを最終結果へ反映するジョブを開始しました。');
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setApplying(false);
    }
  }

  async function deleteReview() {
    if (!selected || !isEditingSavedReview) return;
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
    } catch (err) {
      setPanelError(String(err));
    } finally {
      setDeleting(false);
    }
  }

  async function generateRuleCandidates() {
    setRuleLoading(true);
    setRuleError('');
    setRuleMessage('');
    try {
      const result = await api<RuleCandidateGenerateResult>('/api/reviews/rule-candidates/generate', { method: 'POST' });
      const activeCount = result.status_counts?.active ?? result.total ?? 0;
      const appliedCount = result.status_counts?.applied ?? result.applied_total ?? 0;
      const nextStatus = activeCount === 0 && appliedCount > 0 ? 'applied' : ruleCandidateStatus;
      setRuleMessage(formatRuleCandidateGenerateMessage(result));
      if (nextStatus !== ruleCandidateStatus) {
        setRuleCandidateStatus(nextStatus);
      }
      loadRuleCandidates(nextStatus);
    } catch (err) {
      setRuleError(String(err));
    } finally {
      setRuleLoading(false);
    }
  }

  const extractedValue = String(selected?.extracted_value || '').trim();
  const isEditingSavedReview = selected?.review_saved === 'yes';
  const canSave = Boolean(selected) && !saving && !(decision === 'correct' && !correctedValue.trim()) && !(decision === 'accept' && !extractedValue);

  return (
    <section className="review-layout">
      <div className="stack">
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
        <RuleCandidatesPanel
          data={ruleCandidates}
          loading={ruleLoading}
          message={ruleMessage}
          error={ruleError}
          candidateStatus={ruleCandidateStatus}
          onCandidateStatus={setRuleCandidateStatus}
          onGenerate={generateRuleCandidates}
          onReload={loadRuleCandidates}
          onJob={onJob}
        />
        {data ? (
          <div className="panel review-queue-panel">
            <div className="panel-head">
              <h2>レビュー一覧</h2>
              <p className="muted">左端の編集ボタンで右側のレビュー編集欄に読み込みます。</p>
            </div>
            <DataTable
              data={data.rows}
              columns={withPriorityColumns(data.columns, reviewPriorityColumns)}
              columnLabels={{ ...fieldLabels, ...reviewColumnLabels }}
              onRowClick={pick}
              selectedRowKey={selectedRowKey}
              getRowKey={reviewRowKey}
              rowActionLabel="編集"
              compact
            />
            <Pager page={data.page} totalPages={data.total_pages} total={data.total} onPage={setPage} />
          </div>
        ) : (
          <Empty message="レビューキューを読み込み中です。" />
        )}
      </div>
      <aside className="editor" ref={editorRef}>
        <h2>レビュー編集</h2>
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
            {isEditingSavedReview && <p className="hint">この行は保存済みレビューです。内容を直して保存すると同じ会社年度・項目を上書きします。</p>}
            <label>判定</label>
            <select value={decision} onChange={(e) => setDecision(e.target.value)}>
              <option value="accept">accept</option>
              <option value="correct">correct</option>
              <option value="reject">reject</option>
            </select>
            <label>修正値</label>
            <input value={correctedValue} onChange={(e) => setCorrectedValue(e.target.value)} disabled={decision !== 'correct'} />
            <label>メモ（任意）</label>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={5} />
            <p className="hint">正しい値だけでも学習候補に使います。根拠やラベルが分かる場合だけメモに補足してください。</p>
            {decision === 'correct' && !correctedValue.trim() && <p className="hint">修正値を入力すると保存できます。</p>}
            {decision === 'accept' && !extractedValue && <p className="hint">抽出値が空の行は correct で修正値を入力してください。</p>}
            {panelError && <InlineError message={panelError} />}
            <div className="toolbar">
              <button type="button" onClick={save} disabled={!canSave}>{saving ? '保存中...' : isEditingSavedReview ? '上書き保存' : '保存'}</button>
              {isEditingSavedReview && (
                <button type="button" className="danger" onClick={deleteReview} disabled={deleting}>{deleting ? '削除中...' : 'レビュー削除'}</button>
              )}
              <button type="button" className="secondary" onClick={applyReview} disabled={applying}>{applying ? '反映中...' : '保存値を最終結果に反映'}</button>
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

function RuleCandidatesPanel({
  data,
  loading,
  message,
  error,
  candidateStatus,
  onCandidateStatus,
  onGenerate,
  onReload,
  onJob
}: {
  data: Page | null;
  loading: boolean;
  message: string;
  error: string;
  candidateStatus: string;
  onCandidateStatus: (status: string) => void;
  onGenerate: () => void;
  onReload: () => void;
  onJob: (job: Job) => void;
}) {
  const [selectedCandidate, setSelectedCandidate] = React.useState<Row | null>(null);
  const [applyMessage, setApplyMessage] = React.useState('');
  const [applyError, setApplyError] = React.useState('');
  const [applying, setApplying] = React.useState(false);
  const statusCounts = data?.status_counts;
  const selectedCandidateApplied = String(selectedCandidate?.candidate_status || '').trim() === 'applied';

  async function applySelectedCandidate() {
    if (!selectedCandidate) return;
    if (selectedCandidateApplied) {
      setApplyError('この候補は対応済みです。未対応候補だけ設定へ反映できます。');
      return;
    }
    const fieldId = String(selectedCandidate.field_id || '').trim();
    if (!fieldId) {
      setApplyError('field_id が空の候補は反映できません。');
      return;
    }
    setApplying(true);
    setApplyError('');
    setApplyMessage('');
    try {
      const result = await api<RuleCandidateApplyResult>('/api/reviews/rule-candidates/apply', {
        method: 'POST',
        body: JSON.stringify({ field_ids: [fieldId] })
      });
      const fieldUpdates = result.updated_fields
        .map((item) => `${item.field_id}: ${item.columns.join(', ')}`)
        .join(' / ') || '辞書変更なし';
      const sectionUpdates = result.updated_sections.join(', ') || 'セクション変更なし';
      const warningText = result.warnings.length ? ` / 注意: ${result.warnings.join(' / ')}` : '';
      setApplyMessage(`設定へ反映しました: 候補${result.applied_candidates}件 / ${fieldUpdates} / ${sectionUpdates}${warningText}。この候補は対応済みタブに移動します。`);
      setSelectedCandidate(null);
      onReload();
    } catch (err) {
      setApplyError(String(err));
    } finally {
      setApplying(false);
    }
  }

  async function startReextractWithReview() {
    setApplying(true);
    setApplyError('');
    setApplyMessage('');
    try {
      const next = await api<Job>('/api/jobs/reextract-with-review', { method: 'POST' });
      onJob(next);
      setApplyMessage('レビュー学習後の再取得ジョブを開始しました。');
    } catch (err) {
      setApplyError(String(err));
    } finally {
      setApplying(false);
    }
  }

  function pickCandidate(row: Row) {
    setSelectedCandidate(row);
    setApplyMessage('');
    setApplyError('');
  }

  function statusTabLabel(value: 'active' | 'applied' | 'all', label: string) {
    const count = statusCounts?.[value];
    return typeof count === 'number' ? `${label} ${count}` : label;
  }

  function emptyMessage() {
    if (candidateStatus === 'active') return '未対応の抽出ルール候補はありません。新しい正しい値を保存して候補を更新するか、対応済みタブを確認してください。';
    if (candidateStatus === 'applied') return '対応済みの抽出ルール候補はありません。';
    return '抽出ルール候補はまだありません。';
  }

  return (
    <div className="panel rule-candidates">
      <div className="panel-head">
        <h2>抽出ルール候補</h2>
        <div className="toolbar">
          <div className="segmented-control">
            {[
              { value: 'active', label: '未対応' },
              { value: 'applied', label: '対応済み' },
              { value: 'all', label: '全候補' }
            ].map((item) => (
              <button
                key={item.value}
                type="button"
                className={candidateStatus === item.value ? 'active' : ''}
                onClick={() => onCandidateStatus(item.value)}
              >
                {statusTabLabel(item.value as 'active' | 'applied' | 'all', item.label)}
              </button>
            ))}
          </div>
          <button type="button" onClick={onGenerate} disabled={loading}>{loading ? '更新中...' : '正しい値から候補を更新'}</button>
          <button type="button" className="ghost" onClick={onReload}>再読込</button>
        </div>
      </div>
      <div className="rule-learning-flow">
        <div>
          <strong>レビュー</strong>
          <span>セルごとの accept / correct 判断</span>
        </div>
        <div>
          <strong>正しい値</strong>
          <span>保存された人間確認済みの値</span>
        </div>
        <div>
          <strong>ルール候補</strong>
          <span>同じ項目を他社・他年度で探す条件案</span>
        </div>
      </div>
      {message && <p className="success-text">{message}</p>}
      {error && <InlineError message={error} />}
      <div className="candidate-action">
        <div>
          <strong>{selectedCandidate ? String(selectedCandidate.field_name_ja || selectedCandidate.field_id) : '候補を選択'}</strong>
          <p className="muted">
            {selectedCandidate
              ? `field_id: ${String(selectedCandidate.field_id)} / 証跡数: ${String(selectedCandidate.evidence_count || '-')} / 状態: ${candidateStatusLabel(selectedCandidate.candidate_status)}`
              : '未対応候補を選ぶと設定へ反映できます。対応済み候補は履歴確認用です。'}
          </p>
        </div>
        <button type="button" className="secondary" onClick={applySelectedCandidate} disabled={!selectedCandidate || selectedCandidateApplied || applying}>
          {selectedCandidateApplied ? '対応済み' : applying ? '反映中...' : '選択候補を設定へ反映'}
        </button>
        <button type="button" onClick={startReextractWithReview} disabled={applying}>
          レビュー学習後に再取得
        </button>
      </div>
      {selectedCandidate && (
        <dl className="candidate-detail">
          <dt>XBRLタグ候補</dt>
          <dd>{String(selectedCandidate.proposed_xbrl_tags || '-')}</dd>
          <dt>セクション候補</dt>
          <dd>{String(selectedCandidate.proposed_section_keywords || '-')}</dd>
          <dt>表・行ラベル候補</dt>
          <dd>{[selectedCandidate.proposed_tables, selectedCandidate.proposed_row_labels].filter(Boolean).map(String).join(' / ') || '-'}</dd>
          <dt>信頼度</dt>
          <dd>{String(selectedCandidate.confidence || '-')} / {String(selectedCandidate.inference_notes || '-')}</dd>
        </dl>
      )}
      {applyMessage && <p className="success-text">{applyMessage}</p>}
      {applyError && <InlineError message={applyError} />}
      {data?.rows.length ? (
        <DataTable data={data.rows} columns={data.columns} columnLabels={ruleCandidateLabels} onRowClick={pickCandidate} compact />
      ) : (
        <Empty message={emptyMessage()} />
      )}
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
  const reviewSectionCount = auditResult?.summary?.review_derived_sections ?? '-';
  const candidateCount = auditResult?.summary?.review_learning_candidates ?? '-';

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
              <dd>{String(riskCount)}件 / review_* {String(reviewSectionCount)}件 / 候補 {String(candidateCount)}件</dd>
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
            <button className="ghost" onClick={() => navigator.clipboard.writeText(prompt)}>コピー</button>
          </div>
          {references.length > 0 && <p className="muted">参照: {references.join(', ')}</p>}
          <pre className="prompt">{prompt || '条件を入力してプロンプトを生成してください。'}</pre>
        </div>
        <div className="panel">
          <div className="panel-head">
            <h2>アルゴリズム監査プロンプト</h2>
            <button className="ghost" onClick={() => navigator.clipboard.writeText(auditPrompt)} disabled={!auditPrompt}>コピー</button>
          </div>
          <pre className="prompt">{auditPrompt || '監査パックを生成すると、AIに渡す監査プロンプトが表示されます。'}</pre>
        </div>
      </div>
    </section>
  );
}

function ChartsPanel({ refreshToken }: { refreshToken: number }) {
  const { options, error: optionsError } = useOptions();
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
  const [chartAspect, setChartAspect] = React.useState<(typeof chartAspectOptions)[number]['id']>('16:9');
  const [showCorrelation, setShowCorrelation] = React.useState(false);
  const [scatterX, setScatterX] = React.useState('');
  const [scatterY, setScatterY] = React.useState('');
  const [data, setData] = React.useState<ChartData | null>(null);
  const [error, setError] = React.useState('');

  React.useEffect(() => {
    if (!options) return;
    if (!selectedCompanies.length) {
      setSelectedCompanies(options.companies.slice(0, 5).map((item) => item.id));
    }
    if (!selectedYears.length) {
      setSelectedYears(options.years.slice(-6));
    }
    if (!selectedFields.length) {
      const defaults = ['roe', 'net_sales_consolidated', 'operating_income_consolidated']
        .filter((field) => options.fields.some((item) => item.id === field));
      setSelectedFields(defaults.length ? defaults.slice(0, 1) : options.fields.slice(0, 1).map((item) => item.id));
    }
    if (!scatterX) {
      setScatterX(options.fields.find((item) => item.id === 'net_sales_consolidated')?.id || options.fields[0]?.id || '');
    }
    if (!scatterY) {
      setScatterY(options.fields.find((item) => item.id === 'roe')?.id || options.fields[1]?.id || options.fields[0]?.id || '');
    }
  }, [options, selectedCompanies.length, selectedYears.length, selectedFields.length, scatterX, scatterY]);

  React.useEffect(() => {
    setRightAxisFields((current) => current.filter((fieldId) => selectedFields.includes(fieldId)));
  }, [selectedFields]);

  const queryFields = React.useMemo(() => {
    if (chartKind === 'scatter') {
      return [scatterX, scatterY].filter(Boolean);
    }
    return selectedFields;
  }, [chartKind, scatterX, scatterY, selectedFields]);

  React.useEffect(() => {
    if (!options || !queryFields.length) return;
    const params = new URLSearchParams({
      companies: selectedCompanies.join(','),
      fiscal_years: selectedYears.join(','),
      fields: queryFields.join(','),
      max_rows: '5000'
    });
    api<ChartData>(`/api/charts/data?${params}`)
      .then((next) => {
        setData(next);
        setError('');
      })
      .catch((err) => setError(String(err)));
  }, [options, selectedCompanies, selectedYears, queryFields, refreshToken]);

  if (!options) {
    return <Empty message="グラフ設定を読み込み中です。" />;
  }

  const fieldsById = new Map(options.fields.map((field) => [field.id, field]));
  const fieldChoices = options.fields.filter((field) => chartFieldEligible(field));
  const selectedFieldOptions = selectedFields.map((fieldId) => fieldsById.get(fieldId)).filter(Boolean) as FieldOption[];
  const chartRows = data?.rows || [];
  const trend = buildTrendChartRows(chartRows, selectedFields, fieldsById, selectedCompanies.length);
  const companyBars = buildCompanyChartRows(chartRows, selectedFields, fieldsById, selectedYears.length);
  const scatter = buildScatterRows(chartRows, scatterX, scatterY);
  const fullSeries = mode === 'trend' ? trend.series : companyBars.series;
  const series = fullSeries.slice(0, 18);
  const hasSeriesOverflow = fullSeries.length > series.length;
  const selectedRightAxisFields = rightAxisFields.filter((fieldId) => selectedFields.includes(fieldId));
  const selectedSeries = series.find((item) => item.key === selectedSeriesKey) || series[0] || null;
  const chartAspectRatio = chartAspectOptions.find((item) => item.id === chartAspect)?.ratio || 16 / 9;
  const selectedSeriesIndex = selectedSeries ? Math.max(0, series.findIndex((item) => item.key === selectedSeries.key)) : 0;
  const selectedSeriesStyle = selectedSeries ? seriesStyles[selectedSeries.key] || {} : {};
  const selectedSeriesRenderKind = getSeriesRenderKind(chartKind, selectedSeriesIndex, selectedSeriesStyle);

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

  return (
    <section className="stack">
      <div className="filter-bar chart-filter-bar">
        <label className="filter-field small">
          <span>種類</span>
          <select value={chartKind} onChange={(e) => setChartKind(e.target.value as ChartKind)}>
            <option value="line">折れ線</option>
            <option value="bar">棒</option>
            <option value="combo">複合</option>
            <option value="scatter">散布図</option>
          </select>
        </label>
        {chartKind !== 'scatter' && (
          <label className="filter-field">
            <span>比較軸</span>
            <select value={mode} onChange={(e) => setMode(e.target.value as ChartMode)}>
              <option value="trend">年度推移（会社・項目を系列化）</option>
              <option value="company">会社比較（横軸を会社/年度）</option>
            </select>
          </label>
        )}
        <button className="ghost" onClick={() => {
          setSelectedCompanies(options.companies.slice(0, 5).map((item) => item.id));
          setSelectedYears(options.years.slice(-6));
          setSelectedFields(['roe'].filter((field) => fieldsById.has(field)));
          setRightAxisFields([]);
        }}>
          初期化
        </button>
      </div>
      {optionsError && <InlineError message={optionsError} />}
      {error && <InlineError message={error} />}
      <div className="chart-workbench">
        <div className="panel chart-controls">
          <ChoiceGroup
            title="会社"
            items={options.companies}
            selected={selectedCompanies}
            onToggle={(id) => setSelectedCompanies(toggleValue(selectedCompanies, id))}
            onAll={() => setSelectedCompanies(options.companies.map((item) => item.id))}
            onClear={() => setSelectedCompanies([])}
          />
          <ChoiceGroup
            title="年度"
            items={options.years.map((year) => ({ id: year, label: year, name: year }))}
            selected={selectedYears}
            onToggle={(id) => setSelectedYears(toggleValue(selectedYears, id))}
            onAll={() => setSelectedYears(options.years)}
            onClear={() => setSelectedYears([])}
            compact
          />
          <div className="chart-style-section">
            <div className="choice-head">
              <h3>表示設定</h3>
            </div>
            <label className="filter-field">
              <span>縦横比</span>
              <select value={chartAspect} onChange={(event) => setChartAspect(event.target.value as (typeof chartAspectOptions)[number]['id'])}>
                {chartAspectOptions.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </label>
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
          {chartKind === 'scatter' ? (
            <div className="choice-section">
              <h3>散布図の軸</h3>
              <label className="filter-field">
                <span>X軸</span>
                <select value={scatterX} onChange={(e) => setScatterX(e.target.value)}>
                  {fieldChoices.map((field) => <option key={field.id} value={field.id}>{field.label}</option>)}
                </select>
              </label>
              <label className="filter-field">
                <span>Y軸</span>
                <select value={scatterY} onChange={(e) => setScatterY(e.target.value)}>
                  {fieldChoices.map((field) => <option key={field.id} value={field.id}>{field.label}</option>)}
                </select>
              </label>
            </div>
          ) : (
            <ChoiceGroup
              title="項目"
              items={fieldChoices.map((field) => ({ id: field.id, label: field.label, name: field.name }))}
              selected={selectedFields}
              onToggle={(id) => setSelectedFields(toggleValue(selectedFields, id))}
              onAll={() => setSelectedFields(fieldChoices.slice(0, 8).map((item) => item.id))}
              onClear={() => setSelectedFields([])}
            />
          )}
          {chartKind !== 'scatter' && selectedFieldOptions.length > 1 && (
            <ChoiceGroup
              title="右軸"
              items={selectedFieldOptions.map((field) => ({ id: field.id, label: field.label, name: field.name }))}
              selected={selectedRightAxisFields}
              onToggle={(id) => setRightAxisFields(toggleValue(selectedRightAxisFields, id))}
              onAll={() => setRightAxisFields(selectedFields)}
              onClear={() => setRightAxisFields([])}
              compact
            />
          )}
        </div>
        <div className="panel chart-main-panel">
          <div className="panel-head">
            <div>
              <h2>{chartTitle(chartKind, mode)}</h2>
              <p className="muted">
                {data ? `${data.total}行を読み込み / ${queryFields.length}項目` : 'データを読み込み中です。'}
                {data?.omitted_rows ? ` / ${data.omitted_rows}行は上限超過で省略` : ''}
              </p>
            </div>
            {hasSeriesOverflow && <span className="badge running">系列を18件に制限</span>}
          </div>
          {chartKind === 'scatter' ? (
            <ScatterChartBlock
              rows={scatter}
              xLabel={fieldsById.get(scatterX)?.name || scatterX}
              yLabel={fieldsById.get(scatterY)?.name || scatterY}
              aspectRatio={chartAspectRatio}
              showCorrelation={showCorrelation}
            />
          ) : mode === 'company' ? (
            <BarOrLineChartBlock
              kind={chartKind}
              rows={companyBars.rows}
              xKey="label"
              series={series}
              rightAxisFields={selectedRightAxisFields}
              showValueLabels={showValueLabels}
              seriesStyles={seriesStyles}
              aspectRatio={chartAspectRatio}
            />
          ) : (
            <BarOrLineChartBlock
              kind={chartKind}
              rows={trend.rows}
              xKey="fiscal_year"
              series={series}
              rightAxisFields={selectedRightAxisFields}
              showValueLabels={showValueLabels}
              seriesStyles={seriesStyles}
              aspectRatio={chartAspectRatio}
            />
          )}
          <div className="chart-meta">
            <span>会社 {selectedCompanies.length || '全社'}</span>
            <span>年度 {selectedYears.length || '全年度'}</span>
            <span>項目 {chartKind === 'scatter' ? '2' : selectedFields.length}</span>
            {chartKind !== 'scatter' && <span>右軸 {selectedRightAxisFields.length || 'なし'}</span>}
          </div>
          {selectedFields.length > 1 && chartKind !== 'scatter' && (
            <p className="hint">単位が違う項目を同じ軸に載せると見え方が歪みます。比較しにくい場合は項目を絞ってください。</p>
          )}
        </div>
      </div>
    </section>
  );
}

function ChoiceGroup({
  title,
  items,
  selected,
  onToggle,
  onAll,
  onClear,
  compact = false
}: {
  title: string;
  items: Array<{ id: string; label: string; name?: string }>;
  selected: string[];
  onToggle: (id: string) => void;
  onAll: () => void;
  onClear: () => void;
  compact?: boolean;
}) {
  return (
    <div className="choice-section">
      <div className="choice-head">
        <h3>{title}</h3>
        <div>
          <button className="ghost" type="button" onClick={onAll}>選択</button>
          <button className="ghost" type="button" onClick={onClear}>解除</button>
        </div>
      </div>
      <div className={`choice-grid ${compact ? 'compact-choice' : ''}`}>
        {items.map((item) => {
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
  );
}

function BarOrLineChartBlock({ kind, rows, xKey, series, rightAxisFields, showValueLabels, seriesStyles, aspectRatio }: {
  kind: Exclude<ChartKind, 'scatter'>;
  rows: Row[];
  xKey: string;
  series: ChartSeries[];
  rightAxisFields: string[];
  showValueLabels: boolean;
  seriesStyles: Record<string, SeriesStyle>;
  aspectRatio: number;
}) {
  if (!rows.length || !series.length) {
    return <Empty message="グラフ化できる数値データがありません。" />;
  }
  const ChartComponent = kind === 'bar' ? BarChart : kind === 'combo' ? ComposedChart : LineChart;
  const rightAxisSet = new Set(rightAxisFields);
  const hasRightAxis = series.some((item) => rightAxisSet.has(item.fieldId));
  const seriesByKey = new Map(series.map((item) => [item.key, item]));
  return (
    <div className="chart-canvas">
      <ResponsiveContainer width="100%" aspect={aspectRatio}>
        <ChartComponent data={rows} margin={{ top: 18, right: hasRightAxis ? 38 : 18, bottom: 4, left: 6 }}>
          <CartesianGrid stroke="#edf2f7" vertical={false} />
          <XAxis
            dataKey={xKey}
            axisLine={{ stroke: '#cbd5df' }}
            tick={{ fill: '#5f6f7b', fontSize: 12 }}
            tickLine={false}
            tickMargin={10}
          />
          <YAxis
            yAxisId="left"
            axisLine={false}
            tick={{ fill: '#5f6f7b', fontSize: 12 }}
            tickFormatter={formatAxisTick}
            tickLine={false}
            width={58}
          />
          {hasRightAxis && (
            <YAxis
              yAxisId="right"
              orientation="right"
              axisLine={false}
              tick={{ fill: '#5f6f7b', fontSize: 12 }}
              tickFormatter={formatAxisTick}
              tickLine={false}
              width={58}
            />
          )}
          <Tooltip content={<ChartTooltip seriesByKey={seriesByKey} />} />
          <Legend
            iconSize={9}
            wrapperStyle={{ color: '#4c5f6b', fontSize: 12, paddingTop: 12 }}
            formatter={(value) => seriesByKey.get(String(value))?.label || String(value)}
          />
          {series.map((item, index) => {
            const style = seriesStyles[item.key] || {};
            const color = style.color || chartColors[index % chartColors.length];
            const renderKind = getSeriesRenderKind(kind, index, style);
            const strokeWidth = style.strokeWidth || 2.2;
            const strokeDasharray = style.strokeDasharray || undefined;
            return renderKind === 'bar' ? (
              <Bar
                key={item.key}
                dataKey={item.key}
                name={item.label}
                yAxisId={rightAxisSet.has(item.fieldId) ? 'right' : 'left'}
                fill={color}
                radius={[3, 3, 0, 0]}
                maxBarSize={28}
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
              />
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

function ScatterChartBlock({ rows, xLabel, yLabel, aspectRatio, showCorrelation }: {
  rows: Row[];
  xLabel: string;
  yLabel: string;
  aspectRatio: number;
  showCorrelation: boolean;
}) {
  if (!rows.length) {
    return <Empty message="散布図にできる数値ペアがありません。" />;
  }
  const correlation = pearsonCorrelation(rows);
  return (
    <div className="chart-canvas">
      {showCorrelation && correlation != null && (
        <div className="chart-stat">
          <span>相関係数</span>
          <strong>{correlation.toFixed(3)}</strong>
        </div>
      )}
      <ResponsiveContainer width="100%" aspect={aspectRatio}>
        <ScatterChart margin={{ top: 18, right: 18, bottom: 4, left: 6 }}>
          <CartesianGrid stroke="#edf2f7" vertical={false} />
          <XAxis
            type="number"
            dataKey="x"
            name={xLabel}
            axisLine={{ stroke: '#cbd5df' }}
            tick={{ fill: '#5f6f7b', fontSize: 12 }}
            tickFormatter={formatAxisTick}
            tickLine={false}
            tickMargin={10}
          />
          <YAxis
            type="number"
            dataKey="y"
            name={yLabel}
            axisLine={false}
            tick={{ fill: '#5f6f7b', fontSize: 12 }}
            tickFormatter={formatAxisTick}
            tickLine={false}
            width={58}
          />
          <Tooltip cursor={{ stroke: '#9aa8b5', strokeDasharray: '3 3' }} content={<ScatterTooltip xLabel={xLabel} yLabel={yLabel} />} />
          <Legend wrapperStyle={{ color: '#4c5f6b', fontSize: 12, paddingTop: 12 }} />
          <Scatter name={`${xLabel} x ${yLabel}`} data={rows} fill="#111827" />
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

function ReportPanel({ status, refreshToken }: { status: Status | null; refreshToken: number }) {
  const [report, setReport] = React.useState('');
  const [coverage, setCoverage] = React.useState('');
  const [learningImpact, setLearningImpact] = React.useState('');

  React.useEffect(() => {
    api<{ content: string }>('/api/markdown/run_report').then((data) => setReport(data.content));
    api<{ content: string }>('/api/markdown/field_coverage').then((data) => setCoverage(data.content));
    api<{ content: string }>('/api/markdown/review_learning_impact').then((data) => setLearningImpact(data.content));
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
      <MarkdownBlock title="Run Report" content={report} />
      <MarkdownBlock title="Review Learning Impact" content={learningImpact || 'まだレビュー学習後の再取得が実行されていません。'} />
      <MarkdownBlock title="Field Coverage" content={coverage} />
    </section>
  );
}

function reviewRowKey(row: Row): string {
  return `${String(row.company_year_id || '')}::${String(row.field_id || '')}`;
}

function DataTable({
  data,
  columns,
  columnLabels = {},
  onCellClick,
  onRowClick,
  selectedRowKey = '',
  getRowKey,
  rowActionLabel = '',
  compact = false,
  markEmptyCells = false
}: {
  data: Row[];
  columns: string[];
  columnLabels?: Record<string, string>;
  onCellClick?: (row: Row, column: string) => void;
  onRowClick?: (row: Row) => void;
  selectedRowKey?: string;
  getRowKey?: (row: Row) => string;
  rowActionLabel?: string;
  compact?: boolean;
  markEmptyCells?: boolean;
}) {
  const defs = React.useMemo<ColumnDef<Row>[]>(() => columns.map((column) => ({
    accessorKey: column,
    header: columnLabels[column] || column,
    cell: (info) => {
      const rawValue = info.getValue();
      const value = String(rawValue ?? '');
      if (markEmptyCells && !baseColumns.has(column) && value.trim() === '') {
        return <span className="empty-cell">空欄</span>;
      }
      return renderCellValue(column, rawValue);
    }
  })), [columns, columnLabels, markEmptyCells]);
  const table = useReactTable({ data, columns: defs, getCoreRowModel: getCoreRowModel() });
  if (!data.length) return <Empty message="該当する行がありません。" />;
  return (
    <div className={`table-wrap ${compact ? 'compact' : ''}`}>
      <table>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {rowActionLabel && <th className="row-action-head">操作</th>}
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
                selectedRowKey && getRowKey?.(row.original) === selectedRowKey ? 'row-selected' : ''
              ].filter(Boolean).join(' ')}
              onClick={() => onRowClick?.(row.original)}
            >
              {rowActionLabel && (
                <td className="row-action-cell">
                  <button
                    type="button"
                    className="ghost row-action-button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onRowClick?.(row.original);
                    }}
                  >
                    {rowActionLabel}
                  </button>
                </td>
              )}
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className={onCellClick ? 'cell-clickable' : ''} onClick={(event) => {
                  if (onCellClick) {
                    event.stopPropagation();
                    onCellClick(row.original, cell.column.id);
                  }
                }}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  applied_at: '反映日時'
};

function Pager({ page, totalPages, total, onPage }: { page: number; totalPages: number; total: number; onPage: (page: number) => void }) {
  return (
    <div className="pager">
      <button className="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>前へ</button>
      <span>{page} / {totalPages}（{total}件）</span>
      <button className="ghost" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>次へ</button>
    </div>
  );
}

function FilterBar({ children }: { children: React.ReactNode }) {
  return <div className="filter-bar">{children}</div>;
}

function Empty({ message }: { message: string }) {
  return <div className="empty">{message}</div>;
}

function InlineError({ message }: { message: string }) {
  return <div className="inline-error">{message}</div>;
}

function MarkdownBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="panel">
      <h2>{title}</h2>
      <pre className="markdown">{content || '読み込み中です。'}</pre>
    </div>
  );
}

function split(value: string) {
  return value.split(',').map((part) => part.trim()).filter(Boolean);
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

function toggleValue(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

function getSeriesRenderKind(kind: ChartKind, index: number, style: SeriesStyle = {}): SeriesRenderKind {
  if (kind === 'bar') return 'bar';
  if (kind === 'combo') return style.renderAs || (index === 0 ? 'bar' : 'line');
  return 'line';
}

function chartTitle(kind: ChartKind, mode: ChartMode): string {
  if (kind === 'scatter') return '散布図';
  if (mode === 'company') {
    if (kind === 'bar') return '会社比較 棒グラフ';
    if (kind === 'combo') return '会社比較 複合グラフ';
    return '会社比較 折れ線';
  }
  if (kind === 'bar') return '年度推移 棒グラフ';
  if (kind === 'combo') return '年度推移 複合グラフ';
  return '年度推移 折れ線';
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
    rows: Array.from(byYear.values()).sort((a, b) => Number(a.fiscal_year) - Number(b.fiscal_year)),
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
