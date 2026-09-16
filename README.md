# 🐆 PUMA — Product Intelligence

Production baseline: **Prom + Epicentr + Hotline + independent Ukrainian WEB_SHOPS**.

PUMA takes an Excel product list, searches for the same physical products, validates candidates, preserves accepted offer URLs/prices, and exports an Excel report with offer-level data and price groups.

## Найпростіший запуск

### Windows
1. Download/clone the repository.
2. Double-click `run_puma.bat`.
3. Browser opens PUMA at `http://localhost:8501`.
4. Upload `.xlsx` and press **🐆 Аналізувати**.
5. Download `PUMA_result.xlsx`.

### macOS
Run `run_puma.command` (first launch may require permission to execute).

### Manual
```bash
pip install -e .
streamlit run src/puma_scouts/app.py
```

## Excel input

The first worksheet is used. PUMA recognizes common Ukrainian/Russian/English column aliases for product name, article/SKU, brand and model. Product name is required; article is optional and will be generated when absent.

## Output

`Offers` — accepted marketplace/shop cards with product, marketplace, title, price, URL, match score and availability.

`Price summary` — product + marketplace + price → number of accepted cards.

## Matching rules

- User article is an internal immutable key and is **not** used as the marketplace search query.
- User price does not determine product identity.
- Missing attributes are not automatically contradictions.
- Explicit contradictory identity/specification data can reject a candidate.
- Prom supports relaxed descriptive matching when model fields are absent.

## Source state

- Production: Prom, Epicentr, Hotline, WEB_SHOPS.
- Rozetka: frozen.
- Allo / Comfy: paused.
- Foxtrot / Kasta / Zakupka: outside current production baseline.

Seasonality / demand scoring is the next additive layer and must not break the verified marketplace baseline.
