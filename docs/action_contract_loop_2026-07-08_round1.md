# BuildBase Action Contract Bug Hunt Loop - 2026-07-08 Round 1

## Summary

画面単位ではなく、ユーザーが押す操作単位で10周の浅い監査を行った。今回の主眼は、前回見落とした「画面では対象がありそうなのに、ボタン実行時の候補抽出が0件になる」系のバグを拾うこと。

結論:
- P1修正を3件実装した。
  - 対応付け提案の不一致一括却下を preview -> confirm -> execute に変更。
  - 照合グループの一括accept保存を preview -> confirm -> execute に変更。
  - ファクトブック照合の許容誤差設定で `0` が既定値に潰れるfalseyバグを修正。
- 前回の見落とし原因は、監査単位が「画面導線」中心で、ボタンの候補抽出・件数定義・書き込み先まで貫通していなかったこと。
- 今後のループでは、一括/破壊的/高件数操作は必ず preview 契約を監査対象にする。

## Scoreboard

| Metric | Current |
|---|---:|
| final_master_long rows | 10,152 |
| review_queue rows | 9,401 |
| review_resolved rows | 769 |
| corroboration cells_total | 11,780 |
| corroboration conflicts | 1,226 |
| auto_accepted_with_zero_corroboration | 2,031 |
| golden regression pass | true |
| golden mismatch_count | 0 |
| factbook validation rows | 183 |
| factbook comparable_rows | 0 |
| factbook pending_rows | 178 |
| web runtime | 127.0.0.1:8765 running |

## 10 Loops

### 1. Mapping Bulk Reject

対象:
- UI: `MappingReviewPanel`
- API: `POST /api/mappings/bulk-reject-conflicts`
- Service: `mapping_review.bulk_reject_conflicting_proposals`
- Write path: `semantics.db concept_mappings` and CSV mirrors

発見:
- 一括却下は実行前に対象件数を確認できなかった。
- 「矛盾」というラベルがセル判定と対応付け提案の両方を指し、ユーザーが件数を誤認しやすかった。

修正:
- `preview` を追加。
- 実行前に候補件数とサンプル5件を表示。
- ボタン文言を「対応付け提案の不一致を一括却下」に変更。

### 2. Reconciliation Group Apply

対象:
- UI: `ReconciliationPanel`
- API: `POST /api/reconciliation/apply`
- Service: `reconciliation.apply_reconciliation_group`
- Write path: `review_resolved.csv`

発見:
- グループaccept保存は一括書き込みだが、previewなしで即保存だった。

修正:
- `preview` を追加。
- 対象件数、サンプル、最終表反映が別操作であることを確認してから保存。

### 3. Factbook Validation Tolerance

対象:
- Service: `company_factbooks.validate_factbook_against_yuho`
- Config: `config/company_factbook_sources.yml`

発見:
- `absolute_tolerance_million_yen: 0` や `relative_tolerance: 0` を設定しても、`or 100` / `or 0.005` により既定値へ戻っていた。
- 厳密照合をしたい時に mismatch が pass になる可能性がある。

修正:
- `_config_float` を追加し、未設定と `0` を区別。
- tolerance=0 の境界テストを追加。

### 4. Cell Workbench Similar Apply

対象:
- UI: Cell Workbench 同種セルへ適用
- API: `POST /api/cells/{company_year_id}/{field_id}/apply-similar`
- Service: `cells.apply_similar_reviews`
- Write path: `review_resolved.csv`

判定:
- preview -> execute の契約あり。
- 既存テストあり。
- P0/P1なし。

残課題:
- 「保存のみ」と「最終表更新」の違いは以前より改善しているが、サンプル対象の一覧表示はまだ最低限。

### 5. Cell Workbench Expand To Years

対象:
- UI: Cell Workbench 他年度展開
- API: `POST /api/cells/{company_year_id}/{field_id}/expand-to-years`
- Service: `cells.expand_to_other_years`
- Write path: `source_inference.apply_promotion_plan` -> `review_resolved.csv`

判定:
- preview -> execute の契約あり。
- 会社IDとfield_idでスコープを絞っており、他社波及を防いでいる。
- P0/P1なし。

残課題:
- 実行後も最終表反映は別操作である点はUI文言で出ているが、ジョブ状態への接続はまだ弱い。

### 6. Review Panel Not Applicable / Delete / Apply Review

