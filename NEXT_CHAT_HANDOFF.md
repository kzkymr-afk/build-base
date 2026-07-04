# 次チャット引き継ぎ書

作成日: 2026-06-22  
対象プロジェクト: `/Volumes/SSD_External/Business/Materials/2026-06_有報自動抽出/yuho_auto_extract`

## 最重要の前提

- 目的は「営業戦略分析用の会社×年度表を完成させること」。
- 値を推測して埋めない。根拠が弱い値は `review_required=true` または未突合として残す。
- 会社名ではなく `operating_company_id` / `company_year_id` を主キーにする。
- 連結、単独、セグメント、経審の許可業者ベースを混ぜない。
- OpenAI API / Batch API に依存しない方針。ローカル抽出、EDINET DB、手整理済み正本、CIIC等の低コスト取得を優先する。
- 作業ツリーには、この引き継ぎ作成前から多数の未コミット変更がある。無関係な変更を戻さないこと。

## 直近で完了したこと

Obsidianノート `Obsidian/Kzky-works/ゼネコン各社の技術者数.md` を、技術者数の手整理済み正本として取り込む処理を追加した。

追加したフィールド:

- `architecture_engineers_1st_class`
  - 日本語名: `建築一式_技術職員数_一級`
  - 単位: `人`
  - data_scope: `permit_entity`
  - preferred_method: `MANUAL_OBSIDIAN`
- `architecture_engineers_1st_class_training`
  - 日本語名: `建築一式_技術職員数_一級_講習受講`
  - 単位: `人`
  - data_scope: `permit_entity`
  - preferred_method: `MANUAL_OBSIDIAN`

実装ファイル:

- `src/yuho_auto_extract/services/manual_technicians.py`
- `tests/test_manual_technicians.py`
- `src/yuho_auto_extract/__main__.py`
- `src/yuho_auto_extract/services/pipeline.py`
- `config/field_definition.csv`
- `config/field_definition.xlsx`
- `config/source_registry.yml`
- `config/validation_rules.yml`

## 技術者数取り込みの仕様

- ObsidianノートのMarkdown表を読む。
- ノートの `年度` は「期末年」として扱う。
  - 例: 鹿島建設の `2025` は `fiscal_year_end=2025-03-31` に突合し、`KAJIMA_2024` に入る。
  - 例: 竹中工務店の `2024(12)` は `fiscal_year_end=2024-12-31` に突合する。
- 空欄は0扱いしない。値なしとしてスキップし、必要なら未突合/確認対象に残す。
- 会社マスターにない会社は無理に似た会社へ寄せない。
- `source_file` にObsidianノートのパス、`source_quote` に該当Markdown行を保存する。
- `extraction_method=MANUAL_OBSIDIAN`、`confidence=1.0`、`review_required=false` として取り込む。
- 技術者数2項目は前年比異常検知から外した。講習受講者数は制度・更新タイミングで大きく動くため、手整理済み正本を余計なレビューに落とさないため。

## 生成済みデータ

取り込みコマンド:

```bash
PYTHONPATH=src python3 -m yuho_auto_extract import-manual-technicians
```

生成物:

- `data/intermediate/manual_technician_extracted_long.csv`
- `data/marts/manual_technicians/architecture_engineers_long.csv`
- `data/marts/manual_technicians/architecture_engineers_wide.csv`
- `data/marts/manual_technicians/import_summary.json`
- `data/marts/manual_technicians/unmatched_rows.csv`
- `data/final/final_master_wide.xlsx`
- `data/final/final_master_wide.csv`
- `data/final/source_audit.csv`
- `data/final/field_coverage.md`
- `data/ai_bundle/field_coverage.md`

取り込み結果:

- ノート解析行数: 252
- 取り込み会社年: 200
- 取り込みロング行: 400
- 未突合行: 52
- `final_master_wide` 上のカバー率: 200 / 204 company_years = 98.0%

確認済み例:

- `KAJIMA_2024`
  - `architecture_engineers_1st_class = 2769`
  - `architecture_engineers_1st_class_training = 1465`
  - source_quote: `| 2025     | 鹿島建設   | 2,769      | 1,465    |`

## 未突合の扱い

未突合CSV:

```text
data/marts/manual_technicians/unmatched_rows.csv
```

内訳:

- `company_year_not_in_master`: 20行
  - 主にノートの2015期末行。現行 `company_year_master` は多くの会社が `fiscal_year=2015 / fiscal_year_end=2016-03-31` から始まるため、`2015-03-31` が存在しない。
  - 竹中工務店の `2025(12)` も現行マスター外。
