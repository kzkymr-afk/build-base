# Mac Quick Start

## 1. ZIPを展開

`yuho_auto_extract_mac.zip` を任意の場所に展開します。例:

```bash
cd ~/Documents/yuho_auto_extract
```

## 2. 初回セットアップ

```bash
chmod +x setup_mac.sh yuho yuho.command
./setup_mac.sh
```

この処理で以下を行います。

- `.venv` 作成
- Python依存関係インストール
- `.env` 作成
- 設定Excel作成
- テスト実行

## 3. APIキー

必要なのはEDINET APIキーだけです。

既に有報をダウンロード済みで、完成表だけ更新する場合はAPIキーを使いません。
新しくEDINETから文書一覧取得・ダウンロードをする場合だけ `.env` に入れます。

```text
EDINET_API_KEY=...
YUHO_DATA_DIR=./data
YUHO_LOG_LEVEL=INFO
```

## 4. 実行

ターミナル:

```bash
./yuho
```

Finderから実行したい場合:

```text
yuho.command
```

これだけで、rawデータがなければEDINET取得から、rawデータがあればローカル再抽出から実行します。番号入力は不要です。

## 5. 出力先

```text
data/final/final_master_wide.xlsx
data/final/final_master_wide.csv
data/final/analysis_dataset.xlsx
data/final/analysis_dataset.csv
data/final/final_master_long.csv
data/final/run_report.md
data/review/review_queue.xlsx
data/review/review_queue_local_needs_manual.xlsx
data/ai_bundle/
```

## 6. 補助メニュー

通常は不要です。必要な時だけ開きます。

```bash
./yuho menu
```

## 7. ローカルWebアプリ

ブラウザで結果確認、根拠確認、レビュー保存、AI分析用プロンプト生成をしたい場合:

```bash
./yuho web
```

起動後、次を開きます。

```text
http://127.0.0.1:8765
```

Webアプリはローカルファイルだけを読み書きします。AI APIやクラウド送信は行いません。
