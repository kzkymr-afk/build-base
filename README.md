# BuildBase

ゼネコン・建設業界向けに、各種データを収集・整理し、レビューとグラフ化まで行うローカル分析ワークベンチです。

内部パッケージ名は `yuho_auto_extract`、CLIは `./yuho` のまま使います。

この版は生成AI APIを使いません。EDINETから取得したXBRL/CSV/HTMLをローカルSQLite DBに入れ、XBRLタグ抽出とローカル表パーサで、出典付きロング表・レビューキュー・完成ワイド表・分析用データセットを作ります。

## 使い方

macOS:

```bash
cd /Volumes/SSD_External/Business/Materials/2026-06_有報自動抽出/yuho_auto_extract
./setup_mac.sh
./yuho
```

Mac配布版の詳しい手順は `MAC_README.md` を見てください。

Windows PowerShell:

```powershell
.\setup_windows.ps1
.\yuho.ps1
```

Windows コマンドプロンプト:

```cmd
yuho.cmd
```

上のコマンドだけで、rawデータがなければEDINET取得から、rawデータがあればローカル再抽出から実行し、以下を更新します。

- `data/intermediate/edinet.db`
- `data/intermediate/db_extracted_long.csv`
- `data/review/review_queue.xlsx`
- `data/review/review_resolved_local_pass.xlsx`
- `data/review/review_queue_local_needs_manual.xlsx`
- `data/final/final_master_long.csv`
- `data/final/final_master_wide.xlsx`
- `data/final/final_master_wide.csv`
- `data/final/analysis_dataset.xlsx`
- `data/final/analysis_dataset.csv`
- `data/final/run_report.md`
- `data/ai_bundle/`

## APIキー

必要なのはEDINET APIキーだけです。

`.env`:

```bash
EDINET_API_KEY=ここにEDINETのAPIキー
YUHO_DATA_DIR=./data
YUHO_LOG_LEVEL=INFO
```

既に文書ダウンロード済みの場合、完成表更新だけならEDINET APIキーは使いません。新しく文書一覧取得やダウンロードをする時に使います。

## 10年分を最初から集める場合

```bash
python -m yuho_auto_extract index-annual --fiscal-years 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024
python -m yuho_auto_extract resolve --fiscal-years 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024
python -m yuho_auto_extract download --target-documents data/intermediate/target_documents.parquet
python -m yuho_auto_extract locate-sections
./yuho
```

普段は上記を打つ必要はありません。ダウンロード済みデータがある状態では `./yuho` で完成表まで進みます。

## レビュー

ローカル抽出で検算に通った候補は `data/review/review_resolved_local_pass.xlsx` に分け、完成表へ反映します。

検算未通過・曖昧・欠損など人間確認が必要な候補は、次を見ます。

```text
data/review/review_queue_local_needs_manual.xlsx
```

人間が修正したレビュー済みファイルを反映する場合:

```bash
python -m yuho_auto_extract export-final --reviewed data/review/review_resolved.xlsx
python -m yuho_auto_extract build-analysis
python -m yuho_auto_extract report
```

## 補助コマンド

```bash
./yuho run          # 完成表まで一括実行
./yuho menu         # 補助メニュー
./yuho test         # テスト実行
./yuho init         # 設定ExcelをCSVから再生成
./yuho report       # 実行レポート再生成
./yuho ai           # AI投入用フォルダ生成
./yuho audit        # アルゴリズム監査パック生成
./yuho web          # ローカルWebアプリ起動
./yuho cli build-ai-bundle  # AI投入用フォルダ生成
./yuho cli build-algorithm-audit  # アルゴリズム監査パック生成
./yuho cli --help   # 詳細CLIヘルプ
```

## ローカルWebアプリ

FastAPI + React のローカルWeb UIを同梱しています。APIやデータは手元のファイルだけを使い、外部AI APIやクラウド送信は行いません。

バックエンド起動:

```bash
./yuho web
```

ブラウザで開くURL:

```text
http://127.0.0.1:8765
```

