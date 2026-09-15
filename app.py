#!/usr/bin/env python3
"""Small, paper-first Binance spot trading console."""
from __future__ import annotations

import hashlib, hmac, json, os, queue, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
DB = ROOT / "opentrade.sqlite3"
WEB = ROOT / "web"; DIST = WEB / "dist"


def load_dotenv(path=ROOT / ".env"):
    if not path.exists(): return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1); k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

load_dotenv()


def ema(values, period):
    if not values: return []
    k, out = 2 / (period + 1), [values[0]]
    for value in values[1:]: out.append(value * k + out[-1] * (1 - k))
    return out


def sma(values, period):
    return [sum(values[max(0, i - period + 1):i + 1]) / min(i + 1, period) for i in range(len(values))]


def rolling_std(values, period):
    out = []
    for i in range(len(values)):
        window = values[max(0, i - period + 1):i + 1]; mean = sum(window) / len(window)
        out.append((sum((x - mean) ** 2 for x in window) / len(window)) ** .5)
    return out


def vwap(candles, period=20):
    out = []
    for i in range(len(candles)):
        rows = candles[max(0, i - period + 1):i + 1]; volume = sum(x[5] for x in rows)
        out.append(sum(((x[2] + x[3] + x[4]) / 3) * x[5] for x in rows) / volume if volume else rows[-1][4])
    return out


def rsi(values, period=14):
    if len(values) < 2: return [50.0] * len(values)
    gains, losses, out = [], [], [50.0]
    for a, b in zip(values, values[1:]):
        gains.append(max(b - a, 0)); losses.append(max(a - b, 0))
        g, l = sum(gains[-period:]) / period, sum(losses[-period:]) / period
        out.append(100 if not l else 100 - 100 / (1 + g / l))
    return out


def atr(candles, period=14):
    if not candles: return []
    tr = [c[2] - c[3] for c in candles]
    return [sum(tr[max(0, i - period + 1):i + 1]) / min(i + 1, period) for i in range(len(tr))]


def macd(values, fast=12, slow=26, signal_period=9):
    if not values: return [], [], []
    f, s = ema(values, fast), ema(values, slow)
    line = [a - b for a, b in zip(f, s)]
    sig = ema(line, signal_period)
    return line, sig, [a - b for a, b in zip(line, sig)]


def vwap_z(candles, period=20):
    """Signed z-score of close vs rolling VWAP (typical-price std)."""
    out, vw = [], vwap(candles, period)
    for i, c in enumerate(candles):
        rows = candles[max(0, i - period + 1):i + 1]
        typical = [(x[2] + x[3] + x[4]) / 3 for x in rows]
        mean = sum(typical) / len(typical)
        var = sum((x - mean) ** 2 for x in typical) / len(typical)
        std = var ** .5
        out.append((c[4] - vw[i]) / std if std > 1e-12 else 0.0)
    return out


def bb_bandwidth(closes, period=20, std_mult=2.0):
    mid, std = sma(closes, period), rolling_std(closes, period)
    return [((2 * std_mult * std[i]) / mid[i] * 100) if mid[i] else 0.0 for i in range(len(closes))]


def book_stats(rows):
    bid, ask = rows["bids"][0], rows["asks"][0]; bid_price, ask_price = float(bid[0]), float(ask[0])
    total = float(bid[1]) + float(ask[1])
    return {"bid": bid_price, "ask": ask_price, "spread_bps": (ask_price - bid_price) / ((ask_price + bid_price) / 2) * 10000, "imbalance": (float(bid[1]) - float(ask[1])) / total if total else 0}


