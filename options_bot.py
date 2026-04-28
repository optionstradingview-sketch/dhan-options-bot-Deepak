"""
DHAN NIFTY OPTIONS AUTO TRADING BOT v2
TradingView Webhook -> Dhan API -> Auto Trade
"""

from flask import Flask, request, jsonify
import requests
import logging
from datetime import datetime, timedelta, timezone

# ─────────────────────────────────────────────
#   SETTINGS
# ─────────────────────────────────────────────

CLIENT_ID    = "1102522136"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc3NDM1ODE1LCJpYXQiOjE3NzczNDk0MTUsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAyNTIyMTM2In0.Lgpo4Db-4t2sktFuCakxUEohb0assYBx-m66dldHDNVcTKmoTIEXRmJh1hG-NCADlqvJPGe4qs-9XHSrEooTfg"

LOT_SIZE        = 65
LOTS            = 1
QUANTITY        = LOT_SIZE * LOTS
STRIKE_MULTIPLE = 50
WEBHOOK_SECRET  = "mywebhook2024secret"
MAX_TRADES      = 5

# ─────────────────────────────────────────────
#   LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger()

# ─────────────────────────────────────────────
#   STATE
# ─────────────────────────────────────────────

class State:
    position    = None
    entry_price = 0
    security_id = None
    trades      = 0
    pnl         = 0

state = State()

# ─────────────────────────────────────────────
#   HELPERS
# ─────────────────────────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))

def ist_now():
    return datetime.now(IST)

def is_market_open():
    now = ist_now()
    if now.weekday() > 4:
        return False
    o = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    c = now.replace(hour=15, minute=20, second=0, microsecond=0)
    return o <= now <= c

def get_atm_strike(ltp):
    return round(ltp / STRIKE_MULTIPLE) * STRIKE_MULTIPLE

def get_weekly_expiry():
    now   = ist_now()
    days  = (3 - now.weekday()) % 7
    if days == 0 and now.hour >= 15:
        days = 7
    expiry = now + timedelta(days=days)
    # Dhan format: 2026-04-30
    return expiry.strftime("%Y-%m-%d")

def dhan_headers():
    return {
        "access-token": ACCESS_TOKEN,
        "client-id":    CLIENT_ID,
        "Content-Type": "application/json"
    }

# ─────────────────────────────────────────────
#   OPTION CHAIN — Security ID fetch
# ─────────────────────────────────────────────

def get_option_security_id(strike, option_type, expiry):
    url = "https://api.dhan.co/v2/optionchain"
    payload = {
        "UnderlyingScrip": 13,
        "UnderlyingSeg":   "IDX_I",
        "Expiry":          expiry
    }
    try:
        r = requests.post(url, headers=dhan_headers(), json=payload, timeout=10)
        data = r.json()
        log.info(f"Option chain response keys: {list(data.keys())}")

        # Parse option chain
        chains = data.get("data", {})
        if isinstance(chains, list):
            items = chains
        elif isinstance(chains, dict):
            items = chains.get("oc", chains.get("optionChain", []))
        else:
            items = []

        for item in items:
            sp = item.get("strikePrice", item.get("strike_price", 0))
            ot = item.get("optionType",  item.get("option_type", ""))
            if int(sp) == int(strike) and ot.upper() == option_type.upper():
                sid    = item.get("securityId", item.get("security_id"))
                symbol = item.get("tradingSymbol", item.get("trading_symbol", ""))
                log.info(f"Found: {symbol} | securityId: {sid}")
                return str(sid), symbol

        log.error(f"Option not found: {strike}{option_type} {expiry}. Raw: {data}")
        return None, None
    except Exception as e:
        log.error(f"Option chain error: {e}")
        return None, None

# ─────────────────────────────────────────────
#   ORDER
# ─────────────────────────────────────────────

def place_order(security_id, txn_type, symbol):
    url = "https://api.dhan.co/v2/orders"
    payload = {
        "dhanClientId":    CLIENT_ID,
        "transactionType": txn_type,
        "exchangeSegment": "NSE_FNO",
        "productType":     "INTRADAY",
        "orderType":       "MARKET",
        "validity":        "DAY",
        "securityId":      security_id,
        "quantity":        QUANTITY,
        "price":           0,
        "triggerPrice":    0
    }
    try:
        r    = requests.post(url, headers=dhan_headers(), json=payload, timeout=10)
        data = r.json()
        log.info(f"Order response: {data}")
        oid  = data.get("orderId", data.get("data", {}).get("orderId"))
        log.info(f"✅ Order placed! {txn_type} {symbol} | ID: {oid}")
        return oid
    except Exception as e:
        log.error(f"Order error: {e}")
        return None

