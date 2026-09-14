# OpenTrade

Paper-first multi-exchange scalping console with live charts, fee-aware signals,
multi-slot execution, backtesting, and streaming NVIDIA NIM analysis.

## Run

```sh
# API + built UI
python3 app.py

# DeepSeek Shell (available everywhere after installation)
dsh web
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

Hard rule: the expected 1-ATR move must clear `fee_edge_mult` × all-in friction
(fees + slippage + spread). The gate reads ATR directly, so raising `atr_tp_mult`
reaches for a bigger target without quietly loosening the entry filter.
Leverage amplifies equity fee drag; it does **not** reduce the required price move.

Reward:risk must stay asymmetric. A scalp whose target is pinned near the fee
floor while its stop sits at `stop_loss_pct` risks ~1:1, and the fee floor then
eats the whole expectancy. So:
- `atr_tp_mult` (default 3.0) sets the target in ATRs; `atr_sl_mult` (default .5)
  sizes the stop, floored at round-trip friction.
- `stop_loss_pct` is a **ceiling** on risk per trade, not a fixed stop.
- `take_profit_pct` only raises the target above the fee/min-gain floor.

Practical defaults:
- `fee_style=maker` (0.02% / leg). `taker` needs a thicker edge floor and far fewer trades.
- Stack: EMA 8/21 trend, VWAP location, RSI(4) timing, ATR/BB volatility, book imbalance, local chart-AI confirm (`ai_scan`).
- Cadence: `max_positions` concurrent paper slots + `auto_scan` across a volatile whitelist (not every major).
- Bank exits only when net PnL clears `min_gain_usd` after fees.

Measured across three separate ~16h windows of real 1m Binance data (8-symbol
whitelist, maker fees, 4-slot cap): ~450-620 fills/day and positive net in every
window. Most of the net came from the most volatile symbol; the majors hovered
near breakeven. That concentration, not the aggregate, is the risk to watch.

Do not read the backtest's `net_pnl` as a profit forecast. Position sizing is
all-in fixed-fractional (`position_pct` of cash x `leverage`) and compounding is
reinvested every trade, so a volatile name that catches a few wide bars prints
absurd returns (a top-volume micro-cap scored +4000 on a $10 book in ~16h). The
engine also fills stops at the exact stop price, so it does not model gaps — at
5x leverage one gap can exceed the margin. Judge a setting by win rate and
reward:risk, not by the compounded total.

There is no guaranteed profit or fixed daily trade count. Expectancy halt pauses
entries when the rolling average goes negative and lifts itself after
`halt_cooldown_sec` (a permanent halt previously stopped all trading). Validate
any strategy on untouched holdout data before enabling live mode.
