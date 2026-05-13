"""
DHAN NIFTY OPTIONS AUTO TRADING BOT v3
TradingView -> Dhan API -> Auto Trade
"""

from flask import Flask, request, jsonify
import requests
import logging
import os
from datetime import datetime, timedelta, timezone

# ── SETTINGS ──────────────────────────────────────
CLIENT_ID    = "1102522136"
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
LOT_SIZE     = 65
QUANTITY     = LOT_SIZE
SECRET       = "mywebhook2024secret"
MAX_TRADES   = 5
STRIKE_STEP  = 50
# ──────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger()

IST = timezone(timedelta(hours=5, minutes=30))

class State:
    position    = None
    security_id = None
    trades      = 0

state = State()

def now_ist():
    return datetime.now(IST)

def is_market_open():
    n = now_ist()
    if n.weekday() > 4: return False
    o = n.replace(hour=9,  minute=15, second=0, microsecond=0)
    c = n.replace(hour=15, minute=20, second=0, microsecond=0)
    return o <= n <= c

def get_my_ip():
    try:
        r = requests.get("https://api.ipify.org?format=json", timeout=5)
        ip = r.json().get("ip")
        log.info(f"🌐 My Public IP: {ip}")
        return ip
    except Exception as e:
        log.error(f"IP check error: {e}")
        return None

def update_ip_on_dhan():
    """Automatically update current IP on Dhan portal"""
    try:
        ip = get_my_ip()
        if not ip:
            return False
        r = requests.post(
            "https://api.dhan.co/v2/profile/ipwhitelist",
            headers=headers(),
            json={"ipAddress": ip},
            timeout=10
        )
        log.info(f"🔄 IP update response: {r.status_code} | {r.text[:100]}")
        return True
    except Exception as e:
        log.error(f"IP update error: {e}")
        return False

def atm(price):
    return round(price / STRIKE_STEP) * STRIKE_STEP

def headers():
    return {
        "access-token": ACCESS_TOKEN,
        "client-id":    CLIENT_ID,
        "Content-Type": "application/json"
    }

def get_expiry():
    try:
        r = requests.post(
            "https://api.dhan.co/v2/optionchain/expirylist",
            headers=headers(),
            json={"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"},
            timeout=10
        )
        data = r.json()
        expiries = data.get("data", [])
        log.info(f"Expiries: {expiries[:3]}")
        if expiries:
            return expiries[0]
    except Exception as e:
        log.error(f"Expiry error: {e}")
    n = now_ist()
    d = (3 - n.weekday()) % 7 or 7
    return (n + timedelta(days=d)).strftime("%Y-%m-%d")

def search_security(strike, opt_type, expiry):
    try:
        r = requests.post(
            "https://api.dhan.co/v2/optionchain",
            headers=headers(),
            json={
                "UnderlyingScrip": 13,
                "UnderlyingSeg": "IDX_I",
                "Expiry": expiry
            },
            timeout=10
        )
        data = r.json()
        log.info(f"OC response: {str(data)[:300]}")

        oc_data = data.get("data", {})
        oc      = oc_data.get("oc", {})

        log.info(f"OC type: {type(oc)} | keys count: {len(oc)}")

        for sp_key, opt_data in oc.items():
            try:
                if int(float(sp_key)) != int(strike):
                    continue
            except:
                continue

            opt = opt_data.get(opt_type.lower(), {})
            if not isinstance(opt, dict):
                continue

            sid = str(opt.get("security_id", opt.get("securityId", "")))
            sym = opt.get("trading_symbol", opt.get("tradingSymbol", f"NIFTY{strike}{opt_type}"))
            if sid:
                log.info(f"✅ Found: {sym} | sid={sid}")
                return sid, sym

        log.error(f"Not found: {strike}{opt_type} {expiry}")
        return None, None

    except Exception as e:
        log.error(f"Search error: {e}")
        return None, None

