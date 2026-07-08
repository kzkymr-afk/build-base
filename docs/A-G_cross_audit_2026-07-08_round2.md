# BuildBase A-G/I 差分監査 Round 2 2026-07-08

## Scoreboard

| 指標 | Round 1 | Round 2 |
| --- | ---: | ---: |
| final_master_long rows | 10,152 | 10,152 |
| final_master_wide rows | 205 | 205 |
| needs_reconciliation | 920 | 920 |
| zero-corroboration | 2,031 | 2,031 |
| conflicts | 1,226 | 1,226 |
| mismatch_count | 0 | 0 |
| active_review_items | 8,767 | 8,767 |
| factbook comparable_rows | 0 | 0 |
| 用語ブロッカー数（H由来） | - | 8 |

## Executive Summary

Round 1 Sprint 1で直した「結果表とCell Workbenchの状態不一致」は維持されている。代表セルでは、結果表相当の照合状態がCell Workbenchにも出ている。

新規P0はなし。Round 2の最重要P1は、Cell Workbenchで「候補から採用」したときに、保存APIへ候補IDや出典IDが渡らず、値だけの `correct` として保存され得る点。これはUI上の候補採用と `review_resolved.csv` 上の判断意味がズレるため、A/C/D/I横断の正確性リスク。

H監査では、`needs_reconciliation`、`cell_resolutions`、`pending`、`scope`、`concept_id`、`final` などの生用語がまだ露出している。Sprint 2は、候補採用契約の修正と、Cell Workbench周辺の用語/導線改善を同一コード経路で束ねるのがよい。

## A. 結果表・セル修正

現状:

- `ANDO_HAZAMA_2024 / roe`: `corroboration_needs_review`, label `照合要確認`, resolution `needs_review`。
- `ANDO_HAZAMA_2024 / building_orders_total`: `corroboration_needs_reconciliation`, label `照合要確認`, applied。
- `HASEKO_2024 / building_orders_total`: `blank_with_review_candidate`, next_action はCell Workbench導線へ更新済み。
- `ANDO_HAZAMA_2015 / nc_2d3b60309c0b380a`: `調査研究費_連結`, value `1909.0`, applied。ただし照合状態は `needs_review`。

見つかったバグ/違和感:

- P1: 「候補から採用」ボタンは、選んだ候補ID/出典IDを保存APIへ渡していない。UIでは候補採用に見えるが、保存上は `correct` になり、どの候補を採ったかが残らない。
- P1: Cell Workbench内に `Cell Workbench`, `company_year_id`, `field_id`, `confidence`, `buckets`, `sources`, `decided_at_utc` などが露出する。
- P2: 結果表からCell Workbenchへ進む入口がセルクリックのみで、非技術ユーザーにはどこを押せばよいか弱い。

## B. レビュー・マッピング裏画面

現状:

- mapping conflict summaryは `single_source 5,309`, `needs_review 3,761`, `auto_confirmed 1,501`, `needs_reconciliation 920`, `no_value 251`, `conflicted 55`。
- reconciliation groupsはSprint 1後に `total=13` で表示される。先頭は `needs_reconciliation:building_orders_total`、sourceは `cell_resolutions`、一括applyは無効。

見つかったバグ/違和感:

- P2: 照合グループ画面は `cell_resolutions`, `needs_reconciliation`, `identity_group_mismatch` などの生用語が多く、裏画面としても読み解きにくい。
- P2: 表示専用グループから対象セルへ戻る導線がない。

バグ/仕様判定:

- reconciliation groupsが0件だったRound 1時点の問題は、Sprint 1で「見える化不足」として修正済み。
- 現在の `apply_supported=false` は仕様。誤った一括acceptを避けるため、Sprint 2では一括反映を広げない。

## C. データ更新・ジョブ実行

現状:

- `review_resolved.csv`: 769行。
- `saved_unapplied_reviews=0`。
- automation gateは `active_review_items=8,767` で停止中。
- サーバは `http://127.0.0.1:8765/` で稼働。

見つかったバグ/違和感:

- P1: 候補採用が `correct` として保存されると、後続の反映・golden・監査で「人が手入力した値」と「候補を採用した値」の意味が混ざる。
- P2: `idle/running/failed` などジョブ状態の生値がUIに残る。

## D. データ品質・照合状態

現状:

- `cell_resolutions.csv`: `auto_confirmed 1,501`, `needs_review 3,761`, `needs_reconciliation 920`, `single_source 5,309`, `no_value 251`, `conflicted 55`。
- `/api/corroboration/summary`: `auto_accepted_with_zero_corroboration=2,031`, `conflicts=1,226`。
- `regression-check light`: pass=True, mismatch_count=0。

見つかったバグ/違和感:

- P1: zero-corroborationの2,031件は、まだ結果表のセル状態に直接は反映されていない。値があるだけで信用済みに見えるリスクが残る。
- P1: zero-corroborationの定義は `corroboration_report` 側の `review_required=false`, `corroboration_count=0`, `conflict_count=0`。`cell_resolutions.csv` の単純条件とは一致しないため、実装時は同じ定義を使う必要がある。

