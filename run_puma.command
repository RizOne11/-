#!/bin/bash
cd "$(dirname "$0")"
python3 -m pip install -e . || exit 1
(sleep 2; open http://localhost:8501) &
python3 -m streamlit run src/puma_scouts/app.py --server.port 8501