def place_order(sid, txn, symbol):
    import time
    try:
        payload = {
            "dhanClientId":    CLIENT_ID,
            "transactionType": txn,
            "exchangeSegment": "NSE_FNO",
            "productType":     "INTRADAY",
            "orderType":       "MARKET",
            "validity":        "DAY",
            "securityId":      sid,
            "quantity":        QUANTITY,
            "price":           0,
            "triggerPrice":    0
        }
        r = requests.post(
            "https://api.dhan.co/v2/orders",
            headers=headers(),
            json=payload,
            timeout=10
        )
        data = r.json()
        log.info(f"Order response: {data}")

        # If Invalid IP error - auto update IP and retry once
        if data.get("errorCode") == "DH-905":
            log.warning("⚠️ Invalid IP - auto updating IP on Dhan and retrying...")
            update_ip_on_dhan()
            time.sleep(3)
            r2 = requests.post(
                "https://api.dhan.co/v2/orders",
                headers=headers(),
                json=payload,
                timeout=10
            )
            data = r2.json()
            log.info(f"Retry order response: {data}")

        oid = data.get("orderId") or data.get("data", {}).get("orderId")
        log.info(f"✅ {txn} {symbol} | orderId: {oid}")
        return oid
    except Exception as e:
        log.error(f"Order error: {e}")
        return None

def close_pos():
    if not state.position or not state.security_id:
        log.info("No position to close")
        return
    place_order(state.security_id, "SELL", state.position)
    state.position = state.security_id = None

def buy_ce(price):
    if state.trades >= MAX_TRADES: return {"status": "rejected", "reason": "max trades"}
    if not is_market_open():       return {"status": "rejected", "reason": "market closed"}
    if state.position: close_pos()
    strike = atm(price)
    expiry = get_expiry()
    log.info(f"BUY CE | price={price} strike={strike} expiry={expiry}")
    sid, sym = search_security(strike, "CE", expiry)
    if not sid: return {"status": "error", "reason": "CE not found"}
    oid = place_order(sid, "BUY", sym)
    if not oid: return {"status": "error", "reason": "order failed"}
    state.position = "CE"; state.security_id = sid; state.trades += 1
    return {"status": "success", "action": "CE bought", "strike": strike, "symbol": sym}

def buy_pe(price):
    if state.trades >= MAX_TRADES: return {"status": "rejected", "reason": "max trades"}
    if not is_market_open():       return {"status": "rejected", "reason": "market closed"}
    if state.position: close_pos()
    strike = atm(price)
    expiry = get_expiry()
    log.info(f"BUY PE | price={price} strike={strike} expiry={expiry}")
    sid, sym = search_security(strike, "PE", expiry)
    if not sid: return {"status": "error", "reason": "PE not found"}
    oid = place_order(sid, "BUY", sym)
    if not oid: return {"status": "error", "reason": "order failed"}
    state.position = "PE"; state.security_id = sid; state.trades += 1
    return {"status": "success", "action": "PE bought", "strike": strike, "symbol": sym}

app = Flask(__name__)

# ── Startup: log IP and try to update on Dhan ──
get_my_ip()
update_ip_on_dhan()

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data   = request.get_json(force=True)
        log.info(f"📡 {data}")
        if data.get("secret") != SECRET:
            return jsonify({"status": "unauthorized"}), 401
        signal = data.get("signal", "").upper()
        price  = float(data.get("price", 0) or 0)
        if signal == "BUY":
            if not price: return jsonify({"status": "error", "reason": "price missing"}), 400
            result = buy_ce(price)
        elif signal == "SELL":
            if not price: return jsonify({"status": "error", "reason": "price missing"}), 400
            result = buy_pe(price)
        elif signal == "EXIT":
            close_pos()
            result = {"status": "success", "action": "closed"}
        else:
            result = {"status": "error", "reason": f"unknown: {signal}"}
        return jsonify(result)
    except Exception as e:
        log.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "reason": str(e)}), 500

@app.route("/status")
def status():
    return jsonify({
        "running":     True,
        "position":    state.position,
        "trades":      state.trades,
        "market_open": is_market_open(),
        "time_ist":    now_ist().strftime("%H:%M:%S")
    })

@app.route("/ip")
def show_ip():
    ip = get_my_ip()
    return jsonify({"public_ip": ip})

@app.route("/")
def home():
    return "Dhan Bot v3 Running!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
