import argparse
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract import __main__ as cli


class CliReviewSourceTests(unittest.TestCase):
    def test_export_final_defaults_to_resolved_csv(self):
        args = cli.build_parser().parse_args(["export-final"])

        self.assertEqual(args.reviewed, "data/review/review_resolved.csv")

    def test_run_local_exports_final_from_resolved_csv(self):
        calls = []
        originals = {
            "build_edinet_db": cli.build_edinet_db,
            "extract_from_edinet_db": cli.extract_from_edinet_db,
            "cmd_import_manual_technicians": cli.cmd_import_manual_technicians,
            "cmd_normalize": cli.cmd_normalize,
            "cmd_validate": cli.cmd_validate,
            "cmd_corroborate": cli.cmd_corroborate,
            "cmd_build_review_queue": cli.cmd_build_review_queue,
            "split_local_review_rows": cli.split_local_review_rows,
            "cmd_export_final": cli.cmd_export_final,
            "cmd_build_analysis": cli.cmd_build_analysis,
            "cmd_report": cli.cmd_report,
            "_local_run_input_error": cli._local_run_input_error,
        }

        def fake_export_final(root, args):
            calls.append(args.reviewed)
            return 0

        try:
            cli.build_edinet_db = lambda root, db_path: {"xbrl_facts": 0}
            cli.extract_from_edinet_db = lambda *args, **kwargs: {"combined_rows": 0}
            cli.cmd_import_manual_technicians = lambda root, args: 0
            cli.cmd_normalize = lambda root, args: 0
            cli.cmd_validate = lambda root, args: 0
            cli.cmd_corroborate = lambda root, args: 0
            cli.cmd_build_review_queue = lambda root, args: 0
            cli.split_local_review_rows = lambda root: {"accepted_rows": 0, "manual_rows": 0}
            cli.cmd_export_final = fake_export_final
            cli.cmd_build_analysis = lambda root, args: 0
            cli.cmd_report = lambda root, args: 0
            cli._local_run_input_error = lambda root: ""

            with tempfile.TemporaryDirectory() as tmp:
                code = cli.cmd_run_local(Path(tmp), argparse.Namespace(db="data/intermediate/edinet.db"))

            self.assertEqual(code, 0)
            self.assertEqual(calls, ["data/review/review_resolved.csv"])
        finally:
            for name, value in originals.items():
                setattr(cli, name, value)


if __name__ == "__main__":
    unittest.main()
