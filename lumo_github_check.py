from datetime import datetime, timezone
import json
import os
import requests

LOG_FILE = "lumo_live_log.txt"
STATE_FILE = "lumo_active_trades.json"
BALANCE_FILE = "lumo_balance.json"
SPREAD_COST = 0.60
BASE_LOT = 0.01  # $1 per $1

def log_message(text):
  timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
  formatted_text = f"[{timestamp}] {text}"
  print(formatted_text)
  with open(LOG_FILE, "a") as f:
    f.write(formatted_text + "\n")

def get_lot(balance):
  # YOUR NEW RULE: 0.02 per $100
  lot = int(balance // 100) * 0.02
  if lot < 0.02: lot = 0.02
  if lot > 2.0: lot = 2.0  # safety cap
  return lot

def fetch_hourly_gold_data():
  url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?range=5d&interval=1h"
  headers = {"User-Agent": "Mozilla/5.0"}
  try:
    response = requests.get(url, headers=headers, timeout=10)
    data = response.json()
    result = data["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    timestamps = result["timestamp"]
    cleaned = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]
    cleaned = [(ts, c - 26) for ts, c in cleaned]
    return zip(*cleaned)
  except Exception as e:
    log_message(f"Error fetching data: {e}")
    return [], []

def load_active_trades():
  if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r") as f:
      return json.load(f)
  return []

def save_active_trades(trades):
  with open(STATE_FILE, "w") as f:
    json.dump(trades, f, indent=2)

def load_balance():
  if os.path.exists(BALANCE_FILE):
    with open(BALANCE_FILE, "r") as f:
      return json.load(f)
  return {"balance": 100.00, "wins": 0, "losses": 0, "total_trades": 0}

def save_balance(data):
  with open(BALANCE_FILE, "w") as f:
    json.dump(data, f, indent=2)

def run_single_check():
  timestamps, closes = fetch_hourly_gold_data()
  timestamps = list(timestamps)
  closes = list(closes)
  if len(closes) < 55:
    log_message("Not enough candles.")
    return

  i = len(closes) - 1
  now = closes[i]
  before = closes[i - 5]
  avg50 = sum(closes[i - 50 : i]) / 50
  drop = (now - before) / before * 100
  dt = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
  is_london = 6 <= dt.hour <= 18

  balance_data = load_balance()
  current_lot = get_lot(balance_data["balance"])  # YOUR RULE

  log_message(f"Check @ ${now:.2f} | 50-MA: ${avg50:.2f} | 5-Candle Chg: {drop:+.2f}% | Lot: {current_lot:.2f} | Bal: ${balance_data['balance']:.2f}")

  active_trades = load_active_trades()
  updated_active = []

  for trade in active_trades:
    trade["candles_held"] += 1
    if trade["candles_held"] >= 5:
      exit_price = now
      side = trade["side"]
      entry_price = trade["entry_price"]
      t_lot = trade.get("lot", 0.02)  # use stored lot
      pnl = (exit_price - entry_price) if side == "BUY" else (entry_price - exit_price)
      if pnl > 6: pnl = 6
      if pnl < -2: pnl = -2
      net_pnl = (pnl - SPREAD_COST) * (t_lot / BASE_LOT)  # COMPOUNDING FIX
      
      balance_data["balance"] += net_pnl
      balance_data["total_trades"] += 1
      if net_pnl > 0:
        balance_data["wins"] += 1
        log_message(f"✅ WIN CLOSED #{trade['id']}: {side} {t_lot:.2f}lot @ ${entry_price:.2f} -> ${exit_price:.2f} | Net: ${net_pnl:+.2f} | Bal: ${balance_data['balance']:.2f}")
      else:
        balance_data["losses"] += 1
        log_message(f"❌ LOSS CLOSED #{trade['id']}: {side} {t_lot:.2f}lot @ ${entry_price:.2f} -> ${exit_price:.2f} | Net: ${net_pnl:+.2f} | Bal: ${balance_data['balance']:.2f}")
    else:
      updated_active.append(trade)

  # PROTECTION: 1 trade max
  if is_london and len(updated_active) < 1:
    side = None
    if now > avg50 and drop < -0.30:
      side = "BUY"
    elif now < avg50 and drop > 0.30:
      side = "SELL"
    if side:
      already_entered = any(t["entry_timestamp"] == timestamps[i] for t in active_trades)
      if not already_entered:
        new_trade = {"id": int(timestamps[i]), "side": side, "entry_price": now, "entry_timestamp": timestamps[i], "candles_held": 0, "lot": current_lot}
        updated_active.append(new_trade)
        log_message(f"🚀 NEW SIGNAL: {side} {current_lot:.2f}lot @ ${now:.2f} | Bal ${balance_data['balance']:.2f}")

  save_active_trades(updated_active)
  save_balance(balance_data)

if __name__ == "__main__":
  run_single_check()
