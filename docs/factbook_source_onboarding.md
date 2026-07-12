# Factbook 会社追加・再取得手順

## 既に登録済みの会社を取得する

画面では「データ > ファクトブック」で会社を選び、「<会社名>を取得」を押す。取得後は自動的に有報照合まで実行される。

CLIで安藤・間だけを取得する場合:

```bash
./yuho factbook-refresh --company ANDO_HAZAMA --force
./yuho factbook-validate
./yuho factbook-coverage
```

取得元を厳密に限定する場合:

```bash
./yuho factbook-refresh --source ando_hazama_factbook_documents --force
```

`--fiscal-year 2024` を追加すると解析年度を限定できる。AIによる表の意味判断が必要な文書だけ `--use-ai --ai-tier bulk` を追加する。AI結果は自動採用されず、数値照合とセルレビューを通る。

注意: 現行の `--dry-run` も最終実行状態JSONを更新する。文書・最終表・レビュー資産は変更しないが、完全な読み取り専用ではない。

## 新しい会社を登録する

1. `config/company_factbook_sources.yml` の `company_document_sources` に公式Factbook一覧ページを追加する。
2. `company_id` は `config/company_master.csv` と一致させる。
3. `link_text_includes` と `href_includes` でFactbookだけに絞る。決算説明資料や有報PDFを混ぜない。
4. `scope` と `business_scope` を明記する。用途別受注なら通常 `scope: standalone`、`business_scope: building_orders`。
5. 会社限定で取得し、文書状態と抽出結果を確認する。

設定例:

```yaml
- id: example_factbook_documents
  company_id: EXAMPLE
  company_name: 例示建設
  source_page_url: https://example.co.jp/ir/factbook/
  source_dataset_id: example_factbook
  source_doc_type: factbook
  parse_documents: true
  scope: standalone
  business_scope: building_orders
  follow_links: false
  link_text_includes:
    - FACT BOOK
    - ファクトブック
  href_includes:
    - factbook
```

## 確認する出力

- `data/marts/company_factbooks/source_documents.csv`: URL、年度、取得・解析状態
- `data/marts/company_factbooks/building_orders_by_category.csv`: 機械抽出した正規行
- `data/reports/company_factbook_yuho_validation.csv`: 結果表との照合・空欄候補
- `data/reports/company_factbook_pending_rows.csv`: 自動採用しない要確認行
- `data/reports/company_factbook_target_coverage.csv`: 会社・年度別の取得状況

状態の意味:

- `pass`: 結果表の値との数値照合を通過。検証済みFactbookデータとして利用可能。
- `factbook_only`: 有報に同じ粒度がない正規データ。用途別受注などはこの状態が正常。
- `missing_yuho_value`: 結果表の空欄に採用できるFactbook候補。結果表のセル作業から承認する。
- `outside_result_period`: 現在の結果表の対象年度外。取得失敗ではない。
- `no_mapping`: 設定不足。項目の意味を確認して対応付けを追加する。
- `mismatch`: 結果表の値と不一致。自動採用しない。

汎用PDF/Excelパーサで値が出ない場合は、原本の表構造を確認して会社別パーサを追加する。取得対象を広げる前に1年度で検証し、単位・連結/単独・実績/予想・年度を必ず確認する。
