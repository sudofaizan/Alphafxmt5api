#!/usr/bin/env python3
"""
MT5 VPS Trade API — single-file server for Windows VPS with MetaTrader 5.

  pip install -r requirements.txt
  python server.py

Endpoints:
  GET  /health
  GET  /version
  GET  /getAccountHealth
  GET  /getHistory?start=2026-09-01&end=2026-10-03&symbol=XAUUSD&days=30&closed_only=true
  GET  /getPrice?symbol=XAUUSD
  GET  /getCandles?symbol=XAUUSD&timeframe=M5&count=100
  GET  /getAnalysis?symbol=XAUUSD&timeframe=M5
  GET  /getUpcomingNews?hours=72&impact=High&currency=USD
  GET  /newsAlerts/status
  POST /newsAlerts/start
  POST /newsAlerts/stop
  POST /schedule/grid
  POST /schedule/trade
  GET  /schedule/status
  POST /schedule/cancel
  GET  /suggestionWatch/status
  POST /suggestionWatch/stop
  GET  /telegramAlerts/status
  POST /telegramAlerts/start
  POST /telegramAlerts/stop
  POST /placeOrder
  POST /placeTrades
  GET  /getPositions
  GET  /getOrders
  POST /closePositions
  POST /modifyPosition
  POST /trailPosition_MODE1
  POST /trailPosition_MODE2
  POST /trail/stop
  GET  /trail/status
  POST /placeGrid
  POST /gridGuard/start   — monitor floating profit, auto-close basket at target
  POST /gridGuard/stop
  GET  /gridGuard/status
  POST /basketTp/start    — auto TP+SL on all basket positions (+$ / -$ targets)
  POST /basketTp/stop
  GET  /basketTp/status
  GET  /basketTp/status
  POST /basketTp/apply      — one-shot TP recalc
  GET  /suggestionWatch/status
  POST /suggestionWatch/stop
  POST /telegramAlerts/start   — candle-close OB signals to Telegram
  POST /telegramAlerts/stop
  POST /telegramAlerts/test
  GET  /telegramAlerts/status
  GET  /autoTrade/status
  POST /autoTrade/start    — M5+ candle-close OB signals → auto place (comment alphafxauto)
  POST /autoTrade/stop
  POST /autoTrade/config   — update lot/magic while running
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:
    import tzdata  # noqa: F401 — Windows needs this package for ZoneInfo
except ImportError:
    pass

import certifi
import MetaTrader5 as mt5
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_KEY = "alphafx"
API_VERSION = "1.8.8"
MT5_PATH = os.environ.get("MT5_TERMINAL_PATH", "")
HOST = "0.0.0.0"
PORT = 8080
DEFAULT_MAGIC = int(os.environ.get("MT5_DEFAULT_MAGIC", "202611"))
TRAIL_POLL_MS = int(os.environ.get("TRAIL_POLL_MS", "200"))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUGGESTION_WATCH_STATE_FILE = os.environ.get(
    "SUGGESTION_WATCH_STATE_FILE",
    os.path.join(SCRIPT_DIR, "suggestion_watch_state.json"),
)
SUGGESTION_WATCH_POLL_MS = int(os.environ.get("SUGGESTION_WATCH_POLL_MS", "2000"))
TELEGRAM_BOT_TOKEN = "8841267528:AAG86G9391dZ0mLWe02214O_Pu7sHBEz-iQ"
TELEGRAM_CHAT_ID = "@partneralphafx"
TELEGRAM_ALERT_STATE_FILE = os.environ.get(
    "TELEGRAM_ALERT_STATE_FILE",
    os.path.join(SCRIPT_DIR, "telegram_alert_state.json"),
)
TELEGRAM_ALERT_POLL_MS = int(os.environ.get("TELEGRAM_ALERT_POLL_MS", "5000"))
TELEGRAM_ALERT_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1"]
AUTOTRADE_TIMEFRAMES = ["M5", "M15", "M30", "H1"]
AUTOTRADE_COMMENT = "alphafxauto"
AUTOTRADE_STATE_FILE = os.environ.get(
    "AUTOTRADE_STATE_FILE",
    os.path.join(SCRIPT_DIR, "autotrade_state.json"),
)
AUTOTRADE_POLL_MS = int(os.environ.get("AUTOTRADE_POLL_MS", "5000"))
AUTOTRADE_DEFAULT_LOT = float(os.environ.get("AUTOTRADE_DEFAULT_LOT", "0.01"))
AUTOTRADE_DEFAULT_MAGIC = int(os.environ.get("AUTOTRADE_DEFAULT_MAGIC", "202611"))
NEWS_CALENDAR_URL = os.environ.get(
    "NEWS_CALENDAR_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
)
NEWS_CACHE_TTL = int(os.environ.get("NEWS_CACHE_TTL", "300"))
NEWS_ALERT_STATE_FILE = os.environ.get(
    "NEWS_ALERT_STATE_FILE",
    os.path.join(SCRIPT_DIR, "news_alert_state.json"),
)
NEWS_ALERT_POLL_MS = int(os.environ.get("NEWS_ALERT_POLL_MS", "30000"))
NEWS_ALERT_MINUTES_BEFORE = int(os.environ.get("NEWS_ALERT_MINUTES_BEFORE", "5"))
SCHEDULE_STATE_FILE = os.environ.get(
    "SCHEDULE_STATE_FILE",
    os.path.join(SCRIPT_DIR, "schedule_state.json"),
)
SCHEDULE_POLL_MS_IDLE = int(os.environ.get("SCHEDULE_POLL_MS_IDLE", "500"))
SCHEDULE_POLL_MS_HOT = int(os.environ.get("SCHEDULE_POLL_MS_HOT", "10"))

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/", methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def cors_preflight(path=""):
    return "", 204


# ---------------------------------------------------------------------------
# MT5 helpers
# ---------------------------------------------------------------------------
def last_error() -> dict[str, Any]:
    code, msg = mt5.last_error()
    return {"error_code": code, "error_message": msg}


def ensure_mt5(path: str | None = None) -> tuple[bool, str]:
    if mt5.terminal_info() is not None:
        return True, "already connected"
    kwargs = {}
    if path:
        kwargs["path"] = path
    if not mt5.initialize(**kwargs):
        return False, str(mt5.last_error())
    return True, "connected"


def resolve_symbol(symbol: str) -> tuple[Any | None, str]:
    sym = symbol.strip()
    if not sym:
        return None, symbol

    info = mt5.symbol_info(sym)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(sym, True)
            info = mt5.symbol_info(sym)
        return info, sym

    root = sym.split(".")[0]
    for suffix in (".pr", ".c", ".m", ".i", ".raw", "-c", ".pro"):
        candidate = root + suffix
        if candidate == sym:
            continue
        info = mt5.symbol_info(candidate)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(candidate, True)
                info = mt5.symbol_info(candidate)
            return info, candidate

    root_u = root.upper()
    best_name = None
    best_score = -1
    for s in mt5.symbols_get() or []:
        name = s.name
        name_u = name.upper()
        if name_u == root_u:
            score = 100
        elif name_u.startswith(root_u + "."):
            score = 90 - len(name)
        elif name_u.startswith(root_u):
            score = 70 - len(name)
        else:
            continue
        if score > best_score:
            best_score = score
            best_name = name

    if best_name:
        info = mt5.symbol_info(best_name)
        if info and not info.visible:
            mt5.symbol_select(best_name, True)
            info = mt5.symbol_info(best_name)
        if info:
            return info, best_name

    return None, symbol


def resolve_basket_symbol(symbol: str, magic: int | None = None) -> tuple[Any | None, str]:
    """Resolve broker symbol; fall back to open positions for this magic."""
    info, sym = resolve_symbol(symbol)
    if info is not None:
        return info, sym
    if magic is not None:
        positions = get_basket_positions(None, magic)
        if positions:
            sym = positions[0].symbol
            info = mt5.symbol_info(sym)
            if info:
                if not info.visible:
                    mt5.symbol_select(sym, True)
                    info = mt5.symbol_info(sym)
                return info, sym
    return None, symbol


def supported_filling(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if not info:
        return mt5.ORDER_FILLING_IOC
    mode = info.filling_mode
    if mode & 1:
        return mt5.ORDER_FILLING_FOK
    if mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def round_price(symbol: str, price: float) -> float:
    info = mt5.symbol_info(symbol)
    digits = info.digits if info else 5
    return round(price, digits)


def normalize_volume(symbol: str, volume: float) -> float:
    info = mt5.symbol_info(symbol)
    if not info:
        return volume
    step = info.volume_step or 0.01
    vol = max(info.volume_min, min(volume, info.volume_max))
    steps = round(vol / step)
    return round(steps * step, 8)


def profit_points(pos, tick=None) -> float:
    info = mt5.symbol_info(pos.symbol)
    if not info or info.point <= 0:
        return 0.0
    if tick is None:
        tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return 0.0
    if pos.type == mt5.ORDER_TYPE_BUY:
        return (tick.bid - pos.price_open) / info.point
    return (pos.price_open - tick.ask) / info.point


def sl_price_from_entry_pts(pos, sl_pts_from_entry: float) -> float:
    info = mt5.symbol_info(pos.symbol)
    pt = info.point
    if pos.type == mt5.ORDER_TYPE_BUY:
        return round_price(pos.symbol, pos.price_open + sl_pts_from_entry * pt)
    return round_price(pos.symbol, pos.price_open - sl_pts_from_entry * pt)


def pos_to_dict(p) -> dict[str, Any]:
    return {
        "ticket": p.ticket,
        "symbol": p.symbol,
        "type": "buy" if p.type == mt5.ORDER_TYPE_BUY else "sell",
        "volume": p.volume,
        "price_open": p.price_open,
        "price_current": p.price_current,
        "sl": p.sl,
        "tp": p.tp,
        "profit": p.profit,
        "swap": p.swap,
        "magic": p.magic,
        "comment": p.comment,
    }


def order_to_dict(o) -> dict[str, Any]:
    type_map = {
        mt5.ORDER_TYPE_BUY: "buy",
        mt5.ORDER_TYPE_SELL: "sell",
        mt5.ORDER_TYPE_BUY_LIMIT: "buy_limit",
        mt5.ORDER_TYPE_SELL_LIMIT: "sell_limit",
        mt5.ORDER_TYPE_BUY_STOP: "buy_stop",
        mt5.ORDER_TYPE_SELL_STOP: "sell_stop",
        mt5.ORDER_TYPE_BUY_STOP_LIMIT: "buy_stop_limit",
        mt5.ORDER_TYPE_SELL_STOP_LIMIT: "sell_stop_limit",
        mt5.ORDER_TYPE_CLOSE_BY: "close_by",
    }
    return {
        "ticket": o.ticket,
        "symbol": o.symbol,
        "type": type_map.get(o.type, str(o.type)),
        "volume": o.volume_current,
        "volume_initial": o.volume_initial,
        "price": o.price_open,
        "sl": o.sl,
        "tp": o.tp,
        "magic": o.magic,
        "comment": o.comment,
        "time_setup": (
            datetime.utcfromtimestamp(int(o.time_setup)).isoformat() + "Z"
            if o.time_setup else None
        ),
        "time_expiration": (
            datetime.utcfromtimestamp(int(o.time_expiration)).isoformat() + "Z"
            if o.time_expiration else None
        ),
    }


def result_to_dict(r) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "retcode": r.retcode,
        "deal": r.deal,
        "order": r.order,
        "volume": r.volume,
        "price": r.price,
        "comment": r.comment,
    }


_DEAL_TYPE_LABELS = {
    mt5.DEAL_TYPE_BUY: "buy",
    mt5.DEAL_TYPE_SELL: "sell",
    mt5.DEAL_TYPE_BALANCE: "balance",
    mt5.DEAL_TYPE_CREDIT: "credit",
    mt5.DEAL_TYPE_CHARGE: "charge",
    mt5.DEAL_TYPE_CORRECTION: "correction",
    mt5.DEAL_TYPE_BONUS: "bonus",
    mt5.DEAL_TYPE_COMMISSION: "commission",
    mt5.DEAL_TYPE_COMMISSION_DAILY: "commission_daily",
    mt5.DEAL_TYPE_COMMISSION_MONTHLY: "commission_monthly",
    mt5.DEAL_TYPE_COMMISSION_AGENT_DAILY: "commission_agent_daily",
    mt5.DEAL_TYPE_COMMISSION_AGENT_MONTHLY: "commission_agent_monthly",
    mt5.DEAL_TYPE_INTEREST: "interest",
    mt5.DEAL_TYPE_BUY_CANCELED: "buy_canceled",
    mt5.DEAL_TYPE_SELL_CANCELED: "sell_canceled",
    mt5.DEAL_TYPE_DIVIDEND: "dividend",
    mt5.DEAL_TYPE_DIVIDEND_FRANKED: "dividend_franked",
    mt5.DEAL_TYPE_TAX: "tax",
}

_DEAL_ENTRY_LABELS = {
    mt5.DEAL_ENTRY_IN: "in",
    mt5.DEAL_ENTRY_OUT: "out",
    mt5.DEAL_ENTRY_INOUT: "inout",
    mt5.DEAL_ENTRY_OUT_BY: "out_by",
}

HISTORY_DEFAULT_DAYS = 30
HISTORY_MAX_DAYS = 366
HISTORY_MAX_LIMIT = 10_000


def deal_to_dict(d) -> dict[str, Any]:
    return {
        "ticket": d.ticket,
        "order": d.order,
        "position_id": d.position_id,
        "time": datetime.utcfromtimestamp(int(d.time)).isoformat() + "Z",
        "type": _DEAL_TYPE_LABELS.get(d.type, str(d.type)),
        "type_code": d.type,
        "entry": _DEAL_ENTRY_LABELS.get(d.entry, str(d.entry)),
        "entry_code": d.entry,
        "volume": d.volume,
        "price": d.price,
        "commission": d.commission,
        "swap": d.swap,
        "profit": d.profit,
        "net": d.profit + d.commission + d.swap,
        "symbol": d.symbol,
        "comment": d.comment,
        "magic": d.magic,
        "reason": d.reason,
    }


def parse_history_datetime(value: str, *, end_of_day: bool = False) -> datetime | None:
    """Parse start/end for history queries (VPS local time, naive datetime for MT5)."""
    s = (value or "").strip()
    if not s:
        return None
    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            return dt
        except ValueError:
            continue
    return None


def resolve_history_range(
    start_raw: str | None,
    end_raw: str | None,
    days_raw: str | None,
) -> tuple[datetime, datetime] | tuple[None, None, str]:
    """
    Resolve [from_dt, to_dt] inclusive window for history_deals_get.
    Priority: start+end > start only > end only > days > default days.
    """
    start = parse_history_datetime(start_raw or "", end_of_day=False) if start_raw else None
    end = parse_history_datetime(end_raw or "", end_of_day=True) if end_raw else None

    if start_raw and start is None:
        return None, None, f"invalid start date: {start_raw}"
    if end_raw and end is None:
        return None, None, f"invalid end date: {end_raw}"

    if start and end:
        if end < start:
            return None, None, "end must be on or after start"
        span = (end - start).days
        if span > HISTORY_MAX_DAYS:
            return None, None, f"date range exceeds {HISTORY_MAX_DAYS} days"
        return start, end + timedelta(seconds=1), ""

    now = datetime.now()
    if start:
        to_dt = now + timedelta(days=1)
        if (to_dt - start).days > HISTORY_MAX_DAYS:
            return None, None, f"date range exceeds {HISTORY_MAX_DAYS} days"
        return start, to_dt, ""

    if end:
        from_dt = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=HISTORY_DEFAULT_DAYS)
        return from_dt, end + timedelta(seconds=1), ""

    days = HISTORY_DEFAULT_DAYS
    if days_raw:
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            return None, None, "days must be an integer"
        days = max(1, min(days, HISTORY_MAX_DAYS))

    from_dt = now - timedelta(days=days)
    to_dt = now + timedelta(days=1)
    return from_dt, to_dt, ""


def fetch_history_deals(
    from_dt: datetime,
    to_dt: datetime,
    symbol: str | None = None,
    magic: int | None = None,
    closed_only: bool = False,
    limit: int = 5000,
) -> dict[str, Any]:
    if symbol:
        info, sym = resolve_symbol(symbol)
        if info is None:
            return {"ok": False, "error": f"symbol not found: {symbol}"}
        deals = mt5.history_deals_get(from_dt, to_dt, group=f"*{sym}*")
    else:
        deals = mt5.history_deals_get(from_dt, to_dt)

    if deals is None:
        return {"ok": False, **last_error()}

    rows = list(deals)
    if magic is not None:
        rows = [d for d in rows if d.magic == magic]
    if closed_only:
        rows = [d for d in rows if d.entry == mt5.DEAL_ENTRY_OUT]

    rows.sort(key=lambda d: d.time, reverse=True)
    limit = max(1, min(int(limit), HISTORY_MAX_LIMIT))
    truncated = len(rows) > limit
    rows = rows[:limit]

    payload_rows = [deal_to_dict(d) for d in rows]
    total_profit = sum(r["profit"] for r in payload_rows)
    total_net = sum(r["net"] for r in payload_rows)
    total_commission = sum(r["commission"] for r in payload_rows)
    total_swap = sum(r["swap"] for r in payload_rows)

    return {
        "ok": True,
        "from": from_dt.isoformat(),
        "to": to_dt.isoformat(),
        "count": len(payload_rows),
        "truncated": truncated,
        "summary": {
            "profit": round(total_profit, 2),
            "commission": round(total_commission, 2),
            "swap": round(total_swap, 2),
            "net": round(total_net, 2),
        },
        "deals": payload_rows,
    }


def send_order(req: dict) -> tuple[bool, Any]:
    result = mt5.order_send(req)
    ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
    return ok, result


def build_market_request(
    symbol: str,
    order_type: str,
    volume: float,
    sl: float = 0.0,
    tp: float = 0.0,
    magic: int = 0,
    comment: str = "API",
    deviation: int = 50,
) -> tuple[dict | None, str | None]:
    info, sym = resolve_symbol(symbol)
    if info is None:
        return None, f"symbol not found: {symbol}"

    tick = mt5.symbol_info_tick(sym)
    if not tick:
        return None, "no quote"

    volume = normalize_volume(sym, volume)
    order_type = order_type.lower()

    if order_type == "buy":
        trade_type, price = mt5.ORDER_TYPE_BUY, tick.ask
        action = mt5.TRADE_ACTION_DEAL
    elif order_type == "sell":
        trade_type, price = mt5.ORDER_TYPE_SELL, tick.bid
        action = mt5.TRADE_ACTION_DEAL
    else:
        return None, f"invalid market type: {order_type}"

    req = {
        "action": action,
        "symbol": sym,
        "volume": volume,
        "type": trade_type,
        "price": round_price(sym, price),
        "sl": round_price(sym, sl) if sl else 0.0,
        "tp": round_price(sym, tp) if tp else 0.0,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": supported_filling(sym),
    }
    return req, None


def build_pending_request(
    symbol: str,
    order_type: str,
    volume: float,
    price: float,
    sl: float = 0.0,
    tp: float = 0.0,
    magic: int = 0,
    comment: str = "API",
) -> tuple[dict | None, str | None]:
    info, sym = resolve_symbol(symbol)
    if info is None:
        return None, f"symbol not found: {symbol}"

    volume = normalize_volume(sym, volume)
    order_type = order_type.lower()
    type_map = {
        "buy_limit": mt5.ORDER_TYPE_BUY_LIMIT,
        "sell_limit": mt5.ORDER_TYPE_SELL_LIMIT,
        "buy_stop": mt5.ORDER_TYPE_BUY_STOP,
        "sell_stop": mt5.ORDER_TYPE_SELL_STOP,
    }
    if order_type not in type_map:
        return None, f"invalid pending type: {order_type}"

    req = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": sym,
        "volume": volume,
        "type": type_map[order_type],
        "price": round_price(sym, price),
        "sl": round_price(sym, sl) if sl else 0.0,
        "tp": round_price(sym, tp) if tp else 0.0,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": supported_filling(sym),
    }
    return req, None


def apply_points_to_prices(
    symbol: str,
    order_type: str,
    entry_price: float,
    sl_points: int | None,
    tp_points: int | None,
) -> tuple[float, float]:
    info = mt5.symbol_info(symbol)
    if not info:
        return 0.0, 0.0
    pt = info.point
    sl, tp = 0.0, 0.0
    is_buy = order_type.lower() in ("buy", "buy_limit", "buy_stop")
    if sl_points and sl_points > 0:
        sl = entry_price - sl_points * pt if is_buy else entry_price + sl_points * pt
    if tp_points and tp_points > 0:
        tp = entry_price + tp_points * pt if is_buy else entry_price - tp_points * pt
    return round_price(symbol, sl), round_price(symbol, tp)


def place_grid_fast(
    symbol: str,
    lot: float,
    distance: int,
    initial_distance: int,
    orders_quantity: int,
    incremental: bool = False,
    magic: int = 78001,
    anchor: float | None = None,
    tp_points: int = 0,
    sl_points: int = 0,
) -> dict[str, Any]:
    """
    Fast grid — pending buy/sell stops around hook (anchor).

    Central TP (tp_points > 0):
      All orders share the same take-profit *price* at hook ± tp_points.
      Per-level TP distance = tp_points - offset_pts, where offset_pts is the
      order's distance from hook (initial_distance + (level-1) * distance).
      Buy stops → TP at hook + tp_points; sell stops → TP at hook - tp_points.

    Per-order SL (sl_points > 0):
      Each order gets the same SL distance from its own entry (not central).
      sl_points == 0 → no SL on any order.
    """
    info, sym = resolve_symbol(symbol)
    if info is None:
        return {"ok": False, "error": f"symbol not found: {symbol}"}

    tick = mt5.symbol_info_tick(sym)
    if not tick:
        return {"ok": False, "error": "no quote", **last_error()}

    pt = info.point
    min_dist = max(1, info.trade_stops_level) * pt
    ask, bid = tick.ask, tick.bid
    if anchor is None:
        anchor = round_price(sym, (bid + ask) / 2.0)

    central_tp = int(tp_points) if tp_points else 0
    per_sl = int(sl_points) if sl_points else 0

    filling = supported_filling(sym)
    t0 = time.perf_counter()
    placed = failed = skipped = 0
    results: list[dict] = []

    for i in range(1, orders_quantity + 1):
        vol = lot * (2 ** (i - 1)) if incremental else lot
        vol = normalize_volume(sym, vol)
        offset_pts = initial_distance + (i - 1) * distance
        level_tp_pts = max(0, central_tp - offset_pts) if central_tp > 0 else 0

        buy_price = round_price(sym, anchor + offset_pts * pt)
        if buy_price > ask + min_dist:
            sl, tp = 0.0, 0.0
            if per_sl > 0:
                sl, _ = apply_points_to_prices(sym, "buy_stop", buy_price, per_sl, None)
            if level_tp_pts > 0:
                _, tp = apply_points_to_prices(sym, "buy_stop", buy_price, None, level_tp_pts)
            req = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": sym,
                "volume": vol,
                "type": mt5.ORDER_TYPE_BUY_STOP,
                "price": buy_price,
                "sl": sl,
                "tp": tp,
                "magic": magic,
                "comment": f"GRID_B{i}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            ok, res = send_order(req)
            placed += int(ok)
            failed += int(not ok)
            results.append({
                "side": "buy_stop",
                "level": i,
                "price": buy_price,
                "offset_pts": offset_pts,
                "tp_points": level_tp_pts,
                "sl_points": per_sl if per_sl > 0 else 0,
                "tp": tp,
                "sl": sl,
                "ok": ok,
                "order": getattr(res, "order", None),
            })
        else:
            skipped += 1

        sell_price = round_price(sym, anchor - offset_pts * pt)
        if sell_price < bid - min_dist:
            sl, tp = 0.0, 0.0
            if per_sl > 0:
                sl, _ = apply_points_to_prices(sym, "sell_stop", sell_price, per_sl, None)
            if level_tp_pts > 0:
                _, tp = apply_points_to_prices(sym, "sell_stop", sell_price, None, level_tp_pts)
            req = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": sym,
                "volume": vol,
                "type": mt5.ORDER_TYPE_SELL_STOP,
                "price": sell_price,
                "sl": sl,
                "tp": tp,
                "magic": magic,
                "comment": f"GRID_S{i}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            ok, res = send_order(req)
            placed += int(ok)
            failed += int(not ok)
            results.append({
                "side": "sell_stop",
                "level": i,
                "price": sell_price,
                "offset_pts": offset_pts,
                "tp_points": level_tp_pts,
                "sl_points": per_sl if per_sl > 0 else 0,
                "tp": tp,
                "sl": sl,
                "ok": ok,
                "order": getattr(res, "order", None),
            })
        else:
            skipped += 1

    buy_tp_price = round_price(sym, anchor + central_tp * pt) if central_tp > 0 else None
    sell_tp_price = round_price(sym, anchor - central_tp * pt) if central_tp > 0 else None

    return {
        "ok": placed > 0,
        "symbol": sym,
        "anchor": anchor,
        "hook_price": anchor,
        "central_tp_points": central_tp,
        "central_tp_price_buy_side": buy_tp_price,
        "central_tp_price_sell_side": sell_tp_price,
        "per_order_sl_points": per_sl,
        "placed": placed,
        "failed": failed,
        "skipped": skipped,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "orders_quantity": orders_quantity,
        "distance": distance,
        "initial_distance": initial_distance,
        "magic": magic,
        "results": results,
    }


def get_basket_floating(symbol: str | None = None, magic: int | None = None) -> dict[str, Any]:
    """Sum floating P/L (+ swap) for positions matching symbol and/or magic."""
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    positions = positions or []
    if magic is not None:
        positions = [p for p in positions if p.magic == int(magic)]

    profit = sum(p.profit + p.swap for p in positions)
    return {
        "floating": round(profit, 2),
        "positions_count": len(positions),
        "symbol": symbol,
        "magic": magic,
    }


def cancel_pending_orders(symbol: str | None = None, magic: int | None = None) -> dict[str, Any]:
    orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    orders = orders or []
    if magic is not None:
        orders = [o for o in orders if o.magic == int(magic)]

    cancelled = failed = 0
    results = []
    for o in orders:
        req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
        ok, res = send_order(req)
        if ok:
            cancelled += 1
        else:
            failed += 1
        results.append({"ticket": o.ticket, "ok": ok})

    return {"cancelled": cancelled, "failed": failed, "total": len(orders), "results": results}


def cancel_order_ticket(ticket: int) -> dict[str, Any]:
    orders = mt5.orders_get(ticket=int(ticket))
    if not orders:
        return {"ok": False, "error": "order not found", "ticket": int(ticket)}
    req = {"action": mt5.TRADE_ACTION_REMOVE, "order": int(ticket)}
    ok, res = send_order(req)
    return {"ok": ok, "ticket": int(ticket), "result": result_to_dict(res)}


def modify_pending_order(
    ticket: int,
    price: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
) -> dict[str, Any]:
    orders = mt5.orders_get(ticket=int(ticket))
    if not orders:
        return {"ok": False, "error": "order not found", "ticket": int(ticket)}
    o = orders[0]
    sym = o.symbol
    req = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": int(ticket),
        "price": round_price(sym, price if price is not None else o.price_open),
        "sl": round_price(sym, sl) if sl is not None else o.sl,
        "tp": round_price(sym, tp) if tp is not None else o.tp,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": supported_filling(sym),
    }
    ok, res = send_order(req)
    return {"ok": ok, "ticket": int(ticket), "result": result_to_dict(res)}


def close_basket(symbol: str | None = None, magic: int | None = None, comment: str = "Grid guard close") -> dict[str, Any]:
    """Close all matching positions and cancel pending orders (grid basket exit)."""
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    positions = positions or []
    if magic is not None:
        positions = [p for p in positions if p.magic == int(magic)]

    closed = failed = 0
    pos_results = []
    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            pos_results.append({"ticket": pos.ticket, "ok": False, "error": "no tick"})
            failed += 1
            continue
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": close_price,
            "deviation": 50,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": supported_filling(pos.symbol),
        }
        ok, res = send_order(req)
        if ok:
            closed += 1
        else:
            failed += 1
        pos_results.append({"ticket": pos.ticket, "ok": ok, "result": result_to_dict(res)})

    order_result = cancel_pending_orders(symbol, magic)
    return {
        "ok": True,
        "closed_positions": closed,
        "failed_positions": failed,
        "cancelled_orders": order_result["cancelled"],
        "positions": pos_results,
        "orders": order_result,
    }


def get_basket_positions(symbol: str | None = None, magic: int | None = None) -> list:
    if magic is not None:
        positions = [p for p in (mt5.positions_get() or []) if p.magic == int(magic)]
        if not symbol:
            return positions
        _, sym = resolve_symbol(symbol)
        if sym:
            exact = [p for p in positions if p.symbol == sym]
            if exact:
                return exact
        exact = [p for p in positions if p.symbol == symbol]
        if exact:
            return exact
        root = symbol.split(".")[0].upper()
        prefixed = [p for p in positions if p.symbol.upper().startswith(root)]
        return prefixed or positions

    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    positions = list(positions or [])
    return positions


def position_profit_at_price(pos, close_price: float) -> float:
    action = mt5.ORDER_TYPE_BUY if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_SELL
    profit = mt5.order_calc_profit(action, pos.symbol, pos.volume, pos.price_open, close_price)
    if profit is None:
        return 0.0
    return float(profit) + pos.swap


def basket_profit_at_price(positions: list, close_price: float) -> float:
    return sum(position_profit_at_price(p, close_price) for p in positions)


def find_price_for_basket_profit(
    positions: list,
    target_profit: float,
    price_min: float,
    price_max: float,
    tolerance: float = 0.05,
) -> float | None:
    """Binary-search price where basket P/L equals target (linear in price for one symbol)."""
    if not positions:
        return None
    sym = positions[0].symbol
    lo, hi = price_min, price_max
    p_lo = basket_profit_at_price(positions, lo)
    p_hi = basket_profit_at_price(positions, hi)
    if target_profit < min(p_lo, p_hi) - tolerance or target_profit > max(p_lo, p_hi) + tolerance:
        return None

    for _ in range(80):
        mid = round_price(sym, (lo + hi) / 2.0)
        p_mid = basket_profit_at_price(positions, mid)
        if abs(p_mid - target_profit) <= tolerance:
            return mid
        if p_lo <= p_hi:
            if p_mid < target_profit:
                lo, p_lo = mid, p_mid
            else:
                hi, p_hi = mid, p_mid
        else:
            if p_mid > target_profit:
                lo, p_lo = mid, p_mid
            else:
                hi, p_hi = mid, p_mid
    return round_price(sym, (lo + hi) / 2.0)


def calc_basket_sltp_levels(
    positions: list,
    target_profit: float,
    target_loss: float | None = None,
    search_points: int = 200000,
) -> dict[str, Any]:
    """
    Buy TP / sell SL = price above market where basket P/L = +target_profit.
    Sell TP / buy SL = price below market where basket P/L = +target_profit.
    Buy SL / sell SL (loss side) = prices where basket P/L = -target_loss.
    """
    if not positions:
        return {"ok": False, "error": "no positions"}

    loss_target = target_loss if target_loss and target_loss > 0 else target_profit

    sym = positions[0].symbol
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if not info or not tick:
        return {"ok": False, "error": "no symbol info"}

    pt = info.point
    mid = (tick.bid + tick.ask) / 2.0
    min_dist = max(1, info.trade_stops_level) * pt
    range_px = search_points * pt

    buy_tp = find_price_for_basket_profit(positions, target_profit, mid, mid + range_px)
    sell_tp = find_price_for_basket_profit(positions, target_profit, mid - range_px, mid)
    buy_sl = find_price_for_basket_profit(positions, -loss_target, mid - range_px, mid)
    sell_sl = find_price_for_basket_profit(positions, -loss_target, mid, mid + range_px)

    buys = [p for p in positions if p.type == mt5.POSITION_TYPE_BUY]
    sells = [p for p in positions if p.type == mt5.POSITION_TYPE_SELL]

    if buy_tp is not None and buy_tp < tick.ask + min_dist:
        buy_tp = None
    if sell_tp is not None and sell_tp > tick.bid - min_dist:
        sell_tp = None
    if buy_sl is not None and buy_sl > tick.bid - min_dist:
        buy_sl = None
    if sell_sl is not None and sell_sl < tick.ask + min_dist:
        sell_sl = None

    return {
        "ok": True,
        "symbol": sym,
        "target_profit": target_profit,
        "target_loss": loss_target,
        "current_floating": round(basket_profit_at_price(positions, mid), 2),
        "bid": tick.bid,
        "ask": tick.ask,
        "buy_tp_price": buy_tp,
        "sell_tp_price": sell_tp,
        "buy_sl_price": buy_sl,
        "sell_sl_price": sell_sl,
        "buy_positions": len(buys),
        "sell_positions": len(sells),
    }


def calc_basket_tp_levels(positions: list, target_profit: float, search_points: int = 200000) -> dict[str, Any]:
    return calc_basket_sltp_levels(positions, target_profit, target_profit, search_points)


def modify_position_sltp(
    pos,
    sl: float | None = None,
    tp: float | None = None,
    *,
    update_sl: bool = True,
    update_tp: bool = True,
) -> tuple[bool, Any]:
    new_sl = round_price(pos.symbol, sl) if (update_sl and sl is not None) else (pos.sl or 0.0)
    new_tp = round_price(pos.symbol, tp) if (update_tp and tp is not None) else (pos.tp or 0.0)
    ok, result = send_order({
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pos.symbol,
        "position": pos.ticket,
        "sl": new_sl,
        "tp": new_tp,
    })
    return ok, result


def apply_basket_sltp(
    symbol: str,
    magic: int,
    target_profit: float,
    target_loss: float | None = None,
    price_tolerance: float = 0.0,
) -> dict[str, Any]:
    """Set buy/sell TP+SL so basket hits +target_profit or -target_loss at those prices."""
    positions = get_basket_positions(symbol, magic)
    if not positions:
        return {"ok": False, "error": "no positions", "modified": 0}

    levels = calc_basket_sltp_levels(positions, target_profit, target_loss)
    if not levels.get("ok"):
        return levels

    buy_tp = levels.get("buy_tp_price")
    sell_tp = levels.get("sell_tp_price")
    buy_sl = levels.get("buy_sl_price")
    sell_sl = levels.get("sell_sl_price")
    modified = failed = 0
    results: list[dict[str, Any]] = []

    for pos in positions:
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        desired_tp = buy_tp if is_buy else sell_tp
        desired_sl = buy_sl if is_buy else sell_sl

        if desired_tp is None and desired_sl is None:
            results.append({"ticket": pos.ticket, "ok": False, "skipped": True, "reason": "no sl/tp level"})
            continue

        cur_tp = pos.tp or 0.0
        cur_sl = pos.sl or 0.0
        tp_ok = desired_tp is None or (
            price_tolerance > 0 and cur_tp > 0 and abs(cur_tp - desired_tp) <= price_tolerance
        )
        sl_ok = desired_sl is None or (
            price_tolerance > 0 and cur_sl > 0 and abs(cur_sl - desired_sl) <= price_tolerance
        )
        if tp_ok and sl_ok:
            results.append({
                "ticket": pos.ticket,
                "ok": True,
                "skipped": True,
                "tp": cur_tp,
                "sl": cur_sl,
            })
            continue

        ok, res = modify_position_sltp(
            pos,
            sl=desired_sl,
            tp=desired_tp,
            update_sl=desired_sl is not None,
            update_tp=desired_tp is not None,
        )
        modified += int(ok)
        failed += int(not ok)
        results.append({
            "ticket": pos.ticket,
            "type": "buy" if is_buy else "sell",
            "ok": ok,
            "tp": desired_tp,
            "sl": desired_sl,
            "result": result_to_dict(res),
        })

    return {
        "ok": modified > 0 or any(
            levels.get(k) is not None
            for k in ("buy_tp_price", "sell_tp_price", "buy_sl_price", "sell_sl_price")
        ),
        "modified": modified,
        "failed": failed,
        "buy_tp_price": buy_tp,
        "sell_tp_price": sell_tp,
        "buy_sl_price": buy_sl,
        "sell_sl_price": sell_sl,
        "target_profit": target_profit,
        "target_loss": levels.get("target_loss"),
        "current_floating": levels.get("current_floating"),
        "positions": results,
    }


def apply_basket_tps(
    symbol: str,
    magic: int,
    target_profit: float,
    tp_tolerance: float = 0.0,
) -> dict[str, Any]:
    return apply_basket_sltp(symbol, magic, target_profit, target_profit, tp_tolerance)
def build_account_health() -> dict[str, Any]:
    """Full account + terminal health snapshot."""
    ok, msg = ensure_mt5(MT5_PATH or None)
    if not ok:
        return {"ok": False, "connected": False, "error": msg}

    account = mt5.account_info()
    terminal = mt5.terminal_info()
    if account is None:
        return {"ok": False, "connected": False, "error": "no account info"}

    positions = mt5.positions_get() or []
    orders = mt5.orders_get() or []

    floating_profit = sum(p.profit + p.swap for p in positions)
    floating_swap = sum(p.swap for p in positions)

    # Today's closed P/L from deals
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(today_start, datetime.now() + timedelta(days=1)) or []
    closed_today = sum(d.profit + d.commission + d.swap for d in deals if d.entry == mt5.DEAL_ENTRY_OUT)

    balance = account.balance
    equity = account.equity
    drawdown_usd = balance - equity if balance > equity else 0.0
    drawdown_pct = (drawdown_usd / balance * 100.0) if balance > 0 else 0.0
    margin_level = account.margin_level if account.margin > 0 else None

    health_status = "healthy"
    warnings: list[str] = []
    if not terminal or not terminal.connected:
        health_status = "disconnected"
        warnings.append("terminal not connected to broker")
    if not account.trade_allowed:
        health_status = "restricted"
        warnings.append("trading not allowed on this account")
    if margin_level is not None and margin_level < 150:
        health_status = "warning"
        warnings.append(f"low margin level: {margin_level:.1f}%")
    if drawdown_pct > 10:
        health_status = "warning"
        warnings.append(f"drawdown {drawdown_pct:.1f}%")

    return {
        "ok": True,
        "connected": bool(terminal and terminal.connected),
        "health_status": health_status,
        "warnings": warnings,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "account": {
            "login": account.login,
            "server": account.server,
            "name": account.name,
            "currency": account.currency,
            "leverage": account.leverage,
            "trade_mode": account.trade_mode,
            "trade_allowed": account.trade_allowed,
            "trade_expert": account.trade_expert,
        },
        "balance": {
            "balance": round(balance, 2),
            "equity": round(equity, 2),
            "margin": round(account.margin, 2),
            "free_margin": round(account.margin_free, 2),
            "margin_level": round(margin_level, 2) if margin_level else None,
            "profit": round(account.profit, 2),
            "credit": round(account.credit, 2),
        },
        "drawdown": {
            "usd": round(drawdown_usd, 2),
            "percent": round(drawdown_pct, 2),
        },
        "floating": {
            "positions_count": len(positions),
            "orders_count": len(orders),
            "profit": round(floating_profit, 2),
            "swap": round(floating_swap, 2),
        },
        "positions": [pos_to_dict(p) for p in positions],
        "orders": [order_to_dict(o) for o in orders],
        "today": {
            "closed_pl": round(closed_today, 2),
            "deals_count": len(deals),
        },
        "terminal": {
            "company": terminal.company if terminal else None,
            "name": terminal.name if terminal else None,
            "build": terminal.build if terminal else None,
            "connected": terminal.connected if terminal else False,
            "trade_allowed": terminal.trade_allowed if terminal else False,
        },
        "trail_jobs": len(trail_mgr.status()),
    }


# ---------------------------------------------------------------------------
# Trailing manager
# ---------------------------------------------------------------------------
@dataclass
class TrailJob:
    ticket: int
    mode: int
    step_points: int
    active: bool = True
    last_sl_pts: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class TrailingManager:
    def __init__(self, poll_ms: int = 200):
        self._jobs: dict[int, TrailJob] = {}
        self._lock = threading.Lock()
        self._poll_ms = poll_ms
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="trail-manager")
        self._thread.start()

    def add_mode1(self, ticket: int, trail_points: int) -> dict[str, Any]:
        return self._add(ticket, mode=1, step_points=trail_points)

    def add_mode2(self, ticket: int, step_points: int) -> dict[str, Any]:
        return self._add(ticket, mode=2, step_points=step_points)

    def remove(self, ticket: int) -> bool:
        with self._lock:
            job = self._jobs.pop(int(ticket), None)
        return job is not None

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "ticket": j.ticket,
                    "mode": j.mode,
                    "step_points": j.step_points,
                    "active": j.active,
                    "last_sl_pts": j.last_sl_pts,
                }
                for j in self._jobs.values()
            ]

    def _add(self, ticket: int, mode: int, step_points: int) -> dict[str, Any]:
        positions = mt5.positions_get(ticket=int(ticket))
        if not positions:
            return {"ok": False, "error": "position not found", "ticket": ticket}

        pos = positions[0]
        step_points = max(1, int(step_points))
        job = TrailJob(ticket=int(ticket), mode=mode, step_points=step_points)

        if mode == 2:
            init_sl_pts = -step_points
            new_sl = sl_price_from_entry_pts(pos, init_sl_pts)
            ok = self._modify_sl(pos, new_sl)
            job.last_sl_pts = init_sl_pts
            with self._lock:
                self._jobs[job.ticket] = job
            self.start()
            return {
                "ok": ok,
                "ticket": ticket,
                "mode": 2,
                "step_points": step_points,
                "initial_sl_pts_from_entry": init_sl_pts,
                "sl": new_sl,
            }

        with self._lock:
            self._jobs[job.ticket] = job
        self.start()
        self._tick_job(job)
        return {"ok": True, "ticket": ticket, "mode": 1, "trail_points": step_points, "sl": pos.sl}

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                tickets = list(self._jobs.keys())
            for ticket in tickets:
                with self._lock:
                    job = self._jobs.get(ticket)
                if job and job.active:
                    self._tick_job(job)
            time.sleep(self._poll_ms / 1000.0)

    def _tick_job(self, job: TrailJob) -> None:
        positions = mt5.positions_get(ticket=job.ticket)
        if not positions:
            with self._lock:
                self._jobs.pop(job.ticket, None)
            return

        pos = positions[0]
        pts = profit_points(pos)
        step = job.step_points

        if job.mode == 1:
            locked_pts = pts - step
            if locked_pts < -step:
                return
        else:
            buckets = int(pts // step)
            locked_pts = step * (buckets - 1)

        if job.last_sl_pts is not None and locked_pts <= job.last_sl_pts:
            return

        new_sl = sl_price_from_entry_pts(pos, locked_pts)
        if not self._is_better_sl(pos, new_sl):
            return

        if self._modify_sl(pos, new_sl):
            job.last_sl_pts = locked_pts

    def _is_better_sl(self, pos, new_sl: float) -> bool:
        if pos.sl <= 0:
            return True
        if pos.type == mt5.ORDER_TYPE_BUY:
            return new_sl > pos.sl
        return new_sl < pos.sl

    def _modify_sl(self, pos, new_sl: float) -> bool:
        new_sl = round_price(pos.symbol, new_sl)
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": pos.ticket,
            "sl": new_sl,
            "tp": pos.tp,
        }
        result = mt5.order_send(req)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE


trail_mgr = TrailingManager(poll_ms=TRAIL_POLL_MS)


# ---------------------------------------------------------------------------
# Grid basket guard — auto-close on floating profit target
# ---------------------------------------------------------------------------
@dataclass
class GridGuardJob:
    key: str
    symbol: str
    magic: int
    max_floating_profit: float  # close when floating >= this (e.g. 2 = +$2)
    active: bool = True
    triggered: bool = False
    last_floating: float = 0.0


class GridGuardManager:
    def __init__(self, poll_ms: int = 500):
        self._jobs: dict[str, GridGuardJob] = {}
        self._lock = threading.Lock()
        self._poll_ms = poll_ms
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="grid-guard")
        self._thread.start()

    def _job_key(self, symbol: str, magic: int) -> str:
        return f"{symbol}:{magic}"

    def add(
        self,
        symbol: str,
        magic: int,
        max_floating_profit: float,
    ) -> dict[str, Any]:
        if max_floating_profit <= 0:
            return {"ok": False, "error": "max_floating_profit must be > 0"}

        info, sym = resolve_symbol(symbol)
        if info is None:
            return {"ok": False, "error": f"symbol not found: {symbol}"}

        key = self._job_key(sym, int(magic))
        job = GridGuardJob(
            key=key,
            symbol=sym,
            magic=int(magic),
            max_floating_profit=float(max_floating_profit),
        )
        with self._lock:
            self._jobs[key] = job
        self.start()
        snap = get_basket_floating(sym, job.magic)
        job.last_floating = snap["floating"]
        return {
            "ok": True,
            "symbol": sym,
            "magic": job.magic,
            "max_floating_profit": job.max_floating_profit,
            "current_floating": snap["floating"],
            "message": f"Guard active: close basket if floating >= +{job.max_floating_profit}",
        }

    def remove(self, symbol: str | None = None, magic: int | None = None) -> int:
        removed = 0
        with self._lock:
            if symbol is not None and magic is not None:
                _, sym = resolve_symbol(symbol)
                key = self._job_key(sym or symbol, int(magic))
                if self._jobs.pop(key, None):
                    removed = 1
            else:
                removed = len(self._jobs)
                self._jobs.clear()
        return removed

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "symbol": j.symbol,
                    "magic": j.magic,
                    "active": j.active,
                    "triggered": j.triggered,
                    "max_floating_profit": j.max_floating_profit,
                    "last_floating": j.last_floating,
                }
                for j in self._jobs.values()
            ]

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                keys = list(self._jobs.keys())
            for key in keys:
                with self._lock:
                    job = self._jobs.get(key)
                if job and job.active and not job.triggered:
                    self._tick(job)
            time.sleep(self._poll_ms / 1000.0)

    def _tick(self, job: GridGuardJob) -> None:
        snap = get_basket_floating(job.symbol, job.magic)
        floating = snap["floating"]
        job.last_floating = floating

        if floating < job.max_floating_profit:
            return

        print(f"[GridGuard] floating profit hit: {job.symbol} magic={job.magic} floating=${floating}")
        close_basket(job.symbol, job.magic, comment="GridGuard floating_profit")
        job.triggered = True
        job.active = False
        with self._lock:
            self._jobs.pop(job.key, None)


grid_guard = GridGuardManager(poll_ms=500)


# ---------------------------------------------------------------------------
# Basket SL/TP manager — dynamic per-position TP+SL for basket profit/loss targets
# ---------------------------------------------------------------------------
@dataclass
class BasketTpJob:
    key: str
    symbol: str
    magic: int
    target_profit: float
    target_loss: float
    active: bool = True
    last_position_count: int = 0
    buy_tp_price: float | None = None
    sell_tp_price: float | None = None
    buy_sl_price: float | None = None
    sell_sl_price: float | None = None
    last_floating: float = 0.0
    last_modified: int = 0


class BasketTpManager:
    def __init__(self, poll_ms: int = 1000):
        self._jobs: dict[str, BasketTpJob] = {}
        self._lock = threading.Lock()
        self._poll_ms = poll_ms
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="basket-tp")
        self._thread.start()

    def _job_key(self, symbol: str, magic: int) -> str:
        return f"{symbol}:{magic}"

    def add(
        self,
        symbol: str,
        magic: int,
        target_profit: float,
        target_loss: float | None = None,
    ) -> dict[str, Any]:
        if target_profit <= 0:
            return {"ok": False, "error": "target_profit must be > 0"}

        loss = float(target_loss) if target_loss and target_loss > 0 else float(target_profit)

        info, sym = resolve_basket_symbol(symbol, magic)
        if info is None:
            return {"ok": False, "error": f"symbol not found: {symbol}"}

        key = self._job_key(sym, int(magic))
        job = BasketTpJob(
            key=key,
            symbol=sym,
            magic=int(magic),
            target_profit=float(target_profit),
            target_loss=loss,
        )
        positions = get_basket_positions(sym, job.magic)
        job.last_position_count = len(positions)

        with self._lock:
            self._jobs[key] = job
        self.start()

        apply_result = (
            apply_basket_sltp(sym, job.magic, job.target_profit, job.target_loss)
            if positions
            else {"ok": True, "modified": 0, "message": "waiting for positions"}
        )
        levels = calc_basket_sltp_levels(
            get_basket_positions(sym, job.magic), job.target_profit, job.target_loss
        )
        job.buy_tp_price = levels.get("buy_tp_price")
        job.sell_tp_price = levels.get("sell_tp_price")
        job.buy_sl_price = levels.get("buy_sl_price")
        job.sell_sl_price = levels.get("sell_sl_price")
        job.last_floating = levels.get("current_floating", 0.0)

        return {
            "ok": True,
            "symbol": sym,
            "magic": job.magic,
            "target_profit": job.target_profit,
            "target_loss": job.target_loss,
            "buy_tp_price": job.buy_tp_price,
            "sell_tp_price": job.sell_tp_price,
            "buy_sl_price": job.buy_sl_price,
            "sell_sl_price": job.sell_sl_price,
            "current_floating": job.last_floating,
            "apply": apply_result,
            "message": (
                f"Basket SL/TP active: +${job.target_profit} / -${job.target_loss} | "
                f"buy TP={job.buy_tp_price} SL={job.buy_sl_price} | "
                f"sell TP={job.sell_tp_price} SL={job.sell_sl_price}"
            ),
        }

    def remove(self, symbol: str | None = None, magic: int | None = None) -> int:
        removed = 0
        with self._lock:
            if symbol is not None and magic is not None:
                _, sym = resolve_symbol(symbol)
                key = self._job_key(sym or symbol, int(magic))
                if self._jobs.pop(key, None):
                    removed = 1
            else:
                removed = len(self._jobs)
                self._jobs.clear()
        return removed

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "symbol": j.symbol,
                    "magic": j.magic,
                    "active": j.active,
                    "target_profit": j.target_profit,
                    "target_loss": j.target_loss,
                    "buy_tp_price": j.buy_tp_price,
                    "sell_tp_price": j.sell_tp_price,
                    "buy_sl_price": j.buy_sl_price,
                    "sell_sl_price": j.sell_sl_price,
                    "last_floating": j.last_floating,
                    "last_position_count": j.last_position_count,
                    "last_modified": j.last_modified,
                }
                for j in self._jobs.values()
            ]

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                keys = list(self._jobs.keys())
            for key in keys:
                with self._lock:
                    job = self._jobs.get(key)
                if job and job.active:
                    self._tick(job)
            time.sleep(self._poll_ms / 1000.0)

    def _tick(self, job: BasketTpJob) -> None:
        positions = get_basket_positions(job.symbol, job.magic)
        count = len(positions)

        if count == 0:
            with self._lock:
                self._jobs.pop(job.key, None)
            return

        snap = get_basket_floating(job.symbol, job.magic)
        floating = snap["floating"]
        job.last_floating = floating

        if floating >= job.target_profit:
            print(f"[BasketSLTP] profit target hit: {job.symbol} magic={job.magic} floating=${floating}")
            close_basket(job.symbol, job.magic, comment="BasketSLTP profit")
            with self._lock:
                self._jobs.pop(job.key, None)
            return

        if floating <= -job.target_loss:
            print(f"[BasketSLTP] loss target hit: {job.symbol} magic={job.magic} floating=${floating}")
            close_basket(job.symbol, job.magic, comment="BasketSLTP loss")
            with self._lock:
                self._jobs.pop(job.key, None)
            return

        if job.last_position_count > 0 and count < job.last_position_count:
            print(f"[BasketSLTP] partial SL/TP hit, closing remainder: {job.symbol} magic={job.magic}")
            close_basket(job.symbol, job.magic, comment="BasketSLTP partial")
            with self._lock:
                self._jobs.pop(job.key, None)
            return

        job.last_position_count = count

        info = mt5.symbol_info(job.symbol)
        pt = info.point if info else 0.01
        apply_result = apply_basket_sltp(
            job.symbol, job.magic, job.target_profit, job.target_loss, price_tolerance=pt * 2
        )
        job.last_modified = apply_result.get("modified", 0)

        levels = calc_basket_sltp_levels(positions, job.target_profit, job.target_loss)
        if levels.get("ok"):
            job.buy_tp_price = levels.get("buy_tp_price")
            job.sell_tp_price = levels.get("sell_tp_price")
            job.buy_sl_price = levels.get("buy_sl_price")
            job.sell_sl_price = levels.get("sell_sl_price")


basket_tp_mgr = BasketTpManager(poll_ms=1000)


# ---------------------------------------------------------------------------
# Suggestion watch — auto cancel/update pending orders when OB expires
# ---------------------------------------------------------------------------
@dataclass
class SuggestionWatchJob:
    ticket: int
    symbol: str
    magic: int
    order_type: str
    chart_tf: str
    bar_count: int
    ob_time: str
    ob_type: str
    entry: float
    sl: float
    tp: float
    volume: float
    active: bool = True
    created_at: str = ""
    last_check: str = ""
    last_status: str = "watching"
    last_message: str = ""
    modify_count: int = 0


def _find_ob_in_analysis(analysis: dict[str, Any], ob_time: str, ob_type: str) -> dict[str, Any] | None:
    for ob in analysis.get("order_blocks") or []:
        if ob.get("time") == ob_time and ob.get("type") == ob_type:
            return ob
    return None


def _recalc_ob_order_prices(sym: str, ob: dict, ob_type: str, atr: float) -> tuple[float, float, float]:
    info = mt5.symbol_info(sym)
    pt = info.point if info else 0.01
    min_dist = max(1, (info.trade_stops_level if info else 1)) * pt
    buffer = max(atr * 0.35, min_dist * 2)
    sl_pad = max(atr * 0.75, buffer, min_dist * 3)
    if ob_type == "BULLISH_OB":
        entry = float(ob["low"])
        sl = float(ob["low"]) - sl_pad
        tp = max(entry + atr * 2.0, entry + atr * 1.5)
    else:
        entry = float(ob["high"])
        sl = float(ob["high"]) + sl_pad
        tp = min(entry - atr * 2.0, entry - atr * 1.5)
    return round_price(sym, entry), round_price(sym, sl), round_price(sym, tp)


class SuggestionWatchManager:
    """Persisted maintenance for analysis-based pending orders."""

    def __init__(self, poll_ms: int = SUGGESTION_WATCH_POLL_MS):
        self._jobs: dict[int, SuggestionWatchJob] = {}
        self._lock = threading.Lock()
        self._poll_ms = poll_ms
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.isfile(SUGGESTION_WATCH_STATE_FILE):
            return
        try:
            with open(SUGGESTION_WATCH_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for raw in data.get("jobs") or []:
                job = SuggestionWatchJob(**raw)
                if job.active:
                    self._jobs[int(job.ticket)] = job
            if self._jobs:
                print(f"[SuggestionWatch] restored {len(self._jobs)} job(s) from state file")
                self.start()
        except Exception as exc:
            print(f"[SuggestionWatch] state load failed: {exc}")

    def _save_state(self) -> None:
        try:
            with self._lock:
                payload = {
                    "version": 1,
                    "saved_at": datetime.utcnow().isoformat() + "Z",
                    "jobs": [asdict(j) for j in self._jobs.values() if j.active],
                }
            tmp = SUGGESTION_WATCH_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, SUGGESTION_WATCH_STATE_FILE)
        except Exception as exc:
            print(f"[SuggestionWatch] state save failed: {exc}")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="suggestion-watch")
        self._thread.start()

    def add(
        self,
        ticket: int,
        symbol: str,
        magic: int,
        order_type: str,
        chart_tf: str,
        bar_count: int,
        ob_time: str,
        ob_type: str,
        entry: float,
        sl: float,
        tp: float,
        volume: float,
    ) -> dict[str, Any]:
        job = SuggestionWatchJob(
            ticket=int(ticket),
            symbol=symbol,
            magic=int(magic),
            order_type=str(order_type),
            chart_tf=str(chart_tf).upper(),
            bar_count=int(bar_count),
            ob_time=str(ob_time),
            ob_type=str(ob_type),
            entry=float(entry),
            sl=float(sl),
            tp=float(tp),
            volume=float(volume),
            created_at=datetime.utcnow().isoformat() + "Z",
            last_status="watching",
            last_message="Monitoring OB validity and updating SL/TP",
        )
        with self._lock:
            self._jobs[job.ticket] = job
        self._save_state()
        self.start()
        return {
            "ok": True,
            "ticket": job.ticket,
            "message": f"Watching order #{job.ticket} — auto cancel if OB expires",
            "state_file": SUGGESTION_WATCH_STATE_FILE,
        }

    def remove(self, ticket: int, status: str = "stopped", message: str = "") -> bool:
        with self._lock:
            job = self._jobs.pop(int(ticket), None)
        if job:
            job.active = False
            job.last_status = status
            if message:
                job.last_message = message
            self._save_state()
            return True
        return False

    def remove_all(self) -> int:
        with self._lock:
            n = len(self._jobs)
            self._jobs.clear()
        self._save_state()
        return n

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(j) for j in self._jobs.values()]

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                tickets = list(self._jobs.keys())
            for ticket in tickets:
                with self._lock:
                    job = self._jobs.get(ticket)
                if job and job.active:
                    self._tick(job)
            time.sleep(self._poll_ms / 1000.0)

    def _finish(self, job: SuggestionWatchJob, status: str, message: str) -> None:
        job.last_status = status
        job.last_message = message
        job.last_check = datetime.utcnow().isoformat() + "Z"
        job.active = False
        print(f"[SuggestionWatch] #{job.ticket} {status}: {message}")
        self.remove(job.ticket, status=status, message=message)

    def _tick(self, job: SuggestionWatchJob) -> None:
        job.last_check = datetime.utcnow().isoformat() + "Z"
        orders = mt5.orders_get(ticket=job.ticket)
        if not orders:
            positions = mt5.positions_get(symbol=job.symbol) or []
            positions = [p for p in positions if p.magic == job.magic]
            if positions:
                self._finish(job, "filled", "Order filled — position open, watch stopped")
            else:
                self._finish(job, "removed_manual", "Pending order gone (cancelled manually in MT5)")
            return

        analysis = build_chart_analysis(job.symbol, job.chart_tf, job.bar_count)
        if not analysis.get("ok"):
            job.last_message = analysis.get("error", "analysis failed")
            self._save_state()
            return

        ob = _find_ob_in_analysis(analysis, job.ob_time, job.ob_type)
        if ob is None:
            cancel_order_ticket(job.ticket)
            self._finish(job, "cancelled_ob_missing", "OB no longer found on chart — order cancelled")
            return

        ob_status = ob.get("status", "")
        if not ob.get("is_tradeable") or ob_status in ("BROKEN", "MITIGATED", "STALE"):
            cancel_order_ticket(job.ticket)
            self._finish(
                job,
                "cancelled_ob_expired",
                f"OB {ob_status} — order cancelled automatically",
            )
            return

        atr = float((analysis.get("chart") or {}).get("atr") or 2.0)
        new_entry, new_sl, new_tp = _recalc_ob_order_prices(job.symbol, ob, job.ob_type, atr)
        info = mt5.symbol_info(job.symbol)
        pt = info.point if info else 0.01
        tol = pt * 3

        o = orders[0]
        needs_modify = (
            abs(new_entry - o.price_open) > tol
            or abs(new_sl - o.sl) > tol
            or abs(new_tp - o.tp) > tol
        )
        if needs_modify:
            mod = modify_pending_order(job.ticket, price=new_entry, sl=new_sl, tp=new_tp)
            if mod.get("ok"):
                job.entry, job.sl, job.tp = new_entry, new_sl, new_tp
                job.modify_count += 1
                job.last_message = f"Updated order SL/TP (OB {ob_status})"
            else:
                job.last_message = f"Modify failed: {mod.get('error', 'unknown')}"
        else:
            job.last_message = f"Watching — OB {ob_status}, order still valid"

        job.last_status = "watching"
        self._save_state()


suggestion_watch = SuggestionWatchManager()


# ---------------------------------------------------------------------------
# Telegram candle-close alerts
# ---------------------------------------------------------------------------
def _html_escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mask_telegram_token(token: str) -> str:
    t = token.strip()
    if not t:
        return "—"
    if len(t) <= 12:
        return t[:4] + "****"
    return f"{t[:8]}****{t[-4:]}"


def _telegram_ssl_context() -> ssl.SSLContext:
    """Windows VPS often lacks CA certs — use certifi. Set TELEGRAM_SSL_VERIFY=false if behind SSL-inspecting proxy."""
    if os.environ.get("TELEGRAM_SSL_VERIFY", "true").lower() in ("0", "false", "no"):
        return ssl._create_unverified_context()
    return ssl.create_default_context(cafile=certifi.where())


def send_telegram_message(text: str) -> dict[str, Any]:
    """Send HTML message to configured Telegram channel."""
    token = TELEGRAM_BOT_TOKEN.strip()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
    chat_id = TELEGRAM_CHAT_ID.strip() or "@partneralphafx"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ssl_ctx = _telegram_ssl_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl_ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("ok"):
            return {"ok": True, "message_id": (body.get("result") or {}).get("message_id")}
        return {"ok": False, "error": body.get("description", "telegram api error")}
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read().decode("utf-8"))
            detail = err_body.get("description", str(exc))
        except Exception:
            detail = str(exc)
        return {"ok": False, "error": detail}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def format_telegram_signal(result: dict[str, Any]) -> str:
    ob = result.get("ob") or {}
    sym = result.get("symbol", "?")
    tf = result.get("chart_timeframe", "?")
    side = str(result.get("side", "?"))
    emoji = "🟢" if side == "BUY" else "🔴"
    lines = [
        f"<b>{emoji} AlphaFX Signal</b>",
        "",
        f"<b>Symbol:</b> {_html_escape(sym)}",
        f"<b>Timeframe:</b> {_html_escape(tf)}",
        f"<b>Trend:</b> {_html_escape(result.get('overall_trend', '?'))}",
        "",
        f"<b>{_html_escape(side)}</b> · {_html_escape(result.get('order_type', '?'))}",
        f"<b>Entry:</b> {result.get('entry')}",
        f"<b>SL:</b> {result.get('sl')}",
        f"<b>TP:</b> {result.get('tp')}",
        f"<b>R:R:</b> {result.get('rr')}:1",
        f"<b>Confidence:</b> {_html_escape(result.get('confidence', '?'))}",
        "",
        _html_escape(result.get("reason", "")),
    ]
    if ob:
        lines.append(
            f"OB: {_html_escape(ob.get('type', '?'))} · "
            f"{_html_escape(ob.get('status', '?'))} · "
            f"{_html_escape(ob.get('volume_k', '?'))}"
        )
    bid = result.get("bid")
    if bid is not None:
        lines.append(f"Price: {bid}")
    return "\n".join(lines)


@dataclass
class TelegramAlertState:
    active: bool = False
    symbol: str = "XAUUSD"
    bar_count: int = 200
    timeframes: list[str] = field(default_factory=lambda: list(TELEGRAM_ALERT_TIMEFRAMES))
    last_closed: dict[str, int] = field(default_factory=dict)
    notified_blocks: list[str] = field(default_factory=list)
    started_at: str = ""
    stopped_at: str = ""
    signals_sent: int = 0
    last_check: str = ""
    last_signal_at: str = ""
    last_error: str = ""


class TelegramAlertManager:
    """On each M1/M5/M15/M30/H1 candle close, send trade suggestion to Telegram."""

    def __init__(self, poll_ms: int = TELEGRAM_ALERT_POLL_MS):
        self._state = TelegramAlertState()
        self._resolved_symbol = ""
        self._lock = threading.Lock()
        self._poll_ms = poll_ms
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.isfile(TELEGRAM_ALERT_STATE_FILE):
            return
        try:
            with open(TELEGRAM_ALERT_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            fields = {f.name for f in TelegramAlertState.__dataclass_fields__.values()}
            self._state = TelegramAlertState(**{k: v for k, v in data.items() if k in fields})
            # migrate old per-TF dedup keys
            legacy = data.get("last_sent") or {}
            if isinstance(legacy, dict):
                for key in legacy.values():
                    if key and key not in self._state.notified_blocks:
                        self._state.notified_blocks.append(str(key))
            self._resolved_symbol = str(data.get("resolved_symbol") or "")
            if self._state.active:
                print("[TelegramAlerts] restored active monitoring from state file")
                self.start(resume=True)
        except Exception as exc:
            print(f"[TelegramAlerts] state load failed: {exc}")

    def _save_state(self) -> None:
        try:
            with self._lock:
                payload = asdict(self._state)
                payload["resolved_symbol"] = self._resolved_symbol
                payload["version"] = 1
                payload["saved_at"] = datetime.utcnow().isoformat() + "Z"
            tmp = TELEGRAM_ALERT_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, TELEGRAM_ALERT_STATE_FILE)
        except Exception as exc:
            print(f"[TelegramAlerts] state save failed: {exc}")

    def status(self) -> dict[str, Any]:
        with self._lock:
            st = asdict(self._state)
        return {
            "ok": True,
            "active": st["active"],
            "notifications": "on" if st["active"] else "off",
            "symbol": st["symbol"],
            "resolved_symbol": self._resolved_symbol,
            "bar_count": st["bar_count"],
            "timeframes": st["timeframes"],
            "signals_sent": st["signals_sent"],
            "notified_blocks_count": len(st.get("notified_blocks") or []),
            "started_at": st["started_at"],
            "stopped_at": st["stopped_at"],
            "last_check": st["last_check"],
            "last_signal_at": st["last_signal_at"],
            "last_error": st["last_error"],
            "telegram_configured": bool(TELEGRAM_BOT_TOKEN.strip()),
            "token_masked": mask_telegram_token(TELEGRAM_BOT_TOKEN),
            "channel": TELEGRAM_CHAT_ID,
            "chat_id": TELEGRAM_CHAT_ID,
            "state_file": TELEGRAM_ALERT_STATE_FILE,
        }

    def start(self, symbol: str = "", bar_count: int = 0, resume: bool = False) -> dict[str, Any]:
        if not TELEGRAM_BOT_TOKEN.strip():
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set on server"}

        if self._state.active and not resume:
            return {
                "ok": True,
                "message": "Already monitoring",
                "symbol": self._state.symbol,
                "resolved_symbol": self._resolved_symbol,
                "timeframes": self._state.timeframes,
            }

        ok, msg = ensure_mt5(MT5_PATH or None)
        if not ok:
            return {"ok": False, "error": "mt5 not connected", "detail": msg}

        if symbol:
            self._state.symbol = symbol.strip()
        if bar_count > 0:
            self._state.bar_count = max(50, min(int(bar_count), 5000))

        info, sym = resolve_symbol(self._state.symbol)
        if info is None:
            return {"ok": False, "error": f"symbol not found: {self._state.symbol}"}
        self._resolved_symbol = sym

        self._sync_bar_times(sym, initialize=True)
        self._state.active = True
        self._state.started_at = datetime.utcnow().isoformat() + "Z"
        self._state.stopped_at = ""
        self._state.last_error = ""
        self._save_state()
        self._ensure_thread()

        return {
            "ok": True,
            "message": "Telegram monitoring started",
            "symbol": self._state.symbol,
            "resolved_symbol": sym,
            "timeframes": self._state.timeframes,
            "notifications": "on",
            "token_masked": mask_telegram_token(TELEGRAM_BOT_TOKEN),
            "channel": TELEGRAM_CHAT_ID,
        }

    def stop(self) -> dict[str, Any]:
        self._state.active = False
        self._state.stopped_at = datetime.utcnow().isoformat() + "Z"
        self._save_state()
        return {
            "ok": True,
            "message": "Telegram monitoring stopped",
            "notifications": "off",
        }

    def send_test(self) -> dict[str, Any]:
        """Send a one-off test message to verify bot + channel."""
        if not TELEGRAM_BOT_TOKEN.strip():
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set on server"}
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        msg = (
            "<b>🧪 AlphaFX Test Notification</b>\n\n"
            "Telegram connection is working.\n"
            f"<b>Channel:</b> {_html_escape(TELEGRAM_CHAT_ID)}\n"
            f"<b>API:</b> {_html_escape(API_VERSION)}\n"
            f"<b>Time:</b> {now}"
        )
        tg = send_telegram_message(msg)
        if tg.get("ok"):
            return {
                "ok": True,
                "message": "Test notification sent via server",
                "sent_via": "server",
                "channel": TELEGRAM_CHAT_ID,
                "message_id": tg.get("message_id"),
            }
        return {"ok": False, "error": tg.get("error", "telegram send failed")}

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telegram-alerts")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._state.active:
                self._tick()
            time.sleep(self._poll_ms / 1000.0)

    def _sync_bar_times(self, sym: str, initialize: bool = False) -> None:
        for tf in self._state.timeframes:
            mt5_tf = TIMEFRAMES.get(tf)
            if mt5_tf is None:
                continue
            rates = mt5.copy_rates_from_pos(sym, mt5_tf, 0, 3)
            if rates is None or len(rates) < 2:
                continue
            closed_time = int(rates[-2]["time"])
            prev = self._state.last_closed.get(tf)
            if initialize or prev is None:
                self._state.last_closed[tf] = closed_time
            elif closed_time != prev:
                self._state.last_closed[tf] = closed_time
                self._on_candle_close(sym, tf)

    def _ob_notify_key(self, sym: str, ob: dict) -> str:
        """One key per OB zone — shared across all timeframes so we never repeat."""
        return "|".join([
            sym,
            str(ob.get("type", "")),
            str(ob.get("time", "")),
            str(ob.get("high", "")),
            str(ob.get("low", "")),
        ])

    def _already_notified(self, key: str) -> bool:
        return key in self._state.notified_blocks

    def _mark_notified(self, key: str) -> None:
        if key not in self._state.notified_blocks:
            self._state.notified_blocks.append(key)
        if len(self._state.notified_blocks) > 500:
            self._state.notified_blocks = self._state.notified_blocks[-500:]

    def _on_candle_close(self, sym: str, tf: str) -> None:
        result = build_trade_suggestion(sym, tf, self._state.bar_count)
        if not result.get("ok"):
            self._state.last_error = result.get("error", "analysis failed")
            return
        if not result.get("has_setup"):
            return

        ob = result.get("ob") or {}
        if not ob:
            return
        if not ob.get("is_tradeable", True):
            return
        if ob.get("status") in ("BROKEN", "MITIGATED", "STALE"):
            return

        key = self._ob_notify_key(sym, ob)
        if self._already_notified(key):
            return

        result["symbol"] = sym
        result["chart_timeframe"] = tf
        tg = send_telegram_message(format_telegram_signal(result))
        if tg.get("ok"):
            self._mark_notified(key)
            self._state.signals_sent += 1
            self._state.last_signal_at = datetime.utcnow().isoformat() + "Z"
            self._state.last_error = ""
            print(f"[TelegramAlerts] signal sent {sym} {tf} OB {ob.get('time')} {result.get('side')}")
        else:
            self._state.last_error = tg.get("error", "telegram send failed")
            print(f"[TelegramAlerts] send failed: {self._state.last_error}")

    def _tick(self) -> None:
        self._state.last_check = datetime.utcnow().isoformat() + "Z"
        sym = self._resolved_symbol
        if not sym:
            info, sym = resolve_symbol(self._state.symbol)
            if info is None:
                self._state.last_error = f"symbol not found: {self._state.symbol}"
                self._save_state()
                return
            self._resolved_symbol = sym
        try:
            self._sync_bar_times(sym, initialize=False)
        except Exception as exc:
            self._state.last_error = str(exc)
        self._save_state()


telegram_alerts = TelegramAlertManager()


def has_autotrade_exposure(sym: str, magic: int) -> bool:
    """True if an open position or pending order exists for this symbol with alphafxauto comment."""
    for pos in mt5.positions_get(symbol=sym) or []:
        if pos.magic == magic and AUTOTRADE_COMMENT in (pos.comment or ""):
            return True
    for order in mt5.orders_get(symbol=sym) or []:
        if order.magic == magic and AUTOTRADE_COMMENT in (order.comment or ""):
            return True
    return False


@dataclass
class AutoTradeState:
    active: bool = False
    symbol: str = "XAUUSD"
    bar_count: int = 200
    lot_size: float = AUTOTRADE_DEFAULT_LOT
    magic: int = AUTOTRADE_DEFAULT_MAGIC
    timeframes: list[str] = field(default_factory=lambda: list(AUTOTRADE_TIMEFRAMES))
    last_closed: dict[str, int] = field(default_factory=dict)
    traded_blocks: list[str] = field(default_factory=list)
    started_at: str = ""
    stopped_at: str = ""
    trades_placed: int = 0
    last_check: str = ""
    last_trade_at: str = ""
    last_error: str = ""
    last_order_ticket: int | None = None


class AutoTradeManager:
    """On M5+ candle close, auto-place OB suggestion orders (comment alphafxauto) with suggestion watch."""

    def __init__(self, poll_ms: int = AUTOTRADE_POLL_MS):
        self._state = AutoTradeState()
        self._resolved_symbol = ""
        self._lock = threading.Lock()
        self._poll_ms = poll_ms
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.isfile(AUTOTRADE_STATE_FILE):
            return
        try:
            with open(AUTOTRADE_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            fields = {f.name for f in AutoTradeState.__dataclass_fields__.values()}
            self._state = AutoTradeState(**{k: v for k, v in data.items() if k in fields})
            self._resolved_symbol = str(data.get("resolved_symbol") or "")
            if self._state.active:
                print("[AutoTrade] restored active monitoring from state file")
                self.start(resume=True)
        except Exception as exc:
            print(f"[AutoTrade] state load failed: {exc}")

    def _save_state(self) -> None:
        try:
            with self._lock:
                payload = asdict(self._state)
                payload["resolved_symbol"] = self._resolved_symbol
                payload["version"] = 1
                payload["saved_at"] = datetime.utcnow().isoformat() + "Z"
            tmp = AUTOTRADE_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, AUTOTRADE_STATE_FILE)
        except Exception as exc:
            print(f"[AutoTrade] state save failed: {exc}")

    def status(self) -> dict[str, Any]:
        with self._lock:
            st = asdict(self._state)
        return {
            "ok": True,
            "active": st["active"],
            "autotrading": "on" if st["active"] else "off",
            "symbol": st["symbol"],
            "resolved_symbol": self._resolved_symbol,
            "bar_count": st["bar_count"],
            "lot_size": st["lot_size"],
            "magic": st["magic"],
            "timeframes": st["timeframes"],
            "comment": AUTOTRADE_COMMENT,
            "trades_placed": st["trades_placed"],
            "traded_blocks_count": len(st.get("traded_blocks") or []),
            "started_at": st["started_at"],
            "stopped_at": st["stopped_at"],
            "last_check": st["last_check"],
            "last_trade_at": st["last_trade_at"],
            "last_error": st["last_error"],
            "last_order_ticket": st.get("last_order_ticket"),
            "state_file": AUTOTRADE_STATE_FILE,
        }

    def configure(
        self,
        lot_size: float | None = None,
        magic: int | None = None,
        bar_count: int | None = None,
    ) -> dict[str, Any]:
        if lot_size is not None:
            lot = float(lot_size)
            if lot <= 0:
                return {"ok": False, "error": "lot_size must be > 0"}
            self._state.lot_size = lot
        if magic is not None:
            self._state.magic = int(magic)
        if bar_count is not None and int(bar_count) > 0:
            self._state.bar_count = max(50, min(int(bar_count), 5000))
        self._save_state()
        return {
            "ok": True,
            "message": "Auto-trade settings updated",
            **self.status(),
        }

    def start(
        self,
        symbol: str = "",
        bar_count: int = 0,
        lot_size: float = 0,
        magic: int | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        if self._state.active and not resume:
            return {
                "ok": True,
                "message": "Auto-trading already ON",
                **self.status(),
            }

        ok, msg = ensure_mt5(MT5_PATH or None)
        if not ok:
            return {"ok": False, "error": "mt5 not connected", "detail": msg}

        if symbol:
            self._state.symbol = symbol.strip()
        if bar_count > 0:
            self._state.bar_count = max(50, min(int(bar_count), 5000))
        if lot_size > 0:
            self._state.lot_size = float(lot_size)
        if magic is not None:
            self._state.magic = int(magic)

        info, sym = resolve_symbol(self._state.symbol)
        if info is None:
            return {"ok": False, "error": f"symbol not found: {self._state.symbol}"}
        self._resolved_symbol = sym

        self._sync_bar_times(sym, initialize=True)
        self._state.active = True
        self._state.started_at = datetime.utcnow().isoformat() + "Z"
        self._state.stopped_at = ""
        self._state.last_error = ""
        self._save_state()
        self._ensure_thread()

        return {
            "ok": True,
            "message": "Auto-trading started (M5+ signals)",
            "autotrading": "on",
            **self.status(),
        }

    def stop(self) -> dict[str, Any]:
        self._state.active = False
        self._state.stopped_at = datetime.utcnow().isoformat() + "Z"
        self._save_state()
        return {
            "ok": True,
            "message": "Auto-trading stopped",
            "autotrading": "off",
            **self.status(),
        }

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="autotrade")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._state.active:
                self._tick()
            time.sleep(self._poll_ms / 1000.0)

    def _sync_bar_times(self, sym: str, initialize: bool = False) -> None:
        for tf in self._state.timeframes:
            mt5_tf = TIMEFRAMES.get(tf)
            if mt5_tf is None:
                continue
            rates = mt5.copy_rates_from_pos(sym, mt5_tf, 0, 3)
            if rates is None or len(rates) < 2:
                continue
            closed_time = int(rates[-2]["time"])
            prev = self._state.last_closed.get(tf)
            if initialize or prev is None:
                self._state.last_closed[tf] = closed_time
            elif closed_time != prev:
                self._state.last_closed[tf] = closed_time
                self._on_candle_close(sym, tf)

    def _ob_trade_key(self, sym: str, ob: dict) -> str:
        return "|".join([
            sym,
            str(ob.get("type", "")),
            str(ob.get("time", "")),
            str(ob.get("high", "")),
            str(ob.get("low", "")),
        ])

    def _already_traded(self, key: str) -> bool:
        return key in self._state.traded_blocks

    def _mark_traded(self, key: str) -> None:
        if key not in self._state.traded_blocks:
            self._state.traded_blocks.append(key)
        if len(self._state.traded_blocks) > 500:
            self._state.traded_blocks = self._state.traded_blocks[-500:]

    def _on_candle_close(self, sym: str, tf: str) -> None:
        result = build_trade_suggestion(sym, tf, self._state.bar_count)
        if not result.get("ok"):
            self._state.last_error = result.get("error", "analysis failed")
            return
        if not result.get("has_setup"):
            return
        if result.get("risky"):
            return

        ob = result.get("ob") or {}
        if not ob or not ob.get("is_tradeable", True):
            return
        if ob.get("status") in ("BROKEN", "MITIGATED", "STALE"):
            return

        key = self._ob_trade_key(sym, ob)
        if self._already_traded(key):
            return

        if has_autotrade_exposure(sym, self._state.magic):
            self._state.last_error = f"skip: existing {AUTOTRADE_COMMENT} exposure on {sym}"
            return

        order_type = str(result.get("order_type") or "").lower()
        if not order_type:
            return

        order_data: dict[str, Any] = {
            "symbol": self._state.symbol,
            "type": order_type,
            "volume": self._state.lot_size,
            "sl": result.get("sl"),
            "tp": result.get("tp"),
            "magic": self._state.magic,
            "comment": AUTOTRADE_COMMENT,
            "watch": True,
            "watch_meta": {
                "chart_timeframe": tf,
                "count": self._state.bar_count,
                "ob_time": ob.get("time"),
                "ob_type": ob.get("type"),
                "ob": ob,
            },
        }
        entry = result.get("entry")
        if order_type not in ("buy", "sell") and entry is not None:
            order_data["price"] = entry

        payload, status = _execute_place_order(order_data)
        if payload.get("ok"):
            ticket = None
            res = payload.get("result") or {}
            if isinstance(res, dict):
                ticket = res.get("order")
            elif res is not None:
                ticket = getattr(res, "order", None)
            self._mark_traded(key)
            self._state.trades_placed += 1
            self._state.last_trade_at = datetime.utcnow().isoformat() + "Z"
            self._state.last_order_ticket = int(ticket) if ticket else None
            self._state.last_error = ""
            print(
                f"[AutoTrade] placed {sym} {tf} {result.get('side')} "
                f"#{ticket} lot={self._state.lot_size}"
            )
            if TELEGRAM_BOT_TOKEN.strip():
                tg_body = format_telegram_signal({**result, "symbol": sym, "chart_timeframe": tf})
                send_telegram_message(
                    tg_body + f"\n\n<b>🤖 AUTO PLACED</b> · #{ticket or '?'}"
                    f" · {self._state.lot_size} lot · <code>{AUTOTRADE_COMMENT}</code>"
                )
        else:
            err = payload.get("error") or str(payload.get("result") or "order failed")
            self._state.last_error = str(err)
            print(f"[AutoTrade] place failed: {self._state.last_error}")

    def _tick(self) -> None:
        self._state.last_check = datetime.utcnow().isoformat() + "Z"
        sym = self._resolved_symbol
        if not sym:
            info, sym = resolve_symbol(self._state.symbol)
            if info is None:
                self._state.last_error = f"symbol not found: {self._state.symbol}"
                self._save_state()
                return
            self._resolved_symbol = sym
        try:
            self._sync_bar_times(sym, initialize=False)
        except Exception as exc:
            self._state.last_error = str(exc)
        self._save_state()


auto_trade = AutoTradeManager()


# ---------------------------------------------------------------------------
# Auth / helpers
# ---------------------------------------------------------------------------
def require_api_key(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if API_KEY:
            key = request.headers.get("X-API-Key") or request.args.get("api_key")
            if key != API_KEY:
                return jsonify({"ok": False, "error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


def require_mt5(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ok, msg = ensure_mt5(MT5_PATH or None)
        if not ok:
            return jsonify({"ok": False, "error": "mt5 not connected", "detail": msg}), 503
        return fn(*args, **kwargs)
    return wrapper


def json_body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


_news_cache_lock = threading.Lock()
_news_cache: dict[str, Any] = {"fetched_at": 0.0, "raw": []}


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": f"AlphaFX-MT5-API/{API_VERSION}"})
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_ff_calendar_raw() -> list[dict[str, Any]]:
    now = time.time()
    with _news_cache_lock:
        if now - float(_news_cache.get("fetched_at", 0)) < NEWS_CACHE_TTL and _news_cache.get("raw"):
            return list(_news_cache["raw"])

    merged: list[dict[str, Any]] = []
    try:
        data = _http_get_json(NEWS_CALENDAR_URL)
        if isinstance(data, list):
            merged.extend(data)
    except Exception:
        merged = []

    with _news_cache_lock:
        _news_cache["fetched_at"] = now
        _news_cache["raw"] = merged
    return merged


def _parse_ff_event_time(date_s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(date_s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def build_upcoming_news(
    hours_ahead: int = 72,
    impact: str = "High",
    currencies: list[str] | None = None,
) -> dict[str, Any]:
    """High-impact (red folder) events from Forex Factory calendar feed."""
    raw = _load_ff_calendar_raw()
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=hours_ahead)
    impact_key = (impact or "High").strip().lower()
    currency_set = {c.upper() for c in currencies} if currencies else None

    events: list[dict[str, Any]] = []
    for item in raw:
        item_impact = str(item.get("impact", "")).strip().lower()
        if item_impact != impact_key:
            continue

        currency = str(item.get("country", "")).strip().upper()
        if currency_set and currency not in currency_set:
            continue

        dt = _parse_ff_event_time(str(item.get("date", "")))
        if dt is None:
            continue
        if dt < now - timedelta(minutes=30):
            continue
        if dt > end:
            continue

        minutes_until = int((dt - now).total_seconds() // 60)
        title = str(item.get("title", "")).strip()
        event_id = f"{currency}|{dt.isoformat()}|{title}"
        events.append({
            "event_id": event_id,
            "title": title,
            "currency": currency,
            "country": currency,
            "impact": str(item.get("impact", impact)).strip(),
            "time": dt.isoformat().replace("+00:00", "Z"),
            "forecast": item.get("forecast") or "—",
            "previous": item.get("previous") or "—",
            "minutes_until": minutes_until,
            "is_imminent": -5 <= minutes_until <= 30,
            "is_past": minutes_until < -5,
        })

    events.sort(key=lambda e: e["time"])
    return {
        "ok": True,
        "source": "forex_factory",
        "impact_filter": impact,
        "hours_ahead": hours_ahead,
        "count": len(events),
        "events": events,
    }


def _news_event_key(event: dict[str, Any]) -> str:
    return str(event.get("event_id") or f"{event.get('currency')}|{event.get('time')}|{event.get('title')}")


def format_news_alert_message(event: dict[str, Any], minutes_before: int) -> str:
    cur = _html_escape(str(event.get("currency", "?")))
    title = _html_escape(str(event.get("title", "?")))
    when = _html_escape(str(event.get("time", "?")).replace("T", " ").replace("+00:00", " UTC"))
    forecast = _html_escape(str(event.get("forecast", "—")))
    previous = _html_escape(str(event.get("previous", "—")))
    return (
        f"<b>📕 News in {minutes_before} min</b>\n\n"
        f"<b>{cur}</b> · {title}\n"
        f"<b>Time:</b> {when}\n"
        f"<b>Forecast:</b> {forecast} · <b>Previous:</b> {previous}\n"
        f"<b>Impact:</b> High (red folder)"
    )


@dataclass
class NewsAlertState:
    active: bool = False
    currency_filter: str = "USD"
    hours_ahead: int = 72
    minutes_before: int = NEWS_ALERT_MINUTES_BEFORE
    notified_events: list[str] = field(default_factory=list)
    alerts_sent: int = 0
    started_at: str = ""
    stopped_at: str = ""
    last_check: str = ""
    last_alert_at: str = ""
    last_error: str = ""


class NewsAlertManager:
    """Telegram alert 5 minutes before high-impact calendar events."""

    def __init__(self, poll_ms: int = NEWS_ALERT_POLL_MS):
        self._state = NewsAlertState()
        self._lock = threading.Lock()
        self._poll_ms = poll_ms
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.isfile(NEWS_ALERT_STATE_FILE):
            return
        try:
            with open(NEWS_ALERT_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            fields = {f.name for f in NewsAlertState.__dataclass_fields__.values()}
            self._state = NewsAlertState(**{k: v for k, v in data.items() if k in fields})
            if self._state.active:
                print("[NewsAlerts] restored active monitoring from state file")
                self.start(resume=True)
        except Exception as exc:
            print(f"[NewsAlerts] state load failed: {exc}")

    def _save_state(self) -> None:
        try:
            with self._lock:
                payload = asdict(self._state)
                payload["version"] = 1
                payload["saved_at"] = datetime.utcnow().isoformat() + "Z"
            tmp = NEWS_ALERT_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, NEWS_ALERT_STATE_FILE)
        except Exception as exc:
            print(f"[NewsAlerts] state save failed: {exc}")

    def _currencies_for_filter(self) -> list[str] | None:
        filt = (self._state.currency_filter or "USD").upper()
        if filt == "ALL":
            return None
        return [filt]

    def status(self) -> dict[str, Any]:
        with self._lock:
            st = asdict(self._state)
        return {
            "ok": True,
            "active": st["active"],
            "notifications": "on" if st["active"] else "off",
            "currency_filter": st["currency_filter"],
            "hours_ahead": st["hours_ahead"],
            "minutes_before": st["minutes_before"],
            "alerts_sent": st["alerts_sent"],
            "notified_events_count": len(st.get("notified_events") or []),
            "started_at": st["started_at"],
            "stopped_at": st["stopped_at"],
            "last_check": st["last_check"],
            "last_alert_at": st["last_alert_at"],
            "last_error": st["last_error"],
            "telegram_configured": bool(TELEGRAM_BOT_TOKEN.strip()),
            "token_masked": mask_telegram_token(TELEGRAM_BOT_TOKEN),
            "channel": TELEGRAM_CHAT_ID,
            "chat_id": TELEGRAM_CHAT_ID,
            "state_file": NEWS_ALERT_STATE_FILE,
        }

    def start(
        self,
        currency_filter: str = "USD",
        hours_ahead: int = 72,
        resume: bool = False,
    ) -> dict[str, Any]:
        if not TELEGRAM_BOT_TOKEN.strip():
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set on server"}

        filt = (currency_filter or "USD").strip().upper()
        if filt not in ("USD", "ALL"):
            filt = "USD"

        if self._state.active and not resume:
            self._state.currency_filter = filt
            self._state.hours_ahead = min(168, max(1, int(hours_ahead)))
            self._state.last_error = ""
            self._save_state()
            return {
                "ok": True,
                "message": "News alert settings updated",
                "notifications": "on",
                "currency_filter": self._state.currency_filter,
                "minutes_before": self._state.minutes_before,
            }

        self._state.currency_filter = filt
        self._state.hours_ahead = min(168, max(1, int(hours_ahead)))
        self._state.minutes_before = NEWS_ALERT_MINUTES_BEFORE
        self._state.active = True
        self._state.started_at = datetime.utcnow().isoformat() + "Z"
        self._state.stopped_at = ""
        self._state.last_error = ""
        self._save_state()
        self._ensure_thread()

        return {
            "ok": True,
            "message": f"News alerts started — Telegram {self._state.minutes_before}m before",
            "notifications": "on",
            "currency_filter": self._state.currency_filter,
            "hours_ahead": self._state.hours_ahead,
            "minutes_before": self._state.minutes_before,
            "channel": TELEGRAM_CHAT_ID,
        }

    def stop(self) -> dict[str, Any]:
        self._state.active = False
        self._state.stopped_at = datetime.utcnow().isoformat() + "Z"
        self._save_state()
        return {
            "ok": True,
            "message": "News alerts stopped",
            "notifications": "off",
        }

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="news-alerts")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._state.active:
                self._tick()
            time.sleep(self._poll_ms / 1000.0)

    def _already_notified(self, key: str) -> bool:
        return key in self._state.notified_events

    def _mark_notified(self, key: str) -> None:
        if key not in self._state.notified_events:
            self._state.notified_events.append(key)
        if len(self._state.notified_events) > 500:
            self._state.notified_events = self._state.notified_events[-500:]

    def _tick(self) -> None:
        self._state.last_check = datetime.utcnow().isoformat() + "Z"
        try:
            payload = build_upcoming_news(
                hours_ahead=self._state.hours_ahead,
                impact="High",
                currencies=self._currencies_for_filter(),
            )
        except Exception as exc:
            self._state.last_error = str(exc)
            self._save_state()
            return

        target = self._state.minutes_before
        window_lo = max(1, target - 1)
        window_hi = target + 1

        for event in payload.get("events", []):
            key = _news_event_key(event)
            if self._already_notified(key):
                continue
            minutes_until = int(event.get("minutes_until", 9999))
            if window_lo <= minutes_until <= window_hi:
                tg = send_telegram_message(
                    format_news_alert_message(event, self._state.minutes_before)
                )
                if tg.get("ok"):
                    self._mark_notified(key)
                    self._state.alerts_sent += 1
                    self._state.last_alert_at = datetime.utcnow().isoformat() + "Z"
                    self._state.last_error = ""
                    print(f"[NewsAlerts] sent {event.get('currency')} {event.get('title')} ({minutes_until}m)")
                else:
                    self._state.last_error = tg.get("error", "telegram send failed")
                    print(f"[NewsAlerts] send failed: {self._state.last_error}")

        self._save_state()


news_alerts = NewsAlertManager()


def parse_timeout_mmss(value: str | None) -> int:
    """Parse MM:SS timeout; 00:00 or empty = no timeout."""
    s = str(value or "").strip()
    if not s or s in ("0", "00:00"):
        return 0
    if ":" in s:
        parts = s.split(":", 1)
        try:
            mm = int(parts[0])
            ss = int(parts[1]) if len(parts) > 1 else 0
            return max(0, mm * 60 + ss)
        except ValueError:
            return 0
    try:
        return max(0, int(s))
    except ValueError:
        return 0


def get_ist_timezone():
    """IST tz — ZoneInfo on Linux; fixed UTC+5:30 fallback on Windows without tzdata."""
    try:
        return ZoneInfo("Asia/Kolkata")
    except Exception:
        return timezone(timedelta(hours=5, minutes=30))


def parse_schedule_datetime(
    date_s: str,
    time_s: str,
    offset_hours: float = 0.0,
    tz_name: str = "IST",
) -> datetime:
    """Parse user date/time in IST (default) and convert to UTC for execution."""
    date_s = str(date_s or "").strip()
    time_s = str(time_s or "").strip()
    if not date_s or not time_s:
        raise ValueError("date and time required")

    d = None
    for fmt in ("%Y-%m-%d", "%m.%d.%Y", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            d = datetime.strptime(date_s, fmt).date()
            break
        except ValueError:
            continue
    if d is None:
        raise ValueError(f"invalid date format: {date_s}")

    if len(time_s) == 5 and time_s.count(":") == 1:
        time_s = f"{time_s}:00"

    t = None
    for tfmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(time_s, tfmt).time()
            break
        except ValueError:
            continue
    if t is None:
        raise ValueError(f"invalid time format: {time_s}")

    tz_key = (tz_name or "IST").strip().upper()
    if tz_key in ("IST", "ASIA/KOLKATA", "IN", "INDIA"):
        local_tz = get_ist_timezone()
    else:
        local_tz = timezone.utc

    local_dt = datetime.combine(d, t).replace(tzinfo=local_tz)
    if offset_hours:
        local_dt = local_dt + timedelta(hours=float(offset_hours))
    return local_dt.astimezone(timezone.utc)


def format_schedule_ist(dt_utc: datetime) -> str:
    ist = dt_utc.astimezone(get_ist_timezone())
    return ist.strftime("%d.%m.%Y %H:%M:%S IST")


def _execute_place_grid(data: dict[str, Any]) -> dict[str, Any]:
    symbol = data.get("symbol")
    if not symbol:
        return {"ok": False, "error": "symbol required"}

    magic = int(data.get("magic", 78001))
    max_profit = float(data.get("max_floating_profit") or data.get("floating_profit") or 0)
    basket_tp = float(
        data.get("basket_tp_profit") or data.get("basket_tp") or max_profit or 0
    )
    basket_sl = float(
        data.get("basket_sl_loss") or data.get("basket_sl") or data.get("target_loss") or basket_tp or 0
    )

    result = place_grid_fast(
        symbol=symbol,
        lot=float(data.get("lot", 0.01)),
        distance=int(data.get("distance", 100)),
        initial_distance=int(data.get("initial_distance", 200)),
        orders_quantity=int(data.get("orders_quantity", 50)),
        incremental=bool(data.get("incremental", False)),
        magic=magic,
        anchor=float(data["anchor"]) if data.get("anchor") else None,
        tp_points=int(data.get("tp_points") or data.get("grid_tp_points") or data.get("central_tp_points") or 0),
        sl_points=int(data.get("sl_points") or data.get("grid_sl_points") or data.get("per_order_sl_points") or 0),
    )

    if result.get("ok") and max_profit > 0:
        result["grid_guard"] = grid_guard.add(
            symbol=result.get("symbol", symbol),
            magic=magic,
            max_floating_profit=max_profit,
        )

    if result.get("ok") and basket_tp > 0:
        result["basket_tp"] = basket_tp_mgr.add(
            symbol=result.get("symbol", symbol),
            magic=magic,
            target_profit=basket_tp,
            target_loss=basket_sl if basket_sl > 0 else None,
        )

    return result


def format_schedule_created_telegram(schedule: dict[str, Any]) -> str:
    kind = schedule.get("kind", "?").upper()
    payload = schedule.get("payload") or {}
    sym = _html_escape(str(payload.get("symbol", "?")))
    timeout = schedule.get("timeout_mmss") or "00:00"
    when_ist = _html_escape(str(schedule.get("execute_at_ist") or schedule.get("date_input", "")))
    when_utc = _html_escape(str(schedule.get("execute_at", "?")).replace("T", " ").replace("+00:00", " UTC"))
    return (
        f"<b>📅 Scheduled {kind} created</b>\n\n"
        f"<b>Symbol:</b> {sym}\n"
        f"<b>Execute (IST):</b> {when_ist}\n"
        f"<b>Execute (UTC):</b> {when_utc}\n"
        f"<b>Timeout:</b> {timeout}\n"
        f"<b>ID:</b> <code>{schedule.get('id', '')[:8]}</code>"
    )


def format_schedule_done_telegram(schedule: dict[str, Any], success: bool) -> str:
    kind = schedule.get("kind", "?").upper()
    payload = schedule.get("payload") or {}
    sym = _html_escape(str(payload.get("symbol", "?")))
    label = f"Scheduled {kind} placed" if success else f"Scheduled {kind} failed"
    emoji = "✅" if success else "❌"
    lines = [f"<b>{emoji} {label}</b>", "", f"<b>Symbol:</b> {sym}", f"<b>ID:</b> <code>{schedule.get('id', '')[:8]}</code>"]
    if not success and schedule.get("error"):
        lines.append(f"<b>Error:</b> {_html_escape(str(schedule['error']))}")
    return "\n".join(lines)


def format_schedule_expired_telegram(schedule: dict[str, Any], close_result: dict[str, Any]) -> str:
    kind = schedule.get("kind", "?").upper()
    payload = schedule.get("payload") or {}
    sym = _html_escape(str(payload.get("symbol", "?")))
    return (
        f"<b>⏱ Scheduled {kind} timeout</b>\n\n"
        f"<b>Symbol:</b> {sym}\n"
        f"Closed {close_result.get('closed_positions', 0)} position(s), "
        f"cancelled {close_result.get('cancelled_orders', 0)} order(s)\n"
        f"<b>ID:</b> <code>{schedule.get('id', '')[:8]}</code>"
    )


class ScheduleManager:
    """Millisecond-precision scheduled grid deploy and trade execution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._schedules: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.isfile(SCHEDULE_STATE_FILE):
            return
        try:
            with open(SCHEDULE_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._schedules = list(data.get("schedules") or [])
            print(f"[Schedule] loaded {len(self._schedules)} schedule(s)")
            self._ensure_thread()
        except Exception as exc:
            print(f"[Schedule] load failed: {exc}")

    def _save_state(self) -> None:
        try:
            with self._lock:
                payload = {"version": 1, "schedules": list(self._schedules), "saved_at": datetime.utcnow().isoformat() + "Z"}
            tmp = SCHEDULE_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, SCHEDULE_STATE_FILE)
        except Exception as exc:
            print(f"[Schedule] save failed: {exc}")

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="schedule-ms")
        self._thread.start()

    def _parse_execute_at(self, schedule: dict[str, Any]) -> datetime:
        raw = schedule.get("execute_at", "")
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)

    def _pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s for s in self._schedules if s.get("status") == "pending"]

    def _next_pending_at(self) -> datetime | None:
        pending = self._pending()
        if not pending:
            return None
        return min(self._parse_execute_at(s) for s in pending)

    def _wait_tick(self) -> None:
        nxt = self._next_pending_at()
        if nxt is None:
            time.sleep(SCHEDULE_POLL_MS_IDLE / 1000.0)
            return
        now = datetime.now(timezone.utc)
        delta = (nxt - now).total_seconds()
        if delta > 60:
            time.sleep(SCHEDULE_POLL_MS_IDLE / 1000.0)
        elif delta > 0.05:
            time.sleep(min(max(delta - 0.02, 0.001), SCHEDULE_POLL_MS_HOT / 1000.0))
        else:
            while datetime.now(timezone.utc) < nxt:
                pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                print(f"[Schedule] loop error: {exc}")
            self._wait_tick()

    def _get_by_id(self, schedule_id: str) -> dict[str, Any] | None:
        with self._lock:
            for s in self._schedules:
                if s.get("id") == schedule_id:
                    return s
        return None

    def _update(self, schedule_id: str, **fields: Any) -> None:
        with self._lock:
            for s in self._schedules:
                if s.get("id") == schedule_id:
                    s.update(fields)
                    break

    def create(
        self,
        kind: str,
        date_input: str,
        time_input: str,
        timeout_mmss: str,
        payload: dict[str, Any],
        offset_hours: float = 0.0,
        tz_name: str = "IST",
    ) -> dict[str, Any]:
        if not TELEGRAM_BOT_TOKEN.strip():
            pass  # still allow schedule without telegram

        try:
            execute_at = parse_schedule_datetime(
                date_input, time_input, offset_hours=offset_hours, tz_name=tz_name,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        now = datetime.now(timezone.utc)
        if execute_at <= now:
            return {"ok": False, "error": "execute time must be in the future (IST)"}

        timeout_seconds = parse_timeout_mmss(timeout_mmss)
        schedule_id = str(uuid.uuid4())
        execute_at_ist = format_schedule_ist(execute_at)
        schedule = {
            "id": schedule_id,
            "kind": kind,
            "status": "pending",
            "timezone": (tz_name or "IST").upper(),
            "execute_at": execute_at.isoformat().replace("+00:00", "Z"),
            "execute_at_ist": execute_at_ist,
            "execute_at_ms": int(execute_at.timestamp() * 1000),
            "date_input": date_input,
            "time_input": time_input,
            "offset_hours": float(offset_hours),
            "timeout_mmss": timeout_mmss or "00:00",
            "timeout_seconds": timeout_seconds,
            "payload": payload,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "executed_at": None,
            "expired_at": None,
            "result": None,
            "error": None,
        }

        with self._lock:
            self._schedules.append(schedule)
        self._save_state()
        self._ensure_thread()

        try:
            tg = send_telegram_message(format_schedule_created_telegram(schedule))
        except Exception as exc:
            tg = {"ok": False, "error": str(exc)}
        if not tg.get("ok"):
            schedule["telegram_warning"] = tg.get("error")

        return {"ok": True, "schedule": schedule, "telegram": tg}

    def cancel(self, schedule_id: str) -> dict[str, Any]:
        sched = self._get_by_id(schedule_id)
        if not sched:
            return {"ok": False, "error": "schedule not found"}
        if sched.get("status") != "pending":
            return {"ok": False, "error": f"cannot cancel status={sched.get('status')}"}
        self._update(schedule_id, status="cancelled", error="cancelled by user")
        self._save_state()
        return {"ok": True, "id": schedule_id, "status": "cancelled"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            rows = [dict(s) for s in self._schedules]
        pending = [s for s in rows if s.get("status") == "pending"]
        return {
            "ok": True,
            "count": len(rows),
            "pending_count": len(pending),
            "has_pending": len(pending) > 0,
            "schedules": sorted(rows, key=lambda s: s.get("execute_at", ""), reverse=True),
        }

    def _run_timeout_close(self, schedule: dict[str, Any]) -> None:
        timeout = int(schedule.get("timeout_seconds") or 0)
        if timeout <= 0:
            return
        time.sleep(timeout)
        sched = self._get_by_id(schedule["id"])
        if not sched or sched.get("status") not in ("executed", "expired"):
            return
        payload = sched.get("payload") or {}
        symbol = payload.get("symbol")
        magic = int(payload.get("magic", DEFAULT_MAGIC))
        ok, msg = ensure_mt5(MT5_PATH or None)
        if not ok:
            self._update(schedule["id"], error=f"timeout close: mt5 offline ({msg})")
            self._save_state()
            return
        _, sym = resolve_symbol(str(symbol))
        if not sym:
            self._update(schedule["id"], error="timeout close: symbol not found")
            self._save_state()
            return
        close_result = close_basket(sym, magic, comment=f"Schedule timeout {schedule['id'][:8]}")
        self._update(
            schedule["id"],
            status="expired",
            expired_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            timeout_result=close_result,
        )
        self._save_state()
        send_telegram_message(format_schedule_expired_telegram(sched, close_result))
        print(f"[Schedule] timeout close {sched.get('kind')} {sym} magic={magic}")

    def _execute_schedule(self, schedule: dict[str, Any]) -> None:
        schedule_id = schedule["id"]

        ok_mt5, msg = ensure_mt5(MT5_PATH or None)
        if not ok_mt5:
            self._update(schedule_id, status="failed", error=f"mt5 not connected: {msg}")
            self._save_state()
            send_telegram_message(format_schedule_done_telegram(self._get_by_id(schedule_id) or schedule, False))
            return

        payload = dict(schedule.get("payload") or {})
        kind = schedule.get("kind")
        result: dict[str, Any]
        try:
            if kind == "grid":
                result = _execute_place_grid(payload)
            elif kind == "trade":
                result, status_code = _execute_place_order(payload)
                if status_code != 200:
                    result = {"ok": False, **result}
            else:
                result = {"ok": False, "error": f"unknown kind: {kind}"}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}

        success = bool(result.get("ok"))
        self._update(
            schedule_id,
            status="executed" if success else "failed",
            executed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            result=result,
            error=None if success else str(result.get("error") or "execution failed"),
        )
        self._save_state()

        done_sched = self._get_by_id(schedule_id) or schedule
        send_telegram_message(format_schedule_done_telegram(done_sched, success))
        print(f"[Schedule] executed {kind} {schedule_id[:8]} ok={success}")

        if success and int(schedule.get("timeout_seconds") or 0) > 0:
            threading.Thread(
                target=self._run_timeout_close,
                args=(done_sched,),
                daemon=True,
                name=f"schedule-timeout-{schedule_id[:8]}",
            ).start()

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        due: list[dict[str, Any]] = []
        with self._lock:
            for s in self._schedules:
                if s.get("status") != "pending":
                    continue
                if self._parse_execute_at(s) <= now:
                    s["status"] = "running"
                    due.append(dict(s))
        if due:
            self._save_state()
        for s in due:
            threading.Thread(
                target=self._execute_schedule,
                args=(s,),
                daemon=True,
                name=f"schedule-exec-{s['id'][:8]}",
            ).start()


schedule_mgr = ScheduleManager()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    ok, msg = ensure_mt5(MT5_PATH or None)
    account = mt5.account_info() if ok else None
    return jsonify({
        "ok": ok,
        "service": "mt5-vps-trade-api",
        "version": API_VERSION,
        "mt5": msg,
        "account": account.login if account else None,
        "server": account.server if account else None,
        "trail_jobs": len(trail_mgr.status()),
        "suggestion_watch_jobs": len(suggestion_watch.status()),
        "telegram_alerts_active": telegram_alerts.status().get("active", False),
        "autotrade_active": auto_trade.status().get("active", False),
        "news_alerts_active": news_alerts.status().get("active", False),
        "schedule_pending": schedule_mgr.status().get("pending_count", 0),
    })


@app.route("/version", methods=["GET"])
def get_version():
    """Lightweight version check — no API key, no MT5 required."""
    return jsonify({
        "ok": True,
        "service": "mt5-vps-trade-api",
        "version": API_VERSION,
        "file": "server.py",
    })


@app.route("/getUpcomingNews", methods=["GET"])
@require_api_key
def get_upcoming_news():
    """Upcoming high-impact (red folder) macro events from Forex Factory feed."""
    try:
        hours = int(request.args.get("hours", 72))
    except (TypeError, ValueError):
        hours = 72
    hours = min(168, max(1, hours))

    impact = (request.args.get("impact", "High") or "High").strip()
    cur = (request.args.get("currency", "") or "").strip()
    currencies = [c.strip().upper() for c in cur.split(",") if c.strip()] or None

    try:
        payload = build_upcoming_news(hours_ahead=hours, impact=impact, currencies=currencies)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    if not payload.get("events") and not _news_cache.get("raw"):
        return jsonify({
            "ok": False,
            "error": "calendar feed unavailable",
            "source": "forex_factory",
        }), 502
    return jsonify(payload)


@app.route("/newsAlerts/status", methods=["GET"])
@require_api_key
def news_alerts_status():
    return jsonify(news_alerts.status())


@app.route("/newsAlerts/start", methods=["POST"])
@require_api_key
def news_alerts_start():
    """
    Start Telegram alerts N minutes before high-impact news.
    Body: { "currency_filter": "USD" | "ALL", "hours_ahead": 72 }
    """
    data = json_body()
    currency_filter = str(data.get("currency_filter", "USD")).strip().upper()
    hours_ahead = int(data.get("hours_ahead", 72))
    result = news_alerts.start(currency_filter=currency_filter, hours_ahead=hours_ahead)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/newsAlerts/stop", methods=["POST"])
@require_api_key
def news_alerts_stop():
    result = news_alerts.stop()
    return jsonify(result)


@app.route("/getAccountHealth", methods=["GET"])
@require_api_key
def get_account_health():
    """Full account health: balance, equity, margin, drawdown, floating P/L, today's closed P/L."""
    payload = build_account_health()
    status = 200 if payload.get("ok") else 503
    return jsonify(payload), status


@app.route("/getHistory", methods=["GET"])
@require_api_key
@require_mt5
def get_history():
    """
    Recent deals / closed trades from MT5 account history.
    ?start=2026-09-01&end=2026-10-03
    ?days=30
    &symbol=XAUUSD&magic=78001&closed_only=true&limit=500
    Dates: YYYY-MM-DD, DD.MM.YYYY, or ISO datetime. end date includes full day.
    """
    start_raw = request.args.get("start", "").strip() or None
    end_raw = request.args.get("end", "").strip() or None
    days_raw = request.args.get("days", "").strip() or None
    symbol = request.args.get("symbol", "").strip() or None
    magic_raw = request.args.get("magic", "").strip()
    closed_only = request.args.get("closed_only", "true").lower() in ("1", "true", "yes")
    try:
        limit = int(request.args.get("limit", 5000))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "limit must be an integer"}), 400

    magic = None
    if magic_raw:
        try:
            magic = int(magic_raw)
        except ValueError:
            return jsonify({"ok": False, "error": "magic must be an integer"}), 400

    from_dt, to_dt, err = resolve_history_range(start_raw, end_raw, days_raw)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    result = fetch_history_deals(
        from_dt,
        to_dt,
        symbol=symbol,
        magic=magic,
        closed_only=closed_only,
        limit=limit,
    )
    if symbol and result.get("ok"):
        _, sym = resolve_symbol(symbol)
        result["symbol"] = sym
    if magic is not None:
        result["magic"] = magic
    result["closed_only"] = closed_only
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/getPrice", methods=["GET"])
@require_api_key
@require_mt5
def get_price():
    """Live bid/ask for a symbol. ?symbol=XAUUSD"""
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol query param required"}), 400

    info, sym = resolve_symbol(symbol)
    if info is None:
        return jsonify({"ok": False, "error": f"symbol not found: {symbol}"}), 404

    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        return jsonify({"ok": False, "error": "no quote", **last_error()}), 400

    pt = info.point or 0.00001
    spread_pts = round((tick.ask - tick.bid) / pt, 1) if pt > 0 else 0

    return jsonify({
        "ok": True,
        "symbol": sym,
        "bid": tick.bid,
        "ask": tick.ask,
        "last": tick.last,
        "spread_points": spread_pts,
        "spread": round(tick.ask - tick.bid, info.digits),
        "time": datetime.utcfromtimestamp(tick.time).isoformat() + "Z",
        "digits": info.digits,
        "point": info.point,
    })


