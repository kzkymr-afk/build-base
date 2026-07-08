# BuildBase A-G 浅い横断監査 2026-07-08

## Executive Summary

A-Gの浅い横断監査をread-onlyで実施した。現時点で新規P0は見つからない。直前に修正した `apply-review` 経路は成功状態で、`review_resolved.csv` は `applied 657 / not_applicable 112`、未反映0まで戻っている。

一方で、P1相当の使い勝手・状態不一致が残っている。最初の修正スプリントは、A/C/D/Eの交差部分に絞るのがよい。特に「結果表では照合要確認なのに、Cell Workbenchでは値ありに戻る」「reconciliation対象があるのに照合グループ画面が0件」「候補採用の文言が旧レビュー導線を参照している」「新概念27件のうち23件がfinal値ゼロ」の4点が優先。

監査時点の主要状態:

- `final_master_wide.csv`: 205行
- `final_master_long.csv`: 10,152行
- `source_audit.csv`: 10,152行
- `review_queue.csv`: 9,401行
- `review_resolved.csv`: 769行
- `field_definition.csv`: 92項目
- `canonical_concepts.csv`: 92概念
- `cell_resolutions.csv`: 11,797行
- `automation.review_gate`: `ready=false`, blocking reasonは `active_review_items=8767 exceeds 0`
- `regression-check light`: `pass=True`, `mismatch_count=0`

## A. 結果表・セル修正

現状:

- 結果表のセル状態は概ね出ている。サンプル範囲では `value_present 1336`, `corroboration_needs_reconciliation 532`, `corroboration_needs_review 284`, `blank_with_review_candidate 93`, `blank_review_needs_rule 27`, `not_applicable 10`, `document_failed 8`, `blank_no_candidate 6`。
- `ANDO_HAZAMA_2015 / nc_2d3b60309c0b380a` は `調査研究費_連結`, `1909.0`, `applied` で正常。
- `HASEKO_2024 / building_orders_total` のような空欄候補ありセルは、候補・手入力・対象外の入口がある。

見つかったバグ/違和感:

- P1: 結果表側の `cell_statuses` は `corroboration_needs_review` / `corroboration_needs_reconciliation` に昇格するが、`read_cell_detail` の詳細ヘッダは同じセルを `value_present` として返す。例: `ANDO_HAZAMA_2024 / roe`, `ANDO_HAZAMA_2024 / building_orders_total`。ユーザーは「照合要確認セルを開いたのに、詳細では値あり」と受け取る。
- P1: `blank_with_review_candidate` の `next_action` が「レビュー画面で accept/correct、レビュー反映」と旧導線を案内している。現在の主導線は Cell Workbench の「この値で最終表を更新」。
- P2: `blank_review_needs_rule` は `candidate_count=1` でも「候補なし・要ルール確認」と表示される。実態は「レビューキュー行はあるが抽出値が空」で、候補あり/なしの言葉が混ざる。
- P2: `accept` は候補値が空だと押せないが、手入力で `correct` に切り替える導線はまだ分かりにくい。

代表ケース:

- `HASEKO_2022 / completed_building`: 空欄・候補なし。
- `HASEKO_2024 / building_orders_total`: 空欄・レビュー候補あり。
- `INFR_2024 / roe`: 空欄・ルール確認系。
- `MAEDA_2024 / net_sales_consolidated`: 対象外。
- `TAKENAKA_2015 / completed_building`: 文書未取得。

## B. レビュー・マッピング裏画面

現状:

- レビューカテゴリは `missing 4304`, `new_candidate 2428`, `warning_candidate 778`, `scope_warning 719`, `validation_issue 538`, `resolved_done 634`。
- マッピング提案は1,247件。内訳は `ignore 1013`, `map 195`, `different_scope 39`。
- マッピング照合サマリは `single_source 5309`, `needs_review 3761`, `auto_confirmed 1501`, `needs_reconciliation 920`, `no_value 251`, `conflicted 55`。

見つかったバグ/違和感:

- P1: `needs_reconciliation 920` が存在するのに `/api/reconciliation/groups` は `total=0`。照合グループレビューの受け皿が実データを拾えていない可能性が高い。
- P2: マッピング提案の多くが `ignore` で、通常ユーザーが見るにはノイズが多い。結果表起点の文脈から開く導線に寄せた方がよい。
- P2: `resolved_done` などレビュー裏画面のカテゴリ名は、結果表のセル状態名と対応関係が分かりにくい。

## C. データ更新・ジョブ実行

現状:

- `apply-review` は直近成功しており、`[review-applied] marked=769 total=769`。
- `saved_unapplied_reviews=0` で、保存済みレビューの未反映は解消済み。
- Webサーバは `0.0.0.0:8765` で稼働中。直近ログに新規エラーはない。
- automation gateは `active_review_items=8767 exceeds 0` で年次更新を止めている。

