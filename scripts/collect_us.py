"""미국 주식: 한국투자증권 Open API(해외주식 시세)에서 받아 data/us/ 아래 JSON으로 저장한다.

- 조회 API만 쓴다. 주문·계좌 API는 부르지 않는다. 토큰은 국내 수집기(collect.py)와 같은 것을 함께 쓴다.
- 일봉은 처음 한 번 약 3년치를 받고, 그 뒤엔 최근 100봉만 다시 받아 합친다. 주 1회 전체를 다시 받아 액면분할 등을 반영한다.
- 종목 검색용 목록(나스닥·뉴욕·아멕스의 주식과 ETF)은 주 1회 받는다.
"""
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C  # noqa: E402

CONFIG = os.environ.get("US_CONFIG", "config/us.json")
P = "/uapi/overseas-price/v1/quotations/"
EXS = ("NAS", "NYS", "AMS")
FEE = 0.0025                     # 한국투자증권 온라인 미국주식 편도 수수료(근사)
MARKET = ("AMS", "SPY")          # 시장 필터: S&P500 ETF 가 60일선 위인지
INDICES = [("N", "SPX", "S&P500"), ("N", "COMP", "나스닥"), ("N", ".DJI", "다우"), ("N", "SOX", "필라델피아 반도체"),
           ("N", "VIX", "VIX 공포지수"), ("I", "Y0202", "미국 10년물 금리")]
LONG_PAGES = 8                   # 100봉 × 8 ≈ 3.2년
EPOCH = C.EPOCH


def fn(sym):
    return sym.replace("/", "_")


def ny_now():
    """뉴욕 시각 (서머타임: 3월 둘째 일요일 ~ 11월 첫째 일요일)."""
    u = datetime.now(timezone.utc)
    y = u.year
    mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    mar += timedelta(days=(6 - mar.weekday()) % 7)
    nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    nov += timedelta(days=(6 - nov.weekday()) % 7)
    return u + timedelta(hours=-4 if mar <= u < nov else -5)


def quote(k, ex, sym):
    js = k.get(P + "price", "HHDFS00000300", {"AUTH": "", "EXCD": ex, "SYMB": sym}, f"현재가 {sym}")
    o = (js or {}).get("output") or {}
    last = C.num(o.get("last"))
    if not last:
        return None
    down = str(o.get("sign")) in ("4", "5")
    diff = abs(C.num(o.get("diff")))
    return {"price": last, "change": round(-diff if down else diff, 4), "pct": C.num(o.get("rate")),
            "prev": C.num(o.get("base")), "vol": C.num(o.get("tvol"), int)}


def daily_page(k, ex, sym, bymd=""):
    js = k.get(P + "dailyprice", "HHDFS76240000", {"AUTH": "", "EXCD": ex, "SYMB": sym, "GUBN": "0", "BYMD": bymd, "MODP": "1"}, f"일봉 {sym}")
    if js is None:
        return None
    out = []
    for r in js.get("output2") or []:
        d, c = r.get("xymd"), C.num(r.get("clos"))
        if d and c:
            out.append([int(d), C.num(r.get("open")) or c, C.num(r.get("high")) or c, C.num(r.get("low")) or c, c, C.num(r.get("tvol"), int)])
    return out


