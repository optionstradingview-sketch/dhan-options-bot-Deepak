"""
=============================================================
  DHAN NIFTY OPTIONS AUTO TRADING BOT
  ScalpMaster Pro Signal Based — Webhook Version
  
  Flow: TradingView Alert → Webhook → Dhan API → Auto Trade
=============================================================
"""

from flask import Flask, request, jsonify
import requests
import json
import logging
from datetime import datetime, timedelta
import math

# ─────────────────────────────────────────────
#   AAPKI SETTINGS
# ─────────────────────────────────────────────

CLIENT_ID    = "1102522136"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc3MzUxODY0LCJpYXQiOjE3NzcyNjU0NjQsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAyNTIyMTM2In0.UuMRgSUx_09XYHOpMcSmDjt8W8M7G9NzZwnmWWElpD-_QcnVMXxcZ62sGHrQ5PNJFuMuZAguo4mNy9q4mSLYzg"

# Options Settings
SYMBOL          = "NIFTY"
LOT_SIZE        = 75          # Nifty options lot size
LOTS            = 1           # Kitne lots
QUANTITY        = LOT_SIZE * LOTS   # = 75
STRIKE_OFFSET   = 0           # 0 = ATM, 1 = 1 OTM, 2 = 2 OTM
STRIKE_MULTIPLE = 50          # Nifty strikes 50-50 pe hote hain

# Risk Management
MAX_TRADES_PER_DAY = 5
STOP_LOSS_PERCENT  = 30       # Option premium ka 30% SL
TARGET_PERCENT     = 50       # Option premium ka 50% target

# Webhook Security
WEBHOOK_SECRET = "mywebhook2024secret"   # Isko secret rakho

# ─────────────────────────────────────────────
#   LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("options_bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger()

# ─────────────────────────────────────────────
#   BOT STATE
# ─────────────────────────────────────────────

class BotState:
    def __init__(self):
        self.position       = None    # "CE" ya "PE"
        self.entry_price    = 0
        self.security_id    = None
        self.trades_today   = 0
        self.pnl_today      = 0
        self.order_id       = None
        self.strike         = 0

state = BotState()

# ─────────────────────────────────────────────
#   DHAN API CLASS
# ─────────────────────────────────────────────

class DhanAPI:
    BASE_URL = "https://api.dhan.co"
    
    def __init__(self, client_id, access_token):
        self.client_id    = client_id
        self.access_token = access_token
        self.headers      = {
            "access-token": access_token,
            "client-id":    client_id,
            "Content-Type": "application/json"
        }
    
    def get_nifty_ltp(self):
        """Nifty spot price fetch karo"""
        url = f"{self.BASE_URL}/v2/marketfeed/ltp"
        payload = {"IDX_I": ["13"]}
        try:
            r = requests.post(url, headers=self.headers, json=payload)
            data = r.json()
            log.info(f"LTP API response: {data}")
            try:
                ltp = data["data"]["IDX_I"]["13"]["last_price"]
            except:
                try:
                    ltp = data["data"]["13"]["last_price"]
                except:
                    log.error(f"LTP parse error, raw: {data}")
                    return None
            return float(ltp)
        except Exception as e:
            log.error(f"LTP fetch error: {e}")
            return None
    
    def get_atm_strike(self, ltp):
        """ATM strike calculate karo"""
        atm = round(ltp / STRIKE_MULTIPLE) * STRIKE_MULTIPLE
        return atm
    
    def get_weekly_expiry(self):
        """Is hafte ka Thursday expiry date lo"""
        today = datetime.now()
        days_to_thursday = (3 - today.weekday()) % 7
        if days_to_thursday == 0 and today.hour >= 15:
            days_to_thursday = 7
        expiry = today + timedelta(days=days_to_thursday)
        return expiry.strftime("%d%b%y").upper()   # e.g. "01MAY25"
    
    def search_option(self, strike, option_type, expiry):
        """Option ka Security ID dhundho"""
        url = f"{self.BASE_URL}/v2/optionchain"
        payload = {
            "UnderlyingScrip": 13,
            "UnderlyingSeg":   "IDX_I",
            "Expiry":          expiry
        }
        try:
            r = requests.post(url, headers=self.headers, json=payload)
            data = r.json()
            
            for option in data.get("data", []):
                if (option["strikePrice"] == strike and 
                    option["optionType"] == option_type):
                    return option["securityId"], option["tradingSymbol"]
            
            log.error(f"Option nahi mila: {strike}{option_type} {expiry}")
            return None, None
        except Exception as e:
            log.error(f"Option search error: {e}")
            return None, None
    
    def place_order(self, security_id, transaction_type, quantity, symbol):
        """Order place karo"""
        url = f"{self.BASE_URL}/v2/orders"
        payload = {
            "dhanClientId":      self.client_id,
            "transactionType":   transaction_type,  # "BUY" ya "SELL"
            "exchangeSegment":   "NSE_FNO",
            "productType":       "INTRADAY",
            "orderType":         "MARKET",
            "validity":          "DAY",
            "securityId":        security_id,
            "quantity":          quantity,
            "price":             0,
            "triggerPrice":      0
        }
        try:
            r = requests.post(url, headers=self.headers, json=payload)
            data = r.json()
            order_id = data.get("orderId")
            log.info(f"✅ Order placed! {transaction_type} {symbol} | Order ID: {order_id}")
            return order_id
        except Exception as e:
            log.error(f"❌ Order failed: {e}")
            return None
    
    def get_option_ltp(self, security_id):
        """Option ka current price lo"""
        url = f"{self.BASE_URL}/v2/marketfeed/ltp"
        payload = {
            "NSE_FNO": [security_id]
        }
        try:
            r = requests.post(url, headers=self.headers, json=payload)
            data = r.json()
            ltp = data["data"]["NSE_FNO"][security_id]["last_price"]
            return float(ltp)
        except Exception as e:
            log.error(f"Option LTP error: {e}")
            return None

