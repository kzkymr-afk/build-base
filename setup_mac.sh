#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。macOSにPython 3を入れてから再実行してください。"
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
. ".venv/bin/activate"

python -m pip install -U pip
python -m pip install -e ".[dev]"

if [ ! -f ".env" ]; then
  cp ".env.example" ".env"
  echo ".env を作成しました。新しくEDINETから取得する場合だけ EDINET_API_KEY を入力してください。"
fi

python -m yuho_auto_extract init-xlsx
python -m unittest discover -s tests

echo ""
echo "セットアップ完了。完成表まで更新する場合:"
echo "  ./yuho"
echo ""
echo "主な出力:"
echo "  data/final/final_master_wide.xlsx"
echo "  data/final/analysis_dataset.xlsx"
echo "  data/review/review_queue_local_needs_manual.xlsx"
