
import os, json, requests, csv
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pathlib

SYMBOL = "UCO"
TARGET_PCT = 0.006
STOP_PCT = 0.0045
ET = ZoneInfo("America/New_York")

APCA_KEY = os.getenv("APCA_API_KEY_ID")
APCA_SECRET = os.getenv("APCA_API_SECRET_KEY")
if not APCA_KEY:
    for fp in [r"C:\OilDriller\alpaca_keys.json", r"C:\GoldMiner\alpaca_keys.json", "./alpaca_keys.json", "alpaca_keys.json"]:
        if os.path.exists(fp):
            try:
                j=json.load(open(fp,encoding="utf-8"))
                APCA_KEY=j.get("APCA_API_KEY_ID") or j.get("api_key")
                APCA_SECRET=j.get("APCA_API_SECRET_KEY") or j.get("api_secret")
                if APCA_KEY:
                    print(f"Keys loaded from {fp}: {APCA_KEY[:6]}...")
                    break
            except: pass

APCA_BASE = "https://paper-api.alpaca.markets"
DATA_FILE = os.path.join(os.path.dirname(__file__), "Daily_Log.csv")

app = FastAPI(title="OilDriller Alpaca Railway")

def log(msg):
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    print(f"[{ts}] {msg}", flush=True)

def alpaca_req(endpoint, method="GET", data=None):
    url = f"{APCA_BASE}{endpoint}"
    h = {"APCA-API-KEY-ID": APCA_KEY, "APCA-API-SECRET-KEY": APCA_SECRET}
    try:
        if method=="GET":
            r = requests.get(url, headers=h, timeout=15)
        elif method=="POST":
            r = requests.post(url, headers=h, json=data, timeout=15)
        else:
            r = requests.delete(url, headers=h, timeout=15)
        return r
    except Exception as e:
        log(f"REQ FAIL {endpoint}: {e}")
        return None

def get_account():
    r = alpaca_req("/v2/account")
    return r.json() if r and r.status_code==200 else None

def get_position():
    r = alpaca_req(f"/v2/positions/{SYMBOL}")
    if r and r.status_code==200:
        return r.json()
    return None

def get_open_orders():
    r = alpaca_req(f"/v2/orders?status=open&symbols={SYMBOL}&limit=20&nested=true")
    return r.json() if r and r.status_code==200 else []

def get_quote():
    # Try paper-api quotes first
    for base in [APCA_BASE, "https://data.alpaca.markets"]:
        try:
            url = f"{base}/v2/stocks/{SYMBOL}/quotes/latest"
            h = {"APCA-API-KEY-ID": APCA_KEY, "APCA-API-SECRET-KEY": APCA_SECRET}
            import requests as _rq
            r = _rq.get(url, headers=h, timeout=10)
            if r.status_code==200:
                q = r.json().get("quote",{})
                bid=float(q.get("bp",0) or q.get("bid_price",0) or 0)
                ask=float(q.get("ap",0) or q.get("ask_price",0) or 0)
                if bid>0 and ask>0:
                    mid=(bid+ask)/2
                    spread=(ask-bid)/mid if mid else 0
                    return bid, ask, spread
        except Exception as e:
            log(f"quote fail {base}: {e}")
    # Try trades
    for base in [APCA_BASE, "https://data.alpaca.markets"]:
        try:
            url = f"{base}/v2/stocks/{SYMBOL}/trades/latest"
            h = {"APCA-API-KEY-ID": APCA_KEY, "APCA-API-SECRET-KEY": APCA_SECRET}
            import requests as _rq
            r = _rq.get(url, headers=h, timeout=10)
            if r.status_code==200:
                tr = r.json().get("trade",{})
                p=float(tr.get("p",0) or tr.get("price",0) or 0)
                if p>0:
                    log(f"QUOTE fallback trade {p} from {base}")
                    return p*0.9995, p*1.0005, 0.001
        except Exception as e:
            log(f"trade fail {base}: {e}")
    # Try snapshot
    for base in [APCA_BASE, "https://data.alpaca.markets"]:
        try:
            url = f"{base}/v2/stocks/{SYMBOL}/snapshot"
            h = {"APCA-API-KEY-ID": APCA_KEY, "APCA-API-SECRET-KEY": APCA_SECRET}
            import requests as _rq
            r = _rq.get(url, headers=h, timeout=10)
            if r.status_code==200:
                js=r.json()
                db=js.get("dailyBar",{}) or {}
                pdb=js.get("prevDailyBar",{}) or {}
                p=float(db.get("c",0) or pdb.get("c",0) or 0)
                if p>0:
                    log(f"QUOTE fallback snapshot {p} from {base}")
                    return p*0.9995, p*1.0005, 0.001
        except Exception as e:
            log(f"snap fail {base}: {e}")
    # Try bars latest
    for base in [APCA_BASE, "https://data.alpaca.markets"]:
        try:
            url = f"{base}/v2/stocks/{SYMBOL}/bars/latest"
            h = {"APCA-API-KEY-ID": APCA_KEY, "APCA-API-SECRET-KEY": APCA_SECRET}
            import requests as _rq
            r = _rq.get(url, headers=h, timeout=10)
            if r.status_code==200:
                bar=r.json().get("bar",{}) or r.json().get("bars",{}).get(SYMBOL,[{}])[0] if isinstance(r.json().get("bars"), dict) else {}
                p=float(bar.get("c",0) or bar.get("close",0) or 0)
                if p>0:
                    log(f"QUOTE fallback bar {p} from {base}")
                    return p*0.9995, p*1.0005, 0.001
        except Exception as e:
            log(f"bar fail {base}: {e}")
    log("QUOTE all fallbacks failed, using 0,0,1")
    return 0,0,1

