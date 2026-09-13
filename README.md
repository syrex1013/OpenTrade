# OpenTrade

Paper-first multi-exchange scalping console with live charts, fee-aware signals,
multi-slot execution, backtesting, and streaming NVIDIA NIM analysis.

## Run

```sh
# API + built UI
python3 app.py
```

Open http://127.0.0.1:8765.

## Frontend (Vite + React + Tailwind + Lightweight Charts)

```sh
cd frontend
npm install
npm run build   # writes to web/dist
# live reload:
npm run dev     # starts API and Vite together
```

Public market data works without credentials. NVIDIA analysis requires
`NVIDIA_API_KEY`; Binance live orders require `BINANCE_API_KEY` and
`BINANCE_API_SECRET`. Keep credentials in `.env`, never commit them.

## Checks

```sh
python3 -m unittest -q
```

The strategy is experimental. It does not guarantee profit. Do not enable live trading without independent testing, conservative limits, and exchange-key withdrawal permissions disabled.

## Cadence (sub-minute + multi-slot)

- Intervals include synthetic **15s/30s** (Binance spot 1s klines aggregated).
  The **`hf_scalp`** preset uses 15s bars for higher turnover.
- `max_positions` runs concurrent paper/live slots across the whitelist (hundreds of fills/day are only realistic on volatile keepers, not quiet majors).
- LIVE mode sends **HMAC USDT-M futures** orders (`GTX` post-only when `live_post_only` + maker). Requires `BINANCE_API_KEY` / `BINANCE_API_SECRET`. Unfilled post-only orders are canceled; local book updates only after a fill.

## Fee-aware scalping

Hard rule: expected ATR move must clear `fee_edge_mult` × all-in friction (fees + slippage + spread). Leverage amplifies equity fee drag; it does **not** reduce the required price move.

Practical defaults that stayed +EV on holdouts:
- `fee_style=maker` (0.02% / leg). `taker` needs a thicker edge floor and far fewer trades.
- Stack: EMA 8/21 trend, VWAP location, RSI(4) timing, ATR/BB volatility, book imbalance, local chart-AI confirm (`ai_scan`).
- Cadence: `max_positions` concurrent paper slots + `auto_scan` across a volatile whitelist (not every major).
- Size risk from the stop; bank exits only when net PnL clears `min_gain_usd` after fees.

There is no guaranteed profit or fixed daily trade count. Expectancy halt pauses
when the rolling average goes negative; validate any strategy on untouched
holdout data before enabling live mode.
