# BuildBase ペルソナUX監査 Round 2 2026-07-08

## Persona

建設業界の営業企画／経営企画担当。ゼネコン各社の受注・売上・利益・技術者数を競合比較したい。Excelは使えるが、DB・SQL・XBRL・照合・概念マッピング・corroborationは知らない。

## Scoreboard

| 指標 | Round 2 |
| --- | ---: |
| 用語ブロッカー | 8 |
| 導線ブロッカー | 3 |
| デモ導線上の見た目P1 | 2 |

## シナリオ結果

### 1. 初回起動→この表は信用できるか

結果:

- ホームは充足マップがあり、全体像は分かる。
- `照合` は10回露出するが、非技術ユーザーが「信用済み」「要確認」「未検証」をどう判断すべきかはまだ弱い。
- サイドバーに `idle` が出る。

Finding:

- P1: `idle` は用語ブロッカー。`待機中` などに置換する。
- P2: ホームに「この表はどこまで信用できるか」の要約がほしい。

### 2. A社とB社の建築受注を5年比較

結果:

- グラフ画面は会社・年度・項目が揃っており、比較自体は可能。
- 項目候補が多く、`PPT`, `PNG`, `SVG`, `px`, `1x` などの出力語彙が多い。
- 欠損・対象外・単位混在への説明はまだ弱い。

Finding:

- P2: グラフ出力語彙はデモ導線で必要なものだけ目立たせる。
- P2: factbookや結果表から、設定済みグラフへ移動する導線が必要。

### 3. 結果表の空欄→なぜ空欄か／自分で直せるか

結果:

- 結果表には「数値セルや空欄セルをクリック」とヒントがある。
- ただし、どのセルが押せるかの視覚的な示唆は弱い。
- Cell Workbench内に `Cell Workbench`, `company_year_id`, `field_id`, `confidence`, `source_audit`, `buckets`, `sources`, `decided_at_utc` が出る。
- 候補採用時に、UIは候補採用に見えるが保存契約は値だけの `correct` になり得る。

Finding:

- P1: 用語ブロッカー。Workbench周辺の内部語を平易化する。
- P1: 候補採用契約。選んだ候補/出典が保存へ渡るようにする。
- P2: クリック可能セルを見た目で分かるようにする。

### 4. グラフ1枚を上司資料へ

結果:

- PNG/SVG保存、PPT書き出し向け設定はある。
- 軸・単位・凡例の調整は可能。
- 出典に戻る説明は十分ではない。

Finding:

- P2: グラフから出典・計算根拠に戻る導線。
- P2: デモ用には「資料に貼る」系の表現を優先し、技術的な出力語彙を下げる。

### 5. factbookタブの罠

結果:

- `未検証`, `照合不可`, `比較可能 0`, 注意文が出ており、presence≠trustworthy の表示は改善済み。
- 一方で `pending`, `no_mapping`, `missing_yuho_value`, `missing_yuho_row`, `business_scope`, `category_type=use` が出る。

Finding:

- P1: factbookの状態キーは用語ブロッカー。まず `pending`, `no_mapping`, `missing_yuho_value` を平易化する。
- P2: 「次にどこを直すか」への導線が必要。

## 未翻訳・要平易化リスト

優先度高:

- `Cell Workbench`
- `company_year_id`
- `field_id`
- `confidence`
- `buckets`
- `sources`
- `decided_at_utc`
- `pending`
- `no_mapping`
- `missing_yuho_value`
- `needs_reconciliation`
- `cell_resolutions`

優先度中:

- `idle`
- `running`
- `failed`
- `accept`
- `correct`
- `reject`
- `not_applicable`
- `concept_id`
- `mapping_id`
- `scope`
- `final`
- `XBRL_CSV`
- `LOCAL_TABLE`
- `PPT`
- `PNG`
- `SVG`

## Sprint 2候補

1. Cell Workbench周辺の用語ブロッカーを `terminology.ts` と列ラベルで解消する。
2. 候補採用時に選択候補を保存APIへ渡す。
3. Factbookの状態キーを日本語ラベル化する。

## Sprint 2 実装確認

解消済み:

- `Cell Workbench` は「セル作業」に置換した。
- `idle` は「待機中」に置換した。
- 結果表の `standalone,consolidated,segment` は「単独、連結、セグメント」に置換した。
- セル作業内の `source_audit`、`confidence`、`XBRL_CSV` は表示時に日本語化した。
- 候補採用は `accept` として保存され、選択した `candidate_id` がAPIへ渡る。

残課題:

- Factbookの状態キーは一部辞書登録済みだが、全タブの深い導線までは未確認。
- zero-corroborationの「根拠が弱い値」表示は次スプリントへ送る。
- クリック可能セルの視覚的な示唆強化は次スプリントへ送る。
