from __future__ import annotations

import unittest
from pathlib import Path

from yuho_auto_extract.services import source_inference as si


def _real_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class TokenizeNumbersTests(unittest.TestCase):
    def test_comma_separated_numbers(self):
        tokens = si.tokenize_numbers("受注高 123,456 完成工事高 78,901")
        values = [t.value for t in tokens]
        self.assertIn(123456.0, values)
        self.assertIn(78901.0, values)

    def test_negative_marks_are_parsed(self):
        tokens = si.tokenize_numbers("差異 △1,234 前年比 ▲567")
        values = [t.value for t in tokens]
        self.assertIn(-1234.0, values)
        self.assertIn(-567.0, values)

    def test_zenkaku_digits_are_normalized(self):
        tokens = si.tokenize_numbers("受注高１２３，４５６円")
        values = [t.value for t in tokens]
        self.assertIn(123456.0, values)

    def test_isolated_short_integer_is_excluded_by_default(self):
        # 箇条書き番号のようなノイズ（MATSUI_2018で正解データに混入した実例）
        tokens = si.tokenize_numbers("4不動産事業等の拡充")
        self.assertEqual(tokens, [])

    def test_isolated_short_integer_with_comma_is_kept(self):
        tokens = si.tokenize_numbers("残高 1,234 円")
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].value, 1234.0)

    def test_parenthetical_number_is_flagged(self):
        tokens = si.tokenize_numbers("前期繰越工事高\n(121,098)\n121,135")
        paren_values = [t.value for t in tokens if t.is_parenthetical]
        plain_values = [t.value for t in tokens if not t.is_parenthetical]
        self.assertIn(121098.0, paren_values)
        self.assertIn(121135.0, plain_values)

    def test_decimal_short_number_is_kept(self):
        # 比率(%)等の小数は3桁未満フィルタの対象外。
        tokens = si.tokenize_numbers("特命 7.2 競争 92.8")
        values = [t.value for t in tokens]
        self.assertIn(7.2, values)
        self.assertIn(92.8, values)


class FindRowLabelPositionsTests(unittest.TestCase):
    def test_finds_building_civil_total_labels(self):
        text = "土木工事\n100\n200\n300\n50\n250\n建築工事\n10\n20\n30\n5\n25\n計\n110\n220\n330\n55\n275\n"
        labels = si.find_row_label_positions(text)
        keys = [label[2] for label in labels]
        self.assertIn("civil", keys)
        self.assertIn("building", keys)
        self.assertIn("total", keys)


class FitBacklogTuplesTests(unittest.TestCase):
    def test_five_tuple_fits_when_equations_hold(self):
        # 前期繰越184,321 + 当期受注238,921 = 計423,242(≈423,243) - 完成233,462 = 次期繰越189,780(≈189,781)
        text = "建築工事\n184,321\n238,921\n423,243\n233,462\n189,780\n"
        tokens = si.tokenize_numbers(text)
        labels = si.find_row_label_positions(text)
        fitted = si.fit_backlog_tuples(tokens, labels)
        self.assertEqual(len(fitted), 1)
        self.assertTrue(fitted[0].has_total_column)
        self.assertEqual(fitted[0].row_label_key, "building")
        self.assertAlmostEqual(fitted[0].values[0], 184321.0)
        self.assertAlmostEqual(fitted[0].values[1], 238921.0)
        self.assertAlmostEqual(fitted[0].values[3], 233462.0)
        self.assertAlmostEqual(fitted[0].values[4], 189780.0)

    def test_four_tuple_fallback_when_no_total_column(self):
        # 計列が無い表: 前期繰越1,000 + 当期受注500 - 当期完成300 = 次期繰越1,200
        text = "建築工事\n1,000\n500\n300\n1,200\n"
        tokens = si.tokenize_numbers(text)
        labels = si.find_row_label_positions(text)
        fitted = si.fit_backlog_tuples(tokens, labels)
        self.assertEqual(len(fitted), 1)
        self.assertFalse(fitted[0].has_total_column)
        self.assertEqual(fitted[0].values[0], 1000.0)
        self.assertEqual(fitted[0].values[1], 500.0)
        self.assertIsNone(fitted[0].values[2])
        self.assertEqual(fitted[0].values[3], 300.0)
        self.assertEqual(fitted[0].values[4], 1200.0)

    def test_no_fit_when_equation_does_not_hold(self):
        text = "建築工事\n100\n50\n999\n30\n120\n"
        tokens = si.tokenize_numbers(text)
        labels = si.find_row_label_positions(text)
        fitted = si.fit_backlog_tuples(tokens, labels)
        self.assertEqual(fitted, [])

    def test_multiple_candidates_enumerated_across_window(self):
        # 同じラベルの直後に、恒等式が成立しない値列 → 成立する値列 の順に並ぶ場合、
        # 成立する5個組のみが返る（全候補を列挙し、恒等式を満たすもののみ採用）。
        text = "建築工事\n999\n1\n1\n1\n1\n184,321\n238,921\n423,243\n233,462\n189,780\n"
        tokens = si.tokenize_numbers(text)
        labels = si.find_row_label_positions(text)
        fitted = si.fit_backlog_tuples(tokens, labels)
        self.assertGreaterEqual(len(fitted), 1)
        self.assertTrue(any(f.values[1] == 238921.0 for f in fitted))


