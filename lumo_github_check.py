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
  # Reverted: Your original step-based lot rule ($0.02 per $100)
  lot = int(balance // 100) * 0.02
  if lot < 0.02: lot = 0.02
  if lot > 2.0: lot = 2.0  # safety cap
  return lot

def fetch_hourly_gold_data():
  # Reverted: Using GC=F (Gold Futures) with the -26 offset
  url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=5d&interval=1h"
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
      return [], []
      
    indicators = result[0].get("indicators", {})
    closes = indicators.get("quote", [{}])[0].get("close", [])
    timestamps = result[0].get("timestamp", [])
    
    cleaned = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
    if not cleaned:
      return [], []
      
    # Apply static offset for Gold futures to spot estimation (-26)
    cleaned = [(ts, c - 26) for ts, c in cleaned]
    return zip(*cleaned)
  except Exception as e:
    log_message(f"Error fetching data: {e}")
    return [], []

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

  timestamps, closes = fetch_hourly_gold_data()
  timestamps = list(timestamps)
  closes = list(closes)
  
  if len(closes) < 55:
    log_message("Not enough candles available.")
    return

  i = len(closes) - 1
  now = closes[i]
  before = closes[i - 5]
  avg50 = sum(closes[i - 50 : i]) / 50
  drop = (now - before) / before * 100
  
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

  # Max Daily Loss Circuit Breaker (Stop trading if 2 losses hit today)
  if balance_data.get("daily_losses", 0) >= 2:
    log_message("🛑 MAX DAILY LOSS REACHED: 2 losses recorded today. Bot is resting until tomorrow.")
    save_balance(balance_data)
    return

  current_lot = get_lot(balance_data["balance"])
  log_message(f"Check @ ${now:.2f} | London Time: {dt_london.strftime('%H:%M')} | 50-MA: ${avg50:.2f} | Chg: {drop:+.2f}% | Lot: {current_lot:.2f} | Bal: ${balance_data['balance']:.2f}")

  active_trades = load_active_trades()
  updated_active = []

  for trade in active_trades:
    trade["candles_held"] += 1
    side = trade["side"]
    entry_price = trade["entry_price"]
    t_lot = trade.get("lot", 0.02)
    
    pnl = (now - entry_price) if side == "BUY" else (entry_price - now)
    tp_hit = pnl >= 6.0
    sl_hit = pnl <= -2.0
    
    if tp_hit or sl_hit:
      if tp_hit: pnl = 6.0
      if sl_hit: pnl = -2.0
      
      net_pnl = (pnl * (t_lot / BASE_LOT)) - SPREAD_COST
      balance_data["balance"] += net_pnl
      balance_data["total_trades"] += 1
      
      if net_pnl > 0:
        balance_data["wins"] += 1
        log_message(f"✅ WIN CLOSED #{trade['id']}: {side} {t_lot:.2f}lot @ ${entry_price:.2f} -> ${now:.2f} | Net: ${net_pnl:+.2f} | Bal: ${balance_data['balance']:.2f}")
      else:
        balance_data["losses"] += 1
        balance_data["daily_losses"] = balance_data.get("daily_losses", 0) + 1
        log_message(f"❌ LOSS CLOSED #{trade['id']}: {side} {t_lot:.2f}lot @ ${entry_price:.2f} -> ${now:.2f} | Net: ${net_pnl:+.2f} | Bal: ${balance_data['balance']:.2f}")
    else:
      updated_active.append(trade)
      log_message(f"⏳ HOLDING #{trade['id']}: {side} {t_lot:.2f}lot @ ${entry_price:.2f} | PnL: ${pnl:+.2f} | Held: {trade['candles_held']}h")

  # PROTECTION: Max 1 active trade rule + Cooldown check against recent entries
  if is_london and len(updated_active) < 1:
    side = None
    if now > avg50 and drop < -0.30:
      side = "BUY"
    elif now < avg50 and drop > 0.30:
      side = "SELL"
      
    if side:
      recent_entry_exists = any((timestamps[i] - t["entry_timestamp"]) < 18000 for t in active_trades + updated_active)
      
      if not recent_entry_exists:
        new_trade = {
            "id": int(timestamps[i]),
            "side": side,
            "entry_price": now,
            "entry_timestamp": timestamps[i],
            "candles_held": 0,
            "lot": current_lot
        }
        updated_active.append(new_trade)
        log_message(f"🚀 NEW SIGNAL: {side} {current_lot:.2f}lot @ ${now:.2f} | Bal ${balance_data['balance']:.2f}")

  save_active_trades(updated_active)
  save_balance(balance_data)

if __name__ == "__main__":
  run_single_check()