def long_daily(k, ex, sym, old, ts):
    """새 종목이거나 일주일이 지났으면 약 3년치, 아니면 최근 100봉만 받아 합친다."""
    full = not old or C.EPOCH - ts.get("full", 0) >= 7 * 86400
    rows = {}
    if full:
        bymd = ""
        for _ in range(LONG_PAGES):
            page = daily_page(k, ex, sym, bymd)
            if not page:
                break
            for r in page:
                rows[r[0]] = r
            first = min(r[0] for r in page)
            bymd = (datetime.strptime(str(first), "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            if len(page) < 100:
                break
        if len(rows) < 30:
            return old or [], False
        ts["full"] = C.EPOCH
    else:
        page = daily_page(k, ex, sym)
        if page is None:
            return old, False
        rows = {r[0]: r for r in old}
        for r in page:
            rows[r[0]] = r
    return [rows[d] for d in sorted(rows)][-820:], True


def chart_series(k, div, code, name):
    js = k.get(P + "inquire-daily-chartprice", "FHKST03030100",
               {"FID_COND_MRKT_DIV_CODE": div, "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": C.days_ago(120),
                "FID_INPUT_DATE_2": C.TODAY, "FID_PERIOD_DIV_CODE": "D"}, name)
    days = [[int(r["stck_bsop_date"]), C.num(r.get("ovrs_nmix_prpr"))] for r in (js or {}).get("output2") or []
            if r.get("stck_bsop_date") and C.num(r.get("ovrs_nmix_prpr"))]
    days.sort()
    if len(days) < 2:
        return None
    (_, prev), (d, last) = days[-2], days[-1]
    item = {"code": code, "name": name, "date": d, "price": last, "change": round(last - prev, 4),
            "pct": round((last / prev - 1) * 100, 2), "days": days[-60:]}
    if div == "I":
        item["bp"] = round((last - prev) * 100)              # 금리는 bp(0.01%p) 변화로 본다
    return item


def symbols():
    """나스닥·뉴욕·아멕스 마스터 → [티커, 거래소, 한글명, 영문명, 종류(2 주식 / 3 ETF)]."""
    out = []
    for ex in ("nas", "nys", "ams"):
        url = f"https://new.real.download.dws.co.kr/common/master/{ex}mst.cod.zip"
        try:
            raw = urllib.request.urlopen(url, timeout=60).read()
            z = zipfile.ZipFile(io.BytesIO(raw))
            txt = z.read(z.namelist()[0]).decode("cp949", "replace")
        except Exception as e:
            print("종목 목록 실패", ex, type(e).__name__)
            return None
        for line in txt.splitlines():
            f = line.split("\t")
            if len(f) < 9 or f[8] not in ("2", "3"):
                continue
            sym, kor, eng = f[4].strip(), f[6].strip(), re.sub(r"\s+", " ", f[7]).strip()[:34]
            if not re.match(r"^[A-Z][A-Z0-9/]{0,9}$", sym):
                continue
            out.append([sym, f[2].strip(), "" if kor.upper() == eng.upper()[:len(kor)] else kor, eng, int(f[8])])
    return out


def main():
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    ny = ny_now()
    updated = C.NOW.strftime("%Y-%m-%d %H:%M")
    meta = {"updated": updated, "ny": ny.strftime("%Y-%m-%d %H:%M"), "calls": 0, "errors": []}
    if not C.APP_KEY or not C.APP_SECRET:
        print("KIS_APP_KEY / KIS_APP_SECRET 이 없습니다.")
        return 1
    t = C.read_token() or C.issue_token()
    if not t:
        print("토큰을 받지 못했습니다.")
        return 1
    k = C.Kis(t)

    # 시장: 지수·금리·환율
    old = C.load("us/market.json", {})
    idx = [x for x in (chart_series(k, d, c, n) for d, c, n in INDICES) if x]
    have = {x["code"] for x in idx}
    idx += [x for x in old.get("indices", []) if x["code"] not in have]           # 이번에 못 받은 것은 지난 값 유지
    order = [c for _, c, _ in INDICES]
    idx.sort(key=lambda x: order.index(x["code"]) if x["code"] in order else 99)
    fx = C.fx_usdkrw(k) or old.get("usdkrw")
    C.save("us/market.json", {"updated": updated, "indices": idx, "usdkrw": fx})

    # 시장 필터용 S&P500 ETF 긴 일봉
    old = C.load("us/market_daily.json", {})
    ts = old.get("ts", {})
    rows, ok = long_daily(k, MARKET[0], MARKET[1], [[r[0], r[1], r[1], r[1], r[1], 0] for r in old.get("rows", [])], ts)
    if ok:
        C.save("us/market_daily.json", {"code": MARKET[1], "ts": ts, "rows": [[r[0], r[4]] for r in rows]})

    # 종목
    listing = []
    for s in cfg.get("stocks", []):
        sym, ex = s["code"], s.get("ex", "NAS")
        old = C.load(f"us/stocks/{fn(sym)}.json", {})
        ts = old.get("ts", {})
        d = {"code": sym, "ex": ex, "name": s.get("name", sym), "updated": updated}
        d["quote"] = quote(k, ex, sym) or old.get("quote")
        d["daily"], ok = long_daily(k, ex, sym, old.get("daily") or [], ts)
        d["ts"] = ts
        C.save(f"us/stocks/{fn(sym)}.json", d)
        q = d["quote"] or {}
        listing.append({"code": sym, "ex": ex, "name": d["name"], "group": s.get("group", "watch"), "mode": s.get("mode"),
                        "price": q.get("price"), "change": q.get("change"), "pct": q.get("pct"),
                        "spark": [r[4] for r in d["daily"][-20:]]})
        print(f"{sym}: 일봉 {len(d['daily'])}개, 현재가 {q.get('price')}")
    C.save("us/list.json", {"updated": updated, "ny": meta["ny"], "fee": FEE, "stocks": listing})

    # 검색용 종목 목록: 주 1회
    old = C.load("us/symbols.json", {})
    if C.EPOCH - old.get("ts", 0) >= 7 * 86400 or not old.get("items"):
        items = symbols()
        if items:
            C.save("us/symbols.json", {"ts": C.EPOCH, "items": items})
            print("검색용 종목", len(items))

    meta["calls"], meta["errors"] = k.calls, k.errors[:30]
    C.save("us/meta.json", meta)
    print(f"호출 {k.calls}회, 오류 {len(k.errors)}건")
    for e in k.errors[:30]:
        print("  오류:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