class SyntheticTableTests(unittest.TestCase):
    """合成した「受注工事高、完成工事高及び次期繰越工事高」表で、行×列が正しく確定すること。"""

    TABLE_TEXT = (
        "(1）受注工事高、完成工事高及び次期繰越工事高\n"
        "期別\n区分\n前期繰越\n工事高\n(百万円)\n当期受注\n工事高\n(百万円)\n計\n(百万円)\n"
        "当期完成\n工事高\n(百万円)\n次期繰越\n工事高\n(百万円)\n"
        "前事業年度\n"
        "土木工事\n100,000\n50,000\n150,000\n40,000\n110,000\n"
        "建築工事\n200,000\n80,000\n280,000\n90,000\n190,000\n"
        "計\n300,000\n130,000\n430,000\n130,000\n300,000\n"
        "当事業年度\n"
        "土木工事\n110,000\n60,000\n170,000\n45,000\n125,000\n"
        "建築工事\n190,000\n100,000\n290,000\n95,000\n195,000\n"
        "計\n300,000\n160,000\n460,000\n140,000\n320,000\n"
    )

    def test_building_row_current_period_fits_correctly(self):
        segment = si.extract_table_segment(self.TABLE_TEXT)
        tokens = si.tokenize_numbers(segment)
        labels = si.find_row_label_positions(segment)
        fitted = si.fit_backlog_tuples(tokens, labels, text=segment)
        building_current = [f for f in fitted if f.row_label_key == "building" and f.period == "current"]
        self.assertEqual(len(building_current), 1)
        values = building_current[0].values
        self.assertEqual(values, (190000.0, 100000.0, 290000.0, 95000.0, 195000.0))

    def test_total_row_consistency_holds_for_synthetic_table(self):
        segment = si.extract_table_segment(self.TABLE_TEXT)
        tokens = si.tokenize_numbers(segment)
        labels = si.find_row_label_positions(segment)
        fitted = si.fit_backlog_tuples(tokens, labels, text=segment)
        consistency = si._check_total_row_consistency(fitted)
        # building行(current)のインデックスを特定し、整合フラグがTrueであることを確認する。
        for idx, f in enumerate(fitted):
            if f.row_label_key == "building" and f.period == "current":
                self.assertTrue(consistency.get(idx))


class ColumnHeaderPeriodMarkerTests(unittest.TestCase):
    """列見出しの「前期繰越高」「当期受注高」等が期区分マーカーとして
    誤検出されないこと（修正1）。事業年度表記が無く「第N期」のみで
    年度が区切られる業界標準表（SHIMIZU/TAISEI/TAKENAKA/TOA/PENTA/
    NISHIMATSU型）を模した合成テキストで検証する。
    """

    TABLE_TEXT = (
        "(1) 受注(契約)高，売上高，及び次期繰越高\n"
        "期別\n種類別\n前期\n繰越高\n(百万円)\n当期\n受注(契約)高\n(百万円)\n計\n(百万円)\n"
        "当期\n売上高\n(百万円)\n次期\n繰越高\n(百万円)\n"
        "第113期\n"
        "建築工事\n965,072\n1,040,785\n2,005,858\n1,047,270\n958,588\n"
        "土木工事\n337,633\n380,585\n718,218\n254,386\n463,832\n"
        "計\n1,302,705\n1,421,371\n2,724,077\n1,301,656\n1,422,420\n"
        "第114期\n"
        "建築工事\n958,588\n1,000,775\n1,959,363\n1,067,585\n891,777\n"
        "土木工事\n463,832\n283,858\n747,691\n282,762\n464,928\n"
        "計\n1,422,420\n1,284,633\n2,707,054\n1,350,347\n1,356,706\n"
    )

    def test_column_header_zenki_toki_are_excluded_from_period_markers(self):
        # 列見出し中の「前期繰越」「当期受注」「当期売上」は期区分マーカーの
        # 候補から除外され、"第N期" のみがマーカーとして採用されること。
        markers = si._find_period_markers(self.TABLE_TEXT)
        periods = {period for _pos, period in markers}
        self.assertEqual(periods, {"current", "previous"})
        # 列見出し区間（"前期\n繰越高" 等の出現位置）にマーカーが立っていないこと。
        header_start = self.TABLE_TEXT.index("前期\n繰越高")
        header_end = header_start + len("前期\n繰越高")
        for pos, _period in markers:
            self.assertFalse(header_start <= pos < header_end)

    def test_building_row_period_is_correctly_split_by_nth_period(self):
        # 第113期(前)・第114期(当)の建築行が、列見出しの「前期/当期」に
        # 惑わされず正しく previous/current に分かれ、どちらも一意にフィットすること
        # （誤検出時は両方が current 等の同一区分に落ち、low_confidence の原因になる）。
        segment = si.extract_table_segment(self.TABLE_TEXT)
        tokens = si.tokenize_numbers(segment)
        labels = si.find_row_label_positions(segment)
        fitted = si.fit_backlog_tuples(tokens, labels, text=segment)
        building_current = [f for f in fitted if f.row_label_key == "building" and f.period == "current"]
        building_previous = [f for f in fitted if f.row_label_key == "building" and f.period == "previous"]
        self.assertEqual(len(building_current), 1)
        self.assertEqual(len(building_previous), 1)
        self.assertEqual(
            building_current[0].values, (958588.0, 1000775.0, 1959363.0, 1067585.0, 891777.0)
        )
        self.assertEqual(
            building_previous[0].values, (965072.0, 1040785.0, 2005858.0, 1047270.0, 958588.0)
        )