# ─────────────────────────────────────────────
#   TRADING LOGIC
# ─────────────────────────────────────────────

dhan = DhanAPI(CLIENT_ID, ACCESS_TOKEN)

def is_market_open():
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    if now.weekday() > 4:
        return False
    open_time  = now.replace(hour=9,  minute=15, second=0)
    close_time = now.replace(hour=15, minute=20, second=0)
    return open_time <= now <= close_time

def execute_buy_signal():
    """BUY signal — CE kharido"""
    if state.trades_today >= MAX_TRADES_PER_DAY:
        log.warning("Max trades reached today!")
        return {"status": "rejected", "reason": "max trades reached"}
    
    if not is_market_open():
        log.warning("Market band hai!")
        return {"status": "rejected", "reason": "market closed"}
    
    # Pehle purani position close karo
    if state.position:
        close_position()
    
    # Nifty LTP lo
    nifty_ltp = dhan.get_nifty_ltp()
    if not nifty_ltp:
        return {"status": "error", "reason": "LTP fetch failed"}
    
    # ATM strike calculate karo
    atm_strike = dhan.get_atm_strike(nifty_ltp)
    expiry     = dhan.get_weekly_expiry()
    
    log.info(f"📊 Nifty: {nifty_ltp} | ATM: {atm_strike} | Expiry: {expiry}")
    
    # CE option dhundho
    security_id, symbol = dhan.search_option(atm_strike, "CE", expiry)
    if not security_id:
        return {"status": "error", "reason": "CE option not found"}
    
    # Buy order place karo
    order_id = dhan.place_order(security_id, "BUY", QUANTITY, symbol)
    if not order_id:
        return {"status": "error", "reason": "Order failed"}
    
    # Option LTP lo
    option_ltp = dhan.get_option_ltp(security_id)
    
    # State update karo
    state.position    = "CE"
    state.entry_price = option_ltp or 0
    state.security_id = security_id
    state.strike      = atm_strike
    state.order_id    = order_id
    state.trades_today += 1
    
    log.info(f"🟢 CE BOUGHT | Strike: {atm_strike} | Premium: {option_ltp} | Symbol: {symbol}")
    return {"status": "success", "side": "BUY CE", "strike": atm_strike, "symbol": symbol}

