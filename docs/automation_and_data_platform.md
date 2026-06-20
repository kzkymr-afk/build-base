# 年次自動取得とデータ基盤方針

## 目的

このアプリは、レビューで抽出ルールを育てた後、毎年の有価証券報告書を自動で取り込み、既存の完成表を更新するローカルツールとして運用する。

ただし、財務データでは「値を埋めること」より「誤った値を混ぜないこと」を優先する。年次自動取得はレビュー完了判定を通過した場合だけ進み、未完了なら停止する。

## 2025年度の扱い

2026-06-20 時点では、3月決算会社の 2025年度有価証券報告書が出始める時期に入っている。設定上は `06-01` から `08-15` までを年次取得ウィンドウにしている。

対象年度は開示年の前年として扱うため、2026年6月から8月の取得対象は 2025年度になる。

## 実行ゲート

設定ファイルは `config/automation.yml`。

年次ジョブは以下を満たすまで EDINET 取得へ進まない。

- `review_queue.csv` の未対応レビューが 0 件
- 保存済みだが未反映のレビューが 0 件
- `final_master_wide.csv`、`source_audit.csv`、`field_coverage.csv` が存在する

現在の判定は次で確認する。

```bash
./yuho status
```

2025年度を明示して確認する場合:

```bash
./yuho cli automation-status --fiscal-year 2025
```

## 年次ジョブ

ドライラン:

```bash
./yuho annual --fiscal-year 2025 --dry-run
```

通常実行:

```bash
./yuho annual --fiscal-year 2025
```

レビュー完了前に収集だけ先行する場合:

```bash
./yuho annual --fiscal-year 2025 --force
```

`--force` は意図的な例外扱い。通常運用では使わない。

## ジョブの処理順

1. `company_year_master` に対象年度行がなければ、前年の会社年度行からロールフォワードする。
2. 対象年度の EDINET index を取得する。
3. 対象年度の有価証券報告書を解決する。
4. 新年度の `target_documents` を既存表へマージする。
5. 新年度の文書をダウンロードする。
6. 既存年度も含めてセクション抽出、再抽出、レビュー学習、保存レビュー反映、レポート生成を行う。

新年度だけで `target_documents` を上書きすると過年度値が落ちるため、年次ジョブでは必ずマージする。

## mac 自動実行

launchd テンプレートは `ops/launchd/com.kzky.yuho-auto-extract.annual.plist.template`。

まずはテンプレートのまま確認し、問題なければ `~/Library/LaunchAgents/` にコピーして `launchctl bootstrap` で登録する。自動登録はまだ行っていない。

実行日は 6月20日、7月1日、7月15日、8月1日の 7:30。レビューゲートが閉じていれば停止し、`data/automation/annual_refresh_last.json` とログに理由を残す。

## データ管理方針

今の `data/final/*.csv` は分析用の完成成果物としては妥当だが、今後ソースが増えると正本としては弱い。

今後は次の分離を基本にする。

- `data/raw`: EDINET zip、PDF、Excel、ダウンロード原本
- `data/staging`: ソース別に正規化した中間表
- `data/marts`: 分析で使う正規化済みの横断テーブル
- `data/final`: 画面表示・Excel出力・AI投入用に生成される成果物

巨大化への第一候補は DuckDB + Parquet。ローカル単一ユーザーのままなら Postgres より軽く、CSVより安全に増やせる。クラウド化や複数ユーザー化が必要になった時点で Postgres などを検討する。

## 将来ソース

ソース一覧は `config/source_registry.yml` に置いた。

- 有価証券報告書: active
- 四半期・半期等のEDINET開示: planned
- 国土交通省統計: planned
- ゼネコン各社ファクトブック: planned
- ユーザー提供の同業他社Excel: planned

各ソースは最初から完成表に混ぜず、raw/staging/marts を通して、単位・期間・会社キー・証跡を保持してから統合する。

