# BuildBase A-G/H/I 差分監査 Round 4 2026-07-08

## Scoreboard

| 指標 | Round 3 | Round 4 |
| --- | ---: | ---: |
| final_master_long rows | 10,152 | 10,152 |
| final_master_wide rows | 205 | 205 |
| needs_reconciliation | 920 | 920 |
| zero-corroboration | 2,031 | 2,031 |
| conflicts | 1,226 | 1,226 |
| mismatch_count | 0 | 0 |
| active_review_items | 8,767 | 8,767 |
| factbook comparable_rows | 0 | 0 |
| 用語ブロッカー数（H由来） | 5 | 5 |
| 導線ブロッカー | 3 | 2 |

## Executive Summary

Round 4では、結果表セルが作業対象であることが視覚的に弱い問題を扱った。ユーザーは結果表を主導線として空欄や怪しい値を潰していく方針なので、セルがクリック可能であることはP1寄りのUX品質と判断した。

## A. 結果表・セル修正

現状:

- 結果表の数値/空欄セルをクリックするとセル作業が開く。
- ただし、見た目は通常の表セルに近く、クリック可能性が分かりにくかった。
- 基礎列までクリック対象classを持っていたため、操作対象の境界もやや曖昧だった。

Sprint 4実装:

- `DataTable` でクリック可能セルを `onCellClick && !baseColumns.has(column)` に限定した。
- クリック可能セルに `role=button` / `tabIndex=0` / Enter・Spaceキー操作を追加した。
- クリック可能セルの `title` に「セル作業を開く: 項目名（状態）」を入れた。
- hover/focus-visible時に枠線と背景を出す。
- ステータスチップにhover/focus時だけ矢印を出す。

## H. ペルソナUX

改善:

- 結果表上で「作業できるセル」が視覚的に分かりやすくなった。
- キーボードでもセル作業を開けるため、表操作のアクセシビリティが上がった。

残課題:

- 空欄セルを優先して潰すためのフィルタや並び替えはまだない。
- ホームから結果表へ「未処理セルだけを見る」導線はまだ弱い。

## I. コード健全性

代表リスク:

- 各パネルで個別にクリック導線を作ると、同じ見た目と操作性が散る。

今回の抑制:

- 共通 `DataTable` に寄せて、結果表以外でも同じ契約で使える形にした。

## P0/P1

P0:

- 新規なし。

P1:

1. ホーム品質サマリーから「根拠弱い」「未処理セル」へ直接移動する導線。
2. Factbook状態キーと「次に直す場所」導線。

## Sprint 4 検証

- `npm run build`: pass。
- ブラウザで結果表のクリック可能セルが `role=button` を持ち、hover/focus向けのクラスが付くことを確認する。
