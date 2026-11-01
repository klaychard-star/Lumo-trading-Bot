from datetime import datetime, timezone
import json
import os
import requests

LOG_FILE = "lumo_live_log.txt"
STATE_FILE = "lumo_active_trades.json"
SPREAD_COST = 0.60


def log_message(text):
  timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
  formatted_text = f"[{timestamp}] {text}"
  print(formatted_text)
  with open(LOG_FILE, "a") as f:
    f.write(formatted_text + "\n")


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
    cleaned = [(ts, c - 26) for ts, c in cleaned]  # GC=F $3898 -26 = $3872 spot = your MT5!
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


def run_single_check():
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

  dt = datetime.fromtimestamp(timestamps[i], tz=timezone.utc)
  is_london = 6 <= dt.hour <= 18

  log_message(
      f"Check @ ${now:.2f} | 50-MA: ${avg50:.2f} | 5-Candle Chg: {drop:+.2f}%"
  )

  active_trades = load_active_trades()
  updated_active = []

  # Update existing active positions
  for trade in active_trades:
    trade["candles_held"] += 1
    if trade["candles_held"] >= 5:
      exit_price = now
      side = trade["side"]
      entry_price = trade["entry_price"]
      pnl = (
          (exit_price - entry_price)
          if side == "BUY"
          else (entry_price - exit_price)
      )

      if pnl > 6:
        pnl = 6
      if pnl < -2:
        pnl = -2
      net_pnl = pnl - SPREAD_COST

      log_message(
          f"🔔 CLOSED TRADE #{trade['id']}: {side} @ ${entry_price:.2f} | Exit:"
          f" ${exit_price:.2f} | Net PnL: ${net_pnl:+.2f}"
      )
    else:
      updated_active.append(trade)

  # Evaluate new signals
  if is_london:
    side = None
    if now > avg50 and drop < -0.30:
      side = "BUY"
    elif now < avg50 and drop > 0.30:
      side = "SELL"

    if side:
      already_entered = any(
          t["entry_timestamp"] == timestamps[i] for t in active_trades
      )
      if not already_entered:
        new_trade = {
            "id": int(timestamps[i]),
            "side": side,
            "entry_price": now,
            "entry_timestamp": timestamps[i],
            "candles_held": 0,
        }
        updated_active.append(new_trade)
        log_message(f"🚀 NEW SIGNAL GENERATED: {side} @ ${now:.2f}")

  save_active_trades(updated_active)


if __name__ == "__main__":
  run_single_check()
