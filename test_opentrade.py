import unittest
from app import atr, backtest, book_stats, ema, optimize, rsi, signal, signal_series, sma, Binance, KuCoin, Mexc, Bot, fee_edge_ok, fee_floor_pct, chart_ai_analyze, chart_patterns, confluence_score, vwap_z

class OpenTradeTests(unittest.TestCase):
    def test_indicators(self):
        values = list(range(1, 40)); self.assertEqual(len(ema(values, 9)), len(values)); self.assertEqual(len(sma(values, 20)), len(values)); self.assertEqual(len(rsi(values)), len(values))
        candles = [[i, v, v + 1, v - 1, v, 10] for i, v in enumerate(values)]
        self.assertEqual(len(atr(candles)), len(candles))
    def test_signal_needs_history(self): self.assertEqual(signal([[0, 1, 1, 1, 1, 1]]), "HOLD")
    def test_signal_series_matches_latest_signal(self):
        candles = [[i, 100 + (i % 7), 101 + (i % 7), 99 + (i % 7), 100 + (i % 7), 10] for i in range(80)]
        self.assertEqual(signal_series(candles)[-1], signal(candles))
    def test_backtest_shape(self):
        candles = [[i, 100 + (i % 7), 101 + (i % 7), 99 + (i % 7), 100 + (i % 7), 10] for i in range(80)]
        result = backtest(candles); self.assertEqual(result["capital"], 10); self.assertIn("max_drawdown_pct", result); self.assertGreaterEqual(result["trades"], 0)

    def test_exchange_bases(self):
        self.assertEqual(Binance.base, "https://api.binance.com")
        self.assertEqual(KuCoin.base, "https://api.kucoin.com")
        self.assertEqual(Mexc.base, "https://api.mexc.com")

    def test_book_stats(self):
        stats = book_stats({"bids": [[100, 2]], "asks": [[100.1, 1]]})
        self.assertAlmostEqual(stats["spread_bps"], 9.995, places=2); self.assertAlmostEqual(stats["imbalance"], 1 / 3)

    def test_optimizer_reports_holdout(self):
        candles = [[i, 100 + (i % 7), 101 + (i % 7), 99 + (i % 7), 100 + (i % 7), 10] for i in range(120)]
        result = optimize(candles); self.assertIn("train", result); self.assertIn("holdout", result); self.assertIn("qualified", result)
    def test_optimizer_rejects_flat_tape(self):
        flat = [[i, 100, 100.01, 99.99, 100, 10] for i in range(300)]
        result = optimize(flat, {"fee_rate": .0002, "slippage_rate": .0001, "fee_style": "maker"})
        self.assertFalse(result["qualified"])
        self.assertTrue(result.get("settings") is None or result.get("settings") == {})
        self.assertIn("message", result)
        self.assertLessEqual(result["holdout"].get("net_pnl", 0), 0)

    def test_fee_edge_blocks_quiet_tape(self):
        flat = [[i, 100, 100.01, 99.99, 100, 10] for i in range(120)]
        s = {"fee_rate": .0002, "slippage_rate": .0001, "fee_edge_mult": 3, "atr_tp_mult": 1.0, "atr_period": 14}
        self.assertAlmostEqual(fee_floor_pct(s), 0.06, places=5)
        self.assertFalse(fee_edge_ok(flat, s))

    def test_scalp_risk_rewards_more_than_it_risks(self):
        import math
        from app import PRESETS, dynamic_risk
        s = dict(PRESETS["balanced"]["settings"])
        wave = lambda i: 100 * (1 + .005 * math.sin(i / 4))
        candles = [[i, wave(i), wave(i) + .3, wave(i) - .3, wave(i), 10] for i in range(80)]
        tp, sl = dynamic_risk(candles, s)
        self.assertGreaterEqual(tp, 2 * sl)              # ~1:1 risk cannot pay the fee floor
        self.assertLessEqual(sl, s["stop_loss_pct"])     # stop_loss_pct caps risk, never sets it
        self.assertGreaterEqual(sl, fee_floor_pct(s))    # stop still spans round-trip friction

    def test_entry_edge_gate_ignores_tp_multiple(self):
        volatile = [[i, 100, 100.4, 99.6, 100, 10] for i in range(120)]
        calm = [[i, 100, 100.02, 99.98, 100, 10] for i in range(120)]
        s = {"fee_rate": .0002, "slippage_rate": .0001, "fee_edge_mult": 3, "atr_period": 14}
        self.assertTrue(fee_edge_ok(volatile, s))        # 0.8% ATR clears 3 x 0.06% friction
        self.assertFalse(fee_edge_ok(calm, s))           # 0.04% ATR does not
        self.assertEqual(fee_edge_ok(calm, {**s, "atr_tp_mult": 10.0}), fee_edge_ok(calm, s))
        self.assertEqual(fee_edge_ok(volatile, {**s, "atr_tp_mult": 10.0}), fee_edge_ok(volatile, s))

    def test_chart_ai_and_patterns_shape(self):
        import math
        candles = []
        px = 100.0
        for i in range(160):
            px *= (1 + 0.005 * math.sin(i / 3))
            candles.append([i, px * 0.999, px * 1.008, px * 0.992, px, 40 + i % 7])
        s = {"fee_rate": .0002, "slippage_rate": .0001, "fee_edge_mult": 2, "atr_tp_mult": 1.2,
             "rsi_buy": 45, "rsi_sell": 55, "rsi_period": 4, "buy_score": 2, "sma_fast": 8, "sma_slow": 21,
             "vwap_period": 20, "bb_period": 20, "bb_std": 2, "volume_period": 20, "min_volume_ratio": .25}
        ai = chart_ai_analyze(candles, s)
        self.assertIn(ai["action"], ("BUY", "SELL", "HOLD"))
        self.assertEqual(ai["model"], "chart-ai-local")
        self.assertIn("patterns", ai)
        self.assertEqual(len(vwap_z(candles)), len(candles))
        self.assertIsInstance(confluence_score(candles, s), float)
        self.assertIsInstance(chart_patterns(candles, s)["score"], float)

    def test_open_close_increments_trades(self):
        bot = Bot()
        bot.settings["leverage"] = 5
        bot.settings["min_gain_usd"] = 0.5
        bot.settings["max_positions"] = 1
        bot.settings["fee_rate"] = .0002
        bot.slots = {}
        bot.state.cash, bot.state.coin, bot.state.entry, bot.state.trades, bot.state.wins = 10.0, 0.0, 0.0, 0, 0
        bot.entry_ts, bot.peak_price, bot.trade_pnls, bot.margin = 0.0, 0.0, [], 0.0
        self.assertTrue(bot.open_position(100.0))
        self.assertGreater(bot.state.coin, 0)
        self.assertGreater(bot.margin, 0)
        self.assertLess(bot.state.cash, 10.0)
        # 1% move on 5x notional (~$50) is about $0.50 before fees
        self.assertTrue(bot.close_position(101.0, reason="test"))
        self.assertEqual(bot.state.coin, 0.0)
        self.assertEqual(bot.state.trades, 1)
        self.assertGreater(bot.state.cash, 10.0)
        self.assertGreaterEqual(bot.state.pnl, 0.5 - 0.05)
        bot.state.cash, bot.state.coin, bot.state.entry, bot.state.trades, bot.state.wins = 10.0, 0.0, 0.0, 0, 0
        bot.entry_ts, bot.peak_price, bot.trade_pnls, bot.margin = 0.0, 0.0, [], 0.0
        bot._save_portfolio()


    def test_multi_slot_opens(self):
        bot = Bot()
        bot.settings.update({"max_positions": 3, "leverage": 2, "position_pct": 100, "fee_rate": .0002, "slippage_rate": 0})
        bot.slots = {}
        bot.state.cash, bot.state.coin, bot.state.trades = 30.0, 0.0, 0
        bot.trade_pnls, bot.margin = [], 0.0
        self.assertTrue(bot.open_position(100.0, symbol="AAAUSDT"))
        self.assertTrue(bot.open_position(100.0, symbol="BBBUSDT"))
        self.assertEqual(len(bot.slots), 2)
        self.assertTrue(bot.close_position(101.0, reason="test", symbol="AAAUSDT"))
        self.assertEqual(len(bot.slots), 1)
        self.assertEqual(bot.state.trades, 1)

    def test_aggregate_ohlcv(self):
        from app import aggregate_ohlcv, SUBMINUTE_SECONDS
        rows = []
        for i in range(60):
            rows.append([i * 1000, 100, 101, 99, 100.5, 1.0])
        out = aggregate_ohlcv(rows, 15)
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0][0], 0)
        self.assertEqual(out[0][5], 15.0)
        self.assertIn("15s", SUBMINUTE_SECONDS)

    def test_simulate_multi_slot_day(self):
        from app import simulate_multi_slot_day
        a = {"symbol": "AAAUSDT", "fills": [
            {"side": "BUY", "ts": 1, "price": 100, "quantity": 1, "margin": 2},
            {"side": "SELL", "ts": 2, "price": 101, "pnl": 0.5},
        ]}
        b = {"symbol": "BBBUSDT", "fills": [
            {"side": "BUY", "ts": 1.1, "price": 50, "quantity": 1, "margin": 2},
            {"side": "SELL", "ts": 3, "price": 51, "pnl": 0.2},
        ]}
        c = {"symbol": "CCCUSDT", "fills": [
            {"side": "BUY", "ts": 1.2, "price": 10, "quantity": 1, "margin": 2},
            {"side": "SELL", "ts": 4, "price": 11, "pnl": -0.1},
        ]}
        # max_positions=2: AAA@1 + BBB@1.1 fill slots; CCC@1.2 skipped
        r = simulate_multi_slot_day([a, b, c], max_positions=2, starting_cash=10)
        self.assertEqual(r["trades"], 2)
        self.assertAlmostEqual(r["net_pnl"], 0.7, places=6)


if __name__ == "__main__": unittest.main()
