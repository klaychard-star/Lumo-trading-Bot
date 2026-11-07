"""
LUMO_BOT - LIVE - Gold M5 - FIXED + $3 MAX RISK
  OPEN trades close ANYTIME (even night/weekend)
  NEW trades ONLY 06:00-18:00 London, weekdays
  NEW RULE: If Risk > $3.00 per 0.01 lot -> SKIP TRADE (C1 too big!)
  BUY:  C1 Red, C2 Green BODY CLOSE > C1 Close, C3 taps Half
  SELL: C1 Green, C2 Red BODY CLOSE < C1 Close, C3 taps Half
  Entry   = Half of C1 (High + Low) / 2
  BUY SL  = C1 Low - $0.50  (OUTSIDE C1 - BELOW)
  SELL SL = C1 High + $0.50 (OUTSIDE C1 - ABOVE)
  TP      = 2.5 x Risk
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import os
import tempfile
import requests

LOG_FILE = "lumo_live_log.txt"
STATE_FILE = "lumo_active_trades.json"
BALANCE_FILE = "lumo_balance.json"
SPREAD_COST = 0.60
BASE_LOT = 0.01
INITIAL_BALANCE = 100.00
SL_PIPS = 5
PIP_VALUE = 0.10
SL_OFFSET = SL_PIPS * PIP_VALUE  # 5 pips = $0.50
TP_MULTIPLIER = 2.5
MAX_RISK = 3.00  # <-- NEW! Max $3.00 risk per 0.01 lot

def log_message(text):
  timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
  formatted_text = f"[{timestamp}] {text}"
  print(formatted_text)
  try:
    with open(LOG_FILE, "a") as f:
      f.write(formatted_text + "\n")
  except Exception as e:
    print(f"[{timestamp}] Error writing to log file: {e}")

def get_lot(balance):
  lot = int(balance // 100) * 0.02
  if lot < 0.02:
    lot = 0.02
  if lot > 2.0:
    lot = 2.0
  return lot

def fetch_yahoo_m5_data():
  url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=5d&interval=5m"
  headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
  try:
    response = requests.get(url, headers=headers, timeout=5)
    response.raise_for_status()
    data = response.json()
    result = data.get("chart", {}).get("result")
    if not result:
      log_message("Invalid data structure received from Yahoo Finance.")
      return [], [], [], [], []
    indicators = result[0].get("indicators", {})
    quote = indicators.get("quote", [{}])[0]
    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    timestamps = result[0].get("timestamp", [])
    valid_data = [(ts, o, h, l, c) for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes) if o is not None and h is not None and l is not None and c is not None]
    if not valid_data:
      return [], [], [], [], []
    ts_list, o_list, h_list, l_list, c_list = zip(*valid_data)
    o_list = [x - 26 for x in o_list]
    h_list = [x - 26 for x in h_list]
    l_list = [x - 26 for x in l_list]
    c_list = [x - 26 for x in c_list]
    return list(ts_list), list(o_list), list(h_list), list(l_list), list(c_list)
  except Exception as e:
    log_message(f"Error fetching data: {e}")
    return [], [], [], [], []

def save_json_atomically(filename, data):
  dir_name = os.path.dirname(filename) or "."
  try:
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False) as tf:
      json.dump(data, tf, indent=2)
      temp_name = tf.name
    os.replace(temp_name, filename)
  except Exception as e:
    log_message(f"Error saving {filename} atomically: {e}")

def load_active_trades():
  if os.path.exists(STATE_FILE):
    try:
      with open(STATE_FILE, "r") as f:
        return json.load(f)
    except Exception as e:
      log_message(f"Error reading active trades state: {e}")
  return []

def save_active_trades(trades):
  save_json_atomically(STATE_FILE, trades)

def load_balance():
  if os.path.exists(BALANCE_FILE):
    try:
      with open(BALANCE_FILE, "r") as f:
        return json.load(f)
    except Exception as e:
      log_message(f"Error reading balance file: {e}")
  return {"balance": INITIAL_BALANCE, "wins": 0, "losses": 0, "total_trades": 0}

def save_balance(data):
  save_json_atomically(BALANCE_FILE, data)

def run_single_check():
  now_utc = datetime.now(timezone.utc)
  try:
    dt_london = now_utc.astimezone(ZoneInfo("Europe/London"))
  except Exception:
    dt_london = now_utc

  # === FIXED: DO NOT EXIT HERE ANYMORE! ===
  # We need to still manage OPEN trades even outside London hours!
  is_weekend = now_utc.weekday() >= 5
  is_london_hours = (6 <= dt_london.hour <= 18)
  can_take_new_trade = (not is_weekend) and is_london_hours

  if is_weekend:
    log_message("Weekend detected - No NEW trades, but still managing OPEN trades.")
  elif not is_london_hours:
    log_message(f"Outside London hours ({dt_london.strftime('%H:%M')} London) - No NEW trades, but still managing OPEN trades.")

  balance_data = load_balance()
  if balance_data["balance"] < 20.00:
    log_message(f"CRITICAL: Balance (${balance_data['balance']:.2f}) is below $20.00 safety threshold. Trading halted.")
    return
  timestamps, opens, highs, lows, closes = fetch_yahoo_m5_data()
  if len(closes) < 100:
    log_message("Error: Not enough data fetched to evaluate strategy.")
    return
  i = len(closes) - 2
  current_high = highs[i]
  current_low = lows[i]
  current_price = closes[i]
  avg50 = sum(closes[i - 50 : i]) / 50
  c1_open, c1_high, c1_low, c1_close = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
  c2_open, c2_high, c2_low, c2_close = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
  c3_open, c3_high, c3_low, c3_close = opens[i], highs[i], lows[i], closes[i]
  current_lot = get_lot(balance_data["balance"])
  log_message(f"M5 Check @ ${current_price:.2f} | London Time: {dt_london.strftime('%H:%M')} | 50-MA: ${avg50:.2f} | Lot: {current_lot:.2f} | Bal: ${balance_data['balance']:.2f} | New Trades Allowed: {can_take_new_trade}")

  # === 1. MANAGE OPEN TRADES - ALWAYS RUNS, EVEN AT NIGHT! ===
  active_trades = load_active_trades()
  updated_active = []
  for trade in active_trades:
    side = trade["side"]
    entry = trade["entry"]
    tp = trade["tp"]
    sl = trade["sl"]
    lot = trade["lot"]
    tp_hit = current_high >= tp if side == "BUY" else current_low <= tp
    sl_hit = current_low <= sl if side == "BUY" else current_high >= sl
    if tp_hit or sl_hit:
      if sl_hit and not tp_hit:
        realized_pips = -abs(entry - sl)
        is_win = False
      else:
        realized_pips = abs(tp - entry)
        is_win = True
      net_pnl = (realized_pips * (lot / BASE_LOT)) - SPREAD_COST
      balance_data["balance"] += net_pnl
      balance_data["total_trades"] += 1
      if is_win:
        balance_data["wins"] += 1
        log_message(f"WIN CLOSED: {side} {lot:.2f}lot @ ${entry:.2f} -> TP hit | Net: ${net_pnl:+.2f} | Bal: ${balance_data['balance']:.2f}")
      else:
        balance_data["losses"] = balance_data.get("losses", 0) + 1
        log_message(f"LOSS CLOSED: {side} {lot:.2f}lot @ ${entry:.2f} -> SL hit | Net: ${net_pnl:+.2f} | Bal: ${balance_data['balance']:.2f}")
    else:
      updated_active.append(trade)
      log_message(f"HOLDING active trade: {side} @ ${entry:.2f}")

  # === 2. NEW TRADES - ONLY IF LONDON HOURS + WEEKDAY! ===
  if not updated_active and can_take_new_trade:
    trend_is_bullish = closes[i] > avg50
    trend_is_bearish = closes[i] < avg50
    ref_level = (c1_high + c1_low) / 2
    side = None
    entry_price, tp_price, sl_price = None, None, None
    risk = None
    c1_is_bearish = c1_close < c1_open
    c1_is_bullish = c1_close > c1_open
    c2_is_bullish = c2_close > c2_open
    c2_is_bearish = c2_close < c2_open
    # === BUY: C1 Red, C2 Green BODY CLOSE above C1 Close (NOT wick), C3 taps Half of C1 ===
    if trend_is_bullish and c1_is_bearish and c2_is_bullish and c2_close > c1_close:
      tapped_level = c3_low <= ref_level <= c3_high
      if tapped_level:
        side = "BUY"
        entry_price = ref_level  # Half of C1
        sl_price = c1_low - SL_OFFSET  # C1 Low - $0.50 - OUTSIDE C1 BELOW
        risk = entry_price - sl_price
        tp_price = entry_price + (risk * TP_MULTIPLIER)  # TP = 2.5 x Risk
    # === SELL: C1 Green, C2 Red BODY CLOSE below C1 Close (NOT wick), C3 taps Half of C1 ===
    elif trend_is_bearish and c1_is_bullish and c2_is_bearish and c2_close < c1_close:
      tapped_level = c3_low <= ref_level <= c3_high
      if tapped_level:
        side = "SELL"
        entry_price = ref_level  # Half of C1
        sl_price = c1_high + SL_OFFSET  # C1 High + $0.50 - OUTSIDE C1 ABOVE
        risk = sl_price - entry_price
        tp_price = entry_price - (risk * TP_MULTIPLIER)  # TP = 2.5 x Risk
    if side and entry_price is not None:
      # === NEW: $3.00 MAX RISK RULE - SKIP IF C1 TOO BIG! ===
      if risk is not None and risk > MAX_RISK:
        log_message(f"SKIP {side}: Risk ${risk:.2f} > ${MAX_RISK:.2f} max per 0.01 lot - C1 too big! Entry ${entry_price:.2f} SL ${sl_price:.2f}")
      else:
        new_trade = {"side": side, "entry": entry_price, "tp": tp_price, "sl": sl_price, "lot": current_lot}
        updated_active.append(new_trade)
        log_message(f"NEW SETUP: {side} {current_lot:.2f}lot @ Entry ${entry_price:.2f} (TP: ${tp_price:.2f} | SL: ${sl_price:.2f} | Risk: ${risk:.2f})")
  elif not updated_active and not can_take_new_trade:
    log_message("Skipping NEW setup - Outside London hours / Weekend.")

  save_active_trades(updated_active)
  save_balance(balance_data)

if __name__ == "__main__":
  run_single_check()
