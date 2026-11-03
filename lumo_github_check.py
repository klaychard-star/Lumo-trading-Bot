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
BASE_LOT = 0.01  # $1 per $1 baseline

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
  if lot < 0.02: lot = 0.02
  if lot > 2.0: lot = 2.0  # safety cap
  return lot

def fetch_m5_gold_data():
  """Fetches M5 (5-minute) Gold Futures data from Yahoo Finance and applies the -26 spot offset."""
  url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=1d&interval=5m"
  headers = {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
  }
  try:
    response = requests.get(url, headers=headers, timeout=10)
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
    
    # Filter out None values across all arrays synchronously
    valid_data = [
        (ts, o, h, l, c) for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes)
        if o is not None and h is not None and l is not None and c is not None
    ]
    
    if not valid_data:
      return [], [], [], [], []
      
    timestamps, opens, highs, lows, closes = zip(*valid_data)
    
    # Apply static offset for Gold futures to spot estimation (-26)
    opens = [o - 26 for o in opens]
    highs = [h - 26 for h in highs]
    lows = [l - 26 for l in lows]
    closes = [c - 26 for c in closes]
    
    return list(timestamps), list(opens), list(highs), list(lows), list(closes)
  except Exception as e:
    log_message(f"Error fetching M5 data: {e}")
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
  return {
      "balance": 100.00,
      "wins": 0,
      "losses": 0,
      "total_trades": 0,
      "daily_losses": 0,
      "last_loss_date": ""
  }

def save_balance(data):
  save_json_atomically(BALANCE_FILE, data)

