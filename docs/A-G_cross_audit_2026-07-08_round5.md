# BuildBase A-G/H/I 差分監査 Round 5 2026-07-08

## Scoreboard

| 指標 | Round 4 | Round 5 |
| --- | ---: | ---: |
| final_master_long rows | 10,152 | 10,152 |
| final_master_wide rows | 205 | 205 |
| needs_reconciliation | 920 | 920 |
| zero-corroboration | 2,031 | 2,031 |
| conflicts | 1,226 | 1,226 |
| mismatch_count | 0 | 0 |
| active_review_items | 8,767 | 8,767 |
| factbook comparable_rows | 0 | 0 |
| 用語ブロッカー数（H由来） | 5 | 3 |
| 導線ブロッカー | 2 | 2 |

## Executive Summary

Round 5では、Factbook画面の「データはあるが比較可能0件」の状態について、ユーザーが次に何を直せばよいか分かりにくい問題を扱った。ファクトブック解析本体には触れず、状態表示と次アクションの見える化だけに限定した。

## G. ファクトブック・拡張データ

現状:

- factbook rowsは存在するが、`comparable_rows=0`。
- `no_mapping`、`missing_yuho_value`、`missing_yuho_row` が主な停止理由。
- Round 4時点では、これらのキーが画面上に生で露出していた。

Sprint 5実装:

- Factbook照合ステータスに「次に直すこと」ブロックを追加した。
- `no_mapping` を「対応項目なし」、`missing_yuho_value` を「有報側の値が空欄」などへ表示変換した。
- 集計メトリクスの `pending` を「未確認行」に変更した。
- `category_type=use` / `business_scope` などの説明文を平易化した。
- `use`、`business_scope`、`building_orders_by_use` などFactbook系表示語を `terminology.ts` に追加した。

## H. ペルソナUX

改善:

- Factbookタブで「未検証」の理由が、内部キーではなく作業単位で見えるようになった。
- 「未対応付け上位」「有報値欠損上位」の意味が画面文脈とつながった。

残課題:

- Factbookから結果表の該当セルへ直接飛ぶ導線はまだない。
- Factbookからグラフへプリセット付きで移動する導線はまだない。

## I. コード健全性

代表リスク:

- 表示語彙が各パネルに散ると、同じ内部キーが場所によって別表現になる。

今回の抑制:

- 内部キーの表示変換は `formatTermText` / `terminology.ts` に寄せた。
- 次アクション生成は `factbookNextActions` に閉じ込めた。

## P0/P1

P0:

- 新規なし。

P1:

1. Factbookから結果表セルへの直接導線。
2. ホーム品質サマリーから「根拠弱い」「未処理セル」へ直接移動する導線。

## Sprint 5 検証

- `npm run build`: pass。
- 次アクション表示はブラウザで確認する。
