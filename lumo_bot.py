"""
LUMO_BOT - LIVE - Gold M5 - WITH TREND FILTER + SCAN-BACK
  STRATEGY: SAME AS OCTOBER BACKTEST ($771 VERSION)!
  SCAN-BACK: Checks LAST 6 closed candles, not just 1!
    So if GitHub was late, bot still catches missed setup!
  RULE: If Risk > $5.00 per 0.01 lot -> SKIP TRADE!
  OPEN trades close ANYTIME
  NEW trades ONLY 06:00-18:00 London, weekdays
  TREND FILTER: 50-MA
    BUY ONLY if Price > 50-MA
    SELL ONLY if Price < 50-MA
  BUY:  C1 Red, C2 Green BODY CLOSE > C1 Close, C3 taps Half of C1
  SELL: C1 Green, C2 Red BODY CLOSE < C1 Close, C3 taps Half of C1
  Entry = Half of C1 (High + Low) / 2
  BUY SL = C1 Low - $0.50
  SELL SL = C1 High + $0.50
  TP = 2.5x Risk
  LIVE PRICE + SAVING BALANCE/TRADES - NO DERIV!
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
SL_OFFSET = SL_PIPS * PIP_VALUE
TP_MULTIPLIER = 2.5
MAX_RISK = 5.00
SCAN_BACK = 6  # Check last 6 closed candles!

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

def fetch_live_price():
  try:
    r = requests.get("https://api.gold-api.com/price/XAU", timeout=6)
    if r.status_code == 200:
      price = r.json().get("price")
      if price and 3000 < float(price) < 6000:
        log_message(f"Live Price Source: gold-api.com = ${float(price):.2f}")
        return float(price)
  except Exception as e:
    log_message(f"gold-api failed: {e}")
  try:
    r = requests.get("https://data-asg.goldprice.org/dbXRates/USD", timeout=6)
    if r.status_code == 200:
      items = r.json().get("items", [])
      if items:
        price = items[0].get("curr")
        if price and 3000 < float(price) < 6000:
          log_message(f"Live Price Source: goldprice.org = ${float(price):.2f}")
          return float(price)
  except Exception as e:
    log_message(f"goldprice.org failed: {e}")
  return None

def fetch_yahoo_m5_data():
  for base in ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com"]:
    url = f"{base}/v8/finance/chart/GC=F?range=5d&interval=5m"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
      response = requests.get(url, headers=headers, timeout=10)
      if response.status_code != 200:
        continue
      data = response.json()
      result = data.get("chart", {}).get("result")
      if not result:
        continue
      indicators = result[0].get("indicators", {})
      quote = indicators.get("quote", [{}])[0]
      opens = quote.get("open", [])
      highs = quote.get("high", [])
      lows = quote.get("low", [])
      closes = quote.get("close", [])
      timestamps = result[0].get("timestamp", [])
      valid_data = [(ts, o, h, l, c) for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes) if o is not None and h is not None and l is not None and c is not None]
      if not valid_data or len(valid_data) < 100:
        continue
      ts_list, o_list, h_list, l_list, c_list = zip(*valid_data)
      o_list = list(o_list); h_list = list(h_list); l_list = list(l_list); c_list = list(c_list); ts_list = list(ts_list)
      live_price = fetch_live_price()
      if live_price is not None and len(c_list) > 0:
        yahoo_last = c_list[-1]
        auto_offset = live_price - yahoo_last
        log_message(f"Auto-Sync: Live ${live_price:.2f} - Yahoo ${yahoo_last:.2f} = Offset {auto_offset:+.2f} applied")
        o_list = [x + auto_offset for x in o_list]
        h_list = [x + auto_offset for x in h_list]
        l_list = [x + auto_offset for x in l_list]
        c_list = [x + auto_offset for x in c_list]
      else:
        log_message("WARNING: Live price failed, using Yahoo raw")
      log_message(f"Got {len(c_list)} M5 candles via {base} - Last Close ${c_list[-1]:.2f}")
      return ts_list, o_list, h_list, l_list, c_list
    except Exception as e:
      log_message(f"Error fetching from {base}: {e}")
      continue
  log_message("Error: All Yahoo servers failed")
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
  return {"balance": INITIAL_BALANCE, "wins": 0, "losses": 0, "total_trades": 0, "last_signal": ""}

def save_balance(data):
  save_json_atomically(BALANCE_FILE, data)

def run_single_check():
  now_utc = datetime.now(timezone.utc)
  try:
    dt_london = now_utc.astimezone(ZoneInfo("Europe/London"))
  except Exception:
    dt_london = now_utc

  is_weekend = now_utc.weekday() >= 5
  is_london_hours = (6 <= dt_london.hour <= 18)
  can_take_new_trade = (not is_weekend) and is_london_hours

  if is_weekend:
    log_message("Weekend detected - No NEW trades, but still managing OPEN trades.")
  elif not is_london_hours:
    log_message(f"Outside London hours ({dt_london.strftime('%H:%M')} London) - No NEW trades, but still managing OPEN trades.")

  balance_data = load_balance()
  if "last_signal" not in balance_data:
    balance_data["last_signal"] = ""
  if balance_data["balance"] < 20.00:
    log_message(f"CRITICAL: Balance (${balance_data['balance']:.2f}) is below $20.00 safety threshold. Trading halted.")
    return
  timestamps, opens, highs, lows, closes = fetch_yahoo_m5_data()
  if len(closes) < 100:
    log_message("Error: Not enough data fetched to evaluate strategy.")
    return

  # Latest closed candle for managing OPEN trades
  latest_i = len(closes) - 2
  current_high = highs[latest_i]
  current_low = lows[latest_i]
  current_price = closes[latest_i]
  current_lot = get_lot(balance_data["balance"])
  log_message(f"M5 Check @ ${current_price:.2f} | London: {dt_london.strftime('%H:%M')} | Lot: {current_lot:.2f} | Bal: ${balance_data['balance']:.2f} | New: {can_take_new_trade} | Scan-Back: {SCAN_BACK} candles")

  # === 1. MANAGE OPEN TRADES - Use latest price ===
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

  # === 2. NEW TRADES - SCAN-BACK LAST 6 CANDLES! ===
  if not updated_active and can_take_new_trade:
    found_trade = None
    found_info = ""
    # Start from newest, go backwards - newest setup first!
    for scan_i in range(latest_i, max(latest_i - SCAN_BACK, 50), -1):
      c1_open, c1_high, c1_low, c1_close = opens[scan_i - 2], highs[scan_i - 2], lows[scan_i - 2], closes[scan_i - 2]
      c2_open, c2_high, c2_low, c2_close = opens[scan_i - 1], highs[scan_i - 1], lows[scan_i - 1], closes[scan_i - 1]
      c3_open, c3_high, c3_low, c3_close = opens[scan_i], highs[scan_i], lows[scan_i], closes[scan_i]
      price_at_signal = closes[scan_i]
      avg50 = sum(closes[scan_i - 50 : scan_i]) / 50
      trend_is_bullish = price_at_signal > avg50
      trend_is_bearish = price_at_signal < avg50
      ref_level = (c1_high + c1_low) / 2
      side = None
      entry_price, tp_price, sl_price, risk = None, None, None, None
      c1_is_bearish = c1_close < c1_open
      c1_is_bullish = c1_close > c1_open
      c2_is_bullish = c2_close > c2_open
      c2_is_bearish = c2_close < c2_open

      # BUY: ONLY in UPTREND!
      if trend_is_bullish and c1_is_bearish and c2_is_bullish and c2_close > c1_close:
        if c3_low <= ref_level <= c3_high:
          side = "BUY"
          entry_price = ref_level
          sl_price = c1_low - SL_OFFSET
          risk = entry_price - sl_price
          tp_price = entry_price + (risk * TP_MULTIPLIER)
      # SELL: ONLY in DOWNTREND!
      elif trend_is_bearish and c1_is_bullish and c2_is_bearish and c2_close < c1_close:
        if c3_low <= ref_level <= c3_high:
          side = "SELL"
          entry_price = ref_level
          sl_price = c1_high + SL_OFFSET
          risk = sl_price - entry_price
          tp_price = entry_price - (risk * TP_MULTIPLIER)

      if side and entry_price is not None:
        # Prevent taking SAME setup twice!
        candle_time = datetime.fromtimestamp(timestamps[scan_i], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        signal_key = f"{candle_time}_{side}_{entry_price:.2f}"
        if signal_key == balance_data.get("last_signal", ""):
          log_message(f"SKIP duplicate signal already taken: {signal_key}")
          continue
        if risk is not None and risk > MAX_RISK:
          log_message(f"SCAN-BACK [{candle_time}] SKIP {side}: Risk ${risk:.2f} > ${MAX_RISK:.2f} - C1 too big!")
          continue
        # Found valid fresh setup!
        found_trade = {"side": side, "entry": entry_price, "tp": tp_price, "sl": sl_price, "lot": current_lot}
        found_info = f"SCAN-BACK [{candle_time}] NEW SETUP: {side} {current_lot:.2f}lot @ Entry ${entry_price:.2f} (TP: ${tp_price:.2f} | SL: ${sl_price:.2f} | Risk: ${risk:.2f}) | Trend {'Bull' if trend_is_bullish else 'Bear'} 50-MA ${avg50:.2f}"
        balance_data["last_signal"] = signal_key
        break

    if found_trade:
      updated_active.append(found_trade)
      log_message(found_info)
    else:
      log_message(f"No setup in last {SCAN_BACK} candles - Trend + Half not matched")
  elif not updated_active and not can_take_new_trade:
    log_message("Skipping NEW setup - Outside London hours / Weekend.")

  save_active_trades(updated_active)
  save_balance(balance_data)
  log_message(f"Saved: Balance ${balance_data['balance']:.2f} | Active Trades: {len(updated_active)}")

if __name__ == "__main__":
  run_single_check()
