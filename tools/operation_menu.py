from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.chdir(ROOT)
    os.environ.setdefault("PYTHONPATH", "src")
    os.environ.setdefault("PYTHONPYCACHEPREFIX", str(Path(tempfile.gettempdir()) / "yuho-pycache"))
    load_env_file(ROOT / ".env")

    while True:
        print_menu()
        choice = input("番号を入力: ").strip()
        if choice in {"0", "q", "quit", "exit"}:
            print("終了します。")
            return 0
        try:
            code = handle(choice)
        except KeyboardInterrupt:
            print("\n中断しました。")
            return 130
        except Exception as exc:
            print(f"\nエラー: {exc}")
            code = 1
        if code:
            print(f"\n処理が失敗しました。exit={code}")
        input("\nEnterでメニューに戻る: ")


def print_menu() -> None:
    print("")
    print("有報自動抽出 操作メニュー")
    print("=" * 32)
    print("通常はメニュー不要です。ターミナルで ./yuho だけ実行すると完成表まで更新します。")
    print("")
    print("1. 初期チェック")
    print("2. 完成表まで一括実行")
    print("3. 設定Excelを作る")
    print("4. テストを実行")
    print("5. EDINET文書一覧を取得（過去10年標準）")
    print("6. 対象有報を解決")
    print("7. 文書をダウンロード")
    print("8. EDINET DBを作る")
    print("9. DBから抽出してレビューキューを作る")
    print("10. レビュー反映後の完成表を作る")
    print("11. 分析表と実行レポートを作る")
    print("0. 終了")
    print("")


def handle(choice: str) -> int:
    if choice == "1":
        return check_setup()
    if choice == "2":
        return run_cli(["run-all"])
    if choice == "3":
        return run_cli(["init-xlsx"])
    if choice == "4":
        return run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    if choice == "5":
        years = prompt("対象年度。空白区切り", "2015 2016 2017 2018 2019 2020 2021 2022 2023 2024").split()
        return run_cli(["index-annual", "--fiscal-years", *years])
    if choice == "6":
        years = prompt("対象年度。空白区切り", "2015 2016 2017 2018 2019 2020 2021 2022 2023 2024").split()
        return run_cli(["resolve", "--fiscal-years", *years])
    if choice == "7":
        return run_cli(["download", "--target-documents", "data/intermediate/target_documents.parquet"])
    if choice == "8":
        return run_cli(["build-edinet-db"])
    if choice == "9":
        return run_many([["extract-from-db"], ["normalize"], ["validate"], ["build-review-queue"], ["split-local-review"]])
    if choice == "10":
        reviewed = prompt("レビュー済みファイル", "data/review/review_resolved_local_pass.xlsx")
        return run_cli(["export-final", "--reviewed", reviewed])
    if choice == "11":
        return run_many([["build-analysis"], ["report"]])
    print("未対応の番号です。")
    return 1


def check_setup() -> int:
    print("")
    print(f"プロジェクト: {ROOT}")
    print(f"Python: {sys.executable}")
    env_path = find_env_file()
    print(f".env: {'あり' if env_path and env_path.name == '.env' else 'なし'}")
    if env_path and env_path.name != ".env":
        print(f".env候補: {env_path.name}")
    print(f".venv: {'あり' if (ROOT / '.venv').exists() else 'なし'}")

    env_warnings = []
    if not os.getenv("EDINET_API_KEY"):
        env_warnings.append("EDINET_API_KEY が現在の環境に見えません。.env作成後、source .venv/bin/activate か環境読み込みが必要です。")

    company_path = ROOT / "config" / "company_master.csv"
    fake_codes = []
    if company_path.exists():
        with company_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("edinet_code") == "E00000":
                    fake_codes.append(row.get("operating_company_id", ""))
    if fake_codes:
        env_warnings.append("company_master.csv に仮EDINETコードがあります: " + ", ".join(fake_codes))

    if env_warnings:
        print("")
        print("要確認:")
        for warning in env_warnings:
            print(f"- {warning}")
    else:
        print("")
        print("初期チェックはOKです。")
    return 0


def load_env_file(path: Path) -> None:
    env_path = path if path.exists() else find_env_file()
    if not env_path:
        return
    with env_path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def find_env_file() -> Optional[Path]:
    for name in [".env", ".env.txt", "env.txt"]:
        path = ROOT / name
        if path.exists():
            return path
    return None


def run_many(commands: List[List[str]]) -> int:
    for command in commands:
        code = run_cli(command)
        if code:
            return code
    return 0


def run_cli(args: List[str]) -> int:
    return run([sys.executable, "-m", "yuho_auto_extract", *args])


def run(command: List[str]) -> int:
    print("")
    print("$ " + " ".join(command))
    return subprocess.call(command, cwd=ROOT, env=os.environ.copy())


def prompt(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


if __name__ == "__main__":
    raise SystemExit(main())