## E. 項目管理・概念管理

現状:

- `field_definition.csv`: 92項目。
- `canonical_concepts.csv`: 92概念。
- `nc_*`: 27件。final値あり4件、未実値化23件。
- `target_unit` 空欄8件、`data_scope_required` 空欄4件。

見つかったバグ/違和感:

- P1: `concept_id`, `final`, `scope` が表の項目管理で露出し、非技術ユーザーには意味が取りにくい。
- P2: 未実値化表示は改善済みだが、「次にどう実値化するか」はまだ分からない。

## F. グラフ・分析表示

現状:

- グラフ画面は縦軸自動範囲と手動調整が入っている。
- H監査ではPPT/PNG/SVG/px/1xなど出力語彙が多い。

見つかったバグ/違和感:

- P2: factbookからグラフへの導線はテキスト案内のみで、直接プリセットして移動できない。
- P2: 派生値の出典・計算根拠へ戻る導線はまだ弱い。

## G. ファクトブック・半期・拡張データ

現状:

- factbook order rows 183。
- source documents 1,265、unsupported documents 1,254。
- validationは `incomplete`, pending 178, comparable rows 0。
- pending内訳は `no_mapping 114`, `missing_yuho_value 52`, `missing_yuho_row 12`, `forecast_not_checked 5`。

見つかったバグ/違和感:

- P1: 未検証表示は強くなったが、`pending`, `no_mapping`, `missing_yuho_value`, `business_scope` などの生キーが露出している。
- P2: factbookから「どの表を直せば比較可能になるか」への導線がまだない。

## I. コード健全性・回帰リスク

代表リスク:

- Cell Workbenchの候補表示と保存APIの間に、選択候補を安定して渡す契約がない。

根拠:

- `datasets._cell_candidates` は `review:{index}` / `audit:{index}` を表示用に作るが、保存APIに渡されない。
- `CellDetailPanel.saveReview` は `review_decision`, `corrected_value`, `reviewer_note`, `reviewer` だけを送る。
- 候補クリックは `setDecision('correct')` して値を保存するため、候補採用ではなく手修正扱いになる。
- queueにないセルでは `_synthetic_review_row` が最初の `source_audit` 行を拾うため、複数候補がある場合にUIで選んだ候補と保存出典がズレ得る。

まだ触らない:

- `review_resolved.csv` の列定義変更。
- semantics/reconciliation全体の再設計。
- `main.tsx` の全面分割。

## P0/P1

P0は新規なし。

P1:

1. 候補採用時に候補ID/出典IDが保存契約へ乗らない。
2. Cell Workbench周辺に用語ブロッカーが多い。
3. zero-corroboration 2,031件が結果表で十分に見えない。
4. `needs_reconciliation` 表示専用グループから対象セルへ戻る導線がない。

## P2/P3

- P2: factbook → chart の直接導線。
- P2: job statusの日本語化。
- P2: 項目管理の `concept_id` / `scope` / `final` 表示整理。
- P2: `web/src/main.tsx` 分割。
- P3: デモ導線外の細かな余白・密度・文言調整。

## Sprint 2 切り出し

Sprint 2: **Cell Workbench 候補採用契約と用語ブロッカー解消**

目的:

- ユーザーが候補を選んだとき、どの候補・どの出典を採用したかが保存経路に残るようにする。
- 同じ変更範囲で、Cell Workbench周辺の主要な生用語を日本語化する。

含める:

- `candidate_id` / `candidate_source` をセルレビュー保存APIへ渡せるようにする。
- review queue候補は `accept` として保存する。
- source audit候補は選んだaudit行を根拠に synthetic resolved row を作る。
- 手入力修正は従来どおり `correct`。
- `Cell Workbench`、`company_year_id`、`field_id`、`confidence`、`buckets`、`sources`、`decided_at_utc` などを結果表/Workbench周辺で平易化する。

含めない:

- zero-corroborationの結果表強調。
- factbook → chart の直通導線。
- main.tsx全面分割。

## Sprint 2 実装結果

実装済み:

- セルレビュー保存APIに `candidate_id` を追加した。
- `review:{index}` 候補を選んだ場合は、該当review queue行を `accept` として保存する。
- `audit:{index}` 候補を選んだ場合は、該当source audit行を根拠に保存する。
- 選択候補は `reviewer_note` に `selected_candidate=...` として残す。
- Cell Workbench表記を「セル作業」へ変更し、結果表/セル作業/項目管理周辺の代表的な生用語を日本語化した。
- 結果表の対象範囲値（例: `standalone,consolidated,segment`）を表示時に「単独、連結、セグメント」へ変換する。

検証:

- `npm run build`: pass。
- `./yuho test`: 424 tests OK。
- `./yuho cli regression-check --mode light`: pass=True, mismatch_count=0。
- ブラウザで結果表と `ANDO_HAZAMA_2015 / nc_2d3b60309c0b380a` のセル作業を確認し、`Cell Workbench`、`source_audit`、`confidence` の露出が消えていることを確認した。