対象:
- UI: `ReviewPanel`
- API: `/api/reviews/not-applicable`, `/api/reviews/resolved/delete`, `/api/jobs/apply-review`
- Write path: `review_resolved.csv`, `company_field_exclusions.csv`, final outputs

判定:
- 削除と対象外設定には確認ダイアログあり。
- 対象外設定は保存後に対象件数を返すが、事前previewはない。

優先度:
- P2。対象外範囲の誤操作防止として、将来preview化する価値はある。

### 7. Job Buttons

対象:
- `/api/jobs/run-all`
- `/api/jobs/reextract-with-review`
- `/api/jobs/apply-review`
- `/api/jobs/golden-freeze`
- `/api/jobs/regression-check`

判定:
- JobManagerで同時実行ガードあり。
- 一部はdry-run概念がないが、元々明示実行ジョブ。

優先度:
- P2。ジョブ開始前に「何を読む/何を書く」を1行で表示する改善余地あり。

### 8. Count Consistency

対象:
- Home quality cards
- Results cell status filters
- Corroboration summary
- Mapping review summary

発見:
- セル判定の「矛盾」と、対応付け提案の「不一致」は別集計。
- 今回の一括却下バグはこの同名ラベルも誤認を増やしていた。

修正:
- Mapping側の一括操作ラベルを分離。
- 0件時メッセージで「セル判定の矛盾とは別集計」と明示。

残課題:
- Home側の「矛盾」はセル判定の矛盾として明示する余地あり。

### 9. Falsey Numeric Audit

対象:
- `or default`
- `float(x or default)`
- `int(x or default)`
- frontend `|| default`

発見:
- 今回修正済みの `match_rate=0.0` 系に加えて、ファクトブック許容誤差でも同種のリスクがあった。

修正:
- factbook validation tolerance を修正。

残課題:
- `company_factbooks.py` の timeout/retry/sleep 設定などにも `or default` は残るが、0を許容すべきかは運用仕様次第。現時点ではP2。

### 10. Runtime Freshness / Verification Gate

対象:
- local web runtime
- frontend build
- Python tests
- golden regression summary

確認:
- `npm run build` pass。
- `./yuho test ...` は全431テスト pass。
- 既存の light regression summary は pass true / mismatch_count 0。
- web server is running on 127.0.0.1:8765。

残作業:
- backend API変更を画面へ反映するにはサーバ再起動が必要。
- 最終検証で `./yuho cli regression-check --mode light` を再実行する。

## Implemented Fixes

### P1: Mapping bulk reject preview

実行前に同じ候補抽出ロジックで対象件数を返し、サンプルを確認してから実行する。

追加テスト:
- `test_bulk_reject_conflicting_proposals_preview_does_not_write`

### P1: Reconciliation group apply preview

一括accept保存前に対象件数とサンプルを確認する。

追加テスト:
- `test_apply_group_preview_does_not_write`

### P1: Factbook zero tolerance

未設定と0を区別し、厳密照合が設定できるようにする。

追加テスト:
- `test_validate_factbook_respects_zero_tolerance_config`

## Remaining Backlog

### P1

- Homeの「矛盾」ラベルを「セル値の矛盾」に寄せる。対応付け提案の不一致と混同させない。
- 一括操作のUIに、preview対象と実行対象が同一条件であることを短く表示する。

### P2

- `ReviewPanel` の会社・項目対象外設定にpreviewを追加する。
- ジョブ開始前に読み取り元/書き込み先を1行で表示する。
- `company_factbooks.py` の timeout/retry/sleep などの `or default` が0を許容すべきか仕様化する。
- Cell Workbenchの同種適用プレビューに「現在値があるセル/空欄セル」の内訳を出す。

### P3

- `main.tsx` のさらなる分割。
- 用語ツールチップの追加。
- デモ導線上以外の表現 polish。

## Verification So Far

- `./yuho test tests/test_mapping_review.py tests/test_reconciliation.py tests/test_company_factbooks.py`
  - Result: 431 tests OK
- `npm run build`
  - Result: pass

## Next Gate

1. 全テストを再実行する。
2. `./yuho cli regression-check --mode light` を再実行する。
3. `./yuho web-stop && ./yuho web-start` でサーバを再起動する。
4. APIまたはブラウザで、一括却下previewと照合グループpreviewが新挙動になっていることを確認する。
5. コミット・プッシュする。