def aggregate_ohlcv(rows, bucket_sec):
    """Bucket 1s (or finer) OHLCV rows into synthetic bars for sub-minute scalping."""
    if not rows or bucket_sec <= 0:
        return list(rows or [])
    bucket_ms = int(bucket_sec * 1000)
    out, cur = [], None
    for row in rows:
        ts, o, h, l, c, v = int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
        b = (ts // bucket_ms) * bucket_ms
        if cur is None or cur[0] != b:
            if cur is not None:
                out.append(cur)
            cur = [b, o, h, l, c, v]
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[5] += v
    if cur is not None:
        out.append(cur)
    return out


SUBMINUTE_SECONDS = {"15s": 15, "30s": 30}


def signal(candles, settings=None):
    return signal_series(candles, settings)[-1] if candles else "HOLD"


def fee_floor_pct(settings):
    """Round-trip friction % of price (fees + slippage both legs)."""
    return (2 * float(settings.get("fee_rate", .0002)) + 2 * float(settings.get("slippage_rate", .0001))) * 100


def all_in_friction_pct(settings, spread_bps=0.0):
    return fee_floor_pct(settings) + max(0.0, float(spread_bps or 0)) / 100.0


def fee_edge_mult_of(settings):
    return max(0.0, float((settings or {}).get("fee_edge_mult", 2) or 0))


def expected_edge_pct(candles, settings, i=None):
    settings = settings or {}
    if not candles: return 0.0
    i = len(candles) - 1 if i is None else i
    price = candles[i][4] or 1.0
    atr_pct = (atr(candles[max(0, i - 80):i + 1], int(settings.get("atr_period", 14)))[-1] / price) * 100
    # One ATR is the expected next-bar move. Deliberately NOT scaled by atr_tp_mult:
    # the profit target may reach further, but the entry edge gate must stay honest.
    return atr_pct


def fee_edge_ok(candles, settings, i=None, spread_bps=0.0):
    """HARD: expected move must clear fee_edge_mult × all-in friction."""
    settings = settings or {}
    mult = fee_edge_mult_of(settings)
    if mult <= 0: return True
    return expected_edge_pct(candles, settings, i) >= mult * all_in_friction_pct(settings, spread_bps)


def leverage_of(settings):
    return max(1.0, float((settings or {}).get("leverage", 1) or 1))


def min_gain_usd(settings):
    # Floor at 0; live/backtest exits still require strictly > configured min.
    return max(0.0, float((settings or {}).get("min_gain_usd", .1) or 0))


# Stored settings whose former default was superseded by the corrected risk geometry.
# Only a stored value that still equals the old default is upgraded (see _load_settings).
SUPERSEDED_DEFAULTS = {"atr_tp_mult": (1.2, 3.0), "atr_sl_mult": (.7, .5), "stop_loss_pct": (.25, .2)}

DEFAULT_GATES = {
    "atr": True, "trend": True, "volume": True, "rsi": True, "rsi2": True,
    "vwap": True, "bb": True, "setup": True, "score": True,
    "spread": True, "imbalance": True, "min_gain": True, "halt": True,
    "fee_edge": True, "pattern": True, "ema_cross": True, "breakout": True, "ai": True,
}


def gates_of(settings):
    g = dict(DEFAULT_GATES)
    raw = (settings or {}).get("gates") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in g:
                g[k] = bool(v.get("enabled", v) if isinstance(v, dict) else v)
    return g


def gate_on(settings, name):
    return bool(gates_of(settings).get(name, True))


PRESETS = {
    "balanced": {
        "label": "Balanced",
        "settings": {
            "interval": "1m", "poll_seconds": 2, "position_pct": 100, "leverage": 5, "min_gain_usd": .1,
            "fee_rate": .0002, "slippage_rate": .0001,
            "take_profit_pct": .35, "stop_loss_pct": .2, "min_profit_pct": .12, "max_hold_bars": 6, "trail_pct": .15,
            "max_spread_bps": 12, "min_book_imbalance": .02, "rsi_buy": 45, "rsi_sell": 50, "rsi_period": 4,
            "rsi2_buy": -1, "rsi2_sell": 70, "sma_fast": 8, "sma_slow": 21, "vwap_period": 20, "bb_period": 20, "bb_std": 2,
            "volume_period": 20, "min_volume_ratio": .25, "vwap_distance_pct": .05, "cooldown_bars": 1, "buy_score": 2,
            "min_atr_pct": .05, "trend_tolerance_pct": 1.1, "atr_period": 14, "atr_tp_mult": 3.0, "atr_sl_mult": .5,
            "min_entry_score": 2, "expectancy_window": 40, "expectancy_halt": True, "auto_scan": True,
            "fee_edge_mult": 2, "atr_fee_lambda": 1.0, "vwap_z_entry": 0, "fee_style": "maker", "max_positions": 2, "ai_scan": False,
            "gates": dict(DEFAULT_GATES),
        },
    },
    "hf_scalp": {
        "label": "HF Scalp",
        # Cadence-oriented +EV: fee_edge_mult=3, ATR TP 1.2× — live sweep kept INJ-like names +EV after fees.
        # Looser entries / extreme leverage destroy expectancy under maker-like fees.
        "settings": {
            "interval": "15s", "poll_seconds": 1, "position_pct": 100, "leverage": 5, "min_gain_usd": .1,
            "fee_rate": .0002, "slippage_rate": .0001, "fee_style": "maker", "live_post_only": True,
            "take_profit_pct": .35, "stop_loss_pct": .2, "min_profit_pct": .12, "max_hold_bars": 5, "trail_pct": .15,
            "max_spread_bps": 12, "min_book_imbalance": .02, "rsi_buy": 45, "rsi_sell": 50, "rsi_period": 4,
            "rsi2_buy": -1, "rsi2_sell": 70, "sma_fast": 8, "sma_slow": 21, "vwap_period": 20, "bb_period": 20, "bb_std": 2,
            "volume_period": 20, "min_volume_ratio": .25, "vwap_distance_pct": .05, "cooldown_bars": 0, "buy_score": 2,
            "min_atr_pct": .05, "trend_tolerance_pct": 1.1, "atr_period": 14, "atr_tp_mult": 3.0, "atr_sl_mult": .5,
            "min_entry_score": 2, "expectancy_window": 40, "expectancy_halt": True, "auto_scan": True,
            "fee_edge_mult": 3, "atr_fee_lambda": 1.0, "vwap_z_entry": 0, "ai_scan": True, "max_positions": 4,
            "scan_symbols": 12, "scan_seconds": 5,
            "scan_whitelist": ["INJUSDT", "SOPHUSDT", "REZUSDT", "THEUSDT", "HOLOUSDT", "UNIUSDT", "PYTHUSDT", "SUIUSDT"],
            "gates": dict(DEFAULT_GATES),
        },
    },
}




def min_tp_pct_for_gain(entry, qty, settings, min_gain=None):
    """Price move % needed so exit PnL after fees is > min_gain_usd (or explicit floor)."""
    settings = settings or {}
    notional = max(1e-9, float(entry) * float(qty))
    fee = float(settings.get("fee_rate", .0002)) + float(settings.get("slippage_rate", .0001))
    # Both legs pay `fee` on their own notional.
    # pnl = notional*(r - fee*(2+r)) > min_gain  =>  r > (min_gain/notional + 2*fee) / (1-fee)
    target = (min_gain_usd(settings) if min_gain is None else max(0.0, float(min_gain))) * 1.001
    r = (target / notional + 2 * fee) / max(1e-12, 1 - fee)
    return r * 100


def estimated_pnl(entry, exit_px, qty, settings, include_entry_fee=False):
    """Futures-style PnL on notional qty; fees charged on notional."""
    settings = settings or {}
    fee = float(settings.get("fee_rate", .0002)) + float(settings.get("slippage_rate", .0001))
    notional_exit = float(qty) * float(exit_px)
    pnl = (float(exit_px) - float(entry)) * float(qty) - notional_exit * fee
    if include_entry_fee:
        pnl -= float(qty) * float(entry) * fee
    return pnl


def net_pnl(entry, exit_px, qty, settings):
    """Realized PnL after fees on both legs — the number every stored trade uses."""
    return estimated_pnl(entry, exit_px, qty, settings, include_entry_fee=True)

def dynamic_risk(candles, settings, i=None, entry=None, qty=None):
    """ATR + fee-edge + min-gain sized TP/SL. take_profit_pct is a soft target."""
    settings = settings or {}
    if not candles: return float(settings.get("take_profit_pct", .35)), float(settings.get("stop_loss_pct", .22))
    i = len(candles) - 1 if i is None else i
    price = candles[i][4] or 1.0
    window = candles[max(0, i - 80):i + 1]
    atr_pct = (atr(window, int(settings.get("atr_period", 14)))[-1] / price) * 100
    floor = fee_floor_pct(settings)
    user_tp = float(settings.get("take_profit_pct", .35))
    # Size qty for min-gain floor
    if entry and qty:
        gain_tp = min_tp_pct_for_gain(entry, qty, settings)
    else:
        lev = leverage_of(settings)
        margin = 10.0 * float(settings.get("position_pct", 100)) / 100
        notional = max(margin * lev, 1e-9)
        gain_tp = min_tp_pct_for_gain(price, notional / price, settings)
    atr_tp = atr_pct * float(settings.get("atr_tp_mult", 3.0))
    # Binding floor = fees / ATR / $min-gain. take_profit_pct raises the target, never lowers it.
    edge_floor = floor * max(2.0, fee_edge_mult_of(settings))
    base = max(edge_floor, floor + .08, atr_tp, gain_tp)
    tp = max(base, user_tp)
    # Risk is ATR-sized, floored at round-trip friction and capped by the stop_loss_pct
    # ceiling; tp/2 keeps reward at least twice the risk even if the ATR multiples are
    # misconfigured. Old `max(stop_loss_pct, ...)` made the user ceiling a *floor*, pinning
    # SL to the TP and flattening reward:risk to ~1:1 — the fee floor then ate expectancy.
    risk = max(floor, atr_pct * float(settings.get("atr_sl_mult", .5)))
    sl = min(float(settings.get("stop_loss_pct", .2)), risk, tp / 2)
    return tp, sl


def adapt_settings(candles, settings=None):
    """Live-adaptive thresholds from recent volatility regime."""
    s = dict(settings or {})
    if not candles or len(candles) < 40: return s
    closes = [c[4] for c in candles]
    atr_pct = atr(candles, int(s.get("atr_period", 14)))[-1] / (closes[-1] or 1) * 100
    floor = fee_floor_pct(s)
    # Fee-aware ATR gate: one ATR must clear fee_edge_mult x round-trip friction.
    base_min = float(s.get("min_atr_pct", .05))
    edge_need = floor * max(1.0, fee_edge_mult_of(s))
    s["min_atr_pct"] = max(base_min, floor * float(s.get("atr_fee_lambda", 1.0)), edge_need * .5)
    # Taker scalps need a thicker cushion — leverage multiplies equity fee drag.
    if str(s.get("fee_style", "maker")).lower() == "taker":
        s["fee_edge_mult"] = max(float(s.get("fee_edge_mult", 3) or 3), 4.0)
        s["atr_tp_mult"] = max(float(s.get("atr_tp_mult", 3.0) or 3.0), 3.0)
    # Sub-minute bars: keep wall-clock hold and stop/target distance similar, but ATR% is
    # smaller per bar — scale the ATR multiples and lower the dead-tape floor.
    bar_sec = INTERVAL_SECONDS.get(str(s.get("interval", "1m")), 60)
    if bar_sec < 60:
        scale = max(1, int(round(60 / bar_sec)))
        rt = scale ** 0.5
        s["max_hold_bars"] = int(s.get("max_hold_bars", 5)) * scale
        s["cooldown_bars"] = int(s.get("cooldown_bars", 0)) * scale
        s["min_atr_pct"] = max(0.01, float(s.get("min_atr_pct", 0.05)) / scale)
        # Need more ATR multiples on tiny bars to clear the same fee floor (same wall-clock move).
        s["atr_tp_mult"] = max(float(s.get("atr_tp_mult", 3.0) or 3.0), 3.0 * rt)
        s["atr_sl_mult"] = max(float(s.get("atr_sl_mult", .5) or .5), .5 * rt)
        # Do NOT loosen RSI/score here — short bars are noisier and went −EV when relaxed.
        # Demand a thicker fee multiple instead; 1m remains the +EV default interval.
        s["fee_edge_mult"] = max(float(s.get("fee_edge_mult", 3) or 3), 4.0)
    if atr_pct < .08:
        # Quiet tape: slightly pickier RSI, but don't choke VWAP entries.
        s["rsi_buy"] = min(float(s.get("rsi_buy", 45)), 44)
        s["vwap_distance_pct"] = max(float(s.get("vwap_distance_pct", .05)), .05)
    elif atr_pct > .25:
        s["rsi_buy"] = max(float(s.get("rsi_buy", 45)), 48)
        s["vwap_distance_pct"] = min(float(s.get("vwap_distance_pct", .05)), .045)
        s["trend_tolerance_pct"] = max(float(s.get("trend_tolerance_pct", 1.1)), 1.3)
    tp, sl = dynamic_risk(candles, s)
    s["take_profit_pct"], s["stop_loss_pct"] = tp, sl
    s["min_profit_pct"] = min(float(s.get("min_profit_pct", .18)), tp * .4)
    s["atr_pct"] = atr_pct
    s["fee_floor_pct"] = floor
    s["edge_need_pct"] = floor * max(2.0, fee_edge_mult_of(s))
    return s


def chart_patterns(candles, settings=None, i=None):
    """Scalp patterns: VWAP reclaim, micro pullback, BB squeeze, pin/hammer, breakout."""
    settings = settings or {}
    closes = [c[4] for c in candles]; n = len(closes)
    if n < 55: return {"patterns": [], "score": 0.0, "bias": "HOLD", "vol_ratio": 0.0}
    i = n - 1 if i is None else i
    bb_p = int(settings.get("bb_period", 20)); bb_std = float(settings.get("bb_std", 2))
    vw = vwap(candles, int(settings.get("vwap_period", 20)))
    mid = sma(closes, bb_p); std = rolling_std(closes, bb_p)
    bw = bb_bandwidth(closes, bb_p, bb_std)
    fast = ema(closes, int(settings.get("sma_fast", 8)))
    slow = ema(closes, int(settings.get("sma_slow", 21)))
    volume = [x[5] for x in candles]
    vol_period = max(2, int(settings.get("volume_period", 20)))
    avg_vol = sum(volume[max(0, i - vol_period):i + 1]) / min(i + 1, vol_period) or 1.0
    vol_ratio = volume[i] / avg_vol
    o, h, l, c = candles[i][1], candles[i][2], candles[i][3], candles[i][4]
    body = abs(c - o); wick_low = min(o, c) - l; range_ = max(h - l, 1e-12)
    patterns, score = [], 0.0
    if closes[i - 1] < vw[i - 1] and c >= vw[i] and vol_ratio >= 1.2:
        patterns.append("vwap_reclaim"); score += 3.0
    uptrend = fast[i] >= slow[i] and closes[i] >= slow[i]
    near_support = c <= vw[i] * 1.001 or c <= fast[i] * 1.002
    if uptrend and near_support and c >= o:
        if wick_low >= body * .6 or (closes[i - 1] < closes[i - 2] and c > closes[i - 1]):
            patterns.append("micro_pullback"); score += 2.5
    look = bw[max(0, i - 20):i]
    squeeze = bool(look) and bw[i - 1] <= min(look) * 1.05
    upper = mid[i] + bb_std * std[i]
    if squeeze and c > upper and vol_ratio >= float(settings.get("breakout_volume_ratio", 1.1)):
        patterns.append("bb_squeeze_break"); score += 3.5
    if wick_low >= body * 1.5 and (c - l) / range_ > .6 and c <= vw[i]:
        patterns.append("pin_hammer"); score += 2.0
    br_p = int(settings.get("breakout_period", 5)); br_bps = float(settings.get("breakout_bps", 1))
    if i >= br_p:
        prior_high = max(x[2] for x in candles[i - br_p:i])
        if c >= prior_high * (1 + br_bps / 10000) and vol_ratio >= float(settings.get("breakout_volume_ratio", 1.1)) and uptrend:
            patterns.append("breakout"); score += 2.5
    bias = "BUY" if score >= 2.0 and patterns else ("SELL" if c < slow[i] and c < vw[i] and vol_ratio > 1.3 else "HOLD")
    return {"patterns": patterns, "score": round(score, 2), "bias": bias, "vol_ratio": round(vol_ratio, 3)}


def confluence_score(candles, settings=None, i=None):
    """Independent bullish factors for buy_score gate."""
    settings = settings or {}
    closes = [c[4] for c in candles]; n = len(closes)
    if n < 50: return 0.0
    i = n - 1 if i is None else i
    rsi_p = int(settings.get("rsi_period", 4)); bb_p = int(settings.get("bb_period", 20))
    rr = rsi(closes, rsi_p); rr2 = rsi(closes, 2)
    vw = vwap(candles, int(settings.get("vwap_period", 20)))
    vz = vwap_z(candles, int(settings.get("vwap_period", 20)))
    mid = sma(closes, bb_p); std = rolling_std(closes, bb_p)
    fast = ema(closes, int(settings.get("sma_fast", 8)))
    slow = ema(closes, int(settings.get("sma_slow", 21)))
    volume = [x[5] for x in candles]
    vol_period = max(2, int(settings.get("volume_period", 20)))
    avg_vol = sum(volume[max(0, i - vol_period):i + 1]) / min(i + 1, vol_period) or 1.0
    bb_std = float(settings.get("bb_std", 2)); lower = mid[i] - bb_std * std[i]
    score = 0.0
    if fast[i] >= slow[i]: score += 1.0
    if closes[i] >= slow[i] * (1 - float(settings.get("trend_tolerance_pct", 1.1)) / 100): score += .5
    if closes[i] <= vw[i]: score += 1.0
    if float(settings.get("vwap_z_entry", 0) or 0) > 0 and vz[i] <= -float(settings.get("vwap_z_entry", 1.5)): score += 1.5
    if rr[i] <= float(settings.get("rsi_buy", 45)): score += 1.0
    if rr[i] > rr[i - 1]: score += .5
    if float(settings.get("rsi2_buy", -1)) >= 0 and rr2[i] <= float(settings.get("rsi2_buy", 12)): score += 1.0
    if closes[i] <= lower: score += 1.5
    if volume[i] / avg_vol >= float(settings.get("min_volume_ratio", .25)): score += .5
    macd_line, macd_sig, _ = macd(closes)
    if macd_line[i] > macd_sig[i] and macd_line[i] > macd_line[i - 1]: score += .5
    pats = chart_patterns(candles, settings, i)
    score += min(3.0, pats["score"] * .4)
    return round(score, 3)


def chart_ai_analyze(candles, settings=None, book=None):
    """Local chart AI (no API key): confluence + patterns → BUY/SELL/HOLD JSON."""
    settings = adapt_settings(candles, settings or {})
    if not candles or len(candles) < 50:
        return {"action": "HOLD", "confidence": 0.0, "rationale": "insufficient history", "patterns": [],
                "score": 0, "model": "chart-ai-local", "risks": "warmup", "raw": ""}
    i = len(candles) - 1
    pats = chart_patterns(candles, settings, i)
    conf = confluence_score(candles, settings, i)
    need = float(settings.get("buy_score", 2))
    edge = fee_edge_ok(candles, settings, i, (book or {}).get("spread_bps", 0))
    spread_ok = True
    if book and gate_on(settings, "spread"):
        spread_ok = float(book.get("spread_bps") or 0) <= float(settings.get("max_spread_bps", 12))
    action, confidence = "HOLD", min(0.95, conf / max(need * 1.5, 1))
    parts = []
    if pats["patterns"]: parts.append("patterns=" + ",".join(pats["patterns"]))
    parts += [f"confluence={conf:.2f}/{need:.2f}", f"fee_edge={'ok' if edge else 'fail'}"]
    if conf >= need and edge and spread_ok and (pats["bias"] == "BUY" or conf >= need + 1):
        action = "BUY"; confidence = min(0.98, .45 + conf / 10 + len(pats["patterns"]) * .08); parts.append("buy_low_setup")
    elif pats["bias"] == "SELL" or conf < need * .4:
        rr = rsi([c[4] for c in candles], int(settings.get("rsi_period", 4)))
        if rr[i] >= float(settings.get("rsi_sell", 50)):
            action, confidence = "SELL", min(0.9, .4 + (rr[i] - 50) / 100); parts.append("sell_high_momentum")
    tp, sl = dynamic_risk(candles, settings); price = candles[i][4]
    return {"action": action, "confidence": round(confidence, 3), "entry": price,
            "stop": round(price * (1 - sl / 100), 8), "take_profit": round(price * (1 + tp / 100), 8),
            "rationale": "; ".join(parts),
            "risks": "Fees on notional; leverage amplifies equity fee drag; no guaranteed profit.",
            "patterns": pats["patterns"], "score": conf, "pattern_score": pats["score"],
            "fee_edge_ok": edge, "model": "chart-ai-local", "raw": ""}


def entry_score(candles, settings=None):
    """Rank BUY quality across symbols for multi-pair rotation."""
    settings = adapt_settings(candles, settings)
    if len(candles) < 50: return -1e9
    if float(settings.get("atr_pct", 0)) < float(settings.get("min_atr_pct", .05)): return -1e9
    if not fee_edge_ok(candles, settings): return -1e9
    conf = confluence_score(candles, settings)
    pats = chart_patterns(candles, settings)
    vz = vwap_z(candles, int(settings.get("vwap_period", 20)))[-1]
    return conf * 2 + pats["score"] + max(0, -vz) + float(settings.get("atr_pct", 0))

def signal_series(candles, settings=None):
    """Fee-aware VWAP/BB/EMA scalp + chart patterns + breakout; buy_score confluence.

    Vectorized-hoisted: all indicator series are computed once, pattern/confluence
    checks are O(1) per bar (identical semantics to chart_patterns/confluence_score).
    """
    settings = adapt_settings(candles, settings); closes = [c[4] for c in candles]; n = len(closes)
    if n < 50: return ["HOLD"] * n
    rsi_p = int(settings.get("rsi_period", 4)); bb_p = int(settings.get("bb_period", 20))
    rr = rsi(closes, rsi_p); rr2 = rsi(closes, 2)
    mid = sma(closes, bb_p); std = rolling_std(closes, bb_p)
    fast = ema(closes, int(settings.get("sma_fast", 8)))
    slow = ema(closes, int(settings.get("sma_slow", 21)))
    ranges = atr(candles, int(settings.get("atr_period", 14)))
    opens = [x[1] for x in candles]; highs = [x[2] for x in candles]; lows = [x[3] for x in candles]
    volume = [x[5] for x in candles]
    vw = vwap(candles, int(settings.get("vwap_period", 20)))
    vz = vwap_z(candles, int(settings.get("vwap_period", 20)))
    bb_std = float(settings.get("bb_std", 2))
    bw = bb_bandwidth(closes, bb_p, bb_std)
    macd_line, macd_sig, _ = macd(closes)
    actions = ["HOLD"] * n
    rsi_buy = float(settings.get("rsi_buy", 45)); rsi_sell = float(settings.get("rsi_sell", 55))
    rsi2_buy = float(settings.get("rsi2_buy", 12)); rsi2_sell = float(settings.get("rsi2_sell", 70))
    min_atr = float(settings.get("min_atr_pct", .05)); min_vol = float(settings.get("min_volume_ratio", .25))
    tol = float(settings.get("trend_tolerance_pct", 1.1))
    vwap_dist = float(settings.get("vwap_distance_pct", .08))
    z_need = float(settings.get("vwap_z_entry", 0) or 0)
    buy_need = float(settings.get("buy_score", 2))
    cooldown = int(settings.get("cooldown_bars", 1)); last_buy = -10**9
    br_p = int(settings.get("breakout_period", 5)); br_bps = float(settings.get("breakout_bps", 1))
    br_vol = float(settings.get("breakout_volume_ratio", 1.1))
    vol_period = max(2, int(settings.get("volume_period", 20)))
    floor = fee_floor_pct(settings); edge_mult = fee_edge_mult_of(settings)
    g = gates_of(settings)
    for i in range(50, n):
        c = closes[i]; o = opens[i]; h = highs[i]; l = lows[i]
        atr_pct = ranges[i] / c * 100 if c else 0
        lower = mid[i] - bb_std * std[i]
        avg_volume = sum(volume[max(0, i - vol_period):i + 1]) / min(i + 1, vol_period)
        ratio = volume[i] / avg_volume if avg_volume else 0
        vol_r = volume[i] / (avg_volume or 1.0)
        dist = max(vwap_dist, atr_pct * .35)
        dyn_buy = rsi_buy + (2 if atr_pct > .2 else 0)
        dyn_sell = rsi_sell - (2 if atr_pct > .2 else 0)
        atr_ok = (atr_pct >= min_atr) if g.get("atr", True) else True
        if g.get("fee_edge", True) and edge_mult > 0:
            # One ATR (the expected next-bar move) must clear the fee multiple.
            edge_ok = atr_pct >= edge_mult * floor
        else:
            edge_ok = True
        if not atr_ok or not edge_ok:
            if g.get("rsi", True) and (rr[i] >= dyn_sell or c >= vw[i]): actions[i] = "SELL"
            elif g.get("rsi2", True) and rr2[i] >= rsi2_sell: actions[i] = "SELL"
            continue
        deep = False
        if g.get("bb", True): deep = deep or c <= lower
        if g.get("vwap", True):
            deep = deep or c <= vw[i] * (1 - dist / 100)
            if z_need > 0: deep = deep or vz[i] <= -z_need
        if g.get("rsi", True): deep = deep or rr[i] <= dyn_buy
        if not (g.get("bb", True) or g.get("vwap", True) or g.get("rsi", True)):
            deep = c <= vw[i] or rr[i] <= dyn_buy
        micro = c < vw[i] and rr[i] <= dyn_buy + 8 and rr[i] > rr[i - 1]
        turning = rr[i] > rr[i - 1] and c >= closes[i - 1]
        washed = g.get("rsi", True) and rr[i] <= max(18.0, dyn_buy - 12)
        connors = g.get("rsi2", True) and rsi2_buy >= 0 and rr2[i] <= rsi2_buy and rr2[i] >= rr2[i - 1]
        # --- inline chart_patterns (identical to chart_patterns(candles, settings, i)) ---
        pats_score = 0.0; pattern_ok = False
        if g.get("pattern", True) and i >= 2:
            patterns = []
            body = abs(c - o); wick_low = min(o, c) - l; range_ = max(h - l, 1e-12)
            if closes[i - 1] < vw[i - 1] and c >= vw[i] and vol_r >= 1.2:
                patterns.append("vwap_reclaim"); pats_score += 3.0
            uptrend = fast[i] >= slow[i] and c >= slow[i]
            near_support = c <= vw[i] * 1.001 or c <= fast[i] * 1.002
            if uptrend and near_support and c >= o:
                if wick_low >= body * .6 or (closes[i - 1] < closes[i - 2] and c > closes[i - 1]):
                    patterns.append("micro_pullback"); pats_score += 2.5
            if i >= 1:
                squeeze = bw[i - 1] <= min(bw[max(0, i - 20):i]) * 1.05
            else:
                squeeze = False
            upper = mid[i] + bb_std * std[i]
            if squeeze and c > upper and vol_r >= br_vol:
                patterns.append("bb_squeeze_break"); pats_score += 3.5
            if wick_low >= body * 1.5 and (c - l) / range_ > .6 and c <= vw[i]:
                patterns.append("pin_hammer"); pats_score += 2.0
            if i >= br_p:
                prior_high = max(highs[i - br_p:i])
                if c >= prior_high * (1 + br_bps / 10000) and vol_r >= br_vol and uptrend:
                    patterns.append("breakout"); pats_score += 2.5
            pattern_ok = bool(patterns) and pats_score >= 2.0
        breakout = False
        if g.get("breakout", True) and i >= br_p:
            prior_high = max(highs[i - br_p:i])
            breakout = c >= prior_high * (1 + br_bps / 10000) and ratio >= br_vol and fast[i] >= slow[i]
        trend_ok = (c >= slow[i] * (1 - tol / 100)) if g.get("trend", True) else True
        ema_ok = (fast[i] >= slow[i] * (1 - tol / 200)) if g.get("ema_cross", True) else True
        slope_need = float(settings.get("min_trend_slope_pct", -.05))
        lookback = max(1, int(settings.get("sma_fast", 8)))
        slope = (fast[i] / fast[i - lookback] - 1) * 100 if i >= lookback and fast[i - lookback] else 0
        slope_ok = slope >= slope_need if g.get("trend", True) else True
        vol_ok = (ratio >= min_vol) if g.get("volume", True) else True
        setup = ((deep and turning) or micro or connors or washed or pattern_ok or breakout)
        if not g.get("setup", True):
            setup = (g.get("rsi", True) and rr[i] <= dyn_buy + 5) or turning or micro or washed or pattern_ok
        if g.get("score", True):
            conf = 0.0
            if fast[i] >= slow[i]: conf += 1.0
            if c >= slow[i] * (1 - tol / 100): conf += .5
            if c <= vw[i]: conf += 1.0
            if z_need > 0 and vz[i] <= -z_need: conf += 1.5
            if rr[i] <= rsi_buy: conf += 1.0
            if rr[i] > rr[i - 1]: conf += .5
            if rsi2_buy >= 0 and rr2[i] <= rsi2_buy: conf += 1.0
            if c <= lower: conf += 1.5
            if ratio >= min_vol: conf += .5
            if macd_line[i] > macd_sig[i] and macd_line[i] > macd_line[i - 1]: conf += .5
            conf = round(conf + min(3.0, pats_score * .4), 3)
        else:
            conf = buy_need
        score_ok = conf >= buy_need if g.get("score", True) else True
        if trend_ok and ema_ok and slope_ok and vol_ok and score_ok and edge_ok and i - last_buy >= cooldown and setup:
            actions[i] = "BUY"; last_buy = i
        else:
            sell = False
            if g.get("rsi", True) and rr[i] >= dyn_sell: sell = True
            if g.get("rsi2", True) and rr2[i] >= rsi2_sell: sell = True
            if g.get("vwap", True) and c >= max(mid[i], vw[i] * (1 + dist / 100)): sell = True
            if g.get("trend", True) and c < slow[i] and rr[i] > 50: sell = True
            if g.get("ema_cross", True) and fast[i] < slow[i] and rr[i] > 55: sell = True
            if sell: actions[i] = "SELL"
    return actions

def explain_decision(candles, settings=None, state=None, book=None):
    """One-by-one checklist of every gate that produces BUY/SELL/HOLD."""
    settings = adapt_settings(candles, settings or {})
    state = state or {}
    checks = []
    action = "HOLD"
    reason = "warming_up"
    if not candles or len(candles) < 50:
        checks.append({"id": "warmup", "label": "Warm-up bars", "ok": False, "detail": f"{len(candles) if candles else 0}/50", "value": len(candles) if candles else 0})
        return {"action": action, "reason": reason, "checks": checks, "score": None, "tp_pct": None, "sl_pct": None}

    closes = [c[4] for c in candles]; n = len(closes); i = n - 1
    price = closes[i]
    rsi_p = int(settings.get("rsi_period", 4)); bb_p = int(settings.get("bb_period", 20))
    rr = rsi(closes, rsi_p); rr2 = rsi(closes, 2)
    mid = sma(closes, bb_p); std = rolling_std(closes, bb_p)
    slow = ema(closes, int(settings.get("sma_slow", 21)))
    ranges = atr(candles, int(settings.get("atr_period", 14))); volume = [x[5] for x in candles]
    vw = vwap(candles, int(settings.get("vwap_period", 20)))
    atr_pct = ranges[i] / price * 100 if price else 0
    bb_std = float(settings.get("bb_std", 2))
    lower = mid[i] - bb_std * std[i]
    upper = mid[i] + bb_std * std[i]
    avg_volume = sum(volume[max(0, i - 20):i + 1]) / min(i + 1, 20)
    ratio = volume[i] / avg_volume if avg_volume else 0
    rsi_buy = float(settings.get("rsi_buy", 45)); rsi_sell = float(settings.get("rsi_sell", 55))
    rsi2_buy = float(settings.get("rsi2_buy", 12)); rsi2_sell = float(settings.get("rsi2_sell", 70))
    min_atr = float(settings.get("min_atr_pct", .05)); min_vol = float(settings.get("min_volume_ratio", .25))
    tol = float(settings.get("trend_tolerance_pct", 1.1))
    vwap_dist = float(settings.get("vwap_distance_pct", .08))
    dist = max(vwap_dist, atr_pct * .35)
    dyn_buy = rsi_buy + (2 if atr_pct > .2 else 0)
    dyn_sell = rsi_sell - (2 if atr_pct > .2 else 0)
    vwap_gap = (vw[i] - price) / vw[i] * 100 if vw[i] else 0
    g = gates_of(settings)
    trend_ok = (price >= slow[i] * (1 - tol / 100)) if g.get("trend", True) else True
    fast = ema(closes, int(settings.get("sma_fast", 8)))
    ema_ok = (fast[i] >= slow[i] * (1 - tol / 200)) if g.get("ema_cross", True) else True
    slope_need = float(settings.get("min_trend_slope_pct", -.05))
    lookback = max(1, int(settings.get("sma_fast", 8)))
    slope = (fast[i] / fast[i - lookback] - 1) * 100 if i >= lookback and fast[i - lookback] else 0
    slope_ok = slope >= slope_need if g.get("trend", True) else True
    vol_ok = (ratio >= min_vol) if g.get("volume", True) else True
    atr_ok = (atr_pct >= min_atr) if g.get("atr", True) else True
    deep = False
    if g.get("bb", True): deep = deep or price <= lower
    if g.get("vwap", True): deep = deep or price <= vw[i] * (1 - dist / 100)
    if g.get("rsi", True): deep = deep or rr[i] <= dyn_buy
    if not (g.get("bb", True) or g.get("vwap", True) or g.get("rsi", True)):
        deep = price <= vw[i] or rr[i] <= dyn_buy
    micro = price < vw[i] and rr[i] <= dyn_buy + 8 and rr[i] > rr[i - 1]
    turning = rr[i] > rr[i - 1] and price >= closes[i - 1]
    washed = g.get("rsi", True) and rr[i] <= max(18.0, dyn_buy - 12)
    connors = g.get("rsi2", True) and rsi2_buy >= 0 and rr2[i] <= rsi2_buy and rr2[i] >= rr2[i - 1]
    pats = chart_patterns(candles, settings, i) if g.get("pattern", True) else {"patterns": [], "score": 0}
    pattern_ok = bool(pats["patterns"]) and pats["score"] >= 2.0
    breakout = False
    if g.get("breakout", True) and i >= int(settings.get("breakout_period", 5)):
        br_p = int(settings.get("breakout_period", 5))
        prior_high = max(x[2] for x in candles[i - br_p:i])
        avg_vol20 = sum(volume[max(0, i - 20):i + 1]) / min(i + 1, 20) or 1
        breakout = closes[i] >= prior_high * (1 + float(settings.get("breakout_bps", 1)) / 10000) and (volume[i] / avg_vol20) >= float(settings.get("breakout_volume_ratio", 1.1)) and fast[i] >= slow[i]
    buy_setup = ((deep and turning) or micro or connors or washed or pattern_ok or breakout)
    if not g.get("setup", True):
        buy_setup = (g.get("rsi", True) and rr[i] <= dyn_buy + 5) or turning or micro or washed or pattern_ok
    # Optional local chart-AI confirmation gate
    ai_ok = True
    if g.get("ai", True) and settings.get("ai_scan"):
        ai = chart_ai_analyze(candles, settings)
        ai_ok = ai.get("action") == "BUY"
    sell_setup = False
    if g.get("rsi", True) and rr[i] >= dyn_sell: sell_setup = True
    if g.get("rsi2", True) and rr2[i] >= rsi2_sell: sell_setup = True
    if g.get("vwap", True) and price >= max(mid[i], vw[i] * (1 + dist / 100)): sell_setup = True
    if g.get("trend", True) and price < slow[i] and rr[i] > 50: sell_setup = True
    if g.get("ema_cross", True) and fast[i] < slow[i] and rr[i] > 55: sell_setup = True

    in_pos = float(state.get("coin") or 0) > 0
    entry = float(state.get("entry") or 0)
    qty = float(state.get("coin") or 0)
    tp_pct, sl_pct = dynamic_risk(candles, settings, entry=entry or None, qty=qty or None)
    tp = entry * (1 + tp_pct / 100) if entry else price * (1 + tp_pct / 100)
    sl = entry * (1 - sl_pct / 100) if entry else price * (1 - sl_pct / 100)
    min_gain = min_gain_usd(settings)
    est = net_pnl(entry, price, qty, settings) if in_pos and entry else 0.0
    gain_ok = est > min_gain if in_pos else None

    spread_ok = True; imb_ok = True; spread = None; imb = None
    if book:
        spread = float(book.get("spread_bps") or 0); imb = float(book.get("imbalance") or 0)
        spread_ok = (spread <= float(settings.get("max_spread_bps", 12))) if g.get("spread", True) else True
        imb_ok = (imb >= -abs(float(settings.get("min_book_imbalance", .02)) * 8)) if g.get("imbalance", True) else True

    halted = bool(state.get("halted")) if g.get("halt", True) else False
    score = entry_score(candles, settings)
    score_ok = (score >= float(settings.get("min_entry_score", 0))) if g.get("score", True) else True

    def row(cid, label, ok, detail, value=None):
        enabled = g.get(cid, True) if cid in g else True
        return {"id": cid, "label": label, "ok": True if not enabled else bool(ok), "enabled": enabled,
                "detail": detail if enabled else "disabled", "value": value}

    checks.extend([
        row("atr", "ATR volatility gate", atr_ok, f"{atr_pct:.3f}% ≥ {min_atr:.3f}%", round(atr_pct, 4)),
        row("trend", "Trend vs regime SMA", trend_ok and slope_ok, f"price {price:.6g} vs SMA {slow[i]:.6g} (tol {tol}%) · slope {slope:.3f}%", round(price / slow[i] - 1, 5) if slow[i] else 0),
        row("ema_cross", "EMA cross", ema_ok, f"fast {fast[i]:.6g} vs slow {slow[i]:.6g}", round(fast[i]/slow[i]-1, 5) if slow[i] else 0),
        row("volume", "Volume ratio", vol_ok, f"{ratio:.2f}x ≥ {min_vol:.2f}x", round(ratio, 3)),
        row("rsi", f"RSI({rsi_p})", rr[i] <= dyn_buy if not in_pos else rr[i] >= dyn_sell, f"{rr[i]:.1f} · buy≤{dyn_buy:.0f} sell≥{dyn_sell:.0f}", round(rr[i], 2)),
        row("rsi2", "RSI(2)", (rr2[i] <= rsi2_buy) if rsi2_buy >= 0 and not in_pos else rr2[i] >= rsi2_sell, f"{rr2[i]:.1f} · buy≤{rsi2_buy:.0f} sell≥{rsi2_sell:.0f}", round(rr2[i], 2)),
        row("vwap", "VWAP distance", vwap_gap >= dist if not in_pos else price >= vw[i], f"gap {vwap_gap:.3f}% · need {dist:.3f}%", round(vwap_gap, 4)),
        row("bb", "Bollinger position", price <= lower if not in_pos else price >= mid[i], f"px {price:.6g} · L {lower:.6g} · M {mid[i]:.6g}", round((price - mid[i]) / (std[i] or 1e-9), 3)),
        row("setup", "Entry setup pattern", buy_setup, "deep+turn / micro RSI curl / washout RSI / Connors", int(buy_setup)),
        row("score", "Entry score", score_ok, f"{score:.2f} ≥ {float(settings.get('min_entry_score', 0)):.2f}", round(score, 3)),
        row("fee_edge", "Fee edge", fee_edge_ok(candles, settings, i, spread or 0), f"need {float(settings.get('edge_need_pct', 0) or fee_floor_pct(settings)*max(2,fee_edge_mult_of(settings))):.3f}% · atr_tp~{expected_edge_pct(candles, settings):.3f}%", round(expected_edge_pct(candles, settings), 4)),
        row("pattern", "Chart pattern", pattern_ok, ",".join(pats["patterns"]) or "none", pats["score"]),
        row("breakout", "Breakout", breakout, "donchian+vol" if breakout else "no", int(breakout)),
        row("ai", "AI confirm", ai_ok, "chart-ai BUY" if ai_ok else ("disabled" if not (g.get("ai", True) and settings.get("ai_scan")) else "no BUY"), int(ai_ok)),
        row("spread", "Spread gate", spread_ok, (f"{spread:.1f} bp ≤ {float(settings.get('max_spread_bps', 12)):.1f}" if spread is not None else "no book yet"), spread),
        row("imbalance", "Book imbalance", imb_ok, (f"{imb:.3f}" if imb is not None else "no book yet"), imb),
        row("halt", "Expectancy halt", not halted, "halted" if halted else "active", int(halted)),
        {"id": "position", "label": "Position state", "ok": True, "enabled": True, "detail": ("LONG qty " + f"{qty:.6g} @ {entry:.6g}") if in_pos else "FLAT", "value": qty},
        row("min_gain", f"Min gain ${min_gain:.2f}", (gain_ok if gain_ok is not None else True), (f"est PnL ${est:.4f}" if in_pos else "n/a until filled"), round(est, 4) if in_pos else None),
        {"id": "tp", "label": "Take profit", "ok": True, "enabled": True, "detail": f"{tp_pct:.3f}% → {tp:.6g}", "value": round(tp_pct, 4)},
        {"id": "sl", "label": "Stop loss", "ok": True, "enabled": True, "detail": f"{sl_pct:.3f}% → {sl:.6g}", "value": round(sl_pct, 4)},
        {"id": "leverage", "label": "Leverage", "ok": True, "enabled": True, "detail": f"{leverage_of(settings):.0f}x · margin {float(settings.get('position_pct', 100)):.0f}%", "value": leverage_of(settings)},
    ])

    raw = signal_series(candles, settings)[-1]
    min_gain_gate = g.get("min_gain", True)
    if in_pos:
        peak = float(state.get("peak") or state.get("peak_price") or price)
        peak_pnl = net_pnl(entry, peak, qty, settings) if entry and qty else est
        hi = candles[i][2]
        gain_px = entry * (1 + min_tp_pct_for_gain(entry, qty, settings) / 100) if entry and qty and min_gain > 0 else entry
        if price <= sl:
            action, reason = "SELL", "stop"
        elif hi >= tp or price >= tp:
            action, reason = "SELL", "take_profit"
        elif hi >= gain_px or peak >= gain_px:
            action, reason = "SELL", "min_gain"
        elif (not min_gain_gate or gain_ok) and sell_setup:
            action, reason = "SELL", "signal"
        elif peak_pnl > min_gain:
            action, reason = "HOLD", "trail_armed"
        elif not min_gain_gate or gain_ok:
            action, reason = "HOLD", "await_exit_or_min_gain"
        else:
            action, reason = "HOLD", "below_min_gain"
    else:
        if halted:
            action, reason = "HOLD", "halted"
        elif raw == "BUY" and atr_ok and trend_ok and slope_ok and ema_ok and vol_ok and buy_setup and score_ok and spread_ok and imb_ok and ai_ok and (fee_edge_ok(candles, settings, i, spread or 0) if g.get("fee_edge", True) else True):
            action, reason = "BUY", "entry_setup"
        elif raw == "SELL":
            action, reason = "HOLD", "sell_signal_while_flat"
        else:
            action, reason = "HOLD", "filters_block" if raw == "BUY" else "no_setup"


    return {
        "action": action,
        "reason": reason,
        "raw_signal": raw,
        "price": price,
        "score": round(score, 3),
        "tp_pct": round(tp_pct, 4),
        "sl_pct": round(sl_pct, 4),
        "est_pnl": round(est, 4) if in_pos else None,
        "min_gain_usd": min_gain,
        "leverage": leverage_of(settings),
        "checks": checks,
        "ts": time.time(),
    }



def backtest(candles, settings=None, fee=0.001, slippage=0.0005, capital=10.0, on_progress=None):
    settings = settings or {}; fee, slippage = settings.get("fee_rate", fee), settings.get("slippage_rate", slippage)
    cash, coin, entry, trades, equity, held, peak_price, margin = capital, 0.0, 0.0, [], [], 0, 0.0, 0.0
    fills = []
    trail = float(settings.get("trail_pct", 0) or 0)
    lev = leverage_of(settings)
    min_gain = min_gain_usd(settings) if gate_on(settings, "min_gain") else 0.0
    actions = signal_series(candles, settings)
    start_i = 50 if len(candles) > 50 else 30
    total = max(1, len(candles) - start_i)
    entry_ts = entry_fee = entry_notional = 0.0
    fees_total = 0.0
    for i in range(start_i, len(candles)):
        if on_progress and (i - start_i) % max(1, total // 50) == 0:
            try: on_progress(int((i - start_i) / total * 100), i - start_i, total)
            except Exception: pass
        o, h, l, price, action = candles[i][1], candles[i][2], candles[i][3], candles[i][4], actions[i]
        if not coin and action == "BUY":
            fill = o * (1 + slippage)
            margin = cash * float(settings.get("position_pct", 100)) / 100
            notional = margin * lev
            open_fee = notional * fee
            if margin + open_fee > cash:
                margin = max(0.0, cash / (1 + fee * lev)); notional = margin * lev; open_fee = notional * fee
            if margin <= 0: continue
            entry = fill; coin = notional / entry; cash = cash - margin - open_fee; held = 0; peak_price = h
            entry_ts, entry_fee, entry_notional = candles[i][0], open_fee, notional
            fees_total += open_fee
            fills.append({"side": "BUY", "ts": candles[i][0], "price": fill, "pnl": None, "equity": cash + margin,
                          "quantity": coin, "margin": margin, "notional": notional, "fee_paid": open_fee,
                          "fee_entry": open_fee, "entry": fill, "reason": "", "hold_bars": 0})
            continue
        if coin:
            peak_price = max(peak_price, h)
            tp_pct, sl_pct = dynamic_risk(candles, settings, i, entry=entry, qty=coin)
            tp = entry * (1 + tp_pct / 100)
            sl = entry * (1 - sl_pct / 100)
            gain_px = entry * (1 + min_tp_pct_for_gain(entry, coin, settings) / 100) if min_gain > 0 else entry
# Session read of this span was image-compacted; fragments below resume at verified lines.
            exit_px = exit_reason = None
            if l <= sl:
                exit_px, exit_reason = sl, "stop"
            elif h >= tp:
                exit_px, exit_reason = tp, "take_profit"
            elif h >= gain_px or peak_price >= gain_px:
                exit_px, exit_reason = max(gain_px, min(h if h >= gain_px else peak_price, tp)), "min_gain"
            elif trail > 0 and net_pnl(entry, peak_price, coin, settings) > min_gain and price <= peak_price * (1 - trail / 100) and net_pnl(entry, min(price, peak_price * (1 - trail / 100)), coin, settings) > 0:
                exit_px, exit_reason = min(price, peak_price * (1 - trail / 100)), "trail"
            elif held >= int(settings.get("max_hold_bars", 6)) and net_pnl(entry, price, coin, settings) > min_gain:
                exit_px, exit_reason = price, "time"
            elif action == "SELL" and net_pnl(entry, price, coin, settings) > min_gain:
                exit_px, exit_reason = price, "signal"
            if exit_px is not None:
                pnl = net_pnl(entry, exit_px, coin, settings)
                exit_fee = coin * exit_px * fee
                fees_total += exit_fee
                trades.append(pnl); cash = cash + margin + pnl; coin, margin = 0.0, 0.0
                fills.append({"side": "SELL", "ts": candles[i][0], "price": exit_px, "pnl": pnl, "equity": cash,
                              "quantity": fills[-1]["quantity"] if fills else 0.0, "margin": fills[-1]["margin"] if fills else 0.0,
                              "notional": entry_notional, "fee_paid": entry_fee + exit_fee, "fee_entry": entry_fee,
                              "fee_exit": exit_fee, "gross_pnl": (exit_px - entry) * (fills[-1]["quantity"] if fills else 0.0),
                              "entry": entry, "exit": exit_px, "reason": exit_reason,
                              "hold_bars": int((candles[i][0] - entry_ts) / max(1, candles[i][0] - candles[max(0, i - 1)][0])) if entry_ts else 0,
                              "entry_time": entry_ts, "exit_time": candles[i][0],
                              "return_margin_pct": (pnl / fills[-1]["margin"] * 100) if fills and fills[-1].get("margin") else 0.0})
                entry_ts = entry_fee = entry_notional = 0.0
            else:
                held += 1
        equity.append(cash + margin + ((price - entry) * coin if coin else 0))
    if coin:
        px = candles[-1][4] * (1 - slippage)
        pnl = net_pnl(entry, px, coin, settings)
        trades.append(pnl); ending = cash + margin + pnl
        exit_fee = coin * px * fee
        fees_total += exit_fee
        fills.append({"side": "SELL", "ts": candles[-1][0], "price": px, "pnl": pnl, "equity": ending, "quantity": coin,
                      "margin": margin, "notional": entry_notional, "fee_paid": entry_fee + exit_fee, "fee_entry": entry_fee,
                      "fee_exit": exit_fee, "gross_pnl": (px - entry) * coin, "entry": entry, "exit": px, "reason": "final",
                      "hold_bars": 0, "entry_time": entry_ts, "exit_time": candles[-1][0],
                      "return_margin_pct": (pnl / margin * 100) if margin else 0.0})
    else:
        ending = cash
    if on_progress:
        try: on_progress(100, total, total)
        except Exception: pass
    wins = sum(1 for t in trades if t > 0); losses = sum(1 for t in trades if t <= 0)
    gross_wins = sum(t for t in trades if t > 0); gross_losses = abs(sum(t for t in trades if t < 0))
    gross_total = sum((f.get("gross_pnl") or 0) for f in fills if f.get("side") == "SELL")
    peak = drawdown = 0.0
    for value in equity:
        peak = max(peak, value); drawdown = max(drawdown, (peak - value) / peak if peak else 0)
    step = max(1, len(equity) // 200)
    curve = [{"t": candles[start_i + j][0], "equity": equity[j]} for j in range(0, len(equity), step)] if equity else []
    chart_candles = [{"t": c[0], "o": c[1], "h": c[2], "l": c[3], "c": c[4]} for c in candles[start_i:]]
    return {"capital": capital, "ending_equity": ending, "return_pct": (ending - capital) / capital * 100 if capital else 0,
            "trades": len(trades), "win_rate": (wins / len(trades) * 100) if trades else 0,
            "profit_factor": (gross_wins / gross_losses) if gross_losses else (999 if gross_wins else 0),
            "max_drawdown_pct": -drawdown * 100, "equity_curve": curve, "fills": fills[-400:], "candles": chart_candles,
            "net_pnl": ending - capital, "gross_pnl": gross_total, "fees_total": fees_total,
            "avg_trade": (sum(trades) / len(trades)) if trades else 0, "start_index": start_i,
            "leverage": lev, "min_gain_usd": min_gain, "fee_rate": fee, "slippage_rate": slippage}





def simulate_multi_slot_day(symbol_results, max_positions=4, starting_cash=10.0):
    """Merge per-symbol backtest fills into a shared multi-slot book (fee-aware expectancy proxy).

    `symbol_results` is a list of backtest() dicts that include `fills` with entry/exit times.
    Returns aggregate trades/net/win_rate under a concurrent slot cap.
    """
    events = []
    for res in symbol_results or []:
        sym = res.get("symbol") or "?"
        for f in res.get("fills") or []:
            # backtest fills may use bar indexes; treat as ordered sequence if no timestamps
            side = str(f.get("side") or "").upper()
            t = f.get("ts") or f.get("time") or f.get("t") or f.get("i") or 0
            events.append((float(t), sym, side, f))
    events.sort(key=lambda x: (x[0], 0 if x[2] == "BUY" else 1))
    cash = float(starting_cash)
    slots = {}
    closed = []
    for t, sym, side, f in events:
        if side == "BUY":
            if sym in slots or len(slots) >= max_positions:
                continue
            pnl = None
            entry = float(f.get("price") or f.get("entry") or 0)
            qty = float(f.get("quantity") or f.get("qty") or 0)
            margin = float(f.get("margin") or (cash / max(1, max_positions - len(slots))))
            if entry <= 0 or qty <= 0:
                continue
            slots[sym] = {"entry": entry, "qty": qty, "margin": margin, "t": t}
        elif side == "SELL" and sym in slots:
            pos = slots.pop(sym)
            pnl = float(f.get("pnl") if f.get("pnl") is not None else 0.0)
            cash += pnl
            closed.append(pnl)
    trades = len(closed)
    wins = sum(1 for p in closed if p > 0)
    net = sum(closed)
    return {
        "trades": trades,
        "wins": wins,
        "win_rate": (wins / trades * 100) if trades else 0.0,
        "net_pnl": net,
        "ending_cash": cash,
        "max_positions": max_positions,
        "avg_trade": (net / trades) if trades else 0.0,
    }


def optimize(candles, base=None, on_progress=None):
    """Search hybrid HF+RSI2 params for holdout-validated edge."""
    base = dict(base or {})
    n = len(candles or [])
    split = int(n * .7)
    if n >= 60:
        split = min(max(split, 30), n - 20)
    else:
        split = max(0, min(split, max(0, n - 1)))
    train, test = candles[:split], candles[split:]
    MIN_TRAIN_TRADES = 5
    MIN_HOLDOUT_TRADES = 5
    MIN_HOLDOUT_PF = 1.05
    grid = 0; total_grid = 1
    candidates = []
    # Grid covers the levers that actually move scalp expectancy: RSI timing, buy_score,
    # hold, and — crucially — the ATR reward:risk geometry (atr_tp_mult vs atr_sl_mult).
    # Sampling the TP multiple below the SL multiple only ever produced ~1:1 risk, so the
    # old (1.0, 1.2, 1.5) range could not find a +EV scalp. rsi_sell/z-entry are fixed:
    # they never changed the outcome enough to earn grid slots.
    for rsi_buy in (38, 40, 45):
        for buy_score in (1.5, 2.0, 2.5):
            for hold in (3, 5, 8):
                for atr_tp in (2.0, 3.0, 4.5):
                    for atr_sl in (.35, .5):
                        for edge in (2.0, 3.0):
                            for cooldown in (0, 1):
                                candidates.append((rsi_buy, buy_score, hold, atr_tp, atr_sl, edge, cooldown))
    total_grid = max(1, len(candidates))
    best = None; best_key = None
    best_train = None; best_train_key = (-10**9,)
    best_train_result = None; best_holdout_result = None
    evaluated = 0; holdout_runs = 0
    for grid, (rsi_buy, buy_score, hold, atr_tp, atr_sl, edge, cooldown) in enumerate(candidates, 1):
        if on_progress and grid % max(1, total_grid // 50) == 0:
            try: on_progress(int(grid / total_grid * 100), grid, total_grid)
            except Exception: pass
        # dict(base, ...) preserves fee_style/fee_rate/slippage_rate/live_post_only from base.
        candidate = dict(
            base,
            rsi_buy=rsi_buy, rsi_sell=50, rsi_period=4, rsi2_buy=-1, rsi2_sell=70,
            take_profit_pct=.35, stop_loss_pct=.2, min_profit_pct=.12,
            max_hold_bars=hold, trail_pct=.15, min_atr_pct=.05, vwap_period=20,
            vwap_z_entry=0, cooldown_bars=cooldown, min_volume_ratio=.25, trend_tolerance_pct=1.1,
            bb_std=2, atr_tp_mult=atr_tp, atr_sl_mult=atr_sl, fee_edge_mult=edge, atr_fee_lambda=1.0,
            buy_score=buy_score, min_entry_score=buy_score, auto_scan=True, scan_symbols=20, scan_seconds=5,
            sma_fast=8, sma_slow=21,
        )
        train_result = backtest(train, candidate)
        evaluated += 1
        train_key = (train_result.get("net_pnl", 0), train_result.get("trades", 0),
                     train_result.get("profit_factor", 0), -abs(train_result.get("max_drawdown_pct", 0)))
        if train_key > best_train_key:
            best_train, best_train_key = candidate, train_key
            best_train_result = train_result
        # Practical runtime: only spend a holdout run when train looks viable.
        if not (train_result.get("net_pnl", 0) > 0 and train_result.get("trades", 0) >= MIN_TRAIN_TRADES):
            continue
        holdout_result = backtest(test, candidate)
        holdout_runs += 1
        if not (holdout_result.get("net_pnl", 0) > 0
                and holdout_result.get("profit_factor", 0) > MIN_HOLDOUT_PF
                and holdout_result.get("trades", 0) >= MIN_HOLDOUT_TRADES):
            continue
        key = (holdout_result.get("net_pnl", 0), holdout_result.get("profit_factor", 0),
               train_result.get("net_pnl", 0), -abs(holdout_result.get("max_drawdown_pct", 0)))
        if best_key is None or key > best_key:
            best, best_key = candidate, key
            best_train_result, best_holdout_result = train_result, holdout_result
    if on_progress:
        try: on_progress(100, total_grid, total_grid)
        except Exception: pass
    if best is not None:
        return {"settings": best, "qualified": True, "train": best_train_result, "holdout": best_holdout_result,
                "bars": n, "split": split, "evaluated": evaluated, "holdout_runs": holdout_runs,
                "message": ("Qualified: holdout net +%(net).4f over %(trades)d trades (PF %(pf).2f); "
                            "train net +%(train).4f." % {"net": best_holdout_result.get("net_pnl", 0),
                            "trades": best_holdout_result.get("trades", 0),
                            "pf": best_holdout_result.get("profit_factor", 0),
                            "train": best_train_result.get("net_pnl", 0)})}
    # No candidate survived holdout gates: refuse to bless an unprofitable best.
    if best_train_result is None:
        best_train = dict(base)
        best_train_result = backtest(train, best_train)
        best_holdout_result = backtest(test, best_train)
    elif best_holdout_result is None:
        best_holdout_result = backtest(test, best_train)
    return {"settings": None, "qualified": False, "train": best_train_result, "holdout": best_holdout_result,
            "bars": n, "split": split, "evaluated": evaluated, "holdout_runs": holdout_runs,
            "message": ("No profitable params found: no candidate passed train net_pnl>0 plus holdout "
                        "net_pnl>0, profit_factor>%.2f, trades>=%d (evaluated %d, holdout runs %d). "
                        "Keeping current settings." % (MIN_HOLDOUT_PF, MIN_HOLDOUT_TRADES, evaluated, holdout_runs))}



class Binance:
    base = "https://api.binance.com"
    fapi_base = "https://fapi.binance.com"
    def __init__(self):
        self.key, self.secret = os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET")
        self._filters = {}
    def get(self, path, params=""):
        with urlopen(self.base + path + ("?" + params if params else ""), timeout=10) as r: return json.load(r)
    def _parse_klines(self, rows):
        return [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in rows]
    def candles(self, symbol, interval="1m", limit=200):
        # Native futures/spot klines start at 1m; build 15s/30s from spot 1s bars.
        if interval in SUBMINUTE_SECONDS:
            sec = SUBMINUTE_SECONDS[interval]
            need = min(1000, max(limit * sec, sec * 40))
            rows = self._parse_klines(self.get("/api/v3/klines", f"symbol={symbol}&interval=1s&limit={need}"))
            return aggregate_ohlcv(rows, sec)[-limit:]
        rows = self.get("/api/v3/klines", f"symbol={symbol}&interval={interval}&limit={limit}")
        return self._parse_klines(rows)
    def history(self, symbol, interval="1m", limit=3000, on_page=None):
        if interval in SUBMINUTE_SECONDS:
            sec = SUBMINUTE_SECONDS[interval]
            # Page 1s bars, then bucket. Cap pages to keep runtime sane.
            rows, end, pages = [], None, 0
            need_1s = min(12000, limit * sec)
            while len(rows) < need_1s and pages < 20:
                count = min(1000, need_1s - len(rows))
                query = f"symbol={symbol}&interval=1s&limit={count}" + (f"&endTime={end}" if end else "")
                batch = self.get("/api/v3/klines", query)
                if not batch: break
                pages += 1
                if on_page:
                    try: on_page(pages)
                    except Exception: pass
                chunk = self._parse_klines(batch)
                rows = chunk + rows
                end = chunk[0][0] - 1
                if len(batch) < count: break
            return aggregate_ohlcv(rows, sec)[-limit:]
        rows, end, pages = [], None, 0
        while len(rows) < limit:
            count = min(1000, limit - len(rows)); query = f"symbol={symbol}&interval={interval}&limit={count}" + (f"&endTime={end}" if end else "")
            batch = self.get("/api/v3/klines", query)
            if not batch: break
            pages += 1
            if on_page:
                try: on_page(pages)
                except Exception: pass
            chunk = self._parse_klines(batch)
            rows = chunk + rows
            end = chunk[0][0] - 1
            if len(batch) < count: break
        return rows[-limit:]
    def ticker(self, symbol): return float(self.get("/api/v3/ticker/price", f"symbol={symbol}")["price"])
    def orderbook(self, symbol, limit=20): return book_stats(self.get("/api/v3/depth", f"symbol={symbol}&limit={limit}"))
    def symbols(self):
        return [x["symbol"] for x in self.get("/api/v3/exchangeInfo")["symbols"] if x.get("status") == "TRADING" and x["symbol"].endswith("USDT")]
    def top_symbols(self, limit=30):
        rows = self.get("/api/v3/ticker/24hr")
        usdt = [x for x in rows if x["symbol"].endswith("USDT") and not any(y in x["symbol"] for y in ("UP", "DOWN", "BEAR", "BULL"))]
        usdt.sort(key=lambda x: float(x.get("quoteVolume") or 0), reverse=True)
        return [x["symbol"] for x in usdt[:limit]]

    # ---- USDT-M futures signed trading (live mode) ----
    def fapi_get(self, path, params=""):
        with urlopen(self.fapi_base + path + ("?" + params if params else ""), timeout=10) as r:
            return json.load(r)
    def fapi_request(self, method, path, params=None, signed=False):
        params = dict(params or {})
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if signed:
            if not self.key or not self.secret:
                raise RuntimeError("BINANCE_API_KEY/SECRET required for live orders")
            params.setdefault("timestamp", int(time.time() * 1000))
            params.setdefault("recvWindow", 5000)
            query = urlencode(params)
            sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            query = f"{query}&signature={sig}"
            headers["X-MBX-APIKEY"] = self.key
        else:
            query = urlencode(params) if params else ""
        url = self.fapi_base + path + (("?" + query) if query and method == "GET" else "")
        body = None if method == "GET" else (query.encode() if query else None)
        if method != "GET" and signed:
            headers["X-MBX-APIKEY"] = self.key
        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=10) as r:
            return json.load(r)
    def symbol_filters(self, symbol):
        symbol = symbol.upper()
        if symbol in self._filters:
            return self._filters[symbol]
        info = self.fapi_get("/fapi/v1/exchangeInfo")
        found = next((x for x in info.get("symbols", []) if x.get("symbol") == symbol), None)
        if not found:
            raise RuntimeError(f"futures symbol not found: {symbol}")
        tick = step = 0.0001
        min_qty = 0.0
        for f in found.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                tick = float(f.get("tickSize") or tick)
            elif f.get("filterType") == "LOT_SIZE":
                step = float(f.get("stepSize") or step)
                min_qty = float(f.get("minQty") or 0)
        self._filters[symbol] = {"tick": tick, "step": step, "min_qty": min_qty}
        return self._filters[symbol]
    @staticmethod
    def _round_step(value, step):
        if step <= 0: return value
        precision = max(0, len(f"{step:.16f}".rstrip("0").split(".")[-1]) if "." in f"{step:.16f}".rstrip("0") else 0)
        # floor to step
        n = int(float(value) / step)
        return round(n * step, precision)
    def set_leverage(self, symbol, leverage):
        return self.fapi_request("POST", "/fapi/v1/leverage", {"symbol": symbol.upper(), "leverage": int(leverage)}, signed=True)
    def place_order(self, symbol, side, quantity, price=None, reduce_only=False, post_only=True):
        """LIMIT GTX (post-only) by default to earn maker fees; MARKET if post_only is False and no price."""
        filt = self.symbol_filters(symbol)
        qty = self._round_step(quantity, filt["step"])
        if qty < filt["min_qty"]:
            raise RuntimeError(f"qty {qty} below min {filt['min_qty']}")
        params = {"symbol": symbol.upper(), "side": side.upper(), "quantity": qty}
        if post_only or price is not None:
            if price is None:
                raise RuntimeError("post-only orders need a price")
            px = self._round_step(price, filt["tick"])
            params.update({"type": "LIMIT", "price": px, "timeInForce": "GTX" if post_only else "GTC"})
        else:
            params["type"] = "MARKET"
        if reduce_only:
            params["reduceOnly"] = "true"
        return self.fapi_request("POST", "/fapi/v1/order", params, signed=True)
    def get_order(self, symbol, order_id):
        return self.fapi_request("GET", "/fapi/v1/order", {"symbol": symbol.upper(), "orderId": int(order_id)}, signed=True)
    def cancel_order(self, symbol, order_id):
        return self.fapi_request("DELETE", "/fapi/v1/order", {"symbol": symbol.upper(), "orderId": int(order_id)}, signed=True)
    def wait_order(self, symbol, order_id, timeout=2.5):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.get_order(symbol, order_id)
            status = str(last.get("status") or "")
            if status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                return last
            if status == "PARTIALLY_FILLED" and float(last.get("executedQty") or 0) > 0 and time.time() > deadline - 0.4:
                break
            time.sleep(0.2)
        try:
            self.cancel_order(symbol, order_id)
        except Exception:
            pass
        return self.get_order(symbol, order_id)


class KuCoin(Binance):
    base = "https://api.kucoin.com"
    def _pair(self, symbol): return f"{symbol[:-4]}-{symbol[-4:]}"
    def candles(self, symbol, interval="1m", limit=200):
        if interval in SUBMINUTE_SECONDS:
            interval = "1m"  # KuCoin has no public 1s bars; degrade gracefully
        rows = self.get(f"/api/v1/market/candles", f"type={KUCOIN_INTERVALS.get(interval,'1min')}&symbol={self._pair(symbol)}&startAt=0")["data"]
        rows = list(reversed(rows))[-limit:]
        return [[int(x[0]) * 1000, float(x[1]), float(x[3]), float(x[4]), float(x[2]), float(x[5])] for x in rows]
    def history(self, symbol, interval="1m", limit=3000, on_page=None):
        """Page backwards through KuCoin's per-call cap; ascending, deduped, oldest→newest."""
        by_time, end, pages = {}, None, 0
        for _ in range(max(2, limit // 100 + 3)):
            typ = KUCOIN_INTERVALS.get(interval, "1min")
            q = f"type={typ}&symbol={self._pair(symbol)}" + (f"&endAt={end}" if end else "")
            try:
                rows = self.get("/api/v1/market/candles", q)["data"]
            except Exception:
                break
            if not rows: break
            pages += 1
            if on_page:
                try: on_page(pages)
                except Exception: pass
            for x in rows:
                ts = int(x[0]) * 1000
                by_time[ts] = [ts, float(x[1]), float(x[3]), float(x[4]), float(x[2]), float(x[5])]
            end = int(rows[-1][0]) - 1
            if len(by_time) >= limit: break
        return [by_time[t] for t in sorted(by_time)][-limit:]
    def ticker(self, symbol): return float(self.get(f"/api/v1/market/orderbook/level1", f"symbol={symbol[:-4]}-{symbol[-4:]}")["data"]["price"])
    def orderbook(self, symbol, limit=20):
        data = self.get(f"/api/v1/market/orderbook/level2_{min(limit,100)}", f"symbol={symbol[:-4]}-{symbol[-4:]}")["data"]
        return book_stats({"bids": data["bids"], "asks": data["asks"]})
    def symbols(self):
        return [x["symbol"].replace("-", "") for x in self.get("/api/v2/symbols")["data"] if x.get("enableTrading") and x["symbol"].endswith("-USDT")]
    def top_symbols(self, limit=30):
        rows = self.get("/api/v1/market/allTickers")["data"]["ticker"]
        usdt = [x for x in rows if str(x.get("symbol","")).endswith("-USDT")]
        usdt.sort(key=lambda x: float(x.get("volValue") or 0), reverse=True)
        return [x["symbol"].replace("-", "") for x in usdt[:limit]]


class Mexc(Binance):
    base = "https://api.mexc.com"


EXCHANGES = {"binance": Binance, "kucoin": KuCoin, "mexc": Mexc}

# Per-exchange cost defaults. Source: published USDT-M futures maker schedules
# (Binance 0.02%, KuCoin 0.02%, MEXC 0.00%). Editable per run in Settings.
EXCHANGE_FEES = {
    # Published USDT-M schedules; scalps that take liquidity should use fee_style=taker.
    "binance": {"fee_rate": .0002, "taker_fee_rate": .0004, "slippage_rate": .0001, "label": "Binance USDT-M futures"},
    "kucoin": {"fee_rate": .0002, "taker_fee_rate": .0006, "slippage_rate": .0001, "label": "KuCoin Futures"},
    "mexc": {"fee_rate": .0, "taker_fee_rate": .0002, "slippage_rate": .0001, "label": "MEXC Futures"},
}

INTERVALS = ("15s", "30s", "1m", "5m", "15m", "1h")
INTERVAL_SECONDS = {"15s": 15, "30s": 30, "1m": 60, "5m": 300, "15m": 900, "1h": 3600}
KUCOIN_INTERVALS = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1hour"}

HELP_TEXT = {
    "backtest": "Replays the strategy over recent candles with fees on both legs. Fast, ~1000 bars.",
    "deep_history": "Same replay over up to 10k bars fetched page by page — slower, better statistics.",
    "optimize": "Grid-searches fee-aware entry/exit params on the first 70% of bars, then reports the untouched 30% holdout.",
    "live": "Paper book with live prices unless LIVE mode is armed. Multi-slot + auto_scan + optional 15s/30s synthetic bars. Prefer fee_style=maker (post-only GTX on live). Live mode sends HMAC futures orders when keys are set; unfilled post-only orders are canceled.",
}


def _field(key, label, type_, group, help_="", **extra):
    return {"key": key, "label": label, "type": type_, "group": group, "help": help_, **extra}


SETTINGS_GROUPS = [
    ("General", [
        _field("symbol", "Market", "symbol", "General", "Trading pair, e.g. INJUSDT."),
        _field("interval", "Candle interval", "select", "General", "Bar size for signals and backtests.", options=list(INTERVALS)),
        _field("poll_seconds", "Poll seconds", "int", "General", "Loop cadence.", min=1, max=60, step=1),
        _field("auto_scan", "Auto scan", "bool", "General", "Rotate into the best-scoring market when flat."),
        _field("scan_symbols", "Scan symbols", "int", "General", "How many markets to rank per scan.", min=2, max=50, step=1),
        _field("scan_seconds", "Scan interval", "int", "General", "Seconds between scans.", min=1, max=120, step=1),
        _field("scan_whitelist", "Scan whitelist", "symbols", "General", "Markets to rank; empty = exchange top volume."),
        _field("ai_scan", "AI scan", "bool", "General", "Re-rank scan candidates with local chart AI when flat."),
        _field("max_positions", "Max positions", "int", "General", "Concurrent paper slots across symbols (cadence).", min=1, max=12, step=1),
    ]),
    ("Sizing", [
        _field("position_pct", "Margin %", "float", "Sizing", "Share of free cash posted as margin per new slot.", min=1, max=100, step=1),
        _field("leverage", "Leverage", "float", "Sizing", "Notional = margin x leverage. Amplifies fee drag on equity.", min=1, max=125, step=1),
        _field("min_gain_usd", "Min gain $", "float", "Sizing", "Net profit floor that arms the exit.", min=0, max=100, step=.01),
    ]),
    ("Fees", [
        _field("exchange", "Exchange", "select", "Fees", "Fee schedule + data source.", options=list(EXCHANGES)),
        _field("fee_style", "Fee style", "select", "Fees", "Maker vs taker schedule (edge calc + fills).", options=["maker", "taker"]),
        _field("live_post_only", "Live post-only", "bool", "Fees", "LIVE uses GTX post-only limits (maker). Off = market/taker."),
        _field("fee_rate", "Fee rate", "float", "Fees", "Per-leg fee on notional.", min=0, max=.01, step=.0001),
        _field("slippage_rate", "Slippage", "float", "Fees", "Per-leg slippage on notional.", min=0, max=.01, step=.0001),
    ]),
    ("Exits", [
        _field("take_profit_pct", "Take profit %", "float", "Exits", "Soft target; min-gain and ATR can raise it.", min=0, max=20, step=.05),
        _field("stop_loss_pct", "Max stop loss %", "float", "Exits", "Ceiling on stop distance; ATR sizes the actual stop below it.", min=0, max=20, step=.05),
        _field("min_profit_pct", "Min profit %", "float", "Exits", "Floor on the dynamic TP.", min=0, max=20, step=.05),
        _field("max_hold_bars", "Max hold bars", "int", "Exits", "Bars before a profitable time exit.", min=1, max=200, step=1),
        _field("trail_pct", "Trail %", "float", "Exits", "Give-back allowed from peak.", min=0, max=20, step=.05),
        _field("atr_period", "ATR period", "int", "Exits", "Volatility window.", min=2, max=100, step=1),
        _field("atr_tp_mult", "ATR TP mult", "float", "Exits", "TP = ATR% x mult. Must exceed the SL mult for scalp reward:risk.", min=0, max=10, step=.05),
        _field("atr_sl_mult", "ATR SL mult", "float", "Exits", "SL = ATR% x mult, capped by max stop loss %.", min=0, max=10, step=.05),
    ]),
    ("Entry filters", [
        _field("rsi_buy", "RSI buy", "float", "Entry filters", "Buy below this RSI.", min=0, max=100, step=1),
        _field("rsi_sell", "RSI sell", "float", "Entry filters", "Exit above this RSI.", min=0, max=100, step=1),
        _field("rsi_period", "RSI period", "int", "Entry filters", "RSI lookback.", min=2, max=50, step=1),
        _field("rsi2_buy", "RSI(2) buy", "float", "Entry filters", "Connors RSI(2) washout; -1 disables.", min=-1, max=100, step=1),
        _field("rsi2_sell", "RSI(2) sell", "float", "Entry filters", "RSI(2) overbought exit.", min=0, max=100, step=1),
        _field("sma_fast", "Fast MA", "int", "Entry filters", "Fast average period.", min=2, max=200, step=1),
        _field("sma_slow", "Regime SMA", "int", "Entry filters", "Trend filter average.", min=2, max=400, step=1),
        _field("vwap_period", "VWAP period", "int", "Entry filters", "VWAP window.", min=2, max=200, step=1),
        _field("vwap_z_entry", "VWAP z entry", "float", "Entry filters", "Depth in z-scores required to buy.", min=0, max=6, step=.1),
        _field("vwap_distance_pct", "VWAP distance %", "float", "Entry filters", "Discount below VWAP required.", min=0, max=5, step=.01),
        _field("bb_period", "BB period", "int", "Entry filters", "Bollinger window.", min=2, max=200, step=1),
        _field("bb_std", "BB std", "float", "Entry filters", "Bollinger width in sigmas.", min=.5, max=6, step=.1),
        _field("min_atr_pct", "Min ATR %", "float", "Entry filters", "Skip dead tape.", min=0, max=10, step=.01),
        _field("min_trend_slope_pct", "Min trend slope %", "float", "Entry filters", "Trend slope floor.", min=-5, max=5, step=.01),
        _field("trend_tolerance_pct", "Trend tolerance %", "float", "Entry filters", "Allowed dip below regime SMA.", min=0, max=10, step=.01),
        _field("volume_period", "Volume period", "int", "Entry filters", "Volume average window.", min=2, max=200, step=1),
        _field("min_volume_ratio", "Min volume ratio", "float", "Entry filters", "Volume vs average floor.", min=0, max=10, step=.01),
        _field("cooldown_bars", "Cooldown bars", "int", "Entry filters", "Bars to wait between entries.", min=0, max=100, step=1),
        _field("buy_score", "Buy score", "float", "Entry filters", "Setup score needed to fire.", min=0, max=20, step=1),
        _field("min_entry_score", "Min entry score", "float", "Entry filters", "Ranking floor when scanning.", min=-10, max=50, step=.5),
        _field("breakout_period", "Breakout period", "int", "Entry filters", "Donchian window.", min=2, max=200, step=1),
        _field("breakout_bps", "Breakout bps", "float", "Entry filters", "Clearance over the channel.", min=0, max=500, step=1),
        _field("breakout_volume_ratio", "Breakout volume", "float", "Entry filters", "Volume multiplier for breakouts.", min=0, max=10, step=.1),
    ]),
    ("Order book", [
        _field("max_spread_bps", "Max spread bps", "float", "Order book", "Reject entries above this spread.", min=0, max=500, step=.5),
        _field("min_book_imbalance", "Min imbalance", "float", "Order book", "Bid/ask size skew at entry.", min=-1, max=1, step=.01),
    ]),
    ("Expectancy", [
        _field("expectancy_window", "Window", "int", "Expectancy", "Trades averaged for expectancy.", min=5, max=500, step=1),
        _field("expectancy_halt", "Halt on negative expectancy", "bool", "Expectancy", "Pause entries when the rolling average goes negative."),
        _field("halt_cooldown_sec", "Halt cooldown seconds", "int", "Expectancy", "Seconds before a halt is lifted so scalping resumes.", min=30, max=86400, step=30),
    ]),
    ("Advanced", [
        _field("fee_edge_mult", "Fee edge mult", "float", "Advanced", "Fee multiple the edge must clear.", min=0, max=20, step=.1),
        _field("atr_fee_lambda", "ATR fee lambda", "float", "Advanced", "How hard fees lift the ATR gate.", min=0, max=10, step=.1),
        _field("gates", "Gate toggles", "gates", "Advanced", "Per-gate on/off switches."),
    ]),
]

SETTINGS_SCHEMA = {f["key"]: f for _group, _fields in SETTINGS_GROUPS for f in _fields}
SETTINGS_GROUPS_ORDER = [g for g, _ in SETTINGS_GROUPS]
GATE_KEYS = tuple(DEFAULT_GATES)


def validate_settings(incoming, current=None):
    """Coerce + range-check a settings patch. Returns (clean_patch, errors)."""
    clean, errors = {}, []
    for key, raw in (incoming or {}).items():
        field = SETTINGS_SCHEMA.get(key)
        if not field:
            errors.append(f"unknown setting: {key}")
            continue
        kind = field["type"]
        try:
            if kind == "gates":
                if not isinstance(raw, dict):
                    raise ValueError("must be an object")
                bad = [k for k in raw if k not in GATE_KEYS]
                if bad:
                    raise ValueError(f"unknown gates: {', '.join(sorted(bad))}")
                clean[key] = {k: bool(v.get("enabled", v) if isinstance(v, dict) else v) for k, v in raw.items()}
                continue
            if kind == "symbols":
                if raw in (None, ""):
                    clean[key] = []
                    continue
                if not isinstance(raw, list):
                    raise ValueError("must be a list of symbols")
                clean[key] = [str(x).strip().upper() for x in raw if str(x).strip()]
                continue
            if kind == "symbol":
                value = str(raw or "").strip().upper()
                if not value:
                    raise ValueError("must not be empty")
                clean[key] = value
                continue
            if kind == "bool":
                clean[key] = bool(raw)
                continue
            if kind == "select":
                value = str(raw)
                if value not in (field.get("options") or []):
                    raise ValueError(f"must be one of {', '.join(field.get('options') or [])}")
                clean[key] = value
                continue
            if kind == "int":
                value = int(round(float(raw)))
            else:
                value = float(raw)
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError("must be a finite number")
            if "min" in field and value < field["min"]:
                raise ValueError(f"must be >= {field['min']}")
            if "max" in field and value > field["max"]:
                raise ValueError(f"must be <= {field['max']}")
            clean[key] = value
        except (TypeError, ValueError) as e:
            errors.append(f"{key}: {e}")
    return clean, errors


def apply_exchange_fees(patch, exchange=None):
    """Fill fee_rate/slippage from the exchange schedule unless the patch sets them."""
    ex = exchange or patch.get("exchange")
    fees = EXCHANGE_FEES.get(str(ex))
    if not fees:
        return patch
    style = str(patch.get("fee_style") or "maker").lower()
    if "fee_rate" not in patch:
        patch["fee_rate"] = fees["taker_fee_rate"] if style == "taker" and "taker_fee_rate" in fees else fees["fee_rate"]
    patch.setdefault("slippage_rate", fees["slippage_rate"])
    patch.setdefault("fee_style", style)
    return patch


@dataclass
class State:
    running: bool = False; mode: str = "paper"; exchange: str = "binance"; symbol: str = "INJUSDT"; price: float = 0; action: str = "HOLD"
    cash: float = 10; coin: float = 0; entry: float = 0; pnl: float = 0; realized_pnl: float = 0; equity: float = 10
    trades: int = 0; wins: int = 0; spread_bps: float = 0; imbalance: float = 0; error: str = ""
    halted: bool = False; expectancy: float = 0; halt_until: float = 0

class Bot:
    def __init__(self):
        self.api, self.state, self.settings = Binance(), State(), {
            # Holdout-validated HF VWAP/BB scalp. Maker fees are required for +EV scalps;
            # switch fee_style=taker only if you accept far fewer trades + higher edge floor.
            "interval": "1m", "poll_seconds": 2, "position_pct": 100, "leverage": 5, "min_gain_usd": .1,
            "gates": dict(DEFAULT_GATES),
            "fee_rate": .0002, "slippage_rate": .0001,
            "take_profit_pct": .35, "stop_loss_pct": .2, "min_profit_pct": .12, "max_hold_bars": 6, "trail_pct": .15,
            "max_spread_bps": 12, "min_book_imbalance": .02, "rsi_buy": 45, "rsi_sell": 50, "rsi_period": 4,
            "rsi2_buy": -1, "rsi2_sell": 70, "sma_fast": 8, "sma_slow": 21, "vwap_period": 20, "bb_period": 20, "bb_std": 2,
            "volume_period": 20, "min_volume_ratio": .25, "vwap_distance_pct": .05, "cooldown_bars": 1, "buy_score": 2,
            "breakout_period": 5, "breakout_bps": 1, "breakout_volume_ratio": 1.1, "min_atr_pct": .05,
            "min_trend_slope_pct": -.05, "trend_tolerance_pct": 1.1, "atr_period": 14, "atr_tp_mult": 3.0, "atr_sl_mult": .5,
            "fee_edge_mult": 3, "atr_fee_lambda": 1.0, "vwap_z_entry": 0, "auto_scan": True, "scan_symbols": 12, "scan_seconds": 5,
            "min_entry_score": 2, "expectancy_window": 40, "expectancy_halt": True, "halt_cooldown_sec": 600, "max_positions": 4, "fee_style": "maker", "live_post_only": True,
            "scan_whitelist": ["INJUSDT", "SOPHUSDT", "REZUSDT", "THEUSDT", "HOLOUSDT", "UNIUSDT", "PYTHUSDT", "SUIUSDT"],
            "ai_scan": True,
        }
        self.lock, self.db_lock, self.clients, self.stop_event = threading.Lock(), threading.RLock(), [], threading.Event()
        self.db = sqlite3.connect(DB, check_same_thread=False, isolation_level=None); self.last_scan = 0; self.entry_ts = 0.0; self.peak_price = 0.0
        try:
            self.db.execute("pragma journal_mode=WAL")
            self.db.execute("pragma busy_timeout=3000")
        except Exception:
            pass
        self.margin = 0.0
        self.trade_pnls = []
        self.db.execute("create table if not exists events (ts real, kind text, data text)")
        self.db.execute("create table if not exists portfolio (id integer primary key check (id=1), cash real, coin real, entry real, symbol text, trades integer, wins integer, entry_ts real, peak real, pnls text, margin real)")
        self.db.execute("create table if not exists configs (name text primary key, label text, data text, updated real)")
        self.db.execute("create table if not exists kv (key text primary key, value text)")
        self.backtest_job = {"status": "idle", "pct": 0, "phase": "", "result": None, "error": None, "symbol": None, "started": 0}
        self.jobs, self.job_seq = {}, 0
        self.entry_fee, self.entry_notional = 0.0, 0.0
        cols = {r[1] for r in self.db.execute("pragma table_info(portfolio)")}
        for name, ddl in (("margin", "real default 0"), ("entry_fee", "real default 0"), ("entry_notional", "real default 0")):
            if name not in cols:
                try: self.db.execute(f"alter table portfolio add column {name} {ddl}")
                except Exception: pass
        self.db.commit()
        self.last_decision = None
        self.slots = {}
        self._load_portfolio()
        self._load_slots()
        self._load_settings()
        self._load_mode()
        self._reconcile_trade_events()
    def _kv_get(self, key):
        try:
            row = self._db("select value from kv where key=?", (key,)).fetchone()
        except Exception:
            return None
        return row[0] if row else None
    def _kv_set(self, key, value):
        try:
            self._db("insert into kv(key,value) values (?,?) on conflict(key) do update set value=excluded.value", (key, value))
        except Exception as e:
            print(f"kv: could not persist {key}: {e}", file=sys.stderr)
    def _load_settings(self):
        raw = self._kv_get("settings")
        if not raw: return
        try:
            stored = json.loads(raw)
        except Exception as e:
            print(f"settings: ignoring corrupt stored value ({e}); using defaults", file=sys.stderr)
            return
        if not isinstance(stored, dict): return
        clean, errors = validate_settings(stored)
        for e in errors: print(f"settings: dropping stored value — {e}", file=sys.stderr)
        # A stored value still equal to the superseded default was never hand-tuned, so lift
        # it to the corrected default; a deliberate override (any other value) is preserved.
        for key, (old, new) in SUPERSEDED_DEFAULTS.items():
            if clean.get(key) == old:
                clean[key] = new
        self.settings.update(clean)
        if clean.get("symbol"):
            self.state.symbol = clean["symbol"]
        if clean.get("exchange"):
            self.state.exchange = clean["exchange"]
            self.api = EXCHANGES[self.state.exchange]()
    def _save_settings(self):
        persistable = {k: v for k, v in self.settings.items() if k in SETTINGS_SCHEMA}
        self._kv_set("settings", json.dumps(persistable))
    def _load_mode(self):
        mode = self._kv_get("mode")
        if mode in ("paper", "live"):
            self.state.mode = mode
    def exchange_keys(self):
        """Binance family clients all read BINANCE_* env vars."""
        key = (os.getenv("BINANCE_API_KEY") or "").strip()
        secret = (os.getenv("BINANCE_API_SECRET") or "").strip()
        return {"present": bool(key and secret), "key_hint": f"...{key[-4:]}" if len(key) >= 4 else "", "env": ["BINANCE_API_KEY", "BINANCE_API_SECRET"]}
    def set_mode(self, mode, confirm=False):
        if mode not in ("paper", "live"):
            raise ValueError("mode must be paper or live")
        if mode == "live":
            if not confirm:
                raise RuntimeError("live mode requires explicit confirmation")
            keys = self.exchange_keys()
            if not keys["present"]:
                raise RuntimeError(f"live mode needs {' + '.join(keys['env'])} in .env")
            if float(self.settings.get("position_pct", 100)) > 100:
                raise RuntimeError("live mode requires position_pct <= 100")
        with self.lock:
            self.state.mode = mode
            if mode == "paper":
                self.state.halted = False
            self._kv_set("mode", mode)
            self.emit("mode", {"mode": mode, "exchange": self.state.exchange, "keys": self.exchange_keys()["present"]})
        return self.state.mode
    # ---- async job registry (backtest / optimize / deep history) ----
    def new_job(self, kind, **meta):
        with self.db_lock:
            self.job_seq += 1
            jid = f"{kind}-{self.job_seq}"
            self.jobs[jid] = {"id": jid, "kind": kind, "status": "queued", "pct": 0, "phase": "queued",
                              "started": time.time(), "result": None, "error": None, **meta}
            if len(self.jobs) > 40:
                for stale in sorted(self.jobs, key=lambda j: self.jobs[j]["started"])[: len(self.jobs) - 40]:
                    self.jobs.pop(stale, None)
            return jid
    def job(self, jid):
        with self.db_lock:
            found = self.jobs.get(jid)
            return dict(found) if found else None
    def _job_set(self, jid, **fields):
        with self.db_lock:
            if jid in self.jobs: self.jobs[jid].update(fields)
    def _load_portfolio(self):
        row = self._db("select cash, coin, entry, symbol, trades, wins, entry_ts, peak, pnls, margin, entry_fee, entry_notional from portfolio where id=1").fetchone()
        if not row: return
        self.state.cash, self.state.coin, self.state.entry = float(row[0]), float(row[1]), float(row[2])
        if row[3]: self.state.symbol = row[3]
        self.state.trades, self.state.wins = int(row[4] or 0), int(row[5] or 0)
        self.entry_ts, self.peak_price = float(row[6] or 0), float(row[7] or 0)
        try: self.trade_pnls = json.loads(row[8] or "[]")
        except Exception: self.trade_pnls = []
        self.margin = float(row[9] or 0) if len(row) > 9 else 0.0
        self.entry_fee = float(row[10] or 0) if len(row) > 10 else 0.0
        self.entry_notional = float(row[11] or 0) if len(row) > 11 else 0.0
        if self.trade_pnls:
            recent = self.trade_pnls[-40:]
            self.state.expectancy = sum(recent) / len(recent)
            self.state.realized_pnl = sum(self.trade_pnls)
        mark = self.state.price or self.state.entry or 0
        unreal = (mark - self.state.entry) * self.state.coin if self.state.coin else 0.0
        self.state.equity = self.state.cash + self.margin + unreal
    def _db(self, sql, params=()):
        """Serialize all sqlite use — concurrent emit/save was causing InterfaceError misuse."""
        with self.db_lock:
            cur = self.db.execute(sql, params)
            return cur

    def _save_portfolio(self):
        self._db(
            "insert into portfolio(id,cash,coin,entry,symbol,trades,wins,entry_ts,peak,pnls,margin,entry_fee,entry_notional) values (1,?,?,?,?,?,?,?,?,?,?,?,?) "
            "on conflict(id) do update set cash=excluded.cash, coin=excluded.coin, entry=excluded.entry, symbol=excluded.symbol, "
            "trades=excluded.trades, wins=excluded.wins, entry_ts=excluded.entry_ts, peak=excluded.peak, pnls=excluded.pnls, margin=excluded.margin, "
            "entry_fee=excluded.entry_fee, entry_notional=excluded.entry_notional",
            (self.state.cash, self.state.coin, self.state.entry, self.state.symbol, self.state.trades, self.state.wins, self.entry_ts, self.peak_price, json.dumps(self.trade_pnls[-200:]), self.margin, self.entry_fee, self.entry_notional),
        )
    def _reconcile_trade_events(self):
        """Drop orphan trade events when paper book was reset (trades=0, empty pnls)."""
        if self.state.trades == 0 and not self.trade_pnls:
            self._db("delete from events where kind='trade'")
    def emit(self, kind, data):
        payload = json.dumps({"kind": kind, "data": data, "ts": time.time()})
        try:
            self._db("insert into events values (?,?,?)", (time.time(), kind, payload))
        except Exception as e:
            # Never let logging crash trading / stop.
            self.state.error = f"db:{e}"
        for q in list(self.clients):
            try: q.put_nowait(payload)
            except queue.Full:
                try: self.clients.remove(q)
                except ValueError: pass
    def max_positions(self):
        return max(1, int(self.settings.get("max_positions", 1) or 1))

    def open_slots(self):
        return len(self.slots)

    def _sync_focus(self, prefer=None):
        """Mirror one open slot into legacy State fields for the UI."""
        if not self.slots:
            self.state.coin = 0.0
            self.state.entry = 0.0
            self.margin = 0.0
            self.entry_ts = self.peak_price = 0.0
            self.entry_fee = self.entry_notional = 0.0
            # Keep last realized pnl on state.pnl (do not zero here).
            return
        sym = prefer or (self.state.symbol if self.state.symbol in self.slots else next(iter(self.slots)))
        p = self.slots[sym]
        self.state.symbol = sym
        self.state.coin = p["coin"]
        self.state.entry = p["entry"]
        self.margin = p["margin"]
        self.entry_ts = p["entry_ts"]
        self.peak_price = p["peak"]
        self.entry_fee = p["entry_fee"]
        self.entry_notional = p["entry_notional"]

    def _equity_mark(self, marks=None):
        marks = marks or {}
        unreal = 0.0
        margin_sum = 0.0
        for sym, p in self.slots.items():
            px = marks.get(sym, self.state.price if sym == self.state.symbol else p["entry"])
            unreal += (px - p["entry"]) * p["coin"]
            margin_sum += p["margin"]
        return self.state.cash + margin_sum + unreal

    def _save_slots(self):
        try:
            self._kv_set("slots", json.dumps(self.slots))
        except Exception as e:
            print(f"slots: persist failed: {e}", file=sys.stderr)

    def _load_slots(self):
        raw = self._kv_get("slots")
        self.slots = {}
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    self.slots = {str(k): v for k, v in data.items() if isinstance(v, dict) and float(v.get("coin") or 0) > 0}
            except Exception as e:
                print(f"slots: ignoring corrupt store ({e})", file=sys.stderr)
        # Migrate legacy single portfolio row into slots if needed.
        if not self.slots and float(self.state.coin or 0) > 0 and self.state.symbol:
            self.slots[self.state.symbol] = {
                "coin": float(self.state.coin), "entry": float(self.state.entry), "margin": float(self.margin or 0),
                "entry_ts": float(self.entry_ts or 0), "peak": float(self.peak_price or self.state.entry or 0),
                "entry_fee": float(self.entry_fee or 0), "entry_notional": float(self.entry_notional or 0),
            }
            self._save_slots()
        self._sync_focus()

    def _live_submit(self, symbol, side, qty, price, reduce_only=False):
        """Send HMAC futures order; return (avg_price, filled_qty, raw) or raise."""
        if self.state.exchange != "binance" or not hasattr(self.api, "place_order"):
            raise RuntimeError("HMAC live orders currently supported for Binance USDT-M only")
        if not hasattr(self.api, "fapi_request"):
            raise RuntimeError("exchange client missing signed futures API")
        post_only = bool(self.settings.get("live_post_only", True)) and str(self.settings.get("fee_style", "maker")).lower() == "maker"
        # Prefer book prices for maker joins.
        try:
            book = self.api.orderbook(symbol)
        except Exception:
            book = None
        if post_only and book:
            price = book["bid"] if side == "BUY" else book["ask"]
        if hasattr(self.api, "set_leverage") and not reduce_only:
            try:
                self.api.set_leverage(symbol, leverage_of(self.settings))
            except Exception as e:
                # Non-fatal if leverage already set / restricted.
                print(f"live: set_leverage warn: {e}", file=sys.stderr)
        order = self.api.place_order(symbol, side, qty, price=price, reduce_only=reduce_only, post_only=post_only)
        oid = order.get("orderId")
        filled = self.api.wait_order(symbol, oid) if oid is not None and hasattr(self.api, "wait_order") else order
        exec_qty = float(filled.get("executedQty") or 0)
        avg = float(filled.get("avgPrice") or 0) or float(filled.get("price") or price or 0)
        status = str(filled.get("status") or "")
        if exec_qty <= 0 or status not in ("FILLED", "PARTIALLY_FILLED"):
            raise RuntimeError(f"live order not filled ({status})")
        return avg, exec_qty, filled

    def open_position(self, price, symbol=None):

        """Enter leveraged long (paper futures); supports concurrent slots via max_positions."""
        symbol = (symbol or self.state.symbol or "").upper()
        if price <= 0 or not symbol: return False
        if symbol in self.slots: return False
        max_pos = self.max_positions()
        if len(self.slots) >= max_pos: return False
        free = max_pos - len(self.slots)
        lev = leverage_of(self.settings)
        margin = self.state.cash * float(self.settings.get("position_pct", 100)) / 100 / free
        fee_rate = float(self.settings.get("fee_rate", .0002)) + float(self.settings.get("slippage_rate", .0001))
        notional = margin * lev
        open_fee = notional * fee_rate
        if margin + open_fee > self.state.cash:
            margin = max(0.0, self.state.cash / (1 + fee_rate * lev) / free)
            notional = margin * lev
            open_fee = notional * fee_rate
        if margin <= 0 or notional <= 0: return False
        qty = notional / price
        live_raw = None
        if self.state.mode == "live":
            try:
                price, qty, live_raw = self._live_submit(symbol, "BUY", qty, price, reduce_only=False)
                notional = qty * price
                open_fee = notional * fee_rate
                # Keep margin consistent with filled notional / lev
                margin = notional / lev if lev else margin
            except Exception as e:
                self.state.error = f"live_open:{e}"
                self.emit("error", self.state.error)
                self.emit("live_order", {"intent": "open_long", "symbol": symbol, "status": "rejected", "error": str(e)})
                return False
        # Scale the min-gain floor to this slot's notional share so exit economics
        # match the full-margin backtest that qualified the params ($min_gain per
        # full-notional deployment, not per slot).
        full_margin = max(1e-9, self.state.cash * float(self.settings.get("position_pct", 100)) / 100)
        gain_floor = min_gain_usd(self.settings) * min(1.0, margin / full_margin)
        self.state.cash -= (margin + open_fee)
        self.slots[symbol] = {
            "coin": qty, "entry": price, "margin": margin, "entry_ts": time.time(), "peak": price,
            "entry_fee": open_fee, "entry_notional": notional, "gain_floor": gain_floor,
        }
        self.state.symbol = symbol
        self._sync_focus(symbol)
        self.state.action = "BUY"
        self.state.pnl = 0.0
        self.state.equity = self._equity_mark({symbol: price})
        self._save_portfolio(); self._save_slots()
        fills = {"side": "BUY", "symbol": symbol, "price": price, "quantity": qty, "pnl": None,
                 "leverage": lev, "margin": margin, "notional": notional, "fee_paid": open_fee, "fee_entry": open_fee,
                 "entry": price, "mode": self.state.mode, "slots": len(self.slots), "max_positions": max_pos,
                 "realized_pnl": self.state.realized_pnl, "cash": self.state.cash, "coin": qty,
                 "trades": self.state.trades, "wins": self.state.wins, "equity": self.state.equity}
        self.emit("trade", fills)
        if self.state.mode == "live":
            self.emit("live_order", {**fills, "intent": "open_long", "exchange": live_raw})
        return True

    def close_position(self, price, reason="signal", symbol=None):
        """Flatten one leveraged long slot; cash receives margin + futures PnL."""
        symbol = (symbol or self.state.symbol or "").upper()
        pos = self.slots.get(symbol)
        if not pos or price <= 0: return False
        qty = pos["coin"]
        entry_px, entry_ts = pos["entry"], pos["entry_ts"]
        entry_fee, entry_notional = pos["entry_fee"], pos["entry_notional"]
        margin = pos["margin"]
        lev = leverage_of(self.settings)
        fee = float(self.settings.get("fee_rate", .0002)) + float(self.settings.get("slippage_rate", .0001))
        live_raw = None
        if self.state.mode == "live":
            try:
                price, qty, live_raw = self._live_submit(symbol, "SELL", qty, price, reduce_only=True)
            except Exception as e:
                self.state.error = f"live_close:{e}"
                self.emit("error", self.state.error)
                self.emit("live_order", {"intent": "close_long", "symbol": symbol, "status": "rejected", "error": str(e)})
                return False
        exit_notional = qty * price
        exit_fee = exit_notional * fee
        gross = (price - entry_px) * qty
        pnl = net_pnl(entry_px, price, qty, self.settings)
        win = 1 if pnl > 0 else 0
        self.state.cash += margin + pnl
        del self.slots[symbol]
        self.state.trades += 1
        self.state.wins += win
        self.state.pnl = pnl
        self.state.action = "SELL"
        bar_sec = INTERVAL_SECONDS.get(self.settings.get("interval", "1m"), 60)
        hold_bars = int((time.time() - entry_ts) / bar_sec) if entry_ts else 0
        self.trade_pnls.append(pnl)
        window = int(self.settings.get("expectancy_window", 40))
        recent = self.trade_pnls[-window:]
        self.state.expectancy = sum(recent) / len(recent) if recent else 0.0
        if self.settings.get("expectancy_halt", True) and len(recent) >= max(20, window // 2) and self.state.expectancy < 0:
            # Timed pause, not a latch: a rolling window dips negative often at scalp
            # cadence, and a permanent halt stopped the bot trading altogether.
            self.state.halted = True
            self.state.halt_until = time.time() + float(self.settings.get("halt_cooldown_sec", 600))
            self.emit("halt", {"expectancy": self.state.expectancy, "window": len(recent),
                               "resume_in": float(self.settings.get("halt_cooldown_sec", 600))})
        self.state.realized_pnl = sum(self.trade_pnls)
        self._sync_focus()
        self.state.equity = self._equity_mark()
        self._save_portfolio(); self._save_slots()
        fills = {"side": "SELL", "symbol": symbol, "price": price, "proceeds": self.state.cash,
                 "win": bool(win), "pnl": pnl, "reason": reason, "leverage": lev, "mode": self.state.mode,
                 "entry": entry_px, "exit": price, "quantity": qty, "margin": margin, "notional": entry_notional or qty * entry_px,
                 "fee_paid": entry_fee + exit_fee, "fee_entry": entry_fee, "fee_exit": exit_fee, "gross_pnl": gross,
                 "return_margin_pct": (pnl / margin * 100) if margin else 0.0, "hold_bars": hold_bars,
                 "entry_time": entry_ts, "exit_time": time.time(), "slots": len(self.slots),
                 "realized_pnl": self.state.realized_pnl, "cash": self.state.cash, "coin": self.state.coin,
                 "trades": self.state.trades, "wins": self.state.wins, "equity": self.state.equity}
        self.emit("trade", fills)
        if self.state.mode == "live":
            self.emit("live_order", {**fills, "intent": "close_long", "exchange": live_raw})
        return True

    def save_config(self, name, data, label=None):
        name = (name or "").strip()
        if not name: raise ValueError("config name required")
        payload = json.dumps(data)
        self._db(
            "insert into configs(name,label,data,updated) values (?,?,?,?) "
            "on conflict(name) do update set label=excluded.label, data=excluded.data, updated=excluded.updated",
            (name, label or name, payload, time.time()),
        )
        return {"name": name, "label": label or name, "updated": time.time()}

    def list_configs(self):
        rows = self._db("select name, label, updated from configs order by updated desc").fetchall()
        builtins = [{"name": k, "label": v["label"], "updated": 0, "builtin": True} for k, v in PRESETS.items()]
        user = [{"name": r[0], "label": r[1] or r[0], "updated": r[2], "builtin": False} for r in rows]
        # builtins first if no collision name in user
        names = {u["name"] for u in user}
        return [b for b in builtins if b["name"] not in names] + user

    def load_config(self, name):
        if name in PRESETS:
            return {"name": name, "label": PRESETS[name]["label"], "builtin": True, "data": {"settings": dict(PRESETS[name]["settings"])}}
        row = self._db("select name, label, data, updated from configs where name=?", (name,)).fetchone()
        if not row: raise KeyError(f"config not found: {name}")
        return {"name": row[0], "label": row[1], "builtin": False, "updated": row[3], "data": json.loads(row[2])}

    def delete_config(self, name):
        if name in PRESETS: raise ValueError("cannot delete builtin preset")
        self._db("delete from configs where name=?", (name,))
        return {"deleted": name}

    def run_backtest_async(self, symbol=None, history=False, holdout=False):
        with self.lock:
            if self.backtest_job.get("status") == "running":
                raise RuntimeError("backtest already running")
            self.backtest_job = {"status": "running", "pct": 0, "phase": "loading", "result": None, "error": None,
                                "symbol": symbol or self.state.symbol, "started": time.time()}
        job_id = self.new_job("backtest", symbol=symbol or self.state.symbol, history=history)
        self.backtest_job["job_id"] = job_id
        def job():
            try:
                sym = symbol or self.state.symbol
                self.backtest_job.update({"phase": "loading", "pct": 1})
                self._job_set(job_id, status="running", phase="loading", pct=1)
                self.emit("backtest", dict(self.backtest_job))
                loader = getattr(self.api, "history", self.api.candles)
                limit = 10000 if history else 1000
                def on_page(pages):
                    self.backtest_job.update({"phase": f"loading page {pages}", "pct": min(4, pages)})
                    self._job_set(job_id, phase=f"loading page {pages}", pct=min(4, pages), pages=pages)
                candles = loader(sym, self.settings["interval"], limit, on_page=on_page) if history else loader(sym, self.settings["interval"], limit)
                if holdout:
                    candles = candles[max(0, int(len(candles) * .7)):]
                self.backtest_job.update({"phase": "simulating", "pct": 5, "bars": len(candles)})
                self._job_set(job_id, phase="simulating", pct=5, bars=len(candles))
                self.emit("backtest", dict(self.backtest_job))
                def prog(pct, done, total):
                    self.backtest_job.update({"pct": max(5, min(99, pct)), "phase": "simulating", "done": done, "total": total})
                    self._job_set(job_id, pct=max(5, min(99, pct)), phase="simulating", done=done, total=total)
                    if pct % 5 == 0 or pct >= 99:
                        self.emit("backtest", {k: self.backtest_job[k] for k in ("status", "pct", "phase", "symbol", "done", "total") if k in self.backtest_job})
                result = backtest(candles, self.settings, on_progress=prog)
                result["symbol"] = sym
                result["bars"] = len(candles)
                self.backtest_job.update({"status": "done", "pct": 100, "phase": "done", "result": result})
                self._job_set(job_id, status="done", pct=100, phase="done", result=result)
                self.emit("backtest", {"status": "done", "pct": 100, "phase": "done", "symbol": sym, "result": result})
            except Exception as e:
                self.backtest_job.update({"status": "error", "pct": 100, "phase": "error", "error": str(e)})
                self._job_set(job_id, status="error", pct=100, phase="error", error=str(e))
                self.emit("backtest", {"status": "error", "pct": 100, "phase": "error", "error": str(e)})
        threading.Thread(target=job, daemon=True).start()
        return dict(self.backtest_job)

    def run_optimize_async(self, symbol=None):
        sym = symbol or self.state.symbol
        job_id = self.new_job("optimize", symbol=sym)
        def job():
            try:
                self._job_set(job_id, status="running", phase="loading", pct=1)
                loader = getattr(self.api, "history", self.api.candles)
                def on_page(pages):
                    self._job_set(job_id, phase=f"loading page {pages}", pct=min(3, pages), pages=pages)
                candles = loader(sym, self.settings["interval"], 10000, on_page=on_page)
                self._job_set(job_id, phase="searching", pct=4, bars=len(candles))
                def prog(pct, done, total):
                    self._job_set(job_id, pct=max(4, min(99, pct)), phase="searching", done=done, total=total)
                result = optimize(candles, self.settings, on_progress=prog)
                result["symbol"] = sym
                self._job_set(job_id, status="done", pct=100, phase="done", result=result)
            except Exception as e:
                self._job_set(job_id, status="error", pct=100, phase="error", error=str(e))
        threading.Thread(target=job, daemon=True).start()
        return self.job(job_id)

    def scan_candidates(self, limit=None):
        """Rank whitelisted markets by current entry score; returns BUY-ready symbols first."""
        limit = int(limit or self.settings.get("scan_symbols", 12))
        allow = self.settings.get("scan_whitelist") or []
        symbols = list(allow)[:limit] if allow else self.api.top_symbols()[:limit]
        interval = self.settings.get("interval", "1m")
        def one(sym):
            rows = self.api.candles(sym, interval, 160)
            live = adapt_settings(rows, self.settings)
            return {"symbol": sym, "signal": signal(rows, live), "score": round(entry_score(rows, live), 3),
                    "price": rows[-1][4] if rows else None, "atr_pct": round(float(live.get("atr_pct", 0) or 0), 3)}
        out = []
        with ThreadPoolExecutor(max_workers=min(8, max(2, len(symbols) or 1))) as pool:
            for fut in as_completed([pool.submit(one, sym) for sym in symbols]):
                try: out.append(fut.result())
                except Exception: continue
        return sorted(out, key=lambda r: r["score"], reverse=True)

    def start(self):
        with self.lock:
            if self.state.running: return
            self.state.running, self.state.error, self.state.halted = True, "", False
            self.stop_event.clear()
            threading.Thread(target=self.loop, daemon=True).start()
            self.emit("status", asdict(self.state))
    def stop(self):
        # Flatten every paper slot so restarts never orphan inventory without a trade record.
        with self.lock:
            try:
                for sym in list(self.slots.keys()):
                    px = self.state.price if sym == self.state.symbol and self.state.price > 0 else self.slots[sym]["entry"]
                    try:
                        px = self.api.ticker(sym)
                    except Exception:
                        pass
                    if px and px > 0:
                        self.close_position(px, reason="stop", symbol=sym)
            except Exception as e:
                self.state.error = str(e)
            self.state.running = False
            # Clear sticky loop errors so /api/stop response isn't treated as failure by the UI.
            if self.state.error.startswith("bad parameter") or self.state.error.startswith("db:"):
                self.state.error = ""
            else:
                self.state.error = ""
            self.stop_event.set()
            try:
                self.emit("status", asdict(self.state))
            except Exception:
                pass
    def clear_stats(self):
        """Clear trades/stats history; keeps current cash/portfolio intact."""
        with self.lock:
            try:
                self._db("delete from events where kind='trade'")
            except Exception as e:
                self.emit("error", f"clear_stats:{e}")
            self.trade_pnls = []
            self.state.trades = 0
            self.state.wins = 0
            self.state.realized_pnl = 0.0
            self.state.pnl = 0.0
            self.state.halted = False
            self.state.expectancy = 0.0
            self.state.error = ""
            self._save_portfolio()
            self.emit("status", asdict(self.state))
            return asdict(self.state)

    def set_exchange(self, name):
        if name not in EXCHANGES: raise ValueError("exchange must be binance, kucoin, or mexc")
        if name == self.state.exchange: return
        if self.state.running: raise RuntimeError("stop the bot before changing exchange")
        self.state.exchange, self.api = name, EXCHANGES[name]()
    def _manage_slot_exit(self, symbol, candles, live, interval_sec):
        """Apply TP/SL/trail/time/signal exits for one open slot. Returns (acted, price)."""
        pos = self.slots.get(symbol)
        if not pos or not candles:
            return False, None
        o, h, l, price = candles[-1][1], candles[-1][2], candles[-1][3], candles[-1][4]
        action = signal(candles, live)
        pos["peak"] = max(float(pos.get("peak") or price), h, price)
        trail = float(live.get("trail_pct", 0) or 0)
        slot_live = live
        if pos.get("gain_floor") is not None:
            slot_live = dict(live); slot_live["min_gain_usd"] = float(pos["gain_floor"])
        tp_pct, sl_pct = dynamic_risk(candles, slot_live, entry=pos["entry"], qty=pos["coin"])
        tp = pos["entry"] * (1 + tp_pct / 100)
        sl = pos["entry"] * (1 - sl_pct / 100)
        min_gain = (float(pos["gain_floor"]) if pos.get("gain_floor") is not None else min_gain_usd(live)) if gate_on(live, "min_gain") else 0.0
        gain_px = pos["entry"] * (1 + min_tp_pct_for_gain(pos["entry"], pos["coin"], slot_live) / 100) if min_gain > 0 else pos["entry"]
        trail_px = pos["peak"] * (1 - trail / 100) if trail > 0 else price
        peak_pnl = net_pnl(pos["entry"], pos["peak"], pos["coin"], live)
        trail_exit = min(price, trail_px) if trail > 0 else price
        trail_pnl = net_pnl(pos["entry"], trail_exit, pos["coin"], live)
        fee_floor_usd = (float(live.get("fee_rate", .0002)) + float(live.get("slippage_rate", .0001))) * pos["entry"] * pos["coin"] * 2
        hit_bank = h >= gain_px or pos["peak"] >= gain_px
        hit_tp = h >= tp or pos["peak"] >= tp
        hit_sl = l <= sl
        hit_trail = trail > 0 and peak_pnl > min_gain and (l <= trail_px or price <= trail_px) and trail_pnl > fee_floor_usd
        bar_sec = interval_sec.get(self.settings.get("interval", "1m"), 60)
        held_bars = int((time.time() - pos["entry_ts"]) / bar_sec) if pos.get("entry_ts") else 0
        max_hold = int(live.get("max_hold_bars", self.settings.get("max_hold_bars", 6)))
        hit_time = held_bars >= max_hold and net_pnl(pos["entry"], price, pos["coin"], live) > min_gain
        hit_hard_time = held_bars >= max(6, max_hold * 2)
        exit_px, reason = None, "signal"
        if hit_sl:
            exit_px, reason = sl, "stop"
        elif hit_tp or hit_bank:
            if h >= tp:
                exit_px, reason = tp, "take_profit"
            elif h >= gain_px:
                exit_px, reason = max(gain_px, min(h, tp)), "min_gain"
            elif pos["peak"] >= gain_px:
                exit_px, reason = max(gain_px, min(pos["peak"], tp)), "peak_bank"
        elif hit_trail:
            exit_px, reason = min(price, trail_px), "trail"
        elif hit_time:
            exit_px, reason = price, "time"
        elif hit_hard_time:
            exit_px, reason = price, "time_hard"
        elif action == "SELL" and net_pnl(pos["entry"], price, pos["coin"], live) > min_gain:
            exit_px, reason = price, "signal"
        if exit_px is not None:
            self.close_position(exit_px, reason=reason, symbol=symbol)
            return True, price
        # keep peaks durable
        self.slots[symbol] = pos
        return False, price

    def loop(self):
        interval_sec = INTERVAL_SECONDS
        while self.state.running and not self.stop_event.is_set():
            try:
                if self.state.halted and time.time() >= self.state.halt_until:
                    self.state.halted = False
                    self.emit("halt_clear", {"expectancy": self.state.expectancy, "trades": self.state.trades})
                interval = self.settings["interval"]
                marks = {}
                # 1) Manage exits for every open slot (multi-symbol cadence).
                for sym in list(self.slots.keys()):
                    try:
                        candles = self.api.candles(sym, interval, 250)
                        live = adapt_settings(candles, self.settings)
                        _, px = self._manage_slot_exit(sym, candles, live, interval_sec)
                        if px: marks[sym] = px
                    except Exception as e:
                        self.state.error = f"slot:{sym}:{e}"

                # 2) Fill free slots via auto-scan when edge exists.
                free = self.max_positions() - len(self.slots)
                if self.settings.get("auto_scan", True) and free > 0 and not self.state.halted and time.time() - self.last_scan > float(self.settings.get("scan_seconds", 5)):
                    allow = self.settings.get("scan_whitelist") or []
                    tops = list(allow)[:int(self.settings.get("scan_symbols", 16))] if allow else self.api.top_symbols()[:int(self.settings.get("scan_symbols", 16))]
                    open_syms = set(self.slots)
                    def _scan_one(sym):
                        if sym in open_syms: return None
                        rows = self.api.candles(sym, interval, 160)
                        live = adapt_settings(rows, self.settings)
                        score = entry_score(rows, live)
                        if score < float(self.settings.get("min_entry_score", 0)): return None
                        raw = signal(rows, live)
                        if self.settings.get("ai_scan") and gate_on(live, "ai"):
                            ai = chart_ai_analyze(rows, live)
                            if ai.get("action") != "BUY": return None
                            score += 2 + float(ai.get("confidence") or 0)
                        elif raw != "BUY":
                            return None
                        return (score, sym, rows, live)
                    ranked = []
                    with ThreadPoolExecutor(max_workers=min(8, max(2, len(tops) or 1))) as pool:
                        for fut in as_completed([pool.submit(_scan_one, sym) for sym in tops]):
                            try:
                                item = fut.result()
                            except Exception:
                                continue
                            if item: ranked.append(item)
                    ranked.sort(key=lambda x: x[0], reverse=True)
                    self.last_scan = time.time()
                    opened = []
                    for score, sym, rows, live in ranked:
                        if len(self.slots) >= self.max_positions(): break
                        book = None
                        try: book = self.api.orderbook(sym)
                        except Exception: pass
                        if book:
                            lg = gates_of(live)
                            if lg.get("spread", True) and book["spread_bps"] > live["max_spread_bps"]: continue
                            if lg.get("imbalance", True) and book["imbalance"] < -abs(float(live.get("min_book_imbalance", .02)) * 8): continue
                            self.state.spread_bps, self.state.imbalance = book["spread_bps"], book["imbalance"]
                        px = rows[-1][4]
                        if self.open_position(px, symbol=sym):
                            opened.append({"symbol": sym, "score": score})
                            marks[sym] = px
                    self.emit("scan", {"opened": opened, "candidates": len(tops), "free": free, "slots": len(self.slots)})

                # 3) Focus symbol decision/tick for UI (create entry if flat+signal on focus).
                focus = self.state.symbol if self.state.symbol in self.slots else (self.state.symbol or "INJUSDT")
                candles = self.api.candles(focus, interval, 250)
                live = adapt_settings(candles, self.settings)
                o, h, l, price = candles[-1][1], candles[-1][2], candles[-1][3], candles[-1][4]
                marks[focus] = price
                action = signal(candles, live)
                if focus in self.slots:
                    self._sync_focus(focus)
                    self.state.price = price
                    self.state.pnl = (price - self.state.entry) * self.state.coin
                    self.state.action = "HOLD"
                else:
                    self.state.price = price
                    self.state.pnl = 0.0
                    if action == "BUY" and not self.state.halted and len(self.slots) < self.max_positions():
                        book = self.api.orderbook(focus)
                        self.state.spread_bps, self.state.imbalance = book["spread_bps"], book["imbalance"]
                        lg = gates_of(live)
                        blocked = False
                        if lg.get("spread", True) and book["spread_bps"] > live["max_spread_bps"]: blocked = True
                        if lg.get("imbalance", True) and book["imbalance"] < -abs(float(live.get("min_book_imbalance", .02)) * 8): blocked = True
                        if self.settings.get("ai_scan") and gate_on(live, "ai"):
                            if chart_ai_analyze(candles, live).get("action") != "BUY": blocked = True
                        if not blocked:
                            self.open_position(price, symbol=focus)
                        else:
                            self.state.action = "HOLD"
                    else:
                        self.state.action = action
                self.state.equity = self._equity_mark(marks)
                if not self.slots and self.trade_pnls:
                    self.state.realized_pnl = sum(self.trade_pnls)
                try:
                    book = {"spread_bps": self.state.spread_bps, "imbalance": self.state.imbalance}
                    st = asdict(self.state)
                    st["peak"] = self.peak_price
                    st["slots"] = len(self.slots)
                    st["open_symbols"] = list(self.slots)
                    self.last_decision = explain_decision(candles, live, st, book)
                    self.emit("decision", self.last_decision)
                except Exception as e:
                    self.last_decision = {"action": "HOLD", "reason": "explain_error", "checks": [], "error": str(e)}
                tick = asdict(self.state)
                tick["slots"] = len(self.slots)
                tick["open_symbols"] = list(self.slots)
                self.emit("tick", tick)
                self._save_slots()
            except Exception as e:
                self.state.error = str(e); self.emit("error", self.state.error)
            self.stop_event.wait(self.settings["poll_seconds"])



def build_market_snapshot(candles, settings, indicators, state, book, live_price=None):
    closes = [c[4] for c in candles]
    last = candles[-1] if candles else None
    price = float(live_price) if live_price else (float(closes[-1]) if closes else 0)
    inds = []
    for ind in indicators or []:
        try:
            iid = (ind.get("id") or "").lower()
            period = int(ind.get("period") or 14)
            uid = ind.get("uid") or ind.get("id")
            if iid == "ema":
                vals = ema(closes, period)
                inds.append({"id": iid, "uid": uid, "period": period, "last": vals[-1] if vals else None})
            elif iid == "sma":
                vals = sma(closes, period)
                inds.append({"id": iid, "uid": uid, "period": period, "last": vals[-1] if vals else None})
            elif iid == "rsi":
                vals = rsi(closes, period)
                inds.append({"id": iid, "uid": uid, "period": period, "last": vals[-1] if vals else None})
            elif iid == "atr":
                vals = atr(candles, period)
                inds.append({"id": iid, "uid": uid, "period": period, "last": vals[-1] if vals else None})
            elif iid == "vwap":
                vals = vwap(candles, period)
                inds.append({"id": iid, "uid": uid, "period": period, "last": vals[-1] if vals else None})
            elif iid == "bb":
                mult = float(ind.get("std") or 2)
                mid = sma(closes, period); sd = rolling_std(closes, period)
                upper = (mid[-1] + mult * sd[-1]) if mid and sd else None
                lower = (mid[-1] - mult * sd[-1]) if mid and sd else None
                inds.append({"id": iid, "uid": uid, "period": period, "last": closes[-1] if closes else None,
                             "upper": upper, "lower": lower})
            else:
                inds.append({"id": iid, "uid": uid, "period": period, "last": closes[-1] if closes else None})
        except Exception:
            continue
    try:
        action = signal(candles, settings)
    except Exception:
        action = "HOLD"
    snap = {
        "symbol": getattr(state, "symbol", "?") if hasattr(state, "symbol") else "?",
        "price": price,
        "action": action,
        "bars": len(candles),
        "last_candle": list(last) if last else None,
        "indicators": inds,
        "book": book,
        "state": asdict(state) if hasattr(state, "__dataclass_fields__") else dict(state or {}),
        "strategy": {k: settings.get(k) for k in (
            "rsi_buy", "rsi_sell", "rsi_period", "rsi2_buy", "take_profit_pct",
            "stop_loss_pct", "min_profit_pct", "vwap_z_entry", "cooldown_bars",
            "max_hold_bars", "trail_pct", "atr_tp_mult", "atr_sl_mult",
            "auto_scan", "scan_symbols") if k in (settings or {})},
    }
    return snap


def nim_analyze(snapshot, on_delta=None):
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY missing — set it in .env")
    model = os.getenv("NVIDIA_NIM_MODEL", "meta/llama-3.2-11b-vision-instruct").strip() or "meta/llama-3.2-11b-vision-instruct"
    system = ("You are a cautious crypto paper-trading analyst. Use only the provided snapshot. "
              "Reply with ONE JSON object and no markdown fences. Keys: action (BUY|SELL|HOLD), "
              "confidence (0-1), entry, stop, take_profit, rationale, risks.")
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(snapshot)}],
            "temperature": 0.2, "max_tokens": 700, "stream": bool(on_delta)}
    req = Request("https://integrate.api.nvidia.com/v1/chat/completions",
                  data=json.dumps(body).encode(),
                  headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urlopen(req, timeout=90) as r:
        if not on_delta:
            payload = json.load(r)
            try: raw = payload["choices"][0]["message"]["content"]
            except Exception: raise RuntimeError("NIM returned non-JSON analysis")
        else:
            parts = []
            for line in r:
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"): continue
                chunk = line[5:].strip()
                if chunk == "[DONE]": break
                try: delta = json.loads(chunk)["choices"][0].get("delta", {}).get("content") or ""
                except (TypeError, ValueError, KeyError, IndexError): continue
                if delta:
                    parts.append(delta)
                    on_delta(delta)
            raw = "".join(parts)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("NIM returned non-JSON analysis")
    data = json.loads(raw[start:end + 1])
    data["model"] = model
    data["raw"] = raw
    return data


BOT = Bot()

def nim_snapshot(incoming):
    symbol = incoming.get("symbol") or BOT.state.symbol
    interval = incoming.get("interval") or BOT.settings.get("interval", "1m")
    limit = min(500, max(40, int(incoming.get("limit") or 120)))
    settings = {**BOT.settings, **(incoming.get("settings") or {})}
    candles = BOT.api.candles(symbol, interval, limit)
    try: book = BOT.api.orderbook(symbol)
    except Exception: book = None
    return build_market_snapshot(candles, settings, incoming.get("indicators") or [], BOT.state, book, incoming.get("live_price"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return None

    def send_json(self, data, code=200):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_file(self, path, content_type):
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            index = DIST / "index.html"
            if not index.is_file():
                self.send_error(503, "frontend build missing; run: cd frontend && npm run build")
                return None
            self.send_file(index, "text/html")
            return None
        if path == "/api/settings":
            self.send_json({**BOT.settings, **{
                "gates": gates_of(BOT.settings),
                "exchange": BOT.state.exchange,
                "symbol": BOT.state.symbol,
            }})
            return None
        if path.startswith("/assets/") or path in ("/favicon.ico", "/favicon.svg"):
            target = DIST / path.lstrip("/")
            if target.is_file():
                ctype = {
                    ".js": "application/javascript",
                    ".css": "text/css",
                    ".svg": "image/svg+xml",
                    ".ico": "image/x-icon",
                    ".woff2": "font/woff2",
                    ".map": "application/json",
                }.get(target.suffix, "application/octet-stream")
                self.send_file(target, ctype)
                return None
        if path == "/api/state":
            self.send_json(asdict(BOT.state))
            return None
        if path == "/api/decision":
            if BOT.last_decision:
                self.send_json(BOT.last_decision)
                return None
            try:  # [RECON] try boundaries estimated; every handler below sends 502
                candles = BOT.api.candles(BOT.state.symbol, BOT.settings["interval"], 250)
                live = adapt_settings(candles, BOT.settings)
                try:  # [RECON] orderbook failure retries with book=None
                    book = BOT.api.orderbook(BOT.state.symbol)
                except Exception:
                    book = None
                BOT.last_decision = explain_decision(candles, live, asdict(BOT.state), book)
                self.send_json(BOT.last_decision)
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/symbols":
            self.send_json(BOT.api.symbols())
            return None
        if path == "/api/candles":
            try:  # [RECON] try boundaries estimated
                query = parse_qs(urlparse(self.path).query)
                symbol = query.get("symbol", [BOT.state.symbol])[0]
                interval = query.get("interval", [BOT.settings["interval"]])[0]
                limit = min(1000, max(30, int(query.get("limit", [200])[0])))
                if interval not in INTERVALS:
                    self.send_json({"error": "unsupported interval"}, 400)
                    return None
                self.send_json(BOT.api.candles(symbol, interval, limit))
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/markets":
            self.send_json(BOT.api.top_symbols())
            return None
        if path == "/api/ticker":
            try:  # [RECON] try boundaries estimated
                query = parse_qs(urlparse(self.path).query)
                symbol = query.get("symbol", [BOT.state.symbol])[0]
                self.send_json({"symbol": symbol, "price": BOT.api.ticker(symbol), "ts": time.time()})
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/trades":
            try:  # [RECON] try boundaries estimated
                rows = BOT._db("select data from events where kind='trade' order by ts desc limit 100").fetchall()
                self.send_json([json.loads(row[0]) for row in rows])
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/orderbook":
            try:  # [RECON] try boundaries estimated
                query = parse_qs(urlparse(self.path).query)
                symbol = query.get("symbol", [BOT.state.symbol])[0]
                self.send_json(BOT.api.orderbook(symbol))
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/backtest":
            try:  # [RECON] try boundaries estimated
                query = parse_qs(urlparse(self.path).query)
                symbol = query.get("symbol", [BOT.state.symbol])[0]
                loader = getattr(BOT.api, "history", BOT.api.candles)
                candles = loader(symbol, BOT.settings["interval"], 10000 if query.get("history", ["0"])[0] == "1" else 1000)
                if query.get("holdout", ["0"])[0] == "1":
                    candles = candles[:max(0, int(len(candles) * 0.7))]
                self.send_json(backtest(candles, BOT.settings))
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/backtest/status":
            job = dict(BOT.backtest_job)
            if job.get("status") != "done":
                job = {k: job.get(k) for k in ("status", "pct", "phase", "symbol", "error", "done", "total", "started")}
            self.send_json(job)
            return None
        if path == "/api/configs":
            try:  # [RECON] try boundaries estimated
                query = parse_qs(urlparse(self.path).query)
                qname = query.get("name")
                name = qname[0] if qname else None  # [RECON] default-shape estimated
                if name:
                    self.send_json(BOT.load_config(name))
                else:
                    self.send_json(BOT.list_configs())
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/presets":
            self.send_json([{"name": k, "label": v["label"]} for k, v in PRESETS.items()])
            return None
        if path == "/api/optimize":
            try:  # [RECON] try boundaries estimated
                query = parse_qs(urlparse(self.path).query)
                symbol = query.get("symbol", [BOT.state.symbol])[0]
                loader = getattr(BOT.api, "history", BOT.api.candles)
                self.send_json(optimize(loader(symbol, BOT.settings["interval"], 10000), BOT.settings))
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/settings/schema":
            self.send_json({
                "schema": dict(SETTINGS_SCHEMA),
                "groups": list(SETTINGS_GROUPS_ORDER),
                "gates": gates_of(BOT.settings),
                "presets": [{"name": k, "label": v.get("label", k)} for k, v in PRESETS.items()],
                "intervals": list(INTERVALS),
            })
            return None
        if path == "/api/exchanges":
            self.send_json([{"id": eid, "label": EXCHANGE_FEES.get(eid, {}).get("label", eid),
                             "fee_rate": float(EXCHANGE_FEES.get(eid, {}).get("fee_rate", 0.0002)),
                             "slippage_rate": float(EXCHANGE_FEES.get(eid, {}).get("slippage_rate", 0.0001))}
                            for eid in EXCHANGES])
            return None
        if path == "/api/help":
            self.send_json(dict(HELP_TEXT))
            return None
        if path == "/api/mode":
            keys = BOT.exchange_keys()
            self.send_json({"mode": BOT.state.mode, "exchange": BOT.state.exchange,
                            "keys": keys, "exchanges": list(EXCHANGES)})
            return None
        if path.startswith("/api/jobs/"):
            jid = path[len("/api/jobs/"):]
            found = BOT.job(jid)
            if not found:
                self.send_json({"error": f"unknown job: {jid}"}, 404)
            else:
                self.send_json(found)
            return None
        if path == "/api/scan":
            try:
                limit = int((parse_qs(urlparse(self.path).query).get("limit") or ["12"])[0])
            except Exception:
                limit = 12
            try:
                self.send_json(BOT.scan_candidates(limit))
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/events":
            q = queue.Queue(maxsize=100)
            BOT.clients.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            while True:
                self.wfile.write(("data: " + q.get(timeout=20) + "\n\n").encode())
                self.wfile.flush()
            self.send_error(404)
            return None
        self.send_error(404)
        return None

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/start":
            BOT.start()
            self.send_json(asdict(BOT.state))
            return None
        if path == "/api/stop":
            BOT.stop()
            self.send_json(asdict(BOT.state))
            return None
        if path == "/api/settings":
            try:
                size = int(self.headers.get("Content-Length", 0))
                incoming = json.loads(self.rfile.read(size))
                if not isinstance(incoming, dict):
                    self.send_json({"error": "settings must be an object"}, 400)
                    return None
                if "exchange" in incoming:
                    BOT.set_exchange(incoming["exchange"])
                if "sma_period" in incoming:
                    incoming["sma_slow"] = incoming["sma_period"]
                BOT.settings.update(incoming)
                BOT.state.symbol = incoming.get("symbol", BOT.state.symbol)
                self.send_json(asdict(BOT.state))
            except (TypeError, ValueError):
                self.send_json({"error": "settings must be valid JSON"}, 400)
            except (ValueError, RuntimeError) as e:
                self.send_json({"error": str(e)}, 400)
            return None
        if path == "/api/backtest/run":
            try:
                size = int(self.headers.get("Content-Length", 0))
                try:
                    incoming = json.loads(self.rfile.read(size) or b"{}")
                except (TypeError, ValueError):
                    incoming = {}
                if not isinstance(incoming, dict):
                    incoming = {}
                BOT.run_backtest_async(
                    symbol=incoming.get("symbol") or BOT.state.symbol,
                    history=bool(incoming.get("history")),
                    holdout=bool(incoming.get("holdout")),
                )
            except Exception as e:
                self.send_json({"error": str(e)}, 409)  # [RECON] 409 code from consts
            return None
        if path == "/api/configs":
            try:
                size = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(size)
                incoming = json.loads(raw) if raw else {}
                if not isinstance(incoming, dict):
                    self.send_json({"error": "body must be an object"}, 400)
                    return None
                name = incoming.get("name")
                data = incoming.get("data")
                if not data:
                    settings = incoming.get("settings") or {**BOT.settings, "gates": gates_of(BOT.settings)}
                    data = {"settings": settings, "indicators": incoming.get("indicators")}
                self.send_json(BOT.save_config(name, data, label=incoming.get("label")))
            except (TypeError, ValueError):
                self.send_json({"error": "body must be valid JSON"}, 400)
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return None
        if path == "/api/configs/delete":
            try:
                size = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(size)
                incoming = json.loads(raw) if raw else {}
                self.send_json(BOT.delete_config((incoming or {}).get("name")))
            except (TypeError, ValueError):
                self.send_json({"error": "body must be valid JSON"}, 400)
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return None
        if path == "/api/configs/apply":
            try:
                size = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(size)
                incoming = json.loads(raw) if raw else {}
                cfg = BOT.load_config((incoming or {}).get("name"))
                data = cfg.get("data") or {}
                settings = data.get("settings") or {}
                BOT.settings.update(settings)
                if "symbol" in settings:
                    BOT.state.symbol = settings["symbol"]
                self.send_json({
                    "ok": True,
                    "config": cfg,
                    "state": asdict(BOT.state),
                    "settings": {**BOT.settings, **{
                        "exchange": BOT.state.exchange,
                        "symbol": BOT.state.symbol,
                    }},
                })
            except (TypeError, ValueError):
                self.send_json({"error": "body must be valid JSON"}, 400)
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return None
        if path == "/api/clear":
            try:
                self.send_json(BOT.clear_stats())
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return None
        if path == "/api/analyze":
            try:
                size = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(size)
                incoming = json.loads(raw) if raw else {}
                if not isinstance(incoming, dict):
                    self.send_json({"error": "body must be an object"}, 400)
                    return None
                symbol = incoming.get("symbol") or BOT.state.symbol
                interval = incoming.get("interval") or BOT.settings.get("interval", "1m")
                limit = min(500, max(40, int(incoming.get("limit") or 120)))
                settings = {**BOT.settings, **(incoming.get("settings") or {})}
                candles = BOT.api.candles(symbol, interval, limit)
                try:  # [RECON] orderbook failure retries snapshot with book=None
                    book = BOT.api.orderbook(symbol)
                except Exception:
                    book = None
                snap = build_market_snapshot(
                    candles, settings, incoming.get("indicators") or [],
                    BOT.state, book, incoming.get("live_price"),
                )
                self.send_json(nim_analyze(snap))
            except (TypeError, ValueError):
                self.send_json({"error": "body must be valid JSON"}, 400)
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
            return None
        if path == "/api/analyze/stream":
            try:
                size = int(self.headers.get("Content-Length", 0))
                incoming = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(incoming, dict): raise ValueError("body must be an object")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                def delta(text):
                    self.wfile.write(("data: " + json.dumps({"kind": "delta", "text": text}) + "\n\n").encode())
                    self.wfile.flush()
                result = nim_analyze(nim_snapshot(incoming), on_delta=delta)
                self.wfile.write(("data: " + json.dumps({"kind": "done", "data": result}) + "\n\n").encode())
                self.wfile.flush()
            except Exception as e:
                try:
                    self.wfile.write(("data: " + json.dumps({"kind": "error", "error": str(e)}) + "\n\n").encode())
                    self.wfile.flush()
                except Exception: pass
            return None
        if path == "/api/mode":
            try:
                size = int(self.headers.get("Content-Length", 0))
                incoming = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(incoming, dict):
                    self.send_json({"error": "body must be an object"}, 400)
                    return None
                mode = BOT.set_mode(str(incoming.get("mode") or "").lower(),
                                    bool(incoming.get("confirm")))
                self.send_json({"mode": mode, "exchange": BOT.state.exchange,
                                "keys": BOT.exchange_keys()})
            except (TypeError, ValueError) as e:
                self.send_json({"error": str(e)}, 400)
            except (ValueError, RuntimeError) as e:
                self.send_json({"error": str(e)}, 400)
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return None
        if path == "/api/settings/preset":
            try:
                size = int(self.headers.get("Content-Length", 0))
                incoming = json.loads(self.rfile.read(size) or b"{}")
                name = (incoming or {}).get("preset")
                if name not in PRESETS:
                    self.send_json({"error": f"unknown preset: {name}"}, 404)
                    return None
                preset = PRESETS[name]
                patch = dict(preset.get("settings", {}))
                apply_exchange_fees(patch, BOT.state.exchange)
                BOT.settings.update(patch)
                if "gates" in patch:
                    BOT.settings["gates"] = dict(patch["gates"])
                elif "gates" in preset:
                    BOT.settings["gates"] = dict(preset["gates"])
                BOT._save_settings()
                self.send_json({"preset": name, "settings": {**BOT.settings, **{
                    "gates": gates_of(BOT.settings),
                    "exchange": BOT.state.exchange,
                    "symbol": BOT.state.symbol,
                }}})
            except (TypeError, ValueError):
                self.send_json({"error": "body must be valid JSON"}, 400)
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
            return None
        if path == "/api/optimize/run":
            try:
                size = int(self.headers.get("Content-Length", 0))
                try:
                    incoming = json.loads(self.rfile.read(size) or b"{}")
                except (TypeError, ValueError):
                    incoming = {}
                self.send_json(BOT.run_optimize_async((incoming or {}).get("symbol")))
            except Exception as e:
                self.send_json({"error": str(e)}, 409)
            return None
        self.send_error(404)
        return None


if __name__ == "__main__":
    PORT = int(os.getenv("PORT", "8765"))
    print(f"OpenTrade: http://127.0.0.1:{PORT} (paper mode)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