@app.route("/getCandles", methods=["GET"])
@require_api_key
@require_mt5
def get_candles():
    """
    OHLCV candles from MT5.
    ?symbol=XAUUSD&timeframe=M5&count=100
    count max 5000
    """
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol query param required"}), 400

    tf_str = request.args.get("timeframe", "M5").upper()
    timeframe = TIMEFRAMES.get(tf_str)
    if timeframe is None:
        return jsonify({"ok": False, "error": f"invalid timeframe: {tf_str}", "allowed": list(TIMEFRAMES.keys())}), 400

    count = int(request.args.get("count", 100))
    count = max(1, min(count, 5000))

    info, sym = resolve_symbol(symbol)
    if info is None:
        return jsonify({"ok": False, "error": f"symbol not found: {symbol}"}), 404

    rates = mt5.copy_rates_from_pos(sym, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        return jsonify({"ok": False, "error": "no candle data", **last_error()}), 400

    candles = []
    for r in rates:
        candles.append({
            "time": datetime.utcfromtimestamp(int(r["time"])).isoformat() + "Z",
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
            "spread": int(r["spread"]) if "spread" in r.dtype.names else 0,
            "real_volume": int(r["real_volume"]) if "real_volume" in r.dtype.names else 0,
        })

    return jsonify({
        "ok": True,
        "symbol": sym,
        "timeframe": tf_str,
        "count": len(candles),
        "first": candles[0]["time"] if candles else None,
        "last": candles[-1]["time"] if candles else None,
        "last_close": candles[-1]["close"] if candles else None,
        "candles": candles,
    })


# ---------------------------------------------------------------------------
# Chart analysis — trends, RSI, order blocks
# ---------------------------------------------------------------------------
def calc_ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def calc_rsi(closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi: list[float] = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss else 100.0
        rsi.append(100 - (100 / (1 + rs)))
    return rsi


def calc_rsi_aligned(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI aligned to each close index (None before warm-up)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    rs = avg_gain / avg_loss if avg_loss else 100.0
    out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss else 100.0
        out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _bar_is_swing_low(bars: list[dict[str, Any]], i: int, left: int, right: int) -> bool:
    lo = bars[i]["low"]
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if j < 0 or j >= len(bars):
            return False
        if bars[j]["low"] <= lo:
            return False
    return True


def _bar_is_swing_high(bars: list[dict[str, Any]], i: int, left: int, right: int) -> bool:
    hi = bars[i]["high"]
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if j < 0 or j >= len(bars):
            return False
        if bars[j]["high"] >= hi:
            return False
    return True


def _rsi_is_pivot_low(rsi_aligned: list[float | None], i: int, left: int, right: int) -> bool:
    """ta.pivotlow(osc, left, right) on RSI series."""
    ri = rsi_aligned[i]
    if ri is None:
        return False
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        rj = rsi_aligned[j]
        if rj is None:
            return False
        if rj <= ri:
            return False
    return True


def _rsi_is_pivot_high(rsi_aligned: list[float | None], i: int, left: int, right: int) -> bool:
    """ta.pivothigh(osc, left, right) on RSI series."""
    ri = rsi_aligned[i]
    if ri is None:
        return False
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        rj = rsi_aligned[j]
        if rj is None:
            return False
        if rj >= ri:
            return False
    return True


def find_rsi_divergences(
    bars: list[dict[str, Any]],
    rsi_aligned: list[float | None],
    *,
    rsi_period: int = 14,
    pivot_left: int = 5,
    pivot_right: int = 5,
    range_lower: int = 5,
    range_upper: int = 60,
    plot_bull: bool = True,
    plot_hidden_bull: bool = False,
    plot_bear: bool = True,
    plot_hidden_bear: bool = False,
    max_results: int = 12,
) -> list[dict[str, Any]]:
    """
    TradingView RSI Divergence Indicator (v6) logic:
      - Pivots on RSI (ta.pivotlow / ta.pivothigh), not price
      - Regular bull: price lower low + RSI higher low
      - Hidden bull: price higher low + RSI lower low
      - Regular bear: price higher high + RSI lower high
      - Hidden bear: price lower high + RSI higher high
      - Pivot spacing: range_lower..range_upper bars between confirmations
    """
    divergences: list[dict[str, Any]] = []
    lb_l, lb_r = pivot_left, pivot_right
    piv_low: list[tuple[int, float, float, int]] = []   # center, low, rsi, confirm_k
    piv_high: list[tuple[int, float, float, int]] = []  # center, high, rsi, confirm_k
    start = max(lb_l, rsi_period)

    def point(idx: int, price: float, rsi_val: float) -> dict[str, Any]:
        t = datetime.utcfromtimestamp(bars[idx]["time"]).isoformat() + "Z"
        return {"time": t, "price": round(price, 5), "rsi": round(rsi_val, 2)}

    def append_div(
        direction: str,
        kind: str,
        label: str,
        i1: int,
        p1: float,
        r1: float,
        i2: int,
        p2: float,
        r2: float,
        confirm_k: int,
    ) -> None:
        divergences.append({
            "type": direction,
            "kind": kind,
            "label": label,
            "signal_time": datetime.utcfromtimestamp(bars[confirm_k]["time"]).isoformat() + "Z",
            "signal_index": confirm_k,
            "pivot_index": i2,
            "price": {"p1": point(i1, p1, r1), "p2": point(i2, p2, r2)},
            "rsi": {"p1": point(i1, p1, r1), "p2": point(i2, p2, r2)},
        })

    for k in range(start + lb_r, len(bars) - 1):
        i = k - lb_r
        if i < lb_l or i >= len(bars) - lb_r:
            continue
        ri = rsi_aligned[i]
        if ri is None:
            continue

        # RSI pivot low confirmed (plFound)
        if _rsi_is_pivot_low(rsi_aligned, i, lb_l, lb_r):
            osc_curr = float(ri)
            price_low_curr = float(bars[i]["low"])
            if piv_low:
                i_prev, low_prev, osc_prev, k_prev = piv_low[-1]
                bars_since = k - k_prev
                in_range = range_lower <= bars_since <= range_upper
                if in_range:
                    # Regular bullish — price LL, osc HL
                    if plot_bull and price_low_curr < low_prev and osc_curr > osc_prev:
                        append_div("bullish", "regular", "Bull", i_prev, low_prev, osc_prev, i, price_low_curr, osc_curr, k)
                    # Hidden bullish — price HL, osc LL
                    if plot_hidden_bull and price_low_curr > low_prev and osc_curr < osc_prev:
                        append_div("bullish", "hidden", "H Bull", i_prev, low_prev, osc_prev, i, price_low_curr, osc_curr, k)
            piv_low.append((i, price_low_curr, osc_curr, k))

        # RSI pivot high confirmed (phFound)
        if _rsi_is_pivot_high(rsi_aligned, i, lb_l, lb_r):
            osc_curr = float(ri)
            price_high_curr = float(bars[i]["high"])
            if piv_high:
                i_prev, high_prev, osc_prev, k_prev = piv_high[-1]
                bars_since = k - k_prev
                in_range = range_lower <= bars_since <= range_upper
                if in_range:
                    # Regular bearish — price HH, osc LH
                    if plot_bear and price_high_curr > high_prev and osc_curr < osc_prev:
                        append_div("bearish", "regular", "Bear", i_prev, high_prev, osc_prev, i, price_high_curr, osc_curr, k)
                    # Hidden bearish — price LH, osc HH
                    if plot_hidden_bear and price_high_curr < high_prev and osc_curr > osc_prev:
                        append_div("bearish", "hidden", "H Bear", i_prev, high_prev, osc_prev, i, price_high_curr, osc_curr, k)
            piv_high.append((i, price_high_curr, osc_curr, k))

    divergences.sort(key=lambda d: d["signal_index"], reverse=True)
    return divergences[:max_results]


def _highestbars_offset(highs: list[float], k: int, length: int) -> int:
    """TradingView ta.highestbars — negative offset from k to highest in window."""
    start = max(0, k - length + 1)
    window = highs[start: k + 1]
    if not window:
        return 0
    abs_idx = start + window.index(max(window))
    return abs_idx - k


def _lowestbars_offset(lows: list[float], k: int, length: int) -> int:
    start = max(0, k - length + 1)
    window = lows[start: k + 1]
    if not window:
        return 0
    abs_idx = start + window.index(min(window))
    return abs_idx - k


def _structure_highest_bar(highs: list[float], k: int, lookback: int = 10) -> int:
    length = lookback if k > lookback else k + 1
    max_bar = _highestbars_offset(highs, k, length)
    idx = 0
    for i in range(lookback):
        o1, o2, o0 = k - (i + 1), k - (i + 2), k - i
        if o2 < 0:
            break
        if highs[o1] > highs[o2] and highs[o0] <= highs[o1] and (-(i + 1)) >= max_bar:
            idx = -(i + 1)
    return idx if idx != 0 else max_bar


def _structure_lowest_bar(lows: list[float], k: int, lookback: int = 10) -> int:
    length = lookback if k > lookback else k + 1
    min_bar = _lowestbars_offset(lows, k, length)
    idx = 0
    for i in range(lookback):
        o1, o2, o0 = k - (i + 1), k - (i + 2), k - i
        if o2 < 0:
            break
        if lows[o1] < lows[o2] and lows[o0] >= lows[o1] and (-(i + 1)) >= min_bar:
            idx = -(i + 1)
    return idx if idx != 0 else min_bar


def _smc_ts(bars: list[dict[str, Any]], idx: int) -> str:
    return datetime.utcfromtimestamp(bars[idx]["time"]).isoformat() + "Z"


def calc_atr_series(bars: list[dict[str, Any]], period: int = 200) -> list[float]:
    n = len(bars)
    if n < 2:
        return [0.0] * max(n, 1)
    trs: list[float] = []
    for i in range(n):
        if i == 0:
            trs.append(bars[i]["high"] - bars[i]["low"])
        else:
            pc = bars[i - 1]["close"]
            trs.append(max(
                bars[i]["high"] - bars[i]["low"],
                abs(bars[i]["high"] - pc),
                abs(bars[i]["low"] - pc),
            ))
    atr = [0.0] * n
    if n < period:
        avg = sum(trs) / len(trs) if trs else 0.0
        return [avg] * n
    atr[period - 1] = sum(trs[:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period
    seed = atr[period - 1]
    for i in range(period - 1):
        atr[i] = seed
    return atr


def _infer_bar_period_seconds(bars: list[dict[str, Any]]) -> int:
    if len(bars) < 3:
        return 300
    deltas = sorted(bars[i]["time"] - bars[i - 1]["time"] for i in range(1, min(30, len(bars))))
    return max(60, int(deltas[len(deltas) // 2]))


def _bb_timeframe_changed(bars: list[dict[str, Any]], k: int, period_sec: int) -> bool:
    if k < 1:
        return False
    return (bars[k]["time"] // period_sec) != (bars[k - 1]["time"] // period_sec)


def _bb_find_idx(
    bars: list[dict[str, Any]],
    k: int,
    ms_loc: int,
    *,
    use_max: bool,
    use_ob: bool = False,
    sweep: bool = False,
    xloc: int | None = None,
) -> int:
    anchor = xloc if sweep and xloc is not None else ms_loc
    anchor = max(0, min(anchor, k))
    hi = [b["high"] for b in bars]
    lo = [b["low"] for b in bars]
    if use_max:
        best_idx = anchor
        best_val = hi[anchor]
        for bi in range(anchor + 1, k + 1):
            if hi[bi] >= best_val:
                best_val = hi[bi]
                best_idx = bi
        if use_ob and best_idx + 1 <= k and hi[best_idx + 1] > hi[best_idx]:
            best_idx += 1
        return best_idx
    best_idx = anchor
    best_val = lo[anchor]
    for bi in range(anchor + 1, k + 1):
        if lo[bi] <= best_val:
            best_val = lo[bi]
            best_idx = bi
    if use_ob and best_idx + 1 <= k and lo[best_idx + 1] < lo[best_idx]:
        best_idx += 1
    return best_idx


def _bb_ob_top_bottom(
    bars: list[dict[str, Any]],
    atr: list[float],
    k: int,
    ms_loc: int,
    *,
    ob_mode: str = "Length",
    ob_len: int = 5,
) -> tuple[float, float]:
    id_bull = _bb_find_idx(bars, k, ms_loc, use_max=False, use_ob=True)
    id_bear = _bb_find_idx(bars, k, ms_loc, use_max=True, use_ob=True)
    scale = (atr[id_bull] / (5 / ob_len)) if ob_len else atr[id_bull]
    scale_bear = (atr[id_bear] / (5 / ob_len)) if ob_len else atr[id_bear]
    hi, lo = bars[id_bull]["high"], bars[id_bull]["low"]
    hi_b, lo_b = bars[id_bear]["high"], bars[id_bear]["low"]
    if ob_mode == "Length":
        top_p = max(hi, lo + scale)
        btm_p = min(lo_b, hi_b - scale_bear)
    else:
        top_p, btm_p = hi, lo_b
    return top_p, btm_p


def _bb_mitigate_fvg(box: dict[str, Any], bar: dict[str, Any], src: str = "Close") -> bool:
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    top, btm = box["top"], box["bottom"]
    mid = (top + btm) / 2
    if box["type"] == "bullish":
        if src == "Close":
            return min(c, o) < btm
        if src == "Wick":
            return l < btm
        return l < mid
    if src == "Close":
        return max(c, o) > top
    if src == "Wick":
        return h > top
    return h > mid


def _bb_overlap_fvg_remove(fvgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(fvgs) < 2:
        return fvgs
    out = list(fvgs)
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i in range(len(out) - 1, 0, -1):
            a, b = out[i], out[0]
            if a["bottom"] > b["bottom"] and a["bottom"] < b["top"]:
                out.pop(i); changed = True
            elif a["top"] < b["top"] and a["bottom"] > b["bottom"]:
                out.pop(i); changed = True
            elif a["top"] > b["top"] and a["bottom"] < b["bottom"]:
                out.pop(i); changed = True
            elif a["top"] < b["top"] and a["top"] > b["bottom"]:
                out.pop(i); changed = True
    return out


def analyze_bigbeluga_smc(
    bars: list[dict[str, Any]],
    *,
    mslen: int = 5,
    swing_limit: int = 100,
    ob_last: int = 5,
    fvg_num: int = 5,
    fvg_thresh: float = 0.0,
    buildsweep: bool = True,
    ob_mode: str = "Length",
    ob_len: int = 5,
    fvg_miti: str = "Close",
    fvg_overlap: bool = True,
) -> dict[str, Any]:
    """
    BigBeluga « Smart Money Concepts » — swing BOS/CHoCH, volumetric OBs, FVG.
    Ported from TradingView Pine (© BigBeluga); merged alongside LudoGH68 SMC.
    """
    n = len(bars)
    if n < mslen * 2 + 5:
        return {
            "source": "bigbeluga",
            "trend": 0,
            "fvgs": [],
            "structures": [],
            "order_blocks": [],
            "fvg_count": 0,
            "structure_count": 0,
            "ob_count": 0,
        }

    atr = calc_atr_series(bars, 200)
    period_sec = _infer_bar_period_seconds(bars)
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    opens = [b["open"] for b in bars]
    closes = [b["close"] for b in bars]
    vols = [int(b.get("volume") or 0) for b in bars]

    # --- BigBeluga FVG ---
    bl_fvg: list[dict[str, Any]] = []
    br_fvg: list[dict[str, Any]] = []
    pending_up = False
    pending_dn = False

    for k in range(n):
        if k >= 4 and pending_up:
            bl_fvg.insert(0, {
                "type": "bullish",
                "top": lows[k - 1],
                "bottom": highs[k - 3],
                "left_idx": k - 3,
                "right_idx": k - 1,
                "mitigated": False,
                "is_breaker": False,
            })
        if k >= 4 and pending_dn:
            br_fvg.insert(0, {
                "type": "bearish",
                "top": lows[k - 3],
                "bottom": highs[k - 1],
                "left_idx": k - 3,
                "right_idx": k - 1,
                "mitigated": False,
                "is_breaker": False,
            })

        if k >= 3:
            h2 = highs[k - 2]
            l2 = lows[k - 2]
            h1, l1 = highs[k - 1], lows[k - 1]
            c1 = closes[k - 1]
            cc = _bb_timeframe_changed(bars, k, period_sec)
            bl_th = l1 + (atr[k - 1] * fvg_thresh)
            br_th = h1 - (atr[k - 1] * fvg_thresh)
            pending_up = lows[k] > h2 and cc and c1 > bl_th
            pending_dn = l2 > highs[k] and cc and c1 < br_th

        bar = bars[k]
        still_bl: list[dict[str, Any]] = []
        for box in bl_fvg:
            box["right_idx"] = k
            if not box["mitigated"] and _bb_mitigate_fvg(box, bar, fvg_miti):
                box["mitigated"] = True
                box["mitigate_idx"] = k
            still_bl.append(box)
        bl_fvg = still_bl
        still_br: list[dict[str, Any]] = []
        for box in br_fvg:
            box["right_idx"] = k
            if not box["mitigated"] and _bb_mitigate_fvg(box, bar, fvg_miti):
                box["mitigated"] = True
                box["mitigate_idx"] = k
            still_br.append(box)
        br_fvg = still_br

    active_fvgs = (bl_fvg[:fvg_num] if fvg_num else []) + (br_fvg[:fvg_num] if fvg_num else [])
    if fvg_overlap:
        bulls = _bb_overlap_fvg_remove([f for f in active_fvgs if f["type"] == "bullish"])
        bears = _bb_overlap_fvg_remove([f for f in active_fvgs if f["type"] == "bearish"])
        active_fvgs = bulls + bears

    fvgs_out: list[dict[str, Any]] = []
    for box in active_fvgs:
        li = max(0, min(box["left_idx"], n - 1))
        ri = max(0, min(box.get("right_idx", n - 1), n - 1))
        fvgs_out.append({
            "source": "bigbeluga",
            "type": box["type"],
            "top": round(box["top"], 5),
            "bottom": round(box["bottom"], 5),
            "time_start": _smc_ts(bars, li),
            "time_end": _smc_ts(bars, ri),
            "mitigated": bool(box["mitigated"]),
        })

    # --- BigBeluga market structure + volumetric OBs ---
    blob: list[dict[str, Any]] = []
    brob: list[dict[str, Any]] = []
    bldw: list[dict[str, Any]] = []
    brdw: list[dict[str, Any]] = []
    php: list[float] = []
    phn: list[int] = []
    plp: list[float] = []
    pln: list[int] = []

    ms = {
        "start": 0,
        "trend": 0,
        "bos": None,
        "choch": None,
        "main": None,
        "loc": 0,
        "temp": 0,
        "xloc": 0,
    }
    up_tr = highs[0]
    dn_tr = lows[0]

    def add_ob(bull: bool, cords: float, idx: int) -> None:
        bar = bars[idx]
        if bull:
            blob.insert(0, {
                "type": "bullish",
                "top": cords,
                "bottom": bar["low"],
                "avg": (cords + bar["low"]) / 2,
                "left_idx": idx,
                "right_idx": n - 1,
                "mitigated": False,
                "volume": vols[idx],
            })
        else:
            brob.insert(0, {
                "type": "bearish",
                "top": bar["high"],
                "bottom": cords,
                "avg": (bar["high"] + cords) / 2,
                "left_idx": idx,
                "right_idx": n - 1,
                "mitigated": False,
                "volume": vols[idx],
            })

    def add_line(target: list[dict[str, Any]], x1: int, x2: int, y: float, txt: str, kind: str, sweep: bool = False) -> None:
        target.insert(0, {
            "label": txt,
            "kind": kind,
            "level": round(y, 5),
            "time_start": _smc_ts(bars, x1),
            "time_end": _smc_ts(bars, x2),
            "start_index": x1,
            "end_index": x2,
            "sweep": sweep,
            "source": "bigbeluga",
        })

    for k in range(n):
        piv_i = k - mslen
        if piv_i >= mslen and piv_i < n - mslen:
            window_h = highs[piv_i - mslen: piv_i + mslen + 1]
            window_l = lows[piv_i - mslen: piv_i + mslen + 1]
            if highs[piv_i] == max(window_h):
                phn.insert(0, piv_i)
                php.insert(0, highs[piv_i])
            if lows[piv_i] == min(window_l):
                pln.insert(0, piv_i)
                plp.insert(0, lows[piv_i])

        if php and highs[k] > php[0]:
            php.clear()
            phn.clear()
        if plp and lows[k] < plp[0]:
            plp.clear()
            pln.clear()

        cross_up = cross_dn = False
        if highs[k] > up_tr:
            up_tr, dn_tr = highs[k], lows[k]
            cross_up = True
        if lows[k] < dn_tr:
            up_tr, dn_tr = highs[k], lows[k]
            cross_dn = True

        if ms["start"] == 0:
            ms.update({
                "start": 1,
                "bos": highs[k],
                "choch": lows[k],
                "loc": k,
                "temp": k,
                "xloc": k,
            })
            add_line(bldw, k, k, highs[k], "CHoCH", "bullish")
            add_line(brdw, k, k, lows[k], "CHoCH", "bearish")
            continue

        top_p, btm_p = _bb_ob_top_bottom(bars, atr, k, ms["loc"], ob_mode=ob_mode, ob_len=ob_len)
        c, o = closes[k], opens[k]
        c1, o1 = closes[k - 1], opens[k - 1]

        if ms["start"] == 1:
            if ms["choch"] is not None and c <= ms["choch"]:
                id_bull = _bb_find_idx(bars, k, ms["loc"], use_max=False, use_ob=True)
                add_ob(True, top_p, id_bull)
                ms.update({"trend": -1, "start": 2, "choch": ms["bos"], "bos": None,
                           "main": lows[k], "loc": k, "temp": k, "xloc": k})
                if brdw:
                    brdw[0]["end_index"] = k
                    brdw[0]["time_end"] = _smc_ts(bars, k)
            elif ms["bos"] is not None and c >= ms["bos"]:
                id_bear = _bb_find_idx(bars, k, ms["loc"], use_max=True, use_ob=True)
                add_ob(False, btm_p, id_bear)
                ms.update({"trend": 1, "start": 2, "bos": None,
                           "main": highs[k], "loc": k, "temp": k, "xloc": k})
                if bldw:
                    bldw[0]["end_index"] = k
                    bldw[0]["time_end"] = _smc_ts(bars, k)
            continue

        if ms["start"] != 2:
            continue

        if ms["trend"] == -1:
            if ms["main"] is None or lows[k] <= ms["main"]:
                ms["main"] = lows[k]
                ms["temp"] = k

            if ms["bos"] is None and cross_up and c > o and c1 > o1:
                ms["bos"] = ms["main"]
                ms["loc"] = ms["temp"]
                ms["xloc"] = ms["loc"]
                add_line(brdw, ms["loc"], k, lows[ms["loc"]], "BOS", "bearish")

            if ms["bos"] is not None and c <= ms["bos"]:
                add_line(brdw, ms.get("loc", k), k, ms["bos"], "BOS", "bearish")
                id_bear = _bb_find_idx(bars, k, ms["loc"], use_max=True, use_ob=False)
                add_ob(False, btm_p, id_bear)
                ms["bos"] = None
                hi_idx = _bb_find_idx(bars, k, k, use_max=True, use_ob=False)
                ms["choch"] = highs[hi_idx]
                ms["loc"] = hi_idx
                add_line(bldw, hi_idx, k, highs[hi_idx], "CHoCH", "bullish")

            elif ms["choch"] is not None and c >= ms["choch"]:
                add_line(bldw, ms.get("xloc", k), k, ms["choch"], "CHoCH", "bullish")
                id_bull = _bb_find_idx(bars, k, ms["loc"], use_max=False, use_ob=False)
                add_ob(True, top_p, id_bull)
                ms.update({"trend": 1, "bos": None, "main": highs[k], "loc": k, "temp": k, "xloc": k})

            if brdw:
                brdw[0]["end_index"] = k
                brdw[0]["time_end"] = _smc_ts(bars, k)
            if bldw:
                bldw[0]["end_index"] = k
                bldw[0]["time_end"] = _smc_ts(bars, k)

        elif ms["trend"] == 1:
            if ms["main"] is None or highs[k] >= ms["main"]:
                ms["main"] = highs[k]
                ms["temp"] = k

            if ms["bos"] is None and cross_dn and c < o and c1 < o1:
                ms["bos"] = ms["main"]
                ms["loc"] = ms["temp"]
                ms["xloc"] = ms["loc"]
                add_line(bldw, ms["loc"], k, highs[ms["loc"]], "BOS", "bullish")

            if ms["bos"] is not None and c >= ms["bos"]:
                add_line(bldw, ms.get("loc", k), k, ms["bos"], "BOS", "bullish")
                id_bull = _bb_find_idx(bars, k, ms["loc"], use_max=False, use_ob=False)
                add_ob(True, top_p, id_bull)
                ms["bos"] = None
                lo_idx = _bb_find_idx(bars, k, k, use_max=False, use_ob=False)
                ms["choch"] = lows[lo_idx]
                ms["loc"] = lo_idx
                add_line(brdw, lo_idx, k, lows[lo_idx], "CHoCH", "bearish")

            elif ms["choch"] is not None and c <= ms["choch"]:
                add_line(brdw, ms.get("xloc", k), k, ms["choch"], "CHoCH", "bearish")
                id_bear = _bb_find_idx(bars, k, ms["loc"], use_max=True, use_ob=False)
                add_ob(False, btm_p, id_bear)
                ms.update({"trend": -1, "bos": None, "main": lows[k], "loc": k, "temp": k, "xloc": k})

            if bldw:
                bldw[0]["end_index"] = k
                bldw[0]["time_end"] = _smc_ts(bars, k)
            if brdw:
                brdw[0]["end_index"] = k
                brdw[0]["time_end"] = _smc_ts(bars, k)

    # Mitigate OBs (Close method)
    for block in blob + brob:
        for k in range(block["left_idx"] + 1, n):
            bar = bars[k]
            block["right_idx"] = k
            if block["type"] == "bullish":
                if min(bar["close"], bar["open"]) < block["bottom"]:
                    block["mitigated"] = True
                    break
            elif max(bar["close"], bar["open"]) > block["top"]:
                block["mitigated"] = True
                break

    structures = (bldw[:swing_limit] + brdw[:swing_limit])[:swing_limit]
    obs_raw = (blob[:ob_last] + brob[:ob_last])
    total_vol = sum(o.get("volume", 0) for o in obs_raw) or 1
    order_blocks: list[dict[str, Any]] = []
    for ob in obs_raw[:ob_last]:
        li = ob["left_idx"]
        ri = min(ob.get("right_idx", n - 1), n - 1)
        order_blocks.append({
            "source": "bigbeluga",
            "type": ob["type"],
            "top": round(ob["top"], 5),
            "bottom": round(ob["bottom"], 5),
            "avg": round(ob["avg"], 5),
            "time_start": _smc_ts(bars, li),
            "time_end": _smc_ts(bars, ri),
            "mitigated": bool(ob["mitigated"]),
            "volume": ob.get("volume", 0),
            "volume_pct": round(100.0 * ob.get("volume", 0) / total_vol, 1),
        })

    return {
        "source": "bigbeluga",
        "trend": ms.get("trend", 0),
        "fvgs": fvgs_out,
        "structures": structures,
        "order_blocks": order_blocks,
        "fvg_count": len(fvgs_out),
        "structure_count": len(structures),
        "ob_count": len(order_blocks),
    }



def analyze_smc_structures_fvg(
    bars: list[dict[str, Any]],
    *,
    body_break: bool = True,
    fvg_history: int = 5,
    struct_history: int = 10,
    lookback: int = 10,
    fib_levels: tuple[float, ...] = (0.786, 0.705, 0.618, 0.5, 0.382),
) -> dict[str, Any]:
    """
    LudoGH68 « SMC Structures and FVG » (TradingView) — FVG + BOS/CHoCH + structure fibs.
    """
    n = len(bars)
    if n < 5:
        return {"fvgs": [], "structures": [], "current": None}

    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]

    def low_px(k: int) -> float:
        return closes[k] if body_break else lows[k]

    def high_px(k: int) -> float:
        return closes[k] if body_break else highs[k]

    # Active FVG zones (mutable during scan)
    fvg_boxes: list[dict[str, Any]] = []
    fvgs_out: list[dict[str, Any]] = []

    structure_high = highs[0]
    structure_low = lows[0]
    structure_high_start = 0
    structure_low_start = 0
    structure_direction = 0  # 0=init, 1=bearish leg, 2=bullish leg
    structures: list[dict[str, Any]] = []

    for k in range(n):
        # --- FVG detection (Pine: high[3] < low[1] / low[3] > high[1]) ---
        if k >= 3:
            if highs[k - 3] < lows[k - 1]:
                fvg_boxes.append({
                    "type": "bullish",
                    "left_idx": k - 2,
                    "right_idx": k - 1,
                    "top": lows[k - 1],
                    "bottom": highs[k - 3],
                    "mitigated": False,
                })
            if lows[k - 3] > highs[k - 1]:
                fvg_boxes.append({
                    "type": "bearish",
                    "left_idx": k - 2,
                    "right_idx": k - 1,
                    "top": lows[k - 3],
                    "bottom": highs[k - 1],
                    "mitigated": False,
                })
            while len(fvg_boxes) > fvg_history + 1:
                fvg_boxes.pop(0)

        # --- FVG mitigation + extend right edge ---
        still_active: list[dict[str, Any]] = []
        for box in fvg_boxes:
            box["right_idx"] = k
            if box["type"] == "bullish":
                if lows[k] <= box["bottom"]:
                    continue
                if lows[k] < box["top"]:
                    box["mitigated"] = True
            else:
                if highs[k] >= box["top"]:
                    continue
                if highs[k] > box["bottom"]:
                    box["mitigated"] = True
            still_active.append(box)
        fvg_boxes = still_active

        if k < 3:
            continue

        # --- Structure breaks ---
        lb = low_px(k)
        hb = high_px(k)
        lb1, lb2, lb3 = low_px(k - 1), low_px(k - 2), low_px(k - 3)
        hb1, hb2, hb3 = high_px(k - 1), high_px(k - 2), high_px(k - 3)

        low_broken = (
            lb < structure_low
            and lb1 >= structure_low
            and lb2 >= structure_low
            and lb3 >= structure_low
            and (k - 1) > structure_low_start
            and (k - 2) > structure_low_start
            and (k - 3) > structure_low_start
        ) or (structure_direction == 2 and lb < structure_low)

        high_broken = (
            hb > structure_high
            and hb1 <= structure_high
            and hb2 <= structure_high
            and hb3 <= structure_high
            and (k - 1) > structure_high_start
            and (k - 2) > structure_high_start
            and (k - 3) > structure_high_start
        ) or (structure_direction == 1 and hb > structure_high)

        if low_broken:
            label = "BOS" if structure_direction == 1 else "CHoCH"
            structures.append({
                "source": "ludo",
                "label": label,
                "kind": "bearish",
                "level": round(structure_low, 5),
                "time_start": _smc_ts(bars, structure_low_start),
                "time_end": _smc_ts(bars, k),
                "start_index": structure_low_start,
                "end_index": k,
            })
            while len(structures) > struct_history:
                structures.pop(0)

            structure_direction = 1
            hi_off = _structure_highest_bar(highs, k, lookback)
            structure_high_start = k + hi_off
            structure_high_start = max(0, min(structure_high_start, k))
            structure_low_start = k
            structure_high = highs[structure_high_start]
            structure_low = lows[k]

        elif high_broken:
            label = "BOS" if structure_direction == 2 else "CHoCH"
            structures.append({
                "source": "ludo",
                "label": label,
                "kind": "bullish",
                "level": round(structure_high, 5),
                "time_start": _smc_ts(bars, structure_high_start),
                "time_end": _smc_ts(bars, k),
                "start_index": structure_high_start,
                "end_index": k,
            })
            while len(structures) > struct_history:
                structures.pop(0)

            structure_direction = 2
            lo_off = _structure_lowest_bar(lows, k, lookback)
            structure_high_start = k
            structure_low_start = k + lo_off
            structure_low_start = max(0, min(structure_low_start, k))
            structure_high = highs[k]
            structure_low = lows[structure_low_start]

        else:
            if highs[k] > structure_high and structure_direction in (0, 2):
                skip = body_break and (
                    (k - 1) > structure_high_start
                    and (k - 2) > structure_high_start
                    and (k - 3) > structure_high_start
                )
                if not body_break or not skip:
                    structure_high = highs[k]
                    structure_high_start = k
            elif lows[k] < structure_low and structure_direction in (0, 1):
                skip = body_break and (
                    (k - 1) > structure_low_start
                    and (k - 2) > structure_low_start
                    and (k - 3) > structure_low_start
                )
                if not body_break or not skip:
                    structure_low = lows[k]
                    structure_low_start = k

    # Serialize FVGs
    for box in fvg_boxes[-fvg_history:]:
        fvgs_out.append({
            "source": "ludo",
            "type": box["type"],
            "top": round(box["top"], 5),
            "bottom": round(box["bottom"], 5),
            "time_start": _smc_ts(bars, box["left_idx"]),
            "time_end": _smc_ts(bars, box["right_idx"]),
            "mitigated": bool(box["mitigated"]),
        })

    structure_range = abs(structure_high - structure_low)
    fibs: list[dict[str, Any]] = []
    for fib_val in fib_levels:
        if structure_direction == 1:
            price = structure_high - (structure_range - structure_range * fib_val)
            start_idx = structure_high_start
        else:
            price = structure_low + (structure_range - structure_range * fib_val)
            start_idx = structure_low_start
        fibs.append({
            "value": fib_val,
            "price": round(price, 5),
            "time_start": _smc_ts(bars, start_idx),
        })

    last_struct = structures[-1] if structures else None
    return {
        "fvgs": fvgs_out,
        "structures": structures,
        "current": {
            "direction": structure_direction,
            "structure_high": round(structure_high, 5),
            "structure_low": round(structure_low, 5),
            "high_start": _smc_ts(bars, structure_high_start),
            "low_start": _smc_ts(bars, structure_low_start),
            "high_end": _smc_ts(bars, n - 1),
            "low_end": _smc_ts(bars, n - 1),
            "fibonacci": fibs,
        },
        "last_break": last_struct,
        "fvg_count": len(fvgs_out),
        "structure_count": len(structures),
    }


def analyze_smc_merged(bars: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """LudoGH68 SMC + BigBeluga SMC combined for chart overlays."""
    ludo = analyze_smc_structures_fvg(bars, **kwargs)
    beluga = analyze_bigbeluga_smc(bars)
    inducements = detect_inducements(bars, beluga=beluga)
    out = {**ludo, "beluga": beluga, "inducements": inducements, "sources": ["ludo", "bigbeluga"]}
    out["fvg_count"] = int(ludo.get("fvg_count", 0)) + int(beluga.get("fvg_count", 0))
    out["structure_count"] = int(ludo.get("structure_count", 0)) + int(beluga.get("structure_count", 0))
    out["ob_count"] = int(beluga.get("ob_count", 0))
    out["inducement_count"] = len(inducements)
    return out


def rates_to_bars(rates) -> list[dict[str, Any]]:
    bars = []
    for r in rates:
        bars.append({
            "time": int(r["time"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["tick_volume"]),
        })
    return bars


def calculate_trend(bars: list[dict[str, Any]]) -> str:
    if len(bars) < 5:
        return "NEUTRAL"
    closes = [b["close"] for b in bars]
    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    if not ema9 or not ema21:
        return "NEUTRAL"
    short = ema9[-1]
    long = ema21[-1]
    if short > long * 1.0003:
        return "UP"
    if short < long * 0.9997:
        return "DOWN"
    change = closes[-1] - closes[-4]
    if change > 0:
        return "UP"
    if change < 0:
        return "DOWN"
    return "NEUTRAL"


def find_swing_points(bars: list[dict[str, Any]], lookback: int = 3) -> tuple[list, list]:
    swing_highs, swing_lows = [], []
    if len(bars) < lookback * 2 + 1:
        return swing_highs, swing_lows
    for i in range(lookback, len(bars) - lookback):
        h, l = bars[i]["high"], bars[i]["low"]
        if all(bars[i - j]["high"] < h and bars[i + j]["high"] < h for j in range(1, lookback + 1)):
            swing_highs.append({"index": i, "time": bars[i]["time"], "price": h})
        if all(bars[i - j]["low"] > l and bars[i + j]["low"] > l for j in range(1, lookback + 1)):
            swing_lows.append({"index": i, "time": bars[i]["time"], "price": l})
    return swing_highs, swing_lows


def detect_inducements(
    bars: list[dict[str, Any]],
    *,
    beluga: dict[str, Any] | None = None,
    swing_lookback: int = 6,
    max_events: int = 6,
    scan_bars: int = 180,
    min_swing_age_bars: int = 5,
    min_gap_bars: int = 10,
) -> list[dict[str, Any]]:
    """
    Inducement = liquidity grab through a swing (wick sweep + close back inside).
    Kept strict: significant wick only, one per swing level, spaced apart.
    """
    n = len(bars)
    if n < swing_lookback * 2 + 5:
        return []

    swing_h, swing_l = find_swing_points(bars, swing_lookback)
    atr = calc_atr_series(bars, 14)
    events: list[dict[str, Any]] = []
    seen_times: set[str] = set()
    seen_levels: set[float] = set()
    last_event_bar = -10_000

    def level_key(price: float) -> float:
        return round(price, 1 if price > 10 else 5)

    def push_event(
        *,
        kind: str,
        level: float,
        sweep_price: float,
        bar_idx: int,
        swing_idx: int,
        source: str,
    ) -> None:
        nonlocal last_event_bar
        if bar_idx - last_event_bar < min_gap_bars:
            return
        lk = level_key(level)
        if lk in seen_levels:
            return
        t_iso = _smc_ts(bars, bar_idx)
        if t_iso in seen_times:
            return
        seen_times.add(t_iso)
        seen_levels.add(lk)
        last_event_bar = bar_idx
        events.append({
            "kind": kind,
            "label": "INDUCEMENT",
            "level": round(level, 5),
            "sweep_price": round(sweep_price, 5),
            "time": t_iso,
            "swing_time": _smc_ts(bars, swing_idx),
            "source": source,
        })

    start = max(swing_lookback, n - scan_bars)
    recent_lows = [s for s in swing_l if s["index"] < n - min_swing_age_bars][-6:]
    recent_highs = [s for s in swing_h if s["index"] < n - min_swing_age_bars][-6:]

    for k in range(start, n):
        bar = bars[k]
        bar_rng = bar["high"] - bar["low"]
        min_wick = max((atr[k] if k < len(atr) else 0) * 0.15, bar_rng * 0.35)
        if bar_rng < min_wick:
            continue

        for sl in recent_lows:
            if sl["index"] >= k - min_swing_age_bars:
                continue
            level = sl["price"]
            wick = level - bar["low"]
            if bar["low"] < level and bar["close"] > level and wick >= min_wick:
                push_event(
                    kind="bullish",
                    level=level,
                    sweep_price=bar["low"],
                    bar_idx=k,
                    swing_idx=sl["index"],
                    source="liquidity_sweep",
                )
                break

        for sh in recent_highs:
            if sh["index"] >= k - min_swing_age_bars:
                continue
            level = sh["price"]
            wick = bar["high"] - level
            if bar["high"] > level and bar["close"] < level and wick >= min_wick:
                push_event(
                    kind="bearish",
                    level=level,
                    sweep_price=bar["high"],
                    bar_idx=k,
                    swing_idx=sh["index"],
                    source="liquidity_sweep",
                )
                break

    events.sort(key=lambda e: e.get("time") or "")
    return events[-max_events:]


def find_order_blocks(bars: list[dict[str, Any]], swing_highs: list, swing_lows: list) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for swing in swing_lows[-6:]:
        idx = swing["index"]
        for i in range(idx - 1, max(0, idx - 6), -1):
            if bars[i]["close"] < bars[i]["open"]:
                move = bars[min(idx + 2, len(bars) - 1)]["close"] - bars[i]["low"]
                body = abs(bars[i]["close"] - bars[i]["open"])
                if move > body * 1.5:
                    blocks.append({
                        "type": "BULLISH_OB",
                        "time": bars[i]["time"],
                        "high": bars[i]["high"],
                        "low": bars[i]["low"],
                        "rsi_ob": False,
                    })
                    break
    for swing in swing_highs[-6:]:
        idx = swing["index"]
        for i in range(idx - 1, max(0, idx - 6), -1):
            if bars[i]["close"] > bars[i]["open"]:
                move = bars[i]["high"] - bars[min(idx + 2, len(bars) - 1)]["close"]
                body = abs(bars[i]["close"] - bars[i]["open"])
                if move > body * 1.5:
                    blocks.append({
                        "type": "BEARISH_OB",
                        "time": bars[i]["time"],
                        "high": bars[i]["high"],
                        "low": bars[i]["low"],
                        "rsi_ob": False,
                    })
                    break
    return blocks


def format_volume_k(volume: int | float) -> str:
    """Format tick volume like stream overlays: 5200 → '5.2k'."""
    v = float(volume)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if v >= 1000:
        s = f"{v / 1000:.1f}k"
        return s.replace(".0k", "k")
    return str(int(v))


def enrich_order_block_intensity(bars: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    RSI OB intensity — volume at OB candle (5k, 2.5k) + relative volume (2.5x avg).
    Streams usually label OB strength by tick volume and how extreme RSI was.
    """
    time_to_idx = {b["time"]: i for i, b in enumerate(bars)}
    all_vols = [b["volume"] for b in bars]

    for ob in blocks:
        idx = time_to_idx.get(ob["time"])
        if idx is None:
            continue

        bar = bars[idx]
        vol = int(bar.get("volume") or 0)
        start = max(0, idx - 20)
        window = all_vols[start:idx] or all_vols[max(0, idx - 5): idx + 1]
        avg_vol = sum(window) / len(window) if window else max(vol, 1)
        rel = vol / avg_vol if avg_vol else 1.0

        rsi = float(ob.get("rsi") or 50)
        if ob["type"] == "BULLISH_OB":
            rsi_depth = max(0.0, 35.0 - rsi)
        else:
            rsi_depth = max(0.0, rsi - 65.0)

        vol_score = min(100.0, rel * 25.0)
        rsi_score = min(100.0, rsi_depth * 5.0)
        intensity = round(min(100.0, vol_score * 0.65 + rsi_score * 0.35), 1)

        if intensity >= 70:
            tier = "HIGH"
        elif intensity >= 40:
            tier = "MED"
        else:
            tier = "LOW"

        ob["volume"] = vol
        ob["volume_k"] = format_volume_k(vol)
        ob["volume_avg"] = round(avg_vol)
        ob["volume_rel"] = round(rel, 2)
        ob["volume_rel_label"] = f"{rel:.1f}x".replace(".0x", "x")
        ob["rsi_depth"] = round(rsi_depth, 1)
        ob["intensity"] = intensity
        ob["intensity_tier"] = tier
        ob["intensity_label"] = f"{format_volume_k(vol)} · {ob['volume_rel_label']}"

    blocks.sort(key=lambda x: x.get("intensity", 0), reverse=True)
    return blocks


def enrich_order_block_validity(
    bars: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    current_price: float,
    atr: float,
    max_age_bars: int = 80,
    max_distance_atr: float = 4.0,
) -> list[dict[str, Any]]:
    """
    Classify each OB as tradeable or expired (SMC-style).

    FRESH    — never retested; still valid
    AT_ZONE  — price inside OB now
    TESTED   — one retest, not broken
    MITIGATED — 2+ retests (zone used up)
    BROKEN   — close through OB (bull: close < low, bear: close > high)
    STALE    — too many bars old, or too far without any retest
    """
    time_to_idx = {b["time"]: i for i, b in enumerate(bars)}
    n = len(bars)
    safe_atr = atr if atr > 0 else 1.0

    for ob in blocks:
        idx = time_to_idx.get(ob["time"])
        if idx is None:
            ob.update({
                "status": "UNKNOWN",
                "status_label": "Unknown",
                "is_valid": False,
                "is_tradeable": False,
            })
            continue

        zone_low = float(ob["low"])
        zone_high = float(ob["high"])
        bars_since = n - 1 - idx
        ob["bars_since"] = bars_since

        broken = False
        touches = 0
        for j in range(idx + 1, n):
            bar = bars[j]
            close_p, high_p, low_p = bar["close"], bar["high"], bar["low"]
            if ob["type"] == "BULLISH_OB" and close_p < zone_low:
                broken = True
                break
            if ob["type"] == "BEARISH_OB" and close_p > zone_high:
                broken = True
                break
            if high_p >= zone_low and low_p <= zone_high:
                touches += 1

        if current_price > zone_high:
            dist = current_price - zone_high
        elif current_price < zone_low:
            dist = zone_low - current_price
        else:
            dist = 0.0

        dist_atr = round(dist / safe_atr, 2)
        ob["distance"] = round(dist, 5)
        ob["distance_atr"] = dist_atr
        ob["touch_count"] = touches
        ob["broken"] = broken

        if broken:
            status, label = "BROKEN", "Expired — close through OB"
            is_valid = is_tradeable = False
        elif touches >= 2:
            status, label = "MITIGATED", "Mitigated — tested 2+ times"
            is_valid = is_tradeable = False
        elif dist == 0:
            status, label = "AT_ZONE", "Price at OB now — valid"
            is_valid = is_tradeable = True
        elif bars_since > max_age_bars:
            status, label = "STALE", f"Expired — older than {max_age_bars} bars"
            is_valid = is_tradeable = False
        elif touches == 1:
            status, label = "TESTED", "Tested once — still valid"
            is_valid = is_tradeable = True
        elif dist_atr > max_distance_atr:
            status, label = "STALE", f"Stale — {dist_atr}x ATR away, never tested"
            is_valid = is_tradeable = False
        else:
            status, label = "FRESH", "Fresh — untested OB"
            is_valid = is_tradeable = True

        ob["status"] = status
        ob["status_label"] = label
        ob["is_valid"] = is_valid
        ob["is_tradeable"] = is_tradeable

    return blocks


def mark_rsi_order_blocks(bars: list[dict[str, Any]], blocks: list[dict[str, Any]], period: int = 14) -> list[dict[str, Any]]:
    """Flag OBs where RSI was oversold (bull) or overbought (bear) at formation."""
    closes = [b["close"] for b in bars]
    rsi = calc_rsi(closes, period)
    if not rsi:
        return blocks
    rsi_offset = len(closes) - len(rsi)
    time_to_idx = {b["time"]: i for i, b in enumerate(bars)}
    for ob in blocks:
        idx = time_to_idx.get(ob["time"])
        if idx is None:
            continue
        ri = idx - rsi_offset
        if ri < 0 or ri >= len(rsi):
            continue
        val = rsi[ri]
        ob["rsi"] = round(val, 1)
        if ob["type"] == "BULLISH_OB" and val <= 35:
            ob["rsi_ob"] = True
        if ob["type"] == "BEARISH_OB" and val >= 65:
            ob["rsi_ob"] = True
    return blocks


def analyze_timeframe(sym: str, tf_key: str, count: int = 120) -> dict[str, Any]:
    tf = TIMEFRAMES.get(tf_key)
    if tf is None:
        return {"timeframe": tf_key, "trend": "NEUTRAL", "rsi": None}
    rates = mt5.copy_rates_from_pos(sym, tf, 0, count)
    if rates is None or len(rates) == 0:
        return {"timeframe": tf_key, "trend": "NEUTRAL", "rsi": None}
    bars = rates_to_bars(rates)
    closes = [b["close"] for b in bars]
    rsi_vals = calc_rsi(closes)
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    return {
        "timeframe": tf_key,
        "trend": calculate_trend(bars),
        "rsi": round(rsi_vals[-1], 1) if rsi_vals else None,
        "rsi_signal": (
            "oversold" if rsi_vals and rsi_vals[-1] < 30 else
            "overbought" if rsi_vals and rsi_vals[-1] > 70 else
            "neutral"
        ),
        "ema20": round(ema20[-1], 5) if ema20 else None,
        "ema50": round(ema50[-1], 5) if ema50 else None,
        "close": closes[-1],
    }


def build_chart_analysis(sym: str, chart_tf: str, count: int = 200) -> dict[str, Any]:
    chart_tf = chart_tf.upper()
    tf = TIMEFRAMES.get(chart_tf, mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(sym, tf, 0, count)
    if rates is None or len(rates) == 0:
        return {"ok": False, "error": "no candle data"}

    bars = rates_to_bars(rates)
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    rsi_series = calc_rsi(closes)
    rsi_offset = len(closes) - len(rsi_series)
    rsi_points = [
        {"time": datetime.utcfromtimestamp(bars[rsi_offset + i]["time"]).isoformat() + "Z", "rsi": round(rsi_series[i], 2)}
        for i in range(len(rsi_series))
    ]
    rsi_aligned = calc_rsi_aligned(closes)
    rsi_divergences = find_rsi_divergences(bars, rsi_aligned)
    latest_div = rsi_divergences[0] if rsi_divergences else None
    bull_div_n = sum(1 for d in rsi_divergences if d["type"] == "bullish")
    bear_div_n = sum(1 for d in rsi_divergences if d["type"] == "bearish")

    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    ema20_pts, ema50_pts = [], []
    for i, val in enumerate(ema20):
        t = bars[i + (len(closes) - len(ema20))]["time"]
        ema20_pts.append({"time": datetime.utcfromtimestamp(t).isoformat() + "Z", "value": round(val, 5)})
    for i, val in enumerate(ema50):
        t = bars[i + (len(closes) - len(ema50))]["time"]
        ema50_pts.append({"time": datetime.utcfromtimestamp(t).isoformat() + "Z", "value": round(val, 5)})

    swing_h, swing_l = find_swing_points(bars)
    order_blocks = find_order_blocks(bars, swing_h, swing_l)
    order_blocks = mark_rsi_order_blocks(bars, order_blocks)
    order_blocks = enrich_order_block_intensity(bars, order_blocks)

    tick = mt5.symbol_info_tick(sym)
    current_price = tick.bid if tick else closes[-1]
    atr_val = (
        sum(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, min(15, len(closes)))
        ) / 14
        if len(closes) > 14 else 5.0
    )
    order_blocks = enrich_order_block_validity(bars, order_blocks, current_price, atr_val)

    for ob in order_blocks:
        ob["time"] = datetime.utcfromtimestamp(ob["time"]).isoformat() + "Z"

    mtf = {tf_key: analyze_timeframe(sym, tf_key) for tf_key in ("M1", "M5", "M15", "M30")}
    trends = {k: v["trend"] for k, v in mtf.items()}
    up = sum(1 for t in trends.values() if t == "UP")
    down = sum(1 for t in trends.values() if t == "DOWN")
    if up >= 3:
        overall = "STRONG_UP"
    elif down >= 3:
        overall = "STRONG_DOWN"
    elif up > down:
        overall = "UP"
    elif down > up:
        overall = "DOWN"
    else:
        overall = "NEUTRAL"

    smc = analyze_smc_structures_fvg(bars, fvg_history=3, struct_history=5)
    smc["inducements"] = detect_inducements(bars)
    smc["inducement_count"] = len(smc["inducements"])
    beluga_smc = analyze_bigbeluga_smc(bars, ob_last=5, fvg_num=5, swing_limit=100)
    last_br = smc.get("last_break") or {}

    tick = mt5.symbol_info_tick(sym)
    return {
        "ok": True,
        "symbol": sym,
        "chart_timeframe": chart_tf,
        "price": tick.bid if tick else closes[-1],
        "overall_trend": overall,
        "mtf": mtf,
        "chart": {
            "trend": calculate_trend(bars),
            "rsi": round(rsi_series[-1], 1) if rsi_series else None,
            "rsi_signal": mtf.get(chart_tf, {}).get("rsi_signal"),
            "rsi_divergence": latest_div["type"] if latest_div else None,
            "rsi_divergence_label": latest_div["label"] if latest_div else None,
            "rsi_divergence_bull_count": bull_div_n,
            "rsi_divergence_bear_count": bear_div_n,
            "smc_fvg_count": smc.get("fvg_count", 0),
            "smc_last_break": last_br.get("label"),
            "smc_last_break_kind": last_br.get("kind"),
            "smc_inducement_count": smc.get("inducement_count", 0),
            "beluga_fvg_count": beluga_smc.get("fvg_count", 0),
            "beluga_ob_count": beluga_smc.get("ob_count", 0),
            "beluga_structure_count": beluga_smc.get("structure_count", 0),
            "beluga_trend": beluga_smc.get("trend", 0),
            "atr": round(
                sum(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
                    for i in range(1, min(15, len(closes)))) / 14,
                2,
            ) if len(closes) > 14 else None,
        },
        "rsi_series": rsi_points[-120:],
        "rsi_divergences": rsi_divergences,
        "smc": smc,
        "beluga_smc": beluga_smc,
        "ema20": ema20_pts[-80:],
        "ema50": ema50_pts[-80:],
        "order_blocks": order_blocks,
        "rsi_order_blocks": [ob for ob in order_blocks if ob.get("rsi_ob")],
        "valid_order_blocks": [ob for ob in order_blocks if ob.get("is_tradeable")],
        "valid_rsi_order_blocks": [ob for ob in order_blocks if ob.get("rsi_ob") and ob.get("is_tradeable")],
        "strongest_rsi_ob": next(
            (ob for ob in order_blocks if ob.get("rsi_ob") and ob.get("is_tradeable")),
            next((ob for ob in order_blocks if ob.get("rsi_ob")), None),
        ),
        "swing_highs": [
            {"time": datetime.utcfromtimestamp(s["time"]).isoformat() + "Z", "price": s["price"]}
            for s in swing_h[-8:]
        ],
        "swing_lows": [
            {"time": datetime.utcfromtimestamp(s["time"]).isoformat() + "Z", "price": s["price"]}
            for s in swing_l[-8:]
        ],
    }


def build_trade_suggestion(
    sym: str,
    chart_tf: str,
    count: int = 200,
    ob_time: str | None = None,
    ob_type: str | None = None,
    risky: bool = False,
) -> dict[str, Any]:
    """Suggest pending order (limit/stop) from trend + order-block analysis."""
    analysis = build_chart_analysis(sym, chart_tf, count)
    if not analysis.get("ok"):
        return analysis

    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if not info or not tick:
        return {"ok": False, "error": "no quote"}

    bid, ask = tick.bid, tick.ask
    atr = float(analysis["chart"].get("atr") or 5.0)
    pt = info.point
    min_dist = max(1, info.trade_stops_level) * pt
    buffer = max(atr * 0.35, min_dist * 2)
    sl_pad = max(atr * 0.75, buffer, min_dist * 3)

    overall = analysis["overall_trend"]
    rsi_obs = list(analysis.get("rsi_order_blocks") or [])
    all_obs = list(analysis.get("order_blocks") or [])
    tradeable = lambda ob: ob.get("is_tradeable", True)
    pool = [ob for ob in all_obs if tradeable(ob)]
    blocks = sorted(pool, key=lambda o: o.get("intensity", 0), reverse=True)
    chart_trend = (analysis.get("chart") or {}).get("trend", "NEUTRAL")

    def rr_ratio(entry: float, sl: float, tp: float) -> float:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        return round(reward / risk, 2) if risk > 0 else 0.0

    def nearest_swing_target(levels: list[dict], entry: float, side: str) -> float | None:
        prices = [float(x["price"]) for x in levels]
        if side == "sell":
            below = [p for p in prices if p < entry - buffer]
            return max(below) if below else None
        above = [p for p in prices if p > entry + buffer]
        return min(above) if above else None

    def prices_for_buy_ob(ob: dict) -> tuple[float, float, float]:
        entry = float(ob["low"])
        sl = float(ob["low"]) - sl_pad
        tp = entry + atr * 2.0
        swing_tp = nearest_swing_target(analysis.get("swing_highs") or [], entry, "buy")
        if swing_tp and swing_tp > entry + sl_pad:
            tp = swing_tp
        return entry, sl, max(tp, entry + atr * 1.5)

    def prices_for_sell_ob(ob: dict) -> tuple[float, float, float]:
        entry = float(ob["high"])
        sl = float(ob["high"]) + sl_pad
        tp = entry - atr * 2.0
        swing_tp = nearest_swing_target(analysis.get("swing_lows") or [], entry, "sell")
        if swing_tp and swing_tp < entry - sl_pad:
            tp = swing_tp
        return entry, sl, min(tp, entry - atr * 1.5)

    def clamp_tp_rr(side: str, entry: float, sl: float, tp: float, max_rr: float) -> float:
        risk = abs(entry - sl)
        if risk <= 0:
            return tp
        if side == "BUY":
            cap = entry + risk * max_rr
            return min(tp, cap) if tp > entry else tp
        cap = entry - risk * max_rr
        return max(tp, cap) if tp < entry else tp

    def setup_issues(
        side: str, entry: float, sl: float, tp: float, ob: dict,
        max_dist_atr: float = 2.5, risky: bool = False, selected: bool = False,
    ) -> list[str]:
        issues: list[str] = []
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        min_risk_atr = 0.35 if risky else 0.5
        min_rr = 0.8 if risky else 1.0
        max_rr = 5.0 if risky else 4.0
        if risk < atr * min_risk_atr:
            issues.append("SL too tight for current ATR")
        if side == "BUY":
            if tp <= entry + min_dist:
                issues.append("TP must be above entry")
            if not risky and not selected and entry >= bid - min_dist and ob.get("status") != "AT_ZONE":
                issues.append("entry not below market for buy limit")
        else:
            if tp >= entry - min_dist:
                issues.append("TP must be below entry")
            if not risky and not selected and entry <= ask + min_dist and ob.get("status") != "AT_ZONE":
                issues.append("entry not above market for sell limit")
        rr = reward / risk if risk else 0
        if rr < min_rr:
            issues.append(f"R:R too low ({rr:.1f})")
        if rr > max_rr:
            issues.append(f"R:R unrealistic ({rr:.1f})")
        if not risky and not selected:
            dist = float(ob.get("distance_atr") or 0)
            if dist > max_dist_atr and ob.get("status") not in ("FRESH", "AT_ZONE"):
                issues.append(f"zone {dist}x ATR away — wait for closer retest")
        return issues

    last_reject: list[str] = []

    def try_setup(
        order_type: str,
        side: str,
        entry: float,
        sl: float,
        tp: float,
        reason: str,
        confidence: str,
        ob: dict,
        max_dist_atr: float = 2.5,
        risky: bool = False,
        selected: bool = False,
    ) -> bool:
        nonlocal setup
        bad = setup_issues(
            side, entry, sl, tp, ob, max_dist_atr=max_dist_atr, risky=risky, selected=selected,
        )
        if bad:
            last_reject[:] = bad
            return False
        setup = make_setup(order_type, side, entry, sl, tp, reason, confidence, ob, risky=risky)
        return True

    def make_setup(
        order_type: str,
        side: str,
        entry: float,
        sl: float,
        tp: float,
        reason: str,
        confidence: str,
        ob: dict | None,
        risky: bool = False,
    ) -> dict[str, Any]:
        entry = round_price(sym, entry)
        sl = round_price(sym, sl)
        tp = round_price(sym, tp)
        result = {
            "has_setup": True,
            "side": side,
            "order_type": order_type,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr_ratio(entry, sl, tp),
            "confidence": confidence,
            "reason": reason,
            "bid": bid,
            "ask": ask,
            "atr": atr,
            "overall_trend": overall,
            "ob": ob,
            "risky": risky,
        }
        return result

    setup: dict[str, Any] | None = None

    # --- Clicked OB: build suggestion for that zone (risky if mitigated/broken/stale) ---
    if ob_time and ob_type:
        target = _find_ob_in_analysis(analysis, ob_time, ob_type)
        if target is None:
            return {
                "ok": True,
                "has_setup": False,
                "symbol": sym,
                "chart_timeframe": chart_tf,
                "overall_trend": overall,
                "price": bid,
                "message": "Selected order block not found on chart",
            }
        use_risky = risky or not target.get("is_tradeable", True)
        ob = target
        status = ob.get("status", "FRESH")
        status_label = ob.get("status_label") or status
        max_rr_sel = 5.0 if use_risky else 4.0
        conf = "RISKY" if use_risky else ob.get("intensity_tier", "MED")
        if status == "TESTED" and not use_risky:
            conf = "MED"
        prefix = "⚠️ RISKY — " if use_risky else ""
        if ob["type"] == "BEARISH_OB":
            entry, sl, tp = prices_for_sell_ob(ob)
            side = "SELL"
            if ob.get("status") == "AT_ZONE":
                order_type, entry = "sell", bid
                sl = float(ob["high"]) + sl_pad
            else:
                order_type = "sell_limit" if entry > ask + min_dist else "sell_stop"
                if order_type == "sell_stop" and entry <= ask:
                    entry = ask + min_dist
            tp = clamp_tp_rr(side, entry, sl, tp, max_rr_sel)
            try_setup(
                order_type, side, entry, sl, tp,
                f"{prefix}bearish OB ({ob.get('volume_k', '?')}) · {status} — {status_label}",
                conf, ob, risky=use_risky, selected=True,
            )
        else:
            entry, sl, tp = prices_for_buy_ob(ob)
            side = "BUY"
            if ob.get("status") == "AT_ZONE":
                order_type, entry = "buy", ask
                sl = float(ob["low"]) - sl_pad
            else:
                order_type = "buy_limit" if entry < bid - min_dist else "buy_stop"
                if order_type == "buy_stop" and entry <= ask:
                    entry = ask + min_dist
            tp = clamp_tp_rr(side, entry, sl, tp, max_rr_sel)
            try_setup(
                order_type, side, entry, sl, tp,
                f"{prefix}bullish OB ({ob.get('volume_k', '?')}) · {status} — {status_label}",
                conf, ob, risky=use_risky, selected=True,
            )
        if setup:
            return {"ok": True, "symbol": sym, "chart_timeframe": chart_tf, **setup}
        return {
            "ok": True,
            "has_setup": False,
            "symbol": sym,
            "chart_timeframe": chart_tf,
            "overall_trend": overall,
            "price": bid,
            "risky": use_risky,
            "message": f"No setup for selected OB — {'; '.join(last_reject) if last_reject else 'filters rejected'}",
        }

    # --- Trend-following: SELL at bearish OB ---
    if overall in ("STRONG_DOWN", "DOWN"):
        bears = [ob for ob in blocks if ob["type"] == "BEARISH_OB"]
        bears_above = sorted([ob for ob in bears if ob["high"] > bid], key=lambda o: o["high"])
        ob = bears_above[0] if bears_above else (bears[0] if bears else None)
        if ob:
            entry, sl, tp = prices_for_sell_ob(ob)
            order_type = "sell_limit" if entry > ask + min_dist else "sell_stop"
            if order_type == "sell_stop" and entry <= ask:
                entry = ask + min_dist
            conf = ob.get("intensity_tier", "MED") if ob.get("rsi_ob") else "MED"
            try_setup(
                order_type, "SELL", entry, sl, tp,
                f"{overall} — sell retest bearish OB ({ob.get('volume_k', '?')}) · {ob.get('status', 'FRESH')}",
                conf, ob,
            )

    # --- Trend-following: BUY at bullish OB ---
    elif overall in ("STRONG_UP", "UP"):
        bulls = [ob for ob in blocks if ob["type"] == "BULLISH_OB"]
        bulls_below = sorted([ob for ob in bulls if ob["low"] < ask], key=lambda o: -o["low"])
        ob = bulls_below[0] if bulls_below else (bulls[0] if bulls else None)
        if ob:
            entry, sl, tp = prices_for_buy_ob(ob)
            order_type = "buy_limit" if entry < bid - min_dist else "buy_stop"
            if order_type == "buy_stop" and entry <= ask:
                entry = ask + min_dist
            conf = ob.get("intensity_tier", "MED") if ob.get("rsi_ob") else "MED"
            try_setup(
                order_type, "BUY", entry, sl, tp,
                f"{overall} — buy retest bullish OB ({ob.get('volume_k', '?')}) · {ob.get('status', 'FRESH')}",
                conf, ob,
            )

    # --- NEUTRAL overall: use chart TF trend + any valid OB ---
    elif overall == "NEUTRAL" and blocks:
        prefer_sell = chart_trend in ("DOWN", "STRONG_DOWN")
        prefer_buy = chart_trend in ("UP", "STRONG_UP")
        bears = [ob for ob in blocks if ob["type"] == "BEARISH_OB"]
        bulls = [ob for ob in blocks if ob["type"] == "BULLISH_OB"]
        ob = None
        if prefer_sell and bears:
            bears_fit = sorted([o for o in bears if o["high"] > bid], key=lambda o: float(o.get("distance") or 0))
            ob = bears_fit[0] if bears_fit else bears[0]
        elif prefer_buy and bulls:
            bulls_fit = sorted([o for o in bulls if o["low"] < ask], key=lambda o: float(o.get("distance") or 0))
            ob = bulls_fit[0] if bulls_fit else bulls[0]
        elif bears and not bulls:
            ob = bears[0]
        elif bulls and not bears:
            ob = bulls[0]
        elif blocks:
            ob = blocks[0]
        if ob:
            aligned = (prefer_buy and ob["type"] == "BULLISH_OB") or (prefer_sell and ob["type"] == "BEARISH_OB")
            max_dist = 4.0 if aligned and ob.get("status") in ("FRESH", "TESTED") else 2.5
            conf = ob.get("intensity_tier", "MED")
            if ob.get("status") == "TESTED":
                conf = "MED"
            if ob["type"] == "BEARISH_OB":
                entry, sl, tp = prices_for_sell_ob(ob)
                if ob.get("status") == "AT_ZONE":
                    order_type = "sell"
                    entry = bid
                    sl = float(ob["high"]) + sl_pad
                else:
                    order_type = "sell_limit" if entry > ask + min_dist else "sell_stop"
                    if order_type == "sell_stop" and entry <= ask:
                        entry = ask + min_dist
                try_setup(
                    order_type, "SELL", entry, sl, tp,
                    f"NEUTRAL / {chart_tf} {chart_trend} — bearish OB ({ob.get('volume_k', '?')}) · {ob.get('status', 'FRESH')}",
                    conf, ob, max_dist_atr=max_dist,
                )
            else:
                entry, sl, tp = prices_for_buy_ob(ob)
                if ob.get("status") == "AT_ZONE":
                    order_type = "buy"
                    entry = ask
                    sl = float(ob["low"]) - sl_pad
                else:
                    order_type = "buy_limit" if entry < bid - min_dist else "buy_stop"
                    if order_type == "buy_stop" and entry <= ask:
                        entry = ask + min_dist
                try_setup(
                    order_type, "BUY", entry, sl, tp,
                    f"NEUTRAL / {chart_tf} {chart_trend} — bullish OB ({ob.get('volume_k', '?')}) · {ob.get('status', 'FRESH')}",
                    conf, ob, max_dist_atr=max_dist,
                )

    # --- RSI OB fallback when trend branches did not fire ---
    if setup is None and rsi_obs:
        ob = next((o for o in rsi_obs if tradeable(o)), None)
        if ob and ob["type"] == "BULLISH_OB":
            entry, sl, tp = prices_for_buy_ob(ob)
            order_type = "buy_limit" if entry < bid - min_dist else "buy_stop"
            try_setup(
                order_type, "BUY", entry, sl, tp,
                f"RSI bullish OB ({ob.get('volume_k', '?')} · RSI {ob.get('rsi')}) · {ob.get('status', 'FRESH')}",
                ob.get("intensity_tier", "LOW"), ob,
            )
        elif ob and ob["type"] == "BEARISH_OB":
            entry, sl, tp = prices_for_sell_ob(ob)
            order_type = "sell_limit" if entry > ask + min_dist else "sell_stop"
            try_setup(
                order_type, "SELL", entry, sl, tp,
                f"RSI bearish OB ({ob.get('volume_k', '?')} · RSI {ob.get('rsi')}) · {ob.get('status', 'FRESH')}",
                ob.get("intensity_tier", "LOW"), ob,
            )

    if setup is None:
        valid_n = len(blocks)
        bears_n = sum(1 for o in blocks if o["type"] == "BEARISH_OB")
        bulls_n = sum(1 for o in blocks if o["type"] == "BULLISH_OB")
        if valid_n == 0:
            message = "No valid OB — all zones broken, mitigated, or stale"
        elif overall in ("STRONG_DOWN", "DOWN") and bears_n == 0:
            message = (
                f"{overall} — wait for bearish OB retest above price "
                f"(don't buy dips; {bulls_n} bullish zone(s) below ignored)"
            )
        elif overall in ("STRONG_UP", "UP") and bulls_n == 0:
            message = (
                f"{overall} — wait for bullish OB retest below price "
                f"(don't fade strength; {bears_n} bearish zone(s) above ignored)"
            )
        else:
            if last_reject:
                message = f"No setup — {'; '.join(last_reject)}"
            elif valid_n > 0:
                message = f"No setup — {valid_n} valid OB(s) found but filters rejected the trade"
            else:
                message = f"No setup — {valid_n} valid OB(s) but none match current trend filter"
        return {
            "ok": True,
            "has_setup": False,
            "symbol": sym,
            "chart_timeframe": chart_tf,
            "overall_trend": overall,
            "price": bid,
            "valid_ob_count": valid_n,
            "message": message,
        }

    return {
        "ok": True,
        "symbol": sym,
        "chart_timeframe": chart_tf,
        **setup,
    }


@app.route("/getAnalysis", methods=["GET"])
@require_api_key
@require_mt5
def get_analysis():
    """
    Multi-TF trend LEDs, RSI, order blocks for chart overlays.
    ?symbol=XAUUSD&timeframe=M5&count=200
    """
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    chart_tf = request.args.get("timeframe", "M5").upper()
    count = max(50, min(int(request.args.get("count", 200)), 5000))
    info, sym = resolve_symbol(symbol)
    if info is None:
        return jsonify({"ok": False, "error": f"symbol not found: {symbol}"}), 404
    result = build_chart_analysis(sym, chart_tf, count)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/getTradeSuggestion", methods=["GET"])
@require_api_key
@require_mt5
def get_trade_suggestion():
    """
    Suggested pending order from analysis: type, entry, SL, TP, R:R.
    ?symbol=XAUUSD&timeframe=M5&count=200
    """
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    chart_tf = request.args.get("timeframe", "M5").upper()
    count = max(50, min(int(request.args.get("count", 200)), 5000))
    ob_time = request.args.get("ob_time", "").strip() or None
    ob_type = request.args.get("ob_type", "").strip() or None
    risky = request.args.get("risky", "").lower() in ("1", "true", "yes")
    info, sym = resolve_symbol(symbol)
    if info is None:
        return jsonify({"ok": False, "error": f"symbol not found: {symbol}"}), 404
    result = build_trade_suggestion(sym, chart_tf, count, ob_time=ob_time, ob_type=ob_type, risky=risky)
    return jsonify(result), (200 if result.get("ok") else 400)


def _execute_place_order(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    symbol = data.get("symbol")
    if not symbol:
        return {"ok": False, "error": "symbol required"}, 400

    order_type = str(data.get("type", "buy")).lower()
    volume = float(data.get("volume", 0.01))
    magic = int(data.get("magic", DEFAULT_MAGIC))
    comment = str(data.get("comment", "API"))
    deviation = int(data.get("deviation", 50))
    sl = float(data.get("sl", 0) or 0)
    tp = float(data.get("tp", 0) or 0)
    sl_points = data.get("sl_points")
    tp_points = data.get("tp_points")
    price = data.get("price")

    if order_type in ("buy", "sell"):
        req, err = build_market_request(symbol, order_type, volume, magic=magic, comment=comment, deviation=deviation)
        if err:
            return {"ok": False, "error": err}, 400
        entry = req["price"]
        if sl_points is not None or tp_points is not None:
            slp = int(sl_points) if sl_points is not None else None
            tpp = int(tp_points) if tp_points is not None else None
            sl, tp = apply_points_to_prices(req["symbol"], order_type, entry, slp, tpp)
        if sl:
            req["sl"] = sl
        if tp:
            req["tp"] = tp
    else:
        if price is None:
            return {"ok": False, "error": "price required for pending orders"}, 400
        req, err = build_pending_request(symbol, order_type, volume, float(price), sl=sl, tp=tp, magic=magic, comment=comment)
        if err:
            return {"ok": False, "error": err}, 400
        if sl_points is not None or tp_points is not None:
            slp = int(sl_points) if sl_points is not None else None
            tpp = int(tp_points) if tp_points is not None else None
            sl, tp = apply_points_to_prices(req["symbol"], order_type, float(price), slp, tpp)
            if sl:
                req["sl"] = sl
            if tp:
                req["tp"] = tp

    ok, result = send_order(req)
    if ok:
        payload: dict[str, Any] = {
            "ok": True,
            "request": {k: req[k] for k in req if k != "type_filling"},
            "result": result_to_dict(result),
        }
        if data.get("watch") and result is not None and getattr(result, "order", None):
            meta = data.get("watch_meta") or {}
            ob = meta.get("ob") or {}
            ob_time = meta.get("ob_time") or ob.get("time")
            ob_type = meta.get("ob_type") or ob.get("type")
            chart_tf = meta.get("chart_timeframe") or data.get("chart_timeframe") or "M5"
            bar_count = int(meta.get("count") or data.get("count") or 200)
            if ob_time and ob_type:
                sym = req["symbol"]
                payload["suggestion_watch"] = suggestion_watch.add(
                    ticket=int(result.order),
                    symbol=sym,
                    magic=int(req.get("magic", magic)),
                    order_type=order_type,
                    chart_tf=str(chart_tf),
                    bar_count=bar_count,
                    ob_time=str(ob_time),
                    ob_type=str(ob_type),
                    entry=float(req.get("price") or payload["request"].get("price") or 0),
                    sl=float(req.get("sl") or payload["request"].get("sl") or 0),
                    tp=float(req.get("tp") or payload["request"].get("tp") or 0),
                    volume=float(volume),
                )
            else:
                payload["suggestion_watch"] = {
                    "ok": False,
                    "error": "watch requires watch_meta.ob_time and ob_type",
                }
        return payload, 200
    return {"ok": False, "result": result_to_dict(result), **last_error()}, 400


@app.route("/placeOrder", methods=["POST"])
@require_api_key
@require_mt5
def place_order():
    payload, status = _execute_place_order(json_body())
    return jsonify(payload), status


@app.route("/placeTrades", methods=["POST"])
@require_api_key
@require_mt5
def place_trades():
    trades = json_body().get("trades") or []
    if not trades:
        return jsonify({"ok": False, "error": "trades array required"}), 400
    results = []
    for i, t in enumerate(trades):
        payload, status = _execute_place_order(t)
        results.append({"index": i, "status": status, **payload})
    ok_count = sum(1 for r in results if r.get("ok"))
    return jsonify({"ok": ok_count > 0, "placed": ok_count, "total": len(trades), "results": results})


@app.route("/getPositions", methods=["GET"])
@require_api_key
@require_mt5
def get_positions():
    symbol = request.args.get("symbol")
    ticket = request.args.get("ticket")
    magic = request.args.get("magic")

    if ticket:
        positions = mt5.positions_get(ticket=int(ticket))
    elif symbol:
        _, sym = resolve_symbol(symbol)
        positions = mt5.positions_get(symbol=sym)
    else:
        positions = mt5.positions_get()

    if positions is None:
        return jsonify({"ok": False, **last_error()}), 400

    rows = [pos_to_dict(p) for p in positions]
    if magic is not None:
        rows = [p for p in rows if p["magic"] == int(magic)]

    return jsonify({"ok": True, "count": len(rows), "total_profit": sum(p["profit"] for p in rows), "positions": rows})


@app.route("/getOrders", methods=["GET"])
@require_api_key
@require_mt5
def get_orders():
    """Pending/active orders (limits, stops). ?symbol= &ticket= &magic="""
    symbol = request.args.get("symbol")
    ticket = request.args.get("ticket")
    magic = request.args.get("magic")

    if ticket:
        orders = mt5.orders_get(ticket=int(ticket))
    elif symbol:
        _, sym = resolve_symbol(symbol)
        orders = mt5.orders_get(symbol=sym)
    else:
        orders = mt5.orders_get()

    if orders is None:
        return jsonify({"ok": False, **last_error()}), 400

    rows = [order_to_dict(o) for o in orders]
    if magic is not None:
        rows = [o for o in rows if o["magic"] == int(magic)]

    return jsonify({"ok": True, "count": len(rows), "orders": rows})


@app.route("/closePositions", methods=["POST"])
@require_api_key
@require_mt5
def close_positions():
    data = json_body()
    ticket, symbol, magic, volume = data.get("ticket"), data.get("symbol"), data.get("magic"), data.get("volume")

    if ticket:
        positions = mt5.positions_get(ticket=int(ticket))
    elif symbol:
        _, sym = resolve_symbol(symbol)
        positions = mt5.positions_get(symbol=sym)
    else:
        positions = mt5.positions_get()

    if not positions:
        return jsonify({"ok": True, "closed": 0, "message": "no positions"})

    if magic is not None:
        positions = [p for p in positions if p.magic == int(magic)]

    results = []
    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            results.append({"ticket": pos.ticket, "ok": False, "error": "no tick"})
            continue
        close_vol = normalize_volume(pos.symbol, float(volume) if volume else pos.volume)
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": close_vol,
            "type": close_type,
            "position": pos.ticket,
            "price": close_price,
            "deviation": int(data.get("deviation", 50)),
            "comment": str(data.get("comment", "API close")),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        ok, res = send_order(req)
        results.append({"ticket": pos.ticket, "ok": ok, "result": result_to_dict(res)})

    closed = sum(1 for r in results if r["ok"])
    return jsonify({"ok": closed > 0, "closed": closed, "results": results})


@app.route("/modifyPosition", methods=["POST"])
@require_api_key
@require_mt5
def modify_position():
    data = json_body()
    ticket = data.get("ticket")
    if not ticket:
        return jsonify({"ok": False, "error": "ticket required"}), 400

    positions = mt5.positions_get(ticket=int(ticket))
    if not positions:
        return jsonify({"ok": False, "error": "position not found"}), 404

    pos = positions[0]
    new_sl, new_tp = pos.sl, pos.tp
    side = "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell"

    if "sl_points" in data:
        sp = int(data["sl_points"])
        if sp < 0:
            new_sl = 0.0
        elif sp == 0:
            new_sl = pos.sl
        else:
            new_sl, _ = apply_points_to_prices(pos.symbol, side, pos.price_open, sp, None)
    elif "sl" in data:
        new_sl = float(data["sl"])

    if "tp_points" in data:
        tp = int(data["tp_points"])
        if tp < 0:
            new_tp = 0.0
        elif tp == 0:
            new_tp = pos.tp
        else:
            _, new_tp = apply_points_to_prices(pos.symbol, side, pos.price_open, None, tp)
    elif "tp" in data:
        new_tp = float(data["tp"])

    new_sl = round_price(pos.symbol, new_sl) if new_sl else 0.0
    new_tp = round_price(pos.symbol, new_tp) if new_tp else 0.0

    ok, result = send_order({
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pos.symbol,
        "position": pos.ticket,
        "sl": new_sl,
        "tp": new_tp,
    })
    if ok:
        return jsonify({"ok": True, "ticket": pos.ticket, "sl": new_sl, "tp": new_tp, "result": result_to_dict(result)})
    return jsonify({"ok": False, "result": result_to_dict(result), **last_error()}), 400


@app.route("/trailPosition_MODE1", methods=["POST"])
@require_api_key
@require_mt5
def trail_mode1():
    data = json_body()
    ticket = data.get("ticket")
    trail_pts = data.get("trail_points") or data.get("step_points")
    if not ticket or trail_pts is None:
        return jsonify({"ok": False, "error": "ticket and trail_points required"}), 400
    result = trail_mgr.add_mode1(int(ticket), int(trail_pts))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/trailPosition_MODE2", methods=["POST"])
@require_api_key
@require_mt5
def trail_mode2():
    data = json_body()
    ticket = data.get("ticket")
    step_pts = data.get("step_points") or data.get("trail_points")
    if not ticket or step_pts is None:
        return jsonify({"ok": False, "error": "ticket and step_points required"}), 400
    result = trail_mgr.add_mode2(int(ticket), int(step_pts))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/trail/stop", methods=["POST"])
@require_api_key
def trail_stop():
    data = json_body()
    ticket = data.get("ticket")
    if ticket:
        return jsonify({"ok": trail_mgr.remove(int(ticket)), "ticket": int(ticket)})
    for job in trail_mgr.status():
        trail_mgr.remove(job["ticket"])
    return jsonify({"ok": True, "message": "all trail jobs stopped"})


@app.route("/trail/status", methods=["GET"])
@require_api_key
def trail_status():
    return jsonify({"ok": True, "jobs": trail_mgr.status()})


@app.route("/placeGrid", methods=["POST"])
@require_api_key
@require_mt5
def place_grid():
    result = _execute_place_grid(json_body())
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/schedule/grid", methods=["POST"])
@require_api_key
def schedule_grid():
    """
    Schedule grid deploy at IST time (converted to UTC internally).
    Body: date, time, timezone, timeout_mmss, plus placeGrid params.
    """
    try:
        data = json_body()
        if not data.get("symbol"):
            return jsonify({"ok": False, "error": "symbol required"}), 400
        payload = {k: v for k, v in data.items() if k not in ("date", "time", "offset_hours", "timeout_mmss", "timezone")}
        result = schedule_mgr.create(
            kind="grid",
            date_input=str(data.get("date", "")),
            time_input=str(data.get("time", "")),
            timeout_mmss=str(data.get("timeout_mmss", "00:00")),
            payload=payload,
            offset_hours=float(data.get("offset_hours", 0) or 0),
            tz_name=str(data.get("timezone", "IST")),
        )
        return jsonify(result), (200 if result.get("ok") else 400)
    except Exception as exc:
        print(f"[Schedule] grid error: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/schedule/trade", methods=["POST"])
@require_api_key
def schedule_trade():
    """
    Schedule market trade at IST time (converted to UTC internally).
    Body: date, time, timezone, timeout_mmss, symbol, type, volume, sl_points, tp_points, magic
    """
    try:
        data = json_body()
        if not data.get("symbol"):
            return jsonify({"ok": False, "error": "symbol required"}), 400
        if not data.get("type"):
            return jsonify({"ok": False, "error": "type required (buy/sell)"}), 400
        payload = {k: v for k, v in data.items() if k not in ("date", "time", "offset_hours", "timeout_mmss", "timezone")}
        payload["comment"] = payload.get("comment") or "scheduled-trade"
        result = schedule_mgr.create(
            kind="trade",
            date_input=str(data.get("date", "")),
            time_input=str(data.get("time", "")),
            timeout_mmss=str(data.get("timeout_mmss", "00:00")),
            payload=payload,
            offset_hours=float(data.get("offset_hours", 0) or 0),
            tz_name=str(data.get("timezone", "IST")),
        )
        return jsonify(result), (200 if result.get("ok") else 400)
    except Exception as exc:
        print(f"[Schedule] trade error: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/schedule/status", methods=["GET"])
@require_api_key
def schedule_status():
    return jsonify(schedule_mgr.status())


@app.route("/schedule/cancel", methods=["POST"])
@require_api_key
def schedule_cancel():
    data = json_body()
    schedule_id = str(data.get("id", "")).strip()
    if not schedule_id:
        return jsonify({"ok": False, "error": "id required"}), 400
    result = schedule_mgr.cancel(schedule_id)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/gridGuard/start", methods=["POST"])
@require_api_key
@require_mt5
def grid_guard_start():
    """
    Monitor basket floating profit and auto-close all positions + pending orders.
    Body: symbol, magic, max_floating_profit (e.g. 2 = close at +$2)
    """
    data = json_body()
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    magic = int(data.get("magic", 78001))
    max_profit = float(data.get("max_floating_profit") or data.get("floating_profit") or 0)
    result = grid_guard.add(symbol, magic, max_profit)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/gridGuard/stop", methods=["POST"])
@require_api_key
def grid_guard_stop():
    data = json_body()
    symbol = data.get("symbol")
    magic = data.get("magic")
    if symbol and magic is not None:
        removed = grid_guard.remove(symbol, int(magic))
    else:
        removed = grid_guard.remove()
    return jsonify({"ok": True, "removed": removed})


@app.route("/gridGuard/status", methods=["GET"])
@require_api_key
def grid_guard_status():
    symbol = request.args.get("symbol")
    magic = request.args.get("magic")
    floating = None
    if symbol:
        _, sym = resolve_symbol(symbol)
        floating = get_basket_floating(sym, int(magic) if magic else None)
    return jsonify({
        "ok": True,
        "guards": grid_guard.status(),
        "floating": floating,
    })


@app.route("/closeGridBasket", methods=["POST"])
@require_api_key
@require_mt5
def close_grid_basket():
    """Manually close all positions + cancel pending orders for symbol/magic."""
    data = json_body()
    symbol = data.get("symbol")
    magic = data.get("magic")
    sym = None
    if symbol:
        _, sym = resolve_symbol(symbol)
    result = close_basket(sym, int(magic) if magic is not None else None)
    if symbol and magic is not None:
        grid_guard.remove(symbol, int(magic))
        basket_tp_mgr.remove(symbol, int(magic))
    return jsonify(result)


@app.route("/basketTp/start", methods=["POST"])
@require_api_key
@require_mt5
def basket_tp_start():
    """
    Auto-set buy/sell TP+SL so basket hits +target_profit / -target_loss.
    Keeps recalculating as new grid positions open.
    Body: symbol, magic, target_profit, target_loss (optional, defaults to target_profit)
    """
    data = json_body()
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    magic = int(data.get("magic", 78001))
    target = float(data.get("target_profit") or data.get("basket_tp_profit") or data.get("basket_tp") or 0)
    target_loss = float(
        data.get("target_loss") or data.get("basket_sl_loss") or data.get("basket_sl") or target
    )
    result = basket_tp_mgr.add(symbol, magic, target, target_loss)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/basketTp/stop", methods=["POST"])
@require_api_key
def basket_tp_stop():
    data = json_body()
    symbol = data.get("symbol")
    magic = data.get("magic")
    if symbol and magic is not None:
        removed = basket_tp_mgr.remove(symbol, int(magic))
    else:
        removed = basket_tp_mgr.remove()
    return jsonify({"ok": True, "removed": removed})


@app.route("/basketTp/status", methods=["GET"])
@require_api_key
def basket_tp_status():
    symbol = request.args.get("symbol")
    magic = request.args.get("magic")
    levels = None
    if symbol:
        _, sym = resolve_symbol(symbol)
        positions = get_basket_positions(sym, int(magic) if magic else None)
        jobs = basket_tp_mgr.status()
        target = None
        target_loss = None
        if magic and jobs:
            for j in jobs:
                if j["symbol"] == sym and j["magic"] == int(magic):
                    target = j["target_profit"]
                    target_loss = j.get("target_loss")
                    break
        if positions and target:
            levels = calc_basket_sltp_levels(positions, target, target_loss)
        elif positions:
            levels = {"positions_count": len(positions), "current_floating": get_basket_floating(sym, int(magic) if magic else None)}
    return jsonify({
        "ok": True,
        "jobs": basket_tp_mgr.status(),
        "levels": levels,
    })


@app.route("/basketTp/apply", methods=["POST"])
@require_api_key
@require_mt5
def basket_tp_apply():
    """One-shot: recalculate and apply basket TP+SL without starting background job."""
    data = json_body()
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    magic = int(data.get("magic", 78001))
    target = float(data.get("target_profit") or data.get("basket_tp_profit") or data.get("basket_tp") or 0)
    target_loss = float(
        data.get("target_loss") or data.get("basket_sl_loss") or data.get("basket_sl") or target
    )
    if target <= 0:
        return jsonify({"ok": False, "error": "target_profit must be > 0"}), 400
    _, sym = resolve_basket_symbol(symbol, magic)
    if not sym or mt5.symbol_info(sym) is None:
        return jsonify({"ok": False, "error": f"symbol not found: {symbol}"}), 400
    result = apply_basket_sltp(sym, magic, target, target_loss)
    levels = calc_basket_sltp_levels(get_basket_positions(sym, magic), target, target_loss)
    result["levels"] = levels
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/suggestionWatch/status", methods=["GET"])
@require_api_key
def suggestion_watch_status():
    return jsonify({
        "ok": True,
        "state_file": SUGGESTION_WATCH_STATE_FILE,
        "jobs": suggestion_watch.status(),
    })


@app.route("/suggestionWatch/stop", methods=["POST"])
@require_api_key
def suggestion_watch_stop():
    """Stop watch job(s). Body: {ticket} or {all: true} — does not cancel MT5 order."""
    data = json_body()
    if data.get("all"):
        removed = suggestion_watch.remove_all()
        return jsonify({"ok": True, "removed": removed})
    ticket = data.get("ticket")
    if ticket is None:
        return jsonify({"ok": False, "error": "ticket or all:true required"}), 400
    removed = suggestion_watch.remove(int(ticket), status="stopped", message="Stopped via API")
    return jsonify({"ok": removed, "ticket": int(ticket)})


@app.route("/telegramAlerts/status", methods=["GET"])
@require_api_key
def telegram_alerts_status():
    return jsonify(telegram_alerts.status())


@app.route("/telegramAlerts/start", methods=["POST"])
@require_api_key
@require_mt5
def telegram_alerts_start():
    """
    Start candle-close monitoring → Telegram signals.
    Body: { "symbol": "XAUUSD", "bar_count": 200 }
    """
    data = json_body()
    symbol = str(data.get("symbol", "XAUUSD")).strip()
    bar_count = int(data.get("bar_count", 200))
    result = telegram_alerts.start(symbol=symbol, bar_count=bar_count)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/telegramAlerts/stop", methods=["POST"])
@require_api_key
def telegram_alerts_stop():
    """Stop Telegram monitoring."""
    result = telegram_alerts.stop()
    return jsonify(result)


@app.route("/telegramAlerts/test", methods=["POST"])
@require_api_key
def telegram_alerts_test():
    """Send a test message to the configured Telegram channel."""
    result = telegram_alerts.send_test()
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/autoTrade/status", methods=["GET"])
@require_api_key
def autotrade_status():
    return jsonify(auto_trade.status())


@app.route("/autoTrade/start", methods=["POST"])
@require_api_key
@require_mt5
def autotrade_start():
    """
    Start M5+ auto-trading on candle-close OB signals.
    Body: { "symbol": "XAUUSD", "bar_count": 200, "lot_size": 0.01, "magic": 202611 }
    Orders use comment alphafxauto and suggestion watch for OB maintenance.
    """
    data = json_body()
    symbol = str(data.get("symbol", "XAUUSD")).strip()
    bar_count = int(data.get("bar_count", 200))
    lot_size = float(data.get("lot_size") or data.get("volume") or AUTOTRADE_DEFAULT_LOT)
    magic = data.get("magic")
    result = auto_trade.start(
        symbol=symbol,
        bar_count=bar_count,
        lot_size=lot_size,
        magic=int(magic) if magic is not None else None,
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/autoTrade/stop", methods=["POST"])
@require_api_key
def autotrade_stop():
    """Stop auto-trading (does not close open positions or cancel pending orders)."""
    result = auto_trade.stop()
    return jsonify(result)


@app.route("/autoTrade/config", methods=["POST"])
@require_api_key
def autotrade_config():
    """Update lot size / magic while auto-trading is running."""
    data = json_body()
    lot_size = data.get("lot_size")
    if lot_size is None:
        lot_size = data.get("volume")
    magic = data.get("magic")
    bar_count = data.get("bar_count")
    result = auto_trade.configure(
        lot_size=float(lot_size) if lot_size is not None else None,
        magic=int(magic) if magic is not None else None,
        bar_count=int(bar_count) if bar_count is not None else None,
    )
    return jsonify(result), (200 if result.get("ok") else 400)


if __name__ == "__main__":
    ok, msg = ensure_mt5(MT5_PATH or None)
    print(f"[MT5] {msg}")
    print(f"[MT5] API key: {API_KEY} | listening on port {PORT}")
    trail_mgr.start()
    grid_guard.start()
    basket_tp_mgr.start()
    suggestion_watch.start()
    telegram_alerts._ensure_thread()
    auto_trade._ensure_thread()
    news_alerts._ensure_thread()
    schedule_mgr._ensure_thread()
    app.run(host=HOST, port=PORT, threaded=True)