class SpacedRowLabelTests(unittest.TestCase):
    """行ラベルの字間に空白/改行が挟まる組版（大林組型: 「建　築」「土　木」
    「合　計」）でも行ラベル検出・恒等式フィットが成立すること（修正2）。
    """

    TABLE_TEXT = (
        "(1）受注高、売上高及び繰越高\n"
        "期 別 \n種類別 \n前期繰越高 \n(百万円) \n当期受注高 \n(百万円) \n計 \n(百万円) \n"
        "当期売上高 \n(百万円) \n次期繰越高 \n(百万円) \n"
        "第111期 \n"
        "建設事業 \n"
        "建 築 \n1,091,026 \n986,030 \n2,077,056 \n958,646 \n1,118,410 \n"
        "土 木 \n372,237 \n322,227 \n694,464 \n267,923 \n426,540 \n"
        "合 計 \n1,463,264 \n1,308,257 \n2,771,520 \n1,226,569 \n1,544,950 \n"
        "第112期 \n"
        "建設事業 \n"
        "建 築 \n1,118,410 \n1,069,697 \n2,188,107 \n908,468 \n1,279,639 \n"
        "土 木 \n426,540 \n330,584 \n757,124 \n297,907 \n459,217 \n"
        "合 計 \n1,544,950 \n1,400,281 \n2,945,231 \n1,206,375 \n1,738,856 \n"
    )

    def test_spaced_labels_are_detected(self):
        labels = si.find_row_label_positions(self.TABLE_TEXT)
        keys = [label[2] for label in labels]
        self.assertIn("building", keys)
        self.assertIn("civil", keys)
        self.assertIn("total", keys)

    def test_spaced_building_row_fits_backlog_equation(self):
        segment = si.extract_table_segment(self.TABLE_TEXT)
        tokens = si.tokenize_numbers(segment)
        labels = si.find_row_label_positions(segment)
        fitted = si.fit_backlog_tuples(tokens, labels, text=segment)
        building_current = [f for f in fitted if f.row_label_key == "building" and f.period == "current"]
        self.assertEqual(len(building_current), 1)
        self.assertTrue(building_current[0].has_total_column)
        self.assertEqual(
            building_current[0].values, (1118410.0, 1069697.0, 2188107.0, 908468.0, 1279639.0)
        )


@unittest.skipUnless(
    (_real_project_root() / "data" / "intermediate" / "edinet.db").exists(),
    "実プロジェクトの edinet.db が無い環境ではスキップ",
)
class InferSourceForCellIntegrationTests(unittest.TestCase):
    """実データ（data/intermediate/edinet.db, data/final/final_master_long.csv）を
    読み取り専用で使う結合テスト。書き込みは一切行わない。
    """

    @classmethod
    def setUpClass(cls):
        cls.root = _real_project_root()

    def test_ando_hazama_2015_building_orders_total_is_identified(self):
        # ANDO_HAZAMA_2015 建築工事: 当期受注 238,921 が高信頼度で特定できること。
        result = si.infer_source_for_cell(
            self.root, "ANDO_HAZAMA_2015", "building_orders_total", 238921, unit="百万円"
        )
        self.assertTrue(result["matched"])
        top = result["candidates"][0]
        self.assertGreaterEqual(top["confidence"], 0.9)
        self.assertEqual(top["row_label_key"], "building")

    def test_matsui_2018_known_noise_value_does_not_match_high_confidence(self):
        # MATSUI_2018 の正解データに混入したノイズ値(4)は、恒等式フィット組の
        # どの役割にも一致しない（=低信頼どころか候補ゼロ）ことを回帰確認する。
        result = si.infer_source_for_cell(
            self.root, "MATSUI_2018", "backlog_building_next", 4, unit="百万円"
        )
        if result["candidates"]:
            self.assertLess(result["candidates"][0]["confidence"], 0.9)
        else:
            self.assertFalse(result["matched"])

    def test_known_cells_reproduction_rate_meets_threshold(self):
        # 43セル全件を対象にした結合テスト。閾値は詳細設計の実測固定値
        # `>= 0.9 * 43` をそのままアサートする（実測39/43=90.7%を下回らないことを担保）。
        result = si.learn_company_layouts(self.root)
        repro = result["reproduction"]
        self.assertGreaterEqual(repro["total"], 40)  # データが極端に減っていないことの健全性チェック
        self.assertGreaterEqual(repro["matched_high_confidence"], 0.9 * repro["total"])


if __name__ == "__main__":
    unittest.main()
