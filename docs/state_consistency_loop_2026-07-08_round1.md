# BuildBase 状態整合性バグ炙り出しループ Round 1

作成日: 2026-07-08

## 目的

前回までの横断監査とUXループでは、画面上の違和感や導線の詰まりは拾えた一方で、「ボタンは存在するが、対象件数が実データとズレる」「保存済みと最終表反映済みの状態が食い違う」「wide/long/source_audit のどれかだけ古い」といったデータ経路の不整合を十分に検出できなかった。

今回のループでは、UI目視ではなく、セル状態・レビュー・最終表・出典・プレビュー書き込み契約を機械的に照合する監査コマンドを追加し、10周実行して潜在バグを炙り出す。

## 追加した監査コマンド

```bash
./yuho cli audit-state-consistency --sample-limit 20
./yuho cli audit-state-consistency --sample-limit 20 --fail-on P1
```

主な確認内容:

- `review_resolved.csv` の重複キー、不正な `review_decision`
- `applied` と表示されるレビューが `final_master_long.csv` に存在するか
- `applied_value` と最終表の値が一致するか
- `reject` / `not_applicable` 済みのセルが最終表に残っていないか
- `final_master_long.csv` と `source_audit.csv` のキー集合が一致するか
- `final_master_long.csv` から再構築したwide値と `final_master_wide.csv` が一致するか
- `Cell Workbench` の `review_state` が `review_resolved.csv` と一致するか
- `semantics.db` とCSVミラーの件数がズレていないか
- 一括却下・照合グループ適用の `preview=True` がdry-runとして機能するか
- `0` と未設定を混同しやすい `or 0` / `or 1` 系の静的候補

## 10周ループ設計

同じ監査を10回繰り返すのではなく、観点を切り替えて実行した。

| 周 | 観点 | コマンド条件 | 結果 |
|---:|---|---|---|
| 1 | full | `--sample-limit 20` | pass / P0-P1=0 |
| 2 | core-large | `--sample-limit 50 --no-preview --no-static` | pass / P0-P1=0 |
| 3 | preview-contracts | `--sample-limit 50 --no-static` | pass / P0-P1=0 |
| 4 | static-falsey | `--sample-limit 50 --no-preview` | pass / P0-P1=0 |
| 5 | strict-p1 | `--sample-limit 20 --fail-on P1` | pass / P0-P1=0 |
| 6 | core-wide-sample | `--sample-limit 100 --no-preview --no-static` | pass / P0-P1=0 |
| 7 | full-wide-sample | `--sample-limit 100` | pass / P0-P1=0 |
| 8 | minimal-smoke | `--sample-limit 1` | pass / P0-P1=0 |
| 9 | persistence-check | `--sample-limit 20` | pass / P0-P1=0 |
| 10 | final-strict-p1 | `--sample-limit 20 --fail-on P1` | pass / P0-P1=0 |

共通スコアボード:

- `review_resolved_rows`: 769
- `final_master_long_rows`: 10,152
- `final_master_wide_rows`: 205
- 全周 `status=pass`
- 全周 `p0_p1_count=0`

## 見つかったもの

### P0/P1

なし。

今回追加した監査範囲では、レビュー保存状態、最終表反映状態、wide/long整合、source_audit、Cell Workbench状態、プレビュー契約に即時修正が必要な不整合は出ていない。

### P3: falsey numeric候補 80件

`float(row.get(...) or 0)` や `int(row.get(...) or 0)` のようなコードが80件検出された。

すべてがバグではない。カウント、ソート、デフォルト月など、0を明示的な既定値として扱ってよい箇所も含む。ただし、数値抽出・照合・単位判定では「0」と「未取得」を混同すると空欄潰しや矛盾検出を壊すため、次ラウンドで危険度を分類する。

優先して見るべきサンプル:

- `src/yuho_auto_extract/local_table_extractor.py`
- `src/yuho_auto_extract/review_queue.py`
- `src/yuho_auto_extract/document_resolver.py`
- `src/yuho_auto_extract/services/ai_mapping.py`

### info: final_master_long の複数根拠セル 556件

`final_master_long.csv` には同一 `company_year_id` / `field_id` の複数行がある。これは複数根拠行を保持する設計であり、それ自体はバグではない。

監査では exporter と同じスコアリングでwide値を再構築し、保存済み `final_master_wide.csv` と比較した。wide値の不一致は出ていない。top score 同点で異なる値が競合するケースも検出されていない。

## なぜ前のループで漏れたか

前のループはA-G/H/Iの横断監査として、画面導線・用語・代表ケース・API疎通に強かった。一方で、次のような「状態遷移の契約」は暗黙だった。

- 保存済みレビューと最終表の値が一致する
- 反映済みラベルと `final_master_long` の実体が一致する
- 一括操作の対象件数とdry-run結果が一致する
- `source_audit` が最終表セルに必ず接続される
- `final_master_wide` が `final_master_long` の採用値から再現できる

そのため、「UI上は押せる」「APIは200を返す」だけでは検出できないバグが残り得た。今回の監査コマンドは、この暗黙契約をP0/P1ゲートとして固定する。

## 次の修正スプリント案

1. falsey numeric候補80件を危険度分類する
   - P1候補: 抽出値、照合値、単位判定、レビュー閾値に関わる箇所
   - P2/P3候補: 表示用カウント、ログ、明示的な既定月など

2. `audit-state-consistency --fail-on P1` を定例ゲートへ入れる
   - セル修正UI、レビュー反映、マッピング一括処理を触った後は必ず実行する
   - UIループの前に実行して、データ経路が壊れていない状態で目視する

3. action previewの対象件数をUIにも表示する
   - 「対象がありそうなのに0件」を、実行前のプレビュー差分で検出できるようにする
   - 既にAPI契約は監査対象に入ったため、次は画面表示との一致を見る

4. `final_master_long` 複数根拠セルをユーザー向けに説明する
   - 「同じセルに複数根拠がある」こと自体は正常
   - 最終採用値、補助根拠、却下根拠の区別をCell Workbenchで見せると誤解が減る

## 次ラウンドのループ案

- Round 2-1: falsey候補80件を抽出精度影響あり/なしに分類
- Round 2-2: 影響ありだけ修正し、golden回帰で値変化を確認
- Round 2-3: 一括操作UIの表示件数とpreview API件数の一致を監査
- Round 2-4: Cell Workbenchで複数根拠セルを1件開き、ユーザーに説明可能な表示か確認
- Round 2-5: `audit-state-consistency --fail-on P1` を最終ゲートとして再実行

