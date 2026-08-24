#!/usr/bin/env bash
# Cai dat dyflow
set -e

echo ">> Kiem tra ffmpeg..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "!! Chua co ffmpeg. Cai: sudo apt install ffmpeg  (hoac: brew install ffmpeg)"
  exit 1
fi
echo "   ffmpeg OK"

echo ">> Tao moi truong ao + cai thu vien..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo ">> Tao thu muc..."
mkdir -p work output assets/music assets/fonts assets/broll

echo ""
echo "Xong. Kich hoat moi truong:  source .venv/bin/activate"
echo "Chay thu kiem tra:           python -m dyflow.main --selftest -v"
echo "Chay that:                   python -m dyflow.main --jobs jobs.json -v"
echo ""
echo "Nho: bo 1 font Unicode dam vao assets/fonts/ va tro paths.font_path trong config.yaml"