見つかったバグ/違和感:

- P1: `active_review_items` が9千件近くあるため、年次自動取得が恒常的にブロックされる。これは品質ゲートとしては安全だが、通常運用では「全部レビューしないと次に進めない」状態になりやすい。
- P2: 実行タブには `保存済みレビューで再抽出` と `保存済み修正を最終表に反映` が並ぶ。差は以前より改善したが、通常ユーザーにはまだ判断が難しい。
- P2: ジョブログは正確だが、失敗時に最初に見るべき原因を要約するUIはない。

## D. データ品質・照合状態

現状:

- 照合サマリは `cells_total=11780`, `corroborated_2plus=6759`, `corroborated_1=1525`, `corroborated_0=2270`, `conflicts=1226`, `auto_accepted_with_zero_corroboration=2031`。
- `cell_resolutions.csv` は `single_source 5309`, `needs_review 3761`, `auto_confirmed 1501`, `needs_reconciliation 920`, `no_value 251`, `conflicted 55`。
- validation ruleでは `sum_building_orders fail 167`, `backlog_equation fail 153`, `expense_less_than_sga fail 27`, `gross_profit_identity_standalone fail 23`。
- goldenは `golden_cell_count=2513`, `negative_golden_count=112`, light regressionは `mismatch_count=0`。

見つかったバグ/違和感:

- P1: `corroboration_*` の状態が結果表には出るが、Cell Workbenchの判定文と次アクションに十分反映されない。
- P1: `auto_accepted_with_zero_corroboration=2031` は、結果表上で「値あり」としか見えないと危険。根拠が弱い値として目立たせる設計が必要。
- P2: 照合レポートの注意事項に「validation再実行が必要」とある。レポート生成と検証結果の鮮度関係が画面上で分かりにくい。

## E. 項目管理・概念管理

現状:

- field/conceptはともに92件。
- `nc_*` 新概念は27件。そのうちfinal値ありは4件、final値ゼロは23件。
- `target_unit` 空欄は8項目、`data_scope_required` 空欄は4項目。
- concept mappingsは `confirmed 2129`, `proposed 1247`, `rejected 42`。

見つかったバグ/違和感:

- P1: 新概念27件は定義上は存在するが、実値化できているのは4件だけ。結果表では「項目が増えた」ように見えるが、実データの充足と結びついていない。
- P1: `target_unit` / `data_scope_required` 空欄が残っているため、単位照合・スコープ照合・表示の信頼性が落ちる。
- P2: 表示名、概念名、XBRL observed item labelの違いはまだ画面上で説明されにくい。今回の `調査研究費_連結` のような修正後ケースは改善したが、一般化は未完了。

## F. グラフ・分析表示

現状:

- `net_sales_consolidated` のサンプルは 372,146〜2,911,816百万円。
- `roe` は 0.046〜0.163。
- `average_salary` は 8,823,698〜11,847,369円。
- `rd_expense_to_net_sales_consolidated_ratio` は 0.7618〜1.0697で、source summaryは0件。

見つかったバグ/違和感:

- P1: 派生比率のグラフにsource summaryが無い。値は表示できても、出典・計算根拠に戻れない。
- P2: 給与のような円単位の大きい値、ROEのような小数、売上高のような巨大値が同じグラフUIに乗るため、縦軸自動範囲と手動調整が必要。
- P2: 欠損や対象外をグラフ上でどう扱っているかが分かりにくい。

## G. ファクトブック・半期・拡張データ

現状:

- factbookは有効。source 25件、文書候補1,265件、構造化済みorder rows 183件。
- unsupported documentsは1,254件。
- validationは `status=incomplete`, `pending_rows=178`, `comparable_rows=0`。内訳は `no_mapping 114`, `missing_yuho_value 52`, `missing_yuho_row 12`, `forecast_not_checked 5`。
- period modelは `annual 204`, `semiannual_h1 1`。半期は `KAJIMA_2024H1` のみ。

見つかったバグ/違和感:

- P1: factbook値は183行あるが、有報値との比較可能行が0。画面で「データあり」と見えても、検証済みデータとしてはまだ使えない。
- P2: 文書候補1,265件の大半が `pending_parser` / unsupportedで、画面上は「候補発見」と「値抽出済み」が混ざりやすい。
- P2: 半期はpilot 1行のみ。期間トグルで年次と同格に見える場合、ユーザーが全社半期対応済みと誤解する可能性がある。

## データ経路上の危険箇所

