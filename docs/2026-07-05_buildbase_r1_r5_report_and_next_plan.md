# BuildBase R1-R5 作業報告・監査反映・次期計画

作成日: 2026-07-05  
対象: `Materials/2026-06_有報自動抽出/yuho_auto_extract`

## 1. 現在地

R1-R5 では、旧UI・旧学習機構の削除、5グループIAへの再編、ダーク/ライトUI、概念管理、回帰状態カード、出典チェーン、reconciliation受け皿、%照合、rd_expense単位誤読対策、遡及修正検知、ファクトブック解析基盤、新制度半期報告書の入口までを実装した。

ただし、監査で確認されたとおり、最初の報告には「コードは入ったが、証拠DB・実データ・稼働サーバにまだ反映されていない」項目があった。今回のN0で年次運用状態を復旧し、証拠DB・final・report・Web稼働面を更新した。

最終検証:

- `regression-check --mode light`: `pass=True`, `mismatch_count=0`, `missing_in_actual_count=0`, `negative_golden_violations=0`
- `pytest tests/`: `349 passed`, `1 warning`
- `npm run build`: success
- Web server: `http://127.0.0.1:8765/` で起動済み
- `/api/concepts` と `/api/reconciliation/groups`: `application/json` 応答を確認
- ブラウザ目視: ホーム、概念管理、照合グループ、ダーク/ライト切替、回帰PASS表示、コンソールエラー0を確認

注意: 今回はユーザー方針に従い、`golden-freeze` は実行していない。goldenは更新せず、回帰確認のみ実施した。

## 2. 監査指摘への反映

| 指摘 | 現在の扱い |
| --- | --- |
| B6「27新概念反映済み」は定義レベルのみ | 正。`canonical_concepts` は92件だが、finalは現在68概念、`nc_` 実データは3概念・6行にとどまる。27新概念の実値化は未完。 |
| B1/B2/B3修正が証拠DBに未反映 | N0で `corroborate` と派生成果物を再実行。証拠DBの鮮度は回復。 |
| `document_index` / `target_documents` が半期検証範囲のまま | N0で年次状態へ復旧。`target_documents` は204行すべて `period_type=annual`。 |
| 稼働サーバが旧版 | N0で再起動し、新APIがJSON応答することを確認。 |
| `factbook_ai` はプレースホルダ | 正。現時点ではキーワード分類。`ai_runner` / haiku本接続はN1で実施。 |
| `main.tsx` 分割は部分的、UI目視不足 | 正。ブラウザsmokeは実施済みだが、分割完遂と全パネル詳細目視はN4継続。 |

## 3. N0で実施したこと

### 3.1 年次resolveの安全弁

半期検証行が年次resolveへ混入しないよう、`resolve` に `--period-type annual|semiannual_h1|all` を追加した。デフォルトは `annual`。

変更点:

- `src/yuho_auto_extract/__main__.py`
- `src/yuho_auto_extract/document_resolver.py`
- `src/yuho_auto_extract/services/pipeline.py`
- `tests/test_document_resolver.py`

`annual_refresh` も `period_type="annual"` を明示するようにした。

### 3.2 年次index / target_documents復旧

復旧後の状態:

| 項目 | 値 |
| --- | --- |
| `document_index.parquet` | 307,118行 |
| submit range | 2016-07-04 09:10 から 2025-07-31 17:07 |
| `target_documents.parquet` | 204行 |
| period_type | `annual`: 204 |
| resolution | `resolved`: 203, `failed`: 1 |
| failed | `TAKENAKA_2015` |

補足:

- 2016年6月分のEDINET API取得を試したが0行で、いったん `document_index` を上書きしてしまった。
- その後 `index-annual` で `document_index` を復旧し、既存の良好な `target_documents.csv` を基準に `target_documents.parquet` も復旧した。

### 3.3 証拠DB・final・report再生成

実行した流れ:

```bash
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract locate-sections
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract run-local
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract normalize
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract validate
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract corroborate
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract build-corroboration-report
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract build-review-queue
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract split-local-review
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract export-final --reviewed data/review/review_resolved.csv
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract build-analysis
PYTHONPATH=src .venv/bin/python -m yuho_auto_extract report
```

注意:

- `run-local` は手元のObsidian技術者数ノート探索で警告終了したが、DB抽出・ローカル抽出ファイルは生成済み。
- 技術者数は既存の `manual_technician_extracted_long.csv` 400行を使って後続処理を継続した。

証拠DBの前後比較:

| resolution | Before | After |
| --- | ---: | ---: |
| auto_confirmed | 1,502 | 1,501 |
| conflicted | 60 | 55 |
| needs_reconciliation | 1,042 | 919 |
| needs_review | 2,612 | 3,739 |
| no_value | 76 | 242 |
| single_source | 5,069 | 5,309 |

`rd_expense` の前後比較:

| resolution | Before | After |
| --- | ---: | ---: |
| auto_confirmed | 74 | 79 |
| conflicted | 60 | 55 |
| needs_review | 6 | 6 |
| single_source | 53 | 53 |

重要な補足:

- 監査で期待された `rd_expense` conflict 60件の完全解消には至っていない。
- 残り55件は、鹿島の「億円丸め」とXBRL厳密値の差、浅沼の「2億5千万円」など日本語複合単位の取り扱い、前田系の別表値差などが含まれる。
- これはN3の精度バックログとして扱う。

### 3.4 KAJIMA_2024 `rd_expense`

証拠DB:

- `cell_resolutions`: `auto_confirmed`
- `conflict_count`: 0
- `buckets`: `["local_table", "xbrl"]`
- ローカル表: `222億円` → `22200百万円`
- XBRL: `22207百万円`

final wide:

- `wide_values.xlsx` / `final_master_wide.csv`: `KAJIMA_2024 rd_expense = 22207.0`

対応:

- `build_wide_values` は同一セルに複数ソースがある場合、`field_definition.preferred_method` を優先するよう修正した。
- 人手修正・承認は最優先のまま維持。
- これにより、XBRLとローカルが近接照合した場合でも、分析主表ではXBRL厳密値を代表値として採用する。

## 4. R1-R5作業報告

### R1: 旧機構の削除

旧ルール候補、XBRL未発見、主要財務evidence、review learning同期などを削除した。`review_resolved.csv` によるセル値修正、human confirmed、golden regression、`field_definition.csv` 手編集運用は保持した。

### R2: IA再編とUI刷新

UIを「ホーム、データ、品質レビュー、運用、設定」の5グループへ再編し、ダーク/ライトテーマ、ホーム品質ダッシュボード、回帰状態カード、概念管理、出典チェーンを追加した。`main.tsx` の分割は進めたが、完全分割は未完。

### R3: 精度強化

%単位照合、`rd_expense` の値近傍単位優先、前期XBRL差分による遡及修正疑い、reconciliationグループレビューの受け皿を追加した。今回N0で証拠DBに反映したが、`rd_expense` conflictは55件残っている。

### R4: ファクトブック解析基盤

`pdfplumber`、PDF/Excel/CSV候補表抽出、キーワードプリフィルタ、pending rows出力を追加した。現時点の `factbook_ai` はキーワード分類で、承認方針だった haiku / `ai_runner` 本接続は未実装。

### R5: 新制度半期報告書対応

`semiannual_report` フィルタ、`period_type`、鹿島 `KAJIMA_2024H1` のresolve/download/fact store生成まで確認した。鹿島H1は抽出枠66行まで出たが、非空値0。半期XBRL context条件の特定が次課題。

## 5. 次期計画 N1-N4

### N1: ファクトブック主要3社の実値化

最優先。BuildBase本来目的の「営業戦略分析DB」に直結する。

対象:

- 鹿島建設
- 大林組
- 大成建設

作業:

1. 各社の `source_documents.csv` と保存済みPDF/Excelを確認する。
2. 用途別受注・完工表の表形状を把握する。
3. `factbook_parsers.py` を会社別または表形状別にチューニングする。
4. `factbook_ai.py` を `ai_runner` / haiku に本接続する。AIは表の意味判断のみに使う。
5. 有報値と照合できる行は `validate_factbook_against_yuho` で裏取りする。
6. pass行のみ marts へ採用し、不一致・照合不能は pending に残す。

完了条件:

- 主要3社の用途別受注または完工が marts に入る。
- `factbook-coverage` のparsed / adoptedが増える。
- 照合④のカバレッジが増える。

### N2: 半期の値抽出成立

対象は鹿島 `KAJIMA_2024H1` から始める。

作業:

1. `facts.csv` / `context_index.csv` で半期値が入っているelementとcontextを特定する。
2. 年次field定義をそのまま使える項目と、半期専用条件が必要な項目を分ける。
3. `extract-from-xbrl-fact-store` で非空値を出す。
4. 年次成果物とgolden対象に半期が混ざらないことを確認する。

完了条件:

- `KAJIMA_2024H1` に非空抽出値が出る。
- 年次 `regression-check --mode light` が `pass=True`。
- 半期はgolden対象外のまま。

### N3: 品質バックログ

優先順:

1. `rd_expense` 残り55 conflictの分類と解消。
2. %対応後の `unverifiable` 再判定。
3. 27新概念の実値化。
4. reconciliation残件のグループ処理。
5. conflicts全体の整理。

`rd_expense` は、丸め許容と日本語複合単位の扱いを分けて直す必要がある。単純に許容幅を広げると誤照合を増やすので、XBRL厳密値・ローカル丸め値・出典文言をセットで判定する。

### N4: メンテナンス

作業:

- `main.tsx` の分割完遂。
- 全主要パネルのブラウザ目視。
- ハンドオフ文書更新。
- chunk size警告への対応検討。

## 6. 次に着手すべきこと

次は **N1: ファクトブック主要3社の実値化** から入るのがよい。

理由:

- BuildBaseの業務価値に最も直結する。
- R4で候補文書発見と解析基盤は入っている。
- 半期対応や品質バックログより、年次分析DBの補完価値を先に上げるほうが効果が大きい。

次回の最小ゴール:

- 鹿島1社の最新ファクトブックPDF/Excelから、用途別受注または用途別完工を1表以上 marts に投入する。
- `factbook-validate` と `factbook-coverage` で前後比較を残す。
- その後、大林組・大成建設へ横展開する。
