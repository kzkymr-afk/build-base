# BuildBase A-G/H/I 差分監査 Round 3 2026-07-08

## Scoreboard

| 指標 | Round 2 | Round 3 |
| --- | ---: | ---: |
| final_master_long rows | 10,152 | 10,152 |
| final_master_wide rows | 205 | 205 |
| needs_reconciliation | 920 | 920 |
| zero-corroboration | 2,031 | 2,031 |
| conflicts | 1,226 | 1,226 |
| mismatch_count | 0 | 0 |
| active_review_items | 8,767 | 8,767 |
| factbook comparable_rows | 0 | 0 |
| 用語ブロッカー数（H由来） | 8 | 5 |

## Executive Summary

Round 2 Sprint 2で、セル作業の候補採用契約と代表的な内部語の露出は改善された。Round 3の差分監査では、次に大きいP1として「値はあるが独立照合0件のセルが結果表では信用済みに見える」問題を扱った。

実装は `data/reports/corroboration_cells.csv` の既存定義を使い、`review_required=false` かつ `corroboration_count=0` かつ `conflict_count=0` のセルを、結果表とセル作業で `根拠弱い` と表示する。conflict / reconciliation は既存の強い警告を優先し、上書きしない。

## A. 結果表・セル修正

現状:

- 結果表セル状態は `cell_resolutions` とレビュー状態を統合している。
- Round 2までは zero-corroboration がセル状態に出ていなかった。

見つかったバグ/違和感:

- P1: `auto_accepted_with_zero_corroboration=2,031` のセルが、表上では単なる `値あり` または `照合要確認` に見え、上司資料に使う前の注意が弱い。

Sprint 3実装:

- `datasets._read_zero_corroboration_cells` を追加。
- `datasets._apply_zero_corroboration_status` を追加。
- `value_present` / `value_present_no_audit` / `corroboration_needs_review` のうち zero-corroboration に該当するセルを `corroboration_weak` / `根拠弱い` に変更。
- セル詳細も同じ判定を通す。
- CSSに `status-corroboration_weak` を追加。

実データ確認:

- 全項目・全年度の結果表相当で `corroboration_weak=2,019` を確認。
- `corroboration_needs_reconciliation` と `corroboration_conflicted` は上書きしていない。

## D. データ品質・照合状態

現状:

- `corroboration_summary.json` は zero-corroboration を集計していた。
- ただし、ユーザーの主導線である結果表には出ていなかった。

データ経路上の危険箇所:

- `cell_resolutions.csv` と `corroboration_cells.csv` は目的が近いが、完全に同じ定義ではない。今回の判定は `corroboration_cells.csv` 側を直接読むことで、summaryの定義と揃えた。

## H. ペルソナUX

改善:

- 結果表で「値あり」に見えていた一部セルが `根拠弱い` と見えるようになり、非技術ユーザーでも「この値は使う前に確認が必要」と分かる。

残課題:

- `根拠弱い` の意味をホームの品質サマリーでも説明する必要がある。
- クリック可能セルの視覚的な示唆はまだ弱い。

## I. コード健全性

代表リスク:

- `read_cell_detail` と `read_wide` が別々にステータス合成しているため、状態追加時に二重実装になりやすい。

今回の抑制:

- 判定本体を `_apply_zero_corroboration_status` に寄せ、結果表とセル詳細の両方から呼ぶ形にした。

## P0/P1

P0:

- 新規なし。

P1:

1. `根拠弱い` のホーム品質サマリー説明。
2. クリック可能セルの視覚的な示唆。
3. Factbook状態キーと「次に直す場所」導線。

## Sprint 3 検証

- `./yuho test tests/test_web_services.py`: 426 tests OK。
- `npm run build`: pass。
- 実データで `corroboration_weak=2,019` を確認。