- `cell_statuses -> read_cell_detail`: 結果表の状態昇格が詳細側に反映されない。A/D横断のP1。
- `cell_resolutions -> reconciliation groups`: 920件の `needs_reconciliation` があるのにグループAPIが0件。B/D横断のP1。
- `review_queue -> automation gate`: active review 8,767件で年次更新がブロックされる。C/A横断の運用リスク。
- `field_definition/canonical_concepts -> final`: 新概念は定義済みでも23件がfinal値ゼロ。E/A横断の期待値ギャップ。
- `chart values -> source summary`: 派生比率のグラフ値に根拠が戻らない。F/D横断の説明責任リスク。
- `factbook marts -> validation -> UI`: 183行の値があるが比較可能0。G/D横断で「使えるデータ」と誤認しやすい。

## すぐ直すべき P0/P1

P0は新規なし。

P1:

1. **Cell Workbenchの照合状態不一致を修正**
   - 再現: `ANDO_HAZAMA_2024 / roe`, `ANDO_HAZAMA_2024 / building_orders_total`
   - 期待: 詳細ヘッダ・判定・次アクションも `照合要確認` と理由を表示する。
   - 修正候補: `read_cell_detail` でも `_read_cell_resolution` を使って `corroboration_*` に昇格する。

2. **候補ありセルの旧レビュー導線文言をCell Workbench導線へ置換**
   - 再現: `HASEKO_2024 / building_orders_total`
   - 期待: 「レビュー画面」ではなく「この値で最終表を更新」を案内する。
   - 修正候補: `_classify_cell` の `blank_with_review_candidate` 文言を更新。

3. **reconciliation groupsが0件になる原因を修正**
   - 再現: `cell_resolutions.needs_reconciliation=920`, `/api/reconciliation/groups total=0`
   - 期待: 照合グループ画面にレビュー対象グループが出る。
   - 修正候補: `services/reconciliation.py` のグループ条件を `needs_reconciliation` の実データに合わせる。

4. **新概念の充足状態を項目管理/結果表で明示**
   - 再現: `nc_*` 27件中23件がfinal値ゼロ。
   - 期待: 「定義済みだが未実値化」の項目が見える。
   - 修正候補: 概念管理または項目管理に final rows count / coverage を表示。

5. **factbookの検証済み/未検証を明確化**
   - 再現: order rows 183, comparable rows 0, pending rows 178。
   - 期待: 画面で「抽出済み」ではなく「未検証/有報照合不可」が分かる。
   - 修正候補: FactbooksPanelに validation status と pending内訳を強く表示。

## 後回しでよい P2/P3

- P2: `blank_review_needs_rule` のラベル改善。
- P2: 実行タブの `保存済みレビューで再抽出` と `保存済み修正を最終表に反映` の説明整理。
- P2: `active_review_items` が多い場合の年次更新ゲート緩和または例外導線。
- P2: グラフの縦軸自動範囲、手動範囲、欠損表示。
- P2: マッピング提案の `ignore` ノイズ削減。
- P3: 画面密度、文言、ボタン配置の細かな整理。

## 最初の実装修正スプリント案

### Sprint 1: 結果表起点レビューの状態整合

目的: ユーザーが結果表セルを開いたとき、画面・API・データ経路の状態が一致するようにする。

実装順:

1. `read_cell_detail` の照合状態昇格を `read_wide` と揃える。
2. `blank_with_review_candidate` / `blank_review_needs_rule` の `summary` と `next_action` をCell Workbench前提に更新する。
3. reconciliation groups APIが0件になる原因を修正し、`needs_reconciliation` の代表グループを表示する。
4. 項目/概念管理に `final_value_count` と `coverage_hint` を追加し、`nc_*` の未実値化を可視化する。
5. FactbooksPanelに `comparable_rows=0`, `pending_rows=178`, `unsupported_documents=1254` を明確に表示する。

検証:

- `HASEKO_2024 / building_orders_total` を開き、候補採用案内がCell Workbench導線になっていること。
- `ANDO_HAZAMA_2024 / roe` を開き、詳細でも照合要確認として見えること。
- `/api/reconciliation/groups` が0件でなく、代表グループを返すこと。
- `nc_*` 項目に final値の有無が表示されること。
- factbook画面で未検証状態が誤解なく表示されること。
- `./yuho test tests/test_web_services.py`
- `npm run build`
- `./yuho cli regression-check --mode light`

コミット単位:

1. `Align cell detail status with result table`
2. `Restore reconciliation group visibility`
3. `Show concept coverage and factbook validation state`

## Notes

この監査では保存・反映・一括適用・概念更新などの副作用操作は実行していない。次の実装では、各修正後にWeb/API/CSVの状態を確認し、作業完了時はコミット・プッシュする。