def execute_sell_signal():
    """SELL signal — PE kharido"""
    if state.trades_today >= MAX_TRADES_PER_DAY:
        log.warning("Max trades reached today!")
        return {"status": "rejected", "reason": "max trades reached"}
    
    if not is_market_open():
        log.warning("Market band hai!")
        return {"status": "rejected", "reason": "market closed"}
    
    # Pehle purani position close karo
    if state.position:
        close_position()
    
    # Nifty LTP lo
    nifty_ltp = dhan.get_nifty_ltp()
    if not nifty_ltp:
        return {"status": "error", "reason": "LTP fetch failed"}
    
    # ATM strike calculate karo
    atm_strike = dhan.get_atm_strike(nifty_ltp)
    expiry     = dhan.get_weekly_expiry()
    
    log.info(f"📊 Nifty: {nifty_ltp} | ATM: {atm_strike} | Expiry: {expiry}")
    
    # PE option dhundho
    security_id, symbol = dhan.search_option(atm_strike, "PE", expiry)
    if not security_id:
        return {"status": "error", "reason": "PE option not found"}
    
    # Buy order place karo
    order_id = dhan.place_order(security_id, "BUY", QUANTITY, symbol)
    if not order_id:
        return {"status": "error", "reason": "Order failed"}
    
    # Option LTP lo
    option_ltp = dhan.get_option_ltp(security_id)
    
    # State update karo
    state.position    = "PE"
    state.entry_price = option_ltp or 0
    state.security_id = security_id
    state.strike      = atm_strike
    state.order_id    = order_id
    state.trades_today += 1
    
    log.info(f"🔴 PE BOUGHT | Strike: {atm_strike} | Premium: {option_ltp} | Symbol: {symbol}")
    return {"status": "success", "side": "BUY PE", "strike": atm_strike, "symbol": symbol}

def close_position():
    """Current position sell karo"""
    if not state.position or not state.security_id:
        return
    
    option_ltp = dhan.get_option_ltp(state.security_id)
    order_id   = dhan.place_order(state.security_id, "SELL", QUANTITY, state.position)
    
    if option_ltp and state.entry_price:
        pnl = (option_ltp - state.entry_price) * QUANTITY
        state.pnl_today += pnl
        log.info(f"💰 Position closed | Entry: {state.entry_price} | Exit: {option_ltp} | P&L: ₹{pnl:.0f}")
    
    state.position    = None
    state.entry_price = 0
    state.security_id = None
    state.order_id    = None
    state.strike      = 0

# ─────────────────────────────────────────────
#   FLASK WEBHOOK SERVER
# ─────────────────────────────────────────────

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    """TradingView se signal receive karo"""
    try:
        data = request.get_json()
        log.info(f"📡 Webhook received: {data}")
        
        # Secret check karo
        if data.get("secret") != WEBHOOK_SECRET:
            log.warning("❌ Invalid secret!")
            return jsonify({"status": "unauthorized"}), 401
        
        signal = data.get("signal", "").upper()
        
        if signal == "BUY":
            result = execute_buy_signal()
        elif signal == "SELL":
            result = execute_sell_signal()
        elif signal == "EXIT":
            close_position()
            result = {"status": "success", "action": "position closed"}
        else:
            result = {"status": "error", "reason": f"Unknown signal: {signal}"}
        
        return jsonify(result)
    
    except Exception as e:
        log.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "reason": str(e)}), 500

@app.route("/status", methods=["GET"])
def status():
    """Bot ka status check karo"""
    return jsonify({
        "status":       "running",
        "position":     state.position,
        "entry_price":  state.entry_price,
        "strike":       state.strike,
        "trades_today": state.trades_today,
        "pnl_today":    round(state.pnl_today, 2),
        "market_open":  is_market_open(),
        "time":         datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Dhan Options Bot is running! 🚀"})

# ─────────────────────────────────────────────
#   START
# ─────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("  DHAN NIFTY OPTIONS AUTO BOT - WEBHOOK MODE")
    log.info("=" * 60)
    log.info(f"  Client ID: {CLIENT_ID}")
    log.info(f"  Quantity:  {QUANTITY} (1 lot)")
    log.info(f"  Strike:    ATM")
    log.info(f"  Expiry:    Weekly Thursday")
    log.info("=" * 60)
    log.info("  Webhook URL: http://localhost:5000/webhook")
    log.info("  Status URL:  http://localhost:5000/status")
    log.info("=" * 60)
    
    app.run(host="0.0.0.0", port=5000, debug=False)