def run_single_check():
  balance_data = load_balance()
  
  # Safety Guard: Stop trading if balance falls below $20
  if balance_data["balance"] < 20.00:
    log_message(f"🚨 CRITICAL: Balance (${balance_data['balance']:.2f}) is below $20.00 safety threshold. Trading halted.")
    return

  timestamps, opens, highs, lows, closes = fetch_m5_gold_data()
  
  if len(closes) < 60:
    log_message("Not enough M5 candles available.")
    return

  # Use index of latest completed candle (-2) to avoid repainting, while -1 is current live candle
  i = len(closes) - 2
  current_price = closes[-1]
  
  # 50-period Moving Average on M5 for Trend Filter
  avg50 = sum(closes[i-50:i]) / 50
  trend_is_bullish = closes[i] > avg50
  trend_is_bearish = closes[i] < avg50

  # Extract the 3 completed candles for the pattern: c1 (impetus), c2 (pullback), c3 (tap/execution)
  c1_open, c1_high, c1_low, c1_close = opens[i-2], highs[i-2], lows[i-2], closes[i-2]
  c2_open, c2_high, c2_low, c2_close = opens[i-1], highs[i-1], lows[i-1], closes[i-1]
  c3_open, c3_high, c3_low, c3_close = opens[i], highs[i], lows[i], closes[i]

  # Accurate London Time checking handling DST automatically via zoneinfo
  dt_utc = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
  try:
    dt_london = dt_utc.astimezone(ZoneInfo("Europe/London"))
  except Exception:
    dt_london = dt_utc
  is_london = 6 <= dt_london.hour <= 18

  # Weekend Protection (Block trading on Saturday and Sunday)
  is_weekend = dt_utc.weekday() >= 5
  if is_weekend:
    log_message("🛡️ Weekend detected. Market closed or stale data — skipping check.")
    return

  # Reset daily loss counter if it's a new day
  current_date_str = dt_utc.strftime("%Y-%m-%d")
  if balance_data.get("last_loss_date") != current_date_str:
    balance_data["daily_losses"] = 0
    balance_data["last_loss_date"] = current_date_str

  # Max Daily Loss Circuit Breaker
  if balance_data.get("daily_losses", 0) >= 2:
    log_message("🛑 MAX DAILY LOSS REACHED: 2 losses recorded today. Bot is resting until tomorrow.")
    save_balance(balance_data)
    return

  current_lot = get_lot(balance_data["balance"])
  log_message(f"M5 Check @ ${current_price:.2f} | London Time: {dt_london.strftime('%H:%M')} | 50-MA: ${avg50:.2f} | Lot: {current_lot:.2f} | Bal: ${balance_data['balance']:.2f}")

  active_trades = load_active_trades()
  updated_active = []

  # Manage active trades using dynamic TP and SL targets set upon entry
  for trade in active_trades:
    trade["candles_held"] += 1
    side = trade["side"]
    entry_price = trade["entry_price"]
    t_lot = trade.get("lot", 0.02)
    tp_price = trade["tp_price"]
    sl_price = trade["sl_price"]
    
    pnl = (current_price - entry_price) if side == "BUY" else (entry_price - current_price)
    
    tp_hit = (current_price >= tp_price) if side == "BUY" else (current_price <= tp_price)
    sl_hit = (current_price <= sl_price) if side == "BUY" else (current_price >= sl_price)
    
    if tp_hit or sl_hit:
      if tp_hit:
        realized_pnl_pips = abs(tp_price - entry_price)
      else:
        realized_pnl_pips = -abs(entry_price - sl_price)
        
      net_pnl = (realized_pnl_pips * (t_lot / BASE_LOT)) - SPREAD_COST
      balance_data["balance"] += net_pnl
      balance_data["total_trades"] += 1
      
      if tp_hit:
        balance_data["wins"] += 1
        log_message(f"✅ WIN CLOSED #{trade['id']}: {side} {t_lot:.2f}lot @ ${entry_price:.2f} -> TP hit @ ${current_price:.2f} | Net: ${net_pnl:+.2f} | Bal: ${balance_data['balance']:.2f}")
      else:
        balance_data["losses"] += 1
        balance_data["daily_losses"] = balance_data.get("daily_losses", 0) + 1
        log_message(f"❌ LOSS CLOSED #{trade['id']}: {side} {t_lot:.2f}lot @ ${entry_price:.2f} -> SL hit @ ${current_price:.2f} | Net: ${net_pnl:+.2f} | Bal: ${balance_data['balance']:.2f}")
    else:
      updated_active.append(trade)
      log_message(f"⏳ HOLDING #{trade['id']}: {side} {t_lot:.2f}lot @ ${entry_price:.2f} | PnL Pips: ${pnl:+.2f} | Held: {trade['candles_held']} M5s")

  # Strategy Execution: 3-Candle Pullback Pattern
  if is_london and len(updated_active) < 1:
    side = None
    ref_level = c1_close
    c1_size = c1_high - c1_low
    
    # --- BULLISH SETUP ---
    if trend_is_bullish:
      c2_body_min = min(c2_open, c2_close)
      c2_body_max = max(c2_open, c2_close)
      body_above_line = c2_body_min >= ref_level or (c2_body_min <= ref_level and c2_body_max >= ref_level)
      
      if body_above_line:
        tapped_level = c3_low <= ref_level <= c3_high
        if tapped_level:
          side = "BUY"
          entry_price = ref_level
          tp_price = entry_price + c1_size
          sl_price = c1_open - (c1_size * 0.5)

    # --- BEARISH SETUP ---
    elif trend_is_bearish:
      c2_body_min = min(c2_open, c2_close)
      c2_body_max = max(c2_open, c2_close)
      body_below_line = c2_body_max <= ref_level or (c2_body_min <= ref_level and c2_body_max >= ref_level)
      
      if body_below_line:
        tapped_level = c3_low <= ref_level <= c3_high
        if tapped_level:
          side = "SELL"
          entry_price = ref_level
          tp_price = entry_price - c1_size
          sl_price = c1_open + (c1_size * 0.5)

    if side:
      recent_entry_exists = any((timestamps[i] - t["entry_timestamp"]) < 900 for t in active_trades + updated_active)
      
      if not recent_entry_exists:
        new_trade = {
            "id": int(timestamps[i]),
            "side": side,
            "entry_price": entry_price,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "entry_timestamp": timestamps[i],
            "candles_held": 0,
            "lot": current_lot
        }
        updated_active.append(new_trade)
        log_message(f"🚀 NEW 3-CANDLE SETUP: {side} {current_lot:.2f}lot @ Entry ${entry_price:.2f} (TP: ${tp_price:.2f} | SL: ${sl_price:.2f}) | Bal ${balance_data['balance']:.2f}")

  save_active_trades(updated_active)
  save_balance(balance_data)

if __name__ == "__main__":
  run_single_check()