def ensure_csv():
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    except: pass
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE,"w",newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["Date","Time","Account","Symbol","Qty","Entry","Target","Stop","Exit","P/L","OrderID","Status","Notes"])

def place_bracket():
    ensure_csv()
    bid,ask,spread = get_quote()
    log(f"QUOTE {SYMBOL} bid={bid} ask={ask} spread={spread*100:.3f}%")
    # Skip spread filter only if we have real quote; allow after-hours test when bid==0 fallback already gave us trade price
    if bid>0 and spread>0.003:
        log(f"SKIP spread {spread*100:.3f}% >0.30%")
        return False
    acct = get_account()
    if not acct:
        log("No account - check keys")
        return False
    equity=float(acct.get("equity",0))
    capital=min(equity*0.90, 33333)
    mid=(bid+ask)/2 if bid and ask and bid>0 and ask>0 else 0
    if mid==0:
        # last resort: try account buying power / 666 fallback to avoid 50 placeholder
        acct_tmp=get_account()
        if acct_tmp:
            try:
                eq=float(acct_tmp.get('last_equity',0) or acct_tmp.get('equity',0))
                mid=50  # placeholder but will log
            except: mid=50
        else:
            mid=50
        log(f"QUOTE using fallback mid={mid} because bid/ask 0 after-hours")
    qty=int(capital//mid) if mid else 100
    if qty<1: qty=100
    entry=round(mid,2); target=round(entry*(1+TARGET_PCT),2); stop=round(entry*(1-STOP_PCT),2)
    log(f"PLACING UCO BUY {qty} @ {entry} TGT {target} STP {stop} Cap ${capital:.0f} Eq ${equity:.0f}")
    order={
        "symbol":SYMBOL,"qty":str(qty),"side":"buy","type":"limit","time_in_force":"gtc",
        "limit_price":str(entry),"order_class":"bracket",
        "take_profit":{"limit_price":str(target)},
        "stop_loss":{"stop_price":str(stop)}
    }
    r=alpaca_req("/v2/orders",method="POST",data=order)
    if r and r.status_code in (200,201):
        oid=r.json().get("id","")
        log(f"BRACKET OK ID {oid[:8]} BUY {qty} @ {entry} -> {target}")
        with open(DATA_FILE,"a",newline="", encoding="utf-8") as f:
            now=datetime.now(ET)
            csv.writer(f).writerow([now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S ET"), "PAPER", SYMBOL, qty, entry, target, stop, "", "", oid, f"WAITING BUY {qty} @ {entry}", f"spread {spread*100:.3f}%"])
        return True
    else:
        log(f"BRACKET FAIL {r.status_code if r else 'no'} {r.text[:500] if r else ''}")
        return False

def close_position(reason=""):
    log(f"CLOSE {SYMBOL} {reason}")
    r=alpaca_req(f"/v2/positions/{SYMBOL}",method="DELETE")
    if r:
        log(f"Close {r.status_code} {r.text[:300]}")
        return r.status_code in (200,207)
    return False

def job_entry():
    try:
        log("=== JOB ENTRY 08:01 ET ===")
        if not APCA_KEY:
            log("ERROR No keys")
            return
        pos=get_position()
        orders=get_open_orders()
        has_buy=any(o.get("side")=="buy" for o in orders)
        log(f"Position {pos.get('qty') if pos else 'FLAT'} Open {len(orders)} has_buy {has_buy}")
        if not pos and not has_buy:
            place_bracket()
        else:
            log("Already in position - skip")
        log("=== ENTRY DONE ===")
    except Exception as e:
        log(f"ENTRY EX {e}")

def job_close():
    try:
        log("=== JOB CLOSE 15:58 ET ===")
        pos=get_position()
        if pos:
            close_position("15:58 EOD")
        log("=== CLOSE DONE ===")
    except Exception as e:
        log(f"CLOSE EX {e}")

scheduler = BackgroundScheduler(timezone=ET)
scheduler.add_job(job_entry, CronTrigger(hour=8, minute=1, day_of_week="mon-fri"))
scheduler.add_job(job_close, CronTrigger(hour=15, minute=58, day_of_week="mon-fri"))
scheduler.start()

@app.on_event("startup")
def startup():
    log("=== OilDriller Railway STARTUP - UCO ONLY ===")
    log(f"Keys present: {bool(APCA_KEY)} Base {APCA_BASE}")
    ensure_csv()

@app.get("/", response_class=HTMLResponse)
def root():
    p = pathlib.Path(__file__).parent / "dashboard.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return "<h2>OilDriller Railway - UCO ONLY</h2><p>/api/status /api/run-now POST</p>"

@app.get("/api/status")
def api_status():
    acct=get_account()
    pos=get_position()
    orders=get_open_orders()
    return {"symbol": SYMBOL, "account": {"equity": acct.get("equity") if acct else None, "buying_power": acct.get("buying_power") if acct else None}, "position": pos, "open_orders": orders, "time_et": datetime.now(ET).isoformat()}

@app.post("/api/run-now")
def api_run_now():
    job_entry()
    return {"ok": True}