開発時にReactを別起動する場合:

```bash
cd web
npm install
npm run dev
```

主な機能:

- 抽出パイプライン、レポート再生成、レビュー反映の実行
- `final_master_wide.csv` の閲覧
- `source_audit.csv` による根拠確認
- `review_queue.csv` の確認と `data/review/review_resolved.csv` へのレビュー保存
- レビューの `reviewer_note` から `data/review/rule_candidates.csv` への抽出ルール候補生成
- 選択した抽出ルール候補を `field_definition.csv` / `field_definition.xlsx` / `extraction_sections.yml` へ反映
- レビュー由来ルールでの再取得と保存済みレビュー値の最終反映を一つのジョブで実行
- AI分析用Markdownプロンプト生成
- 抽出アルゴリズム監査用の `data/algorithm_audit/` パック生成

レビュー保存は `company_year_id + field_id` をキーに `review_resolved.csv` へupsertし、一時ファイルからatomic renameします。`review_queue.csv` や `review_resolved_local_pass.csv` はWebアプリから直接上書きしません。

一度保存したレビューを修正する場合は、レビュー画面の絞り込みで `保存済みのみ` を選び、対象行をクリックします。保存済みの判定・修正値・メモが編集欄へ読み込まれるので、内容を直して `上書き保存` を押します。

保存済みレビューを取り消す場合は、保存済み行を選んで `レビュー削除` を押します。削除されるのは `review_resolved.csv` の保存済み判断だけで、元の `review_queue.csv` の候補行は残ります。

抽出ルール候補は、レビューのメモに書いた `LOC`, `TABLE`, `LABEL`, `XBRL_TAG`, `RULE_HINT` を `field_id` ごとに集計します。候補生成は設定ファイルを直接変更しません。レビュー画面で候補行を選び、`選択候補を設定へ反映` を押すと、既存値を消さずにXBRLタグ候補・セクションキーワード・行ラベル候補を追記します。反映前には対象設定ファイルのバックアップを作ります。

Webの `保存値を最終結果に反映` は `review_resolved.csv` の保存済み値を最終CSVへ入れ直す処理で、再取得は行いません。反映後、レビュー行には `applied_status`, `applied_value`, `applied_at` を記録します。レビューは監査ログとして残し、再出力・再取得・修正の根拠にも使うため、自動削除しません。レビューのメモを抽出アルゴリズムへ反映して他年度・他社を取り直す場合は、`レビューから候補作成` → `選択候補を設定へ反映` → `レビュー学習後に再取得` の順で実行します。`レビュー学習後に再取得` は候補ブロックの再探索から実行するため、追加した `review_*` セクションが全社・全年度の再抽出対象になります。

レビュー由来ルールが増えた場合は、定期的に `アルゴリズム監査パック生成` または `./yuho cli build-algorithm-audit` を実行します。生成先は `data/algorithm_audit/` です。`ALGORITHM_AUDIT_PROMPT.md` と同梱ファイルをAIに渡し、`review_*` セクションの肥大化、証跡1件の全社共通化、XBRL候補や同義語の広がりすぎ、低カバレッジ項目を監査します。AIの提案は自動反映せず、人間が妥当性を確認してから実装します。

## 実装前提

- EDINET API仕様書 Version 2 を前提にしています。
- 有価証券報告書は主に `ordinanceCode=010`, `formCode=030000`、訂正有報は `030001` を対象にします。
- 対象21社、標準対象年度は2015-2024年度です。
- 前田建設のHD化後年度は、開示主体であるインフロニアHDの有報から該当セクションを抽出します。
- 用途別受注高、特命受注高、設計施工受注高、リニューアル受注高は、有報だけでは金額を安定して特定できないため今回対象外です。特命/競争の建築受注比率は対象に残します。
- 値が取れない・単位不明・スコープ不明・検算失敗の場合はレビュー対象にします。
- 会社名だけでは結合せず、`operating_company_id` と `company_year_id` を主キーにします。

参考:

- EDINET API関連資料: https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html

## テスト

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
