"""한국투자증권 Open API에서 시세·정보를 받아 data/ 아래 JSON으로 저장한다.

- 조회 API만 사용한다. 주문·계좌 API는 부르지 않는다.
- 키와 토큰은 출력하지 않는다. 토큰은 하루 동안 다시 쓰기 위해 앱 시크릿으로 암호화해서만 저장한다.
- 이미 받아 둔 데이터는 다시 받지 않고, 바뀌는 부분만 받는다.
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.strftime("%Y%m%d")
EPOCH = int(time.time())
ROOT = os.environ.get("DATA_DIR", "data")
CONFIG = os.environ.get("CONFIG", "config/stocks.json")
APP_KEY = os.environ.get("KIS_APP_KEY", "").strip()
APP_SECRET = os.environ.get("KIS_APP_SECRET", "").strip()
DART_KEY = os.environ.get("DART_API_KEY", "").strip()
DART = "https://opendart.fss.or.kr/api/"
DOMAINS = [("모의투자", "https://openapivts.koreainvestment.com:29443"),
           ("실전", "https://openapi.koreainvestment.com:9443")]
GAP = 0.5           # 호출 사이 간격(초). 모의투자는 초당 한도가 낮다.
Q = "/uapi/domestic-stock/v1/quotations/"
HIST_DAYS = 5       # 30분봉으로 보관할 지난 거래일 수


def days_ago(n):
    return (NOW - timedelta(days=n)).strftime("%Y%m%d")


def num(v, kind=float):
    try:
        return kind(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def load(path, default):
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def http(method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"msg1": type(e).__name__}


# ── 토큰: 하루짜리라 매번 새로 받지 않고, 암호화해서 보관했다가 다시 쓴다 ──
TOKEN_FILE = os.path.join(ROOT, "state", "token.enc")


def _openssl(args, data):
    env = dict(os.environ, KIS_PASS=APP_SECRET)
    p = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-a", "-pass", "env:KIS_PASS"] + args,
                       input=data, capture_output=True, env=env)
    return p.stdout if p.returncode == 0 else None


def read_token():
    try:
        raw = open(TOKEN_FILE, "rb").read()
    except OSError:
        return None
    dec = _openssl(["-d"], raw)
    try:
        t = json.loads(dec.decode()) if dec else None
    except ValueError:
        return None
    if t and t.get("exp", 0) > EPOCH + 1800:
        return t
    return None


def issue_token():
    for label, base in DOMAINS:
        status, js = http("POST", base + "/oauth2/tokenP", {"content-type": "application/json"},
                          {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET})
        token = js.get("access_token") if isinstance(js, dict) else None
        if status == 200 and token:
            t = {"token": token, "label": label, "base": base, "exp": EPOCH + int(js.get("expires_in", 86400)) - 600}
            enc = _openssl([], json.dumps(t).encode())
            if enc:
                os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
                open(TOKEN_FILE, "wb").write(enc)
            return t
        print(f"토큰 발급 실패 ({label}): HTTP {status} {js.get('error_code', '')}")
        time.sleep(1.2)
    return None


class Kis:
    def __init__(self, t):
        self.t = t
        self.calls = 0
        self.errors = []
        print(f"::add-mask::{t['token']}")

    def get(self, path, tr_id, params, what=""):
        for attempt in range(3):
            time.sleep(GAP)
            self.calls += 1
            headers = {"content-type": "application/json; charset=utf-8", "authorization": "Bearer " + self.t["token"],
                       "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": tr_id, "custtype": "P"}
            status, js = http("GET", self.t["base"] + path + "?" + urllib.parse.urlencode(params), headers)
            js = js if isinstance(js, dict) else {}
            if status == 200 and str(js.get("rt_cd")) == "0":
                return js
            code = js.get("msg_cd", "")
            if code in ("EGW00123", "EGW00121") and attempt == 0:      # 토큰 만료·무효 → 한 번만 새로 받는다
                t = issue_token()
                if t:
                    self.t = t
                    print(f"::add-mask::{t['token']}")
                    continue
            if code == "EGW00201" or status in (0, 500, 502, 503):      # 호출 한도·일시 오류 → 잠깐 쉬고 다시
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        self.errors.append(f"{what or path.rsplit('/', 1)[-1]}: HTTP {status} {js.get('msg_cd', '')}")
        return None


def prev_minute(hhmmss):
    t = datetime.strptime(hhmmss, "%H%M%S") - timedelta(minutes=1)
    return t.strftime("%H%M%S")


def bar(r):
    return [int(r["stck_cntg_hour"][:4]), num(r["stck_oprc"], int), num(r["stck_hgpr"], int), num(r["stck_lwpr"], int),
            num(r["stck_prpr"], int), num(r["cntg_vol"], int)]


def quote(k, code):
    js = k.get(Q + "inquire-price", "FHKST01010100", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}, f"현재가 {code}")
    o = (js or {}).get("output") or {}
    if not o:
        return None
    return {"price": num(o.get("stck_prpr"), int), "change": num(o.get("prdy_vrss"), int), "pct": num(o.get("prdy_ctrt")),
            "open": num(o.get("stck_oprc"), int), "high": num(o.get("stck_hgpr"), int), "low": num(o.get("stck_lwpr"), int),
            "prev": num(o.get("stck_sdpr"), int), "vol": num(o.get("acml_vol"), int), "sector": o.get("bstp_kor_isnm", "")}


def minutes_today(k, code, old):
    """오늘 1분봉. 이미 가진 시각 이전은 다시 받지 않는다."""
    have = {b[0]: b for b in old}
    last = max(have) if have else 0
    hour = min(NOW.strftime("%H%M%S"), "153000")
    if hour < "090000":
        return old
    for _ in range(14):
        js = k.get(Q + "inquire-time-itemchartprice", "FHKST03010200",
                   {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": hour,
                    "FID_PW_DATA_INCU_YN": "Y", "FID_ETC_CLS_CODE": ""}, f"분봉 {code}")
        rows = [r for r in (js or {}).get("output2") or [] if r.get("stck_bsop_date") == TODAY]
        if not rows:
            break
        for r in rows:
            b = bar(r)
            have[b[0]] = b
        oldest = min(r["stck_cntg_hour"] for r in rows)
        if len(rows) < 30 or oldest <= "090000" or int(oldest[:4]) <= last:
            break
        hour = prev_minute(oldest)
    return [have[h] for h in sorted(have)]


def to_30(bars):
    out = {}
    for h, o, hi, lo, c, v in bars:
        key = h // 100 * 100 + (30 if h % 100 >= 30 else 0)
        if key >= 1530:
            key = 1500                      # 마감 동시호가는 마지막 봉에 합친다
        b = out.get(key)
        if b is None:
            out[key] = [key, o, hi, lo, c, v]
        else:
            b[2] = max(b[2], hi); b[3] = min(b[3], lo); b[4] = c; b[5] += v
    return [out[x] for x in sorted(out)]


def minutes_past(k, code, date):
    """지난 날짜의 1분봉을 받아 30분봉으로 묶는다."""
    have, hour = {}, "153000"
    for _ in range(5):
        js = k.get(Q + "inquire-time-dailychartprice", "FHKST03010230",
                   {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": hour,
                    "FID_INPUT_DATE_1": date, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "N"}, f"지난 분봉 {code}")
        rows = [r for r in (js or {}).get("output2") or [] if r.get("stck_bsop_date") == date]
        if not rows:
            break
        for r in rows:
            b = bar(r)
            have[b[0]] = b
        oldest = min(r["stck_cntg_hour"] for r in rows)
        if len(rows) < 100 or oldest <= "090000":
            break
        hour = prev_minute(oldest)
    return to_30([have[h] for h in sorted(have)])


def daily(k, code, windows=((140, 0), (285, 141))):
    rows = {}
    for a, b in windows:
        js = k.get(Q + "inquire-daily-itemchartprice", "FHKST03010100",
                   {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": days_ago(a),
                    "FID_INPUT_DATE_2": days_ago(b), "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}, f"일봉 {code}")
        for r in (js or {}).get("output2") or []:
            d = r.get("stck_bsop_date")
            if d and num(r.get("stck_clpr")) > 0:
                rows[d] = [int(d), num(r["stck_oprc"], int), num(r["stck_hgpr"], int), num(r["stck_lwpr"], int),
                           num(r["stck_clpr"], int), num(r["acml_vol"], int)]
    return [rows[d] for d in sorted(rows)]


def opinions(k, code):
    js = k.get(Q + "invest-opinion", "FHKST663300C0",
               {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633", "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": days_ago(180), "FID_INPUT_DATE_2": TODAY}, f"의견 {code}")
    if js is None:
        return None
    return [{"d": r.get("stck_bsop_date"), "firm": r.get("mbcr_name"), "op": r.get("invt_opnn"), "was": r.get("rgbf_invt_opnn"),
             "target": num(r.get("hts_goal_prc"), int)} for r in (js.get("output") or [])[:40]]


def news(k, code):
    js = k.get(Q + "news-title", "FHKST01011800",
               {"FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": code, "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": "", "FID_INPUT_HOUR_1": "", "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": ""}, f"뉴스 {code}")
    if js is None:
        return None
    return [{"d": r.get("data_dt"), "t": (r.get("data_tm") or "")[:4], "title": (r.get("hts_pbnt_titl_cntt") or "").strip(),
             "src": r.get("dorg")} for r in (js.get("output") or [])[:20]]


def etf_parts(k, code):
    js = k.get("/uapi/etfetn/v1/quotations/inquire-component-stock-price", "FHKST121600C0",
               {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_COND_SCR_DIV_CODE": "11216"}, f"ETF 구성 {code}")
    if js is None:
        return None
    o = js.get("output1") or {}
    parts = [{"code": r.get("stck_shrn_iscd"), "name": r.get("hts_kor_isnm"), "weight": num(r.get("etf_cnfg_issu_rlim")),
              "pct": num(r.get("prdy_ctrt"))} for r in js.get("output2") or []]
    parts.sort(key=lambda x: -x["weight"])
    return {"nav": num(o.get("nav")), "count": num(o.get("etf_cnfg_issu_cnt"), int), "parts": parts[:10]}


def index(k, code, name):
    js = k.get(Q + "inquire-index-price", "FHPUP02100000", {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code}, f"지수 {name}")
    o = (js or {}).get("output") or {}
    if not o:
        return None
    out = {"code": code, "name": name, "price": num(o.get("bstp_nmix_prpr")), "change": num(o.get("bstp_nmix_prdy_vrss")),
           "pct": num(o.get("bstp_nmix_prdy_ctrt")), "open": num(o.get("bstp_nmix_oprc")), "high": num(o.get("bstp_nmix_hgpr")),
           "low": num(o.get("bstp_nmix_lwpr")), "up": num(o.get("ascn_issu_cnt"), int), "down": num(o.get("down_issu_cnt"), int), "line": []}
    out["prev"] = round(out["price"] - out["change"], 2)
    js = k.get(Q + "inquire-time-indexchartprice", "FHKUP03500200",
               {"FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0", "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": "300",
                "FID_PW_DATA_INCU_YN": "N"}, f"지수 흐름 {name}")
    rows = [r for r in (js or {}).get("output2") or [] if r.get("stck_bsop_date") == TODAY]
    out["line"] = sorted([int(r["stck_cntg_hour"][:4]), num(r["bstp_nmix_prpr"])] for r in rows)
    return out


# ── 전자공시(DART): 분기 실적과 재무. 인증키는 조회 전용이다. ──
DART_ERR = []


def dart(path, params):
    url = DART + path + "?" + urllib.parse.urlencode(dict(params, crtfc_key=DART_KEY))
    time.sleep(0.15)
    status, js = http("GET", url, {})
    if status == 200 and isinstance(js, dict) and js.get("status") == "000":
        return js.get("list") or []
    if not (isinstance(js, dict) and js.get("status") == "013"):          # 013 = 해당 보고서 없음(정상)
        DART_ERR.append(f"{path} HTTP {status} {js.get('status', '') if isinstance(js, dict) else ''}")
    return None


def corp_map():
    """종목코드 → 전자공시 고유번호. 한 달에 한 번만 내려받는다."""
    old = load("state/corp.json", {})
    if old.get("map") and EPOCH - old.get("ts", 0) < 30 * 86400:
        return old["map"]
    try:
        with urllib.request.urlopen(DART + "corpCode.xml?crtfc_key=" + DART_KEY, timeout=60) as r:
            raw = r.read()
        z = zipfile.ZipFile(io.BytesIO(raw))
        root = ET.fromstring(z.read(z.namelist()[0]))
        m = {}
        for e in root.iter("list"):
            code = (e.findtext("stock_code") or "").strip()
            if code:
                m[code] = (e.findtext("corp_code") or "").strip()
        if m:
            save("state/corp.json", {"ts": EPOCH, "map": m})
            return m
    except Exception as e:
        DART_ERR.append("corpCode " + type(e).__name__)
    return old.get("map") or {}


def periods():
    """최근 보고서부터 거꾸로: (사업연도, 보고서 코드, 이름, 연간 여부)"""
    out = []
    for y in (NOW.year, NOW.year - 1, NOW.year - 2):
        out += [(y, "11011", f"{y % 100}.4Q", True), (y, "11014", f"{y % 100}.3Q", False),
                (y, "11012", f"{y % 100}.2Q", False), (y, "11013", f"{y % 100}.1Q", False)]
    return out


def report(corp, year, code):
    """한 보고서의 주요 계정. 연결재무제표를 먼저 쓰고 없으면 별도."""
    rows = dart("fnlttSinglAcnt.json", {"corp_code": corp, "bsns_year": str(year), "reprt_code": code})
    if not rows:
        return None
    for fs in ("CFS", "OFS"):
        acc = {}
        for r in rows:
            if r.get("fs_div") != fs:
                continue
            name = (r.get("account_nm") or "").replace(" ", "")
            key = ("rev" if name in ("매출액", "수익(매출액)", "영업수익", "매출") else "op" if name.startswith("영업이익") else
                   "net" if name.startswith("당기순이익") or name.startswith("분기순이익") or name.startswith("반기순이익") else
                   "assets" if name == "자산총계" else "debt" if name == "부채총계" else "equity" if name == "자본총계" else None)
            if key and key not in acc:
                acc[key] = num(r.get("thstrm_amount"), int)
                if key in ("rev", "op", "net"):
                    acc[key + "_sum"] = num(r.get("thstrm_add_amount"), int)     # 분기보고서의 누적 금액
        if "rev" in acc or "op" in acc:
            return acc
    return None


def financials(corp):
    got = []
    for y, code, label, annual in periods():
        if len(got) >= 8:
            break
        acc = report(corp, y, code)
        if acc:
            got.append({"y": y, "q": label, "annual": annual, **acc})
    if not got:
        return None
    by = {(g["y"], g["q"][-2:]): g for g in got}
    quarters = []
    for g in reversed(got):                                    # 과거 → 최근
        item = {"q": g["q"]}
        for k in ("rev", "op", "net"):
            v = g.get(k)
            if g["annual"]:                                    # 4분기 = 연간 − 1~3분기
                parts = [by.get((g["y"], q)) for q in ("1Q", "2Q", "3Q")]
                v = v - sum(p.get(k, 0) for p in parts) if v is not None and all(parts) else None
            item[k] = v
        if item["rev"] is not None or item["op"] is not None:
            quarters.append(item)
    last = got[0]
    return {"quarters": quarters[-6:], "bs": {"asof": last["q"], "assets": last.get("assets"), "debt": last.get("debt"), "equity": last.get("equity")}}


def latest_brief(corp):
    """섹터 상위 종목용: 가장 최근 보고서 하나만."""
    for y, code, label, annual in periods()[:5]:
        acc = report(corp, y, code)
        if acc:
            return {"q": (f"{y}년 연간" if annual else label), "rev": acc.get("rev"), "op": acc.get("op"), "net": acc.get("net"),
                    "debt": acc.get("debt"), "equity": acc.get("equity")}
    return None


def stale(ts, key, seconds):
    return EPOCH - ts.get(key, 0) >= seconds


def main():
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    meta = {"updated": NOW.strftime("%Y-%m-%d %H:%M"), "env": None, "calls": 0, "errors": []}
    if not APP_KEY or not APP_SECRET:
        print("KIS_APP_KEY / KIS_APP_SECRET 이 없습니다.")
        return 1
    t = read_token()
    reused = t is not None
    t = t or issue_token()
    if not t:
        print("토큰을 받지 못했습니다.")
        return 1
    k = Kis(t)
    meta["env"] = t["label"]
    print("토큰:", "보관한 것 다시 사용" if reused else "새로 발급", "/", t["label"])

    # 시장 지수
    market = [x for x in (index(k, i["code"], i["name"]) for i in cfg["indices"]) if x]
    if market:
        save("market.json", {"updated": meta["updated"], "indices": market})

    # 종목
    listing = []
    for s in cfg["stocks"]:
        code = s["code"]
        old = load(f"stocks/{code}.json", {})
        ts = old.get("ts", {})
        d = {"code": code, "name": s["name"], "type": s["type"], "updated": meta["updated"], "date": TODAY}
        d["quote"] = quote(k, code) or old.get("quote")
        d["m1"] = minutes_today(k, code, old.get("m1", []) if old.get("date") == TODAY else [])
        if stale(ts, "daily", 3 * 3600) or not old.get("daily"):
            got = daily(k, code)
            d["daily"] = got or old.get("daily", [])
            ts["daily"] = EPOCH if got else ts.get("daily", 0)
        else:
            d["daily"] = old["daily"]
        # 지난 거래일의 30분봉: 없는 날짜만 받는다
        days = [str(r[0]) for r in d["daily"] if str(r[0]) < TODAY][-HIST_DAYS:]
        m30 = {x: y for x, y in (old.get("m30") or {}).items() if x in days and y}
        for day in days:
            if day not in m30:
                got = minutes_past(k, code, day)
                if got:
                    m30[day] = got
        d["m30"] = m30
        for key, fn, every, on in (("news", news, 20 * 60, True), ("opinion", opinions, 6 * 3600, s["type"] == "stock"),
                                   ("etf", etf_parts, 6 * 3600, s["type"] == "etf")):
            if not on:
                continue
            if stale(ts, key, every) or old.get(key) is None:
                got = fn(k, code)
                d[key] = got if got is not None else old.get(key)
                ts[key] = EPOCH if got is not None else ts.get(key, 0)
            else:
                d[key] = old[key]
        if DART_KEY and s["type"] == "stock":
            if stale(ts, "fin", 24 * 3600) or old.get("fin") is None:
                corp = corp_map().get(code)
                got = financials(corp) if corp else None
                d["fin"] = got if got is not None else old.get("fin")
                ts["fin"] = EPOCH if got is not None else ts.get("fin", 0)
            else:
                d["fin"] = old["fin"]
        d["ts"] = ts
        save(f"stocks/{code}.json", d)
        q = d["quote"] or {}
        listing.append({"code": code, "name": s["name"], "type": s["type"], "group": s["group"], "price": q.get("price"),
                        "change": q.get("change"), "pct": q.get("pct"), "spark": [b[4] for b in d["m1"]][::10] or [r[4] for r in d["daily"][-20:]]})
        print(f"{s['name']}: 1분봉 {len(d['m1'])}개, 일봉 {len(d['daily'])}개, 30분봉 {len(m30)}일치")
    save("list.json", {"updated": meta["updated"], "stocks": listing})

    # 섹터: 대표 ETF의 일봉 종가
    old = load("sectors.json", {})
    if EPOCH - old.get("ts", 0) >= 30 * 60 or not old.get("sectors") or any("parts" not in s for s in old["sectors"]):
        out = []
        had = {s["code"]: s.get("parts") for s in old.get("sectors", [])}
        parts_old = EPOCH - old.get("parts_ts", 0) >= 6 * 3600          # 구성 종목은 6시간마다만 다시 받는다
        got_parts = False
        for i, s in enumerate([cfg["sector_market"]] + cfg["sectors"]):
            rows = daily(k, s["code"], windows=((140, 0),))
            if not rows:
                continue
            item = {"code": s["code"], "name": s["name"], "closes": [[r[0], r[4]] for r in rows[-70:]]}
            if i > 0:
                parts = had.get(s["code"])
                if parts_old or not parts:
                    e = etf_parts(k, s["code"])
                    if e and e["parts"]:
                        parts, got_parts = e["parts"][:5], True
                item["parts"] = parts or []
            out.append(item)
        if out:
            save("sectors.json", {"updated": meta["updated"], "ts": EPOCH, "parts_ts": EPOCH if got_parts else old.get("parts_ts", 0),
                                  "market": out[0], "sectors": out[1:]})

    if DART_KEY:
        sect = load("sectors.json", {})
        old = load("fin_parts.json", {})
        codes = sorted({p["code"] for s in sect.get("sectors", []) for p in s.get("parts") or [] if p.get("code")})
        if codes and (EPOCH - old.get("ts", 0) >= 7 * 86400 or any(c not in old.get("items", {}) for c in codes)):
            cm, items = corp_map(), dict(old.get("items", {}))
            for c in codes:
                if c in items and EPOCH - old.get("ts", 0) < 7 * 86400:
                    continue
                items[c] = (latest_brief(cm[c]) if c in cm else None) or items.get(c) or {}
            save("fin_parts.json", {"ts": EPOCH, "items": {c: items[c] for c in codes}})
        meta["dart_errors"] = DART_ERR[:10]
        if DART_ERR:
            print("전자공시 오류:", DART_ERR[:10])

    meta["calls"] = k.calls
    meta["errors"] = k.errors[:30]
    save("meta.json", meta)
    print(f"호출 {k.calls}회, 오류 {len(k.errors)}건")
    for e in k.errors[:30]:
        print("  오류:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
