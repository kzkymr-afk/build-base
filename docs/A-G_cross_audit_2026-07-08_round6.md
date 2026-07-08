# BuildBase A-G/H/I 差分監査 Round 6 2026-07-08

## Scoreboard

| 指標 | Round 5 | Round 6 |
| --- | ---: | ---: |
| final_master_long rows | 10,152 | 10,152 |
| final_master_wide rows | 205 | 205 |
| needs_reconciliation | 920 | 920 |
| zero-corroboration | 2,031 | 2,031 |
| conflicts | 1,226 | 1,226 |
| mismatch_count | 0 | 0 |
| active_review_items | 8,767 | 8,767 |
| factbook comparable_rows | 0 | 0 |
| 用語ブロッカー数（H由来） | 3 | 3 |
| 導線ブロッカー | 2 | 1 |

## Executive Summary

Round 6では、結果表で見えるようになった「根拠弱い値」が、ホームの入口では見えない問題を扱った。データ経路や判定ロジックは増やさず、既存の照合サマリーをホーム品質カードへ露出させるだけに限定した。

## A. 結果表・セル修正

現状:

- 結果表セルには `corroboration_weak` の状態バッジが出る。
- ただし、ユーザーはホームから最初に品質を判断するため、結果表まで行かないと弱い根拠の量が分からない。

Sprint 6実装:

- ホームの品質カードに「根拠弱い値」を追加した。
- 値は既存 `/api/corroboration/summary` の `auto_accepted_with_zero_corroboration` を使用する。
- 新しい永続化・再計算・判定ロジックは追加していない。

## H. ペルソナUX

改善:

- 初回起動時に「自動確定済みだが照合0件」の危険量が見える。
- `自動確定だが照合0件` として、専門用語を避けた短い説明を添えた。

残課題:

- ホームカードから該当セル一覧へ直接絞り込む導線はまだない。
- 結果表側のフィルタは会社・年度中心で、セル状態フィルタをまだ持っていない。

## I. コード健全性

代表リスク:

- ホームカードが4列固定で、カード追加時に狭い画面で詰まりやすい。

今回の抑制:

- `.dashboard-grid` を `auto-fit` に変更し、カード数が増えても自然に折り返すようにした。

## P0/P1

P0:

- 新規なし。

P1:

1. 結果表にセル状態フィルタを追加し、根拠弱い値だけを絞り込めるようにする。
2. ホームカードからそのフィルタ済み結果表へ直接移動する。

## Sprint 6 検証

- `npm run build`: 実施する。
- `./yuho test`: 実施する。
- `./yuho cli regression-check --mode light`: 実施する。
- ブラウザでホームに「根拠弱い値 2031」が表示されることを確認する。