# ─────────────────────────────────────────────
#   CLOSE POSITION
# ─────────────────────────────────────────────

def close_position():
    if not state.position or not state.security_id:
        log.info("No open position to close")
        return
    log.info(f"Closing {state.position} position...")
    place_order(state.security_id, "SELL", state.position)
    state.position    = None
    state.entry_price = 0
    state.security_id = None

# ─────────────────────────────────────────────
#   EXECUTE BUY (CE)
# ─────────────────────────────────────────────

def execute_buy(nifty_price):
    if state.trades >= MAX_TRADES:
        return {"status": "rejected", "reason": "max trades reached"}
    if not is_market_open():
        return {"status": "rejected", "reason": "market closed"}
    if state.position:
        close_position()

    strike = get_atm_strike(nifty_price)
    expiry = get_weekly_expiry()
    log.info(f"BUY CE | Nifty: {nifty_price} | Strike: {strike} | Expiry: {expiry}")

    sid, symbol = get_option_security_id(strike, "CE", expiry)
    if not sid:
        return {"status": "error", "reason": "CE option not found"}

    oid = place_order(sid, "BUY", symbol)
    if not oid:
        return {"status": "error", "reason": "Order failed"}

    state.position    = "CE"
    state.security_id = sid
    state.trades     += 1
    log.info(f"🟢 CE BOUGHT | {symbol} | Strike: {strike}")
    return {"status": "success", "action": "CE bought", "strike": strike, "symbol": symbol}

# ─────────────────────────────────────────────
#   EXECUTE SELL (PE)
# ─────────────────────────────────────────────

def execute_sell(nifty_price):
    if state.trades >= MAX_TRADES:
        return {"status": "rejected", "reason": "max trades reached"}
    if not is_market_open():
        return {"status": "rejected", "reason": "market closed"}
    if state.position:
        close_position()

    strike = get_atm_strike(nifty_price)
    expiry = get_weekly_expiry()
    log.info(f"BUY PE | Nifty: {nifty_price} | Strike: {strike} | Expiry: {expiry}")

    sid, symbol = get_option_security_id(strike, "PE", expiry)
    if not sid:
        return {"status": "error", "reason": "PE option not found"}

    oid = place_order(sid, "BUY", symbol)
    if not oid:
        return {"status": "error", "reason": "Order failed"}

    state.position    = "PE"
    state.security_id = sid
    state.trades     += 1
    log.info(f"🔴 PE BOUGHT | {symbol} | Strike: {strike}")
    return {"status": "success", "action": "PE bought", "strike": strike, "symbol": symbol}

# ─────────────────────────────────────────────
#   FLASK APP
# ─────────────────────────────────────────────

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data   = request.get_json(force=True)
        log.info(f"📡 Webhook: {data}")

        if data.get("secret") != WEBHOOK_SECRET:
            return jsonify({"status": "unauthorized"}), 401

        signal = data.get("signal", "").upper()
        price  = data.get("price")
        try:
            price = float(price) if price else None
        except:
            price = None

        if signal == "BUY":
            if not price:
                return jsonify({"status": "error", "reason": "price missing"}), 400
            result = execute_buy(price)
        elif signal == "SELL":
            if not price:
                return jsonify({"status": "error", "reason": "price missing"}), 400
            result = execute_sell(price)
        elif signal == "EXIT":
            close_position()
            result = {"status": "success", "action": "closed"}
        else:
            result = {"status": "error", "reason": f"unknown signal: {signal}"}

        return jsonify(result)
    except Exception as e:
        log.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "reason": str(e)}), 500

@app.route("/status")
def status():
    return jsonify({
        "status":      "running",
        "position":    state.position,
        "trades":      state.trades,
        "pnl":         state.pnl,
        "market_open": is_market_open(),
        "time_ist":    ist_now().strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route("/")
def home():
    return jsonify({"message": "Dhan Options Bot v2 Running! 🚀"})

if __name__ == "__main__":
    log.info("=" * 50)
    log.info("  DHAN OPTIONS BOT v2 — STARTING")
    log.info(f"  Client ID: {CLIENT_ID}")
    log.info(f"  Quantity:  {QUANTITY} | Lot Size: {LOT_SIZE}")
    log.info("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