- `company_not_in_master`: 32行
  - `フジタ`: 11行
  - `ナカノフドー`: 11行
  - `新日本建設`: 10行

次にこれらを使うなら、まず `company_master.csv` と `company_year_master.csv` に会社・年度を正式追加すること。会社名だけで既存会社に寄せない。

## 再生成コマンド

技術者数ノートだけ再取り込み:

```bash
PYTHONPATH=src python3 -m yuho_auto_extract import-manual-technicians
```

技術者数込みで中間表から最終表まで再生成:

```bash
PYTHONPATH=src python3 -m yuho_auto_extract normalize
PYTHONPATH=src python3 -m yuho_auto_extract validate
PYTHONPATH=src python3 -m yuho_auto_extract export-final --reviewed data/review/review_resolved_local_pass.xlsx
PYTHONPATH=src python3 -m yuho_auto_extract build-analysis
PYTHONPATH=src python3 -m yuho_auto_extract report
```

ローカル抽出全体を回す場合:

```bash
PYTHONPATH=src python3 -m yuho_auto_extract run-local
```

`run-local` の中で `import-manual-technicians` も呼ぶようにした。

## テスト

`.venv` では通過済み:

```bash
.venv/bin/python -m pytest tests/test_manual_technicians.py tests/test_validator.py tests/test_web_services.py -q
```

結果:

```text
50 passed
```

注意:

- システムPythonで同じ広めのテストを回すと、`pyarrow` 不在で既存Parquet関連テストが落ちる場合がある。
- プロジェクトの `.venv/bin/python` を使うこと。

## CIIC確認メモ

CIICサイト `http://www7.ciic.or.jp/` はChrome操作で商号検索から詳細PDFまで到達確認済み。

鹿島建設の確認結果:

- 許可番号: `00-002100`
- 審査基準日: `2025-03-31`
- PDF上の建築一式行:
  - 一級: `2,769`
  - 講習受講: `1,465`

重要:

- CIICの詳細通知書はHTML表ではなく、1ページ画像PDFとして返る。
- そのためHTMLパースだけでは技術者数を取れない。
- Chrome操作は確認・デバッグ用。本処理はフォームPOSTでPDF保存し、画像PDFから固定レイアウト抽出/OCRを行う方がよい。
- CIIC公表サイトは有効期間内の最新結果が対象。過去10年時系列はこのサイト単独では取れない。
- 今回は過去分をObsidianノートから取り込む方針で実装済み。

## 既存作業ツリーの注意

`git status --short` では多数の変更がある。今回の技術者数取り込み以外にも、既に以下のような未コミット変更/追加が存在する。

- ローカルWebアプリ関連
- 自動更新関連
- company factbook関連
- market/stock関連
- rule candidate / review関連
- `web/` 配下の大きな変更

次チャットで作業する場合:

- まず `git status --short` を見る。
- 今回触った技術者数関連だけを確認したい場合は、下記を中心に見る。
  - `src/yuho_auto_extract/services/manual_technicians.py`
  - `tests/test_manual_technicians.py`
  - `src/yuho_auto_extract/__main__.py`
  - `src/yuho_auto_extract/services/pipeline.py`
  - `config/field_definition.csv`
  - `config/source_registry.yml`
  - `config/validation_rules.yml`
  - `data/marts/manual_technicians/`
  - `data/intermediate/manual_technician_extracted_long.csv`
  - `data/final/final_master_wide.xlsx`
  - `data/final/source_audit.csv`

## 次にやるとよいこと

1. `unmatched_rows.csv` を見て、`フジタ`、`ナカノフドー`、`新日本建設` を分析対象に加えるか決める。
2. 加える場合は、`company_master.csv` と `company_year_master.csv` に正式追加してから `import-manual-technicians` を再実行する。
3. 2015期末行を使うか決める。使うなら `company_year_master` に `fiscal_year=2014 / fiscal_year_end=2015-03-31` 相当を追加するか、分析対象外として明示する。
4. CIICの年次更新は、年1回で十分。Obsidianノート更新後に `import-manual-technicians` を回す。
5. 最終表の空欄については `data/final/field_coverage.md` と `data/final/source_audit.csv` を見て、未対応項目を順番に潰す。

## 次チャットへの依頼文テンプレ

```text
/Volumes/SSD_External/Business/Materials/2026-06_有報自動抽出/yuho_auto_extract の NEXT_CHAT_HANDOFF.md を読んで続きから進めて。
まず git status を確認し、未突合の技術者数データと field_coverage を見て、次に埋めるべき項目を提案して。
OpenAI Batch/APIには依存しない方針で、ローカル抽出・手整理済み正本・CIIC/EDINET DB優先で進めて。
```
