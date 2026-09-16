from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from puma_scouts.production import run_excel

st.set_page_config(page_title="PUMA", page_icon="🐆", layout="centered")
st.title("🐆 PUMA")
st.caption("Product Intelligence • Production v1.3")
st.write("Завантаж Excel з товарами, натисни **Аналізувати** — PUMA перевірить Prom, Epicentr, Hotline та незалежні українські магазини.")

uploaded = st.file_uploader("Excel з товарами", type=["xlsx"])

if uploaded:
    st.success(f"Файл готовий: {uploaded.name}")
    if st.button("🐆 Аналізувати", type="primary", use_container_width=True):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.xlsx"
            result = Path(td) / "PUMA_result.xlsx"
            source.write_bytes(uploaded.getvalue())
            with st.status("PUMA полює…", expanded=True) as status:
                st.write("Читаю товари з Excel")
                st.write("Шукаю та перевіряю пропозиції")
                try:
                    run_excel(source, result)
                except Exception as exc:
                    status.update(label="Помилка аналізу", state="error")
                    st.exception(exc)
                else:
                    status.update(label="Аналіз завершено", state="complete")
                    st.download_button(
                        "⬇️ Завантажити PUMA_result.xlsx",
                        data=result.read_bytes(),
                        file_name="PUMA_result.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
                    st.success("Готово 😈")

st.divider()
st.caption("Production sources: Prom • Epicentr • Hotline • WEB_SHOPS")
