#!/usr/bin/env python3
"""미국 추천 종목 스크리닝 (S&P500 + 나스닥100).

1) 대상: 위키백과의 S&P500·나스닥100 구성표(주 1회 갱신, 실패하면 지난 목록)
2) 실적: 나스닥 공개 자료의 분기 매출·EPS(최근 3개 회계연도). SEC 는 GitHub 서버 접속을 막는다.
   판정 규칙은 국내(screen.judge)와 같고, 영업이익 자리에 EPS 를 넣는다.
3) 주가: 한국투자증권 해외주식 일봉(받아 둔 것은 최근 구간만 이어 받음). 판정·점수는 국내와 같다.
4) 등급 A/B/C, 과열 제외, 금융 업종·내 종목·관심 종목 제외. 추천 종목은 날마다 추적한다.

결과: data/us/screen/result.json, data/us/screen/track.json (공개 시세·공시만 담는다)
"""
import json
import os
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as c          # noqa: E402
import collect_us as U       # noqa: E402
import screen as S           # noqa: E402

UA = "stock-board personal dashboard Maldugi00@users.noreply.github.com"
PX = "state/us_screen_px.json"
UNIV = "state/us_universe2.json"
FINANCE = ("Financials", "Financial")


def http(url, timeout=60, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (i + 1))
        except Exception:
            time.sleep(2 * (i + 1))
    return None


class Tables(HTMLParser):
    """위키백과 표 → [[셀 문자열]] (id 가 있는 표만)"""
    def __init__(self):
        super().__init__()
        self.tables, self.cur, self.row, self.cell, self.depth, self.tid = {}, None, None, None, 0, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.depth += 1
            if self.depth == 1 and a.get("id"):
                self.tid, self.cur = a["id"], []
        elif self.cur is not None and self.depth == 1:
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.cell = ""

    def handle_endtag(self, tag):
        if tag == "table":
            if self.depth == 1 and self.cur is not None:
                self.tables[self.tid] = self.cur
                self.cur = None
            self.depth -= 1
        elif self.cur is not None and self.depth == 1:
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row.append(re.sub(r"\s+", " ", self.cell).strip())
                self.cell = None
            elif tag == "tr" and self.row:
                self.cur.append(self.row)
                self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data


def wiki_table(page, need):
    raw = http(f"https://en.wikipedia.org/wiki/{page}")
    if not raw:
        return []
    p = Tables()
    p.feed(raw.decode("utf-8", "replace"))
    for rows in p.tables.values():
        if rows and all(any(n.lower() in h.lower() for h in rows[0]) for n in need):
            return rows
    return []


def universe(names):
    """→ {티커(KIS 형식): {name, ex, sector, idx}}"""
    old = c.load(UNIV, {})
    if c.EPOCH - old.get("ts", 0) < 7 * 86400 and len(old.get("items", {})) > 400:
        return old["items"]
    items = {}
    rows = wiki_table("List_of_S%26P_500_companies", ("Symbol", "GICS Sector"))
    if rows:
        h = rows[0]; si, ni, gi = h.index(next(x for x in h if x.startswith("Symbol"))), 1, next(i for i, x in enumerate(h) if "GICS Sector" in x)
        for r in rows[1:]:
            if len(r) > gi:
                items[r[si].replace(".", "/")] = {"sector": r[gi], "idx": "S&P500"}
    n_sp = len(items)
    js = nasdaq("quote/list-type/nasdaq100")
    for r in (((js or {}).get("data") or {}).get("data") or {}).get("rows") or []:
        t = r.get("symbol", "").replace(".", "/")
        if not t:
            continue
        if t in items:
            items[t]["idx"] = "S&P500·나스닥100"
        else:
            items[t] = {"sector": "기타(나스닥100)", "idx": "나스닥100"}
    print(f"대상 종목: S&P500 {n_sp}개 + 나스닥100 → 합계 {len(items)}개", flush=True)
    if len(items) < 400:
        print("구성표를 제대로 못 받아 지난 목록을 씁니다.")
        return old.get("items", {})
    for t, x in items.items():
        ex, nm = names.get(t, (None, t))
        x["ex"], x["name"] = ex, nm
    items = {t: x for t, x in items.items() if x["ex"]}
    c.save(UNIV, {"ts": c.EPOCH, "items": items})
    return items


# ───────────────────────── 실적 (나스닥 공개 자료) ─────────────────────────
# SEC 는 GitHub 서버 접속을 막아서, 나스닥 사이트의 '매출·EPS' 표(최근 3개 회계연도 분기)를 쓴다.
NH = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
      "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
FIN = "state/us_fin.json"


def nasdaq(path):
    for i in range(3):
        try:
            req = urllib.request.Request("https://api.nasdaq.com/api/" + path, headers=NH)
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def money(t):
    m = re.search(r"\$?(-?\(?[\d,]+\.?\d*\)?)", t or "")
    if not m:
        return None
    v = m.group(1).replace(",", "")
    neg = v.startswith("(") or v.startswith("-")
    v = float(v.strip("()-"))
    return -v if neg else v


def quarters_of(sym):
    """→ [(라벨, 매출(백만달러), EPS, 날짜)] 오래된 순, 빈칸 없이 이어진 구간만"""
    js = nasdaq(f"company/{sym.replace('/', '.')}/revenue?limit=1")
    tab = ((js or {}).get("data") or {}).get("revenueTable") or {}
    rows, cols = tab.get("rows") or [], [k for k in ("value2", "value3", "value4")]
    out, rev = [], {}
    for r in rows:
        lab = r.get("value1", "")
        if lab == "Totals":
            break
        if lab == "Revenue":
            rev = {k: money(r.get(k)) for k in cols}
        elif lab == "EPS":
            for k in cols:
                m = re.search(r"\((\d\d)/(\d\d)/(\d{4})\)", r.get(k) or "")
                e = money((r.get(k) or "").split("(")[0])
                if m and e is not None and rev.get(k) is not None:
                    d = int(m.group(3) + m.group(1) + m.group(2))
                    out.append((d, rev[k], e))
    out.sort()
    tail = []
    for row in reversed(out):                       # 분기 간격(약 3개월)이 끊기지 않은 최근 구간
        if tail:
            gap = (U.datetime.strptime(str(tail[-1][0]), "%Y%m%d") - U.datetime.strptime(str(row[0]), "%Y%m%d")).days
            if not 70 <= gap <= 110:
                break
        tail.append(row)
    res = []
    for d, rv, e in reversed(tail):
        y, mth = d // 10000, d // 100 % 100
        res.append((f"{y % 100}.{(mth - 1) // 3 + 1}Q", rv, e, d))
    return res


def financials(uni, budget_end):
    """3일마다 다시 받는다 (실적은 분기마다 바뀜). → {티커: [(라벨, 매출, EPS)]}"""
    cache = c.load(FIN, {})
    n = 0
    for t in uni:
        ent = cache.get(t) or {}
        if c.EPOCH - ent.get("ts", 0) < 3 * 86400 and ent.get("q"):
            continue
        if time.time() > budget_end:
            print("시간 제한으로 실적 받기를 멈춥니다.", flush=True)
            break
        q = quarters_of(t)
        time.sleep(0.25)
        n += 1
        if q:
            cache[t] = {"ts": c.EPOCH, "q": [list(x) for x in q]}
        elif ent:
            ent["ts"] = c.EPOCH - 2 * 86400              # 실패하면 하루 뒤 다시
    c.save(FIN, {t: v for t, v in cache.items() if t in uni})
    print(f"실적 새로 받음 {n}개", flush=True)
    return {t: [(x[0], x[1], x[2]) for x in v["q"]] for t, v in cache.items() if t in uni}


# ───────────────────────── 주가 ─────────────────────────
def prices(k, uni, budget_end):
    px = c.load(PX, {})
    fresh = 0
    for t, x in uni.items():
        if time.time() > budget_end:
            print("시간 제한으로 일봉 받기를 멈춥니다. 다음 실행에서 이어 받습니다.", flush=True)
            break
        ent = px.get(t) or {}
        rows = ent.get("rows") or []
        if c.EPOCH - ent.get("ts", 0) < 10 * 3600 and rows:
            continue
        full = len(rows) < 140 or c.EPOCH - ent.get("full", 0) > 30 * 86400
        got = []
        bymd = ""
        for _ in range(2 if full else 1):
            page = U.daily_page(k, x["ex"], t, bymd)
            if not page:
                break
            got += page
            first = min(r[0] for r in page)
            bymd = (U.datetime.strptime(str(first), "%Y%m%d") - U.timedelta(days=1)).strftime("%Y%m%d")
            if len(page) < 100:
                break
        if not got:
            continue
        new = {r[0]: [r[0], r[2], r[3], r[4], r[5]] for r in got}
        if not full:
            old = {r[0]: r for r in rows}
            if any(abs(old[d][3] - r[3]) > 0.01 * r[3] for d, r in new.items() if d in old and r[3]):
                ent["full"] = 0                     # 분할 등으로 수정주가가 바뀜 → 다음에 전체를 다시 받는다
            old.update(new)
            new = old
        else:
            ent["full"] = c.EPOCH
        ent["rows"], ent["ts"] = [new[d] for d in sorted(new)][-160:], c.EPOCH
        px[t] = ent
        fresh += 1
    c.save(PX, {t: v for t, v in px.items() if t in uni})
    return {t: px[t]["rows"] for t in uni if t in px}, fresh


# ───────────────────────── 등급 ─────────────────────────
def grade_all(items, rows, mkt, exclude):
    feats = {t: S.features(r, mkt) for t, r in rows.items()}
    groups = {}
    for x in items:
        groups.setdefault(x["sector"], []).append(x)
    sec_ok = {}
    for key, xs in groups.items():
        known = [x for x in xs if x["cls"] != "nodata"]
        share = sum(1 for x in known if x["cls"] in ("up", "start")) / len(known) if known else 0
        rss = sorted(feats[x["code"]]["rs"] for x in xs if feats.get(x["code"]) and feats[x["code"]]["rs"] is not None)
        med = rss[len(rss) // 2] if rss else None
        sec_ok[key] = {"share": share, "strong": len(known) >= 3 and share >= 0.4 and (med or 0) > 1.0}
    out, excl = [], []
    for x in items:
        if x["code"] in exclude or x["cls"] == "down" or any(w in x["sector"] for w in FINANCE):
            continue
        f = feats.get(x["code"])
        if not f:
            continue
        st, pwhy = S.price_state(f)
        if st == "hot":
            excl.append({"code": x["code"], "name": x["name"], "why": pwhy, "ewhy": x["why"]})
            continue
        sec = sec_ok.get(x["sector"], {})
        grade = None
        if x["cls"] in ("up", "start") and st == "early":
            grade = "A"
        elif x["cls"] in ("up", "start") and st == "base":
            grade = "B"
        elif x["cls"] == "watch" and sec.get("strong") and st in ("early", "base"):
            grade = "C"
        if not grade:
            continue
        ps, sig = S.price_score(f, st)
        sig = [s.replace("코스피", "S&P500") for s in sig]
        out.append({"code": x["code"], "name": x["name"], "ex": x["ex"], "idx": x["idx"], "grade": grade, "score": x["score"] + ps,
                    "ecls": x["cls"], "ewhy": x["why"], "basis": x["basis"], "pstate": st, "pwhy": pwhy, "signals": sig,
                    "ind": x["sector"], "ind_share": round(sec.get("share", 0), 2), "q": x["q"], "rev": x["rev"], "op": x["op"],
                    "close": f["close"], "r20": round(f["r20"], 4), "rsi": round(f["rsi"], 1), "spark": f["spark"]})
    order = {"A": 0, "B": 1, "C": 2}
    out.sort(key=lambda y: (order[y["grade"]], -y["score"]))
    return out, excl, feats


def track(picks, graded, excl, items, feats, mkt, exclude, asof):
    """국내 screen.track 과 같은 규칙. 비교 기준은 S&P500(SPY)."""
    tr = c.load("us/screen/track.json", {"items": {}})
    T = tr["items"]
    gmap = {y["code"]: y for y in graded}
    hmap = {h["code"]: h for h in excl}
    emap = {x["code"]: x for x in items}
    m_now = mkt.get(asof)
    for code in picks:
        if code not in T and feats.get(code):
            y = gmap[code]
            T[code] = {"code": code, "name": y["name"], "ex": y["ex"], "added": asof, "grade0": y["grade"], "close0": feats[code]["close"], "mkt0": m_now, "log": []}
    for code, t in T.items():
        if t.get("closed"):
            continue
        if code in exclude:
            t.update(closed=asof, verdict="moved", vtext="관심·내 종목으로 옮겨져 추적을 마쳤어요")
            continue
        f = feats.get(code)
        if not f:
            continue
        grade = gmap[code]["grade"] if code in gmap else "과열" if code in hmap else \
            "실적 악화" if emap.get(code, {}).get("cls") == "down" else "해당 없음"
        if not t["log"] or t["log"][-1][0] != asof:
            t["log"].append([asof, grade, f["close"]])
        t["log"] = t["log"][-80:]
        days = len(t["log"]) - 1
        ret = f["close"] / t["close0"] - 1
        mret = (m_now / t["mkt0"] - 1) if (m_now and t.get("mkt0")) else 0
        rel = ret - mret
        why = []
        if grade in ("과열", "실적 악화"):
            why.append("과열로 바뀜" if grade == "과열" else "실적이 악화로 바뀜")
        if f["close"] < f["ma60"] * 0.97:
            why.append("60일선을 3% 넘게 깨고 내려감")
        if days >= 20 and rel < -0.05:
            why.append(f"추천 후 {days}거래일 동안 S&P500보다 {rel * 100:.0f}%p 약함")
        if why:
            verdict, vtext = "drop", "추천에서 제외 권장: " + ", ".join(why)
            t.setdefault("drop_since", asof)
        elif days >= 10 and grade in ("A", "B") and f["close"] > f["ma60"] and rel > 0:
            verdict, vtext = "add", f"관심종목 추가 권장: {days}거래일째 {grade}등급 유지, 60일선 위, S&P500보다 {rel * 100:+.0f}%p"
            t.pop("drop_since", None)
        else:
            left = max(0, 10 - days)
            verdict = "watch"
            vtext = "계속 관찰: " + (f"추천 {days}거래일째, 판단까지 {left}거래일 더 지켜봐요" if left else
                                   f"지금 {grade}" + (", S&P500보다 약함" if rel <= 0 else "") + (", 60일선 아래" if f["close"] <= f["ma60"] else ""))
            t.pop("drop_since", None)
        t.update(grade=grade, days=days, ret=round(ret, 4), mret=round(mret, 4), rel=round(rel, 4), verdict=verdict, vtext=vtext)
        if verdict == "drop" and t.get("drop_since") and sum(1 for e in t["log"] if e[0] >= t["drop_since"]) > 10:
            t["closed"] = asof
    done = sorted([x for x in T.values() if x.get("closed")], key=lambda x: str(x["closed"]))[:-30]
    for x in done:
        T.pop(x["code"], None)
    c.save("us/screen/track.json", tr)
    rows = sorted(T.values(), key=lambda x: (bool(x.get("closed")), str(x.get("added"))))
    return [{k: v for k, v in x.items() if k != "log"} | {"spark": [e[2] for e in x["log"]][-40:]} for x in rows]


def main():
    t0 = time.time()
    budget_end = t0 + int(os.environ.get("SCREEN_US_MIN", "35")) * 60
    sym = c.load("us/symbols.json", {}).get("items", [])
    names = {r[0]: (r[1], r[2] or r[3].title()[:24]) for r in sym}
    uni = universe(names)
    if not uni:
        print("대상 종목이 없습니다.")
        return 1
    cfg = json.load(open("config/us.json", encoding="utf-8"))
    exclude = {s["code"] for s in cfg.get("stocks", [])}

    # 실적
    fin = financials(uni, t0 + 12 * 60)

    items = []
    for t, x in uni.items():
        qs, basis = fin.get(t, []), "eps"
        cls, why, score, det = S.judge(qs)
        if cls != "nodata":
            why = why.replace("영업이익", "EPS").replace("흑자전환", "EPS 흑자전환").replace("적자전환", "EPS 적자전환").replace("이익률", "매출 대비 EPS")
        items.append({"code": t, "name": x["name"], "ex": x["ex"], "sector": x["sector"], "idx": x["idx"], "cls": cls, "why": why,
                      "score": score, "basis": basis, "q": [q[0] for q in qs][-8:],
                      "rev": [round(q[1]) for q in qs][-8:], "op": [q[2] for q in qs][-8:]})
    cnt = {}
    for x in items:
        cnt[x["cls"]] = cnt.get(x["cls"], 0) + 1
    print("실적 판정:", cnt, flush=True)

    # 주가
    tok = c.read_token() or c.issue_token()
    if not tok:
        print("토큰을 받지 못했습니다.")
        return 1
    k = c.Kis(tok)
    rows, fresh = prices(k, uni, budget_end)
    print(f"일봉 새로 받음 {fresh}개, 보유 {len(rows)}개, 호출 {k.calls}회, 오류 {len(k.errors)}건", flush=True)
    md = c.load("us/market_daily.json", {})
    mkt = {r[0]: r[1] for r in md.get("rows", [])}

    graded, excl, feats = grade_all(items, rows, mkt, exclude)
    dates = [f["date"] for f in feats.values() if f]
    asof = max(set(dates), key=dates.count) if dates else None
    picks = [y["code"] for y in graded if y["grade"] == "A"][:3]
    if len(picks) < 3:
        picks += [y["code"] for y in graded if y["grade"] == "B" and y["code"] not in picks][:3 - len(picks)]
    trk = track(picks, graded, excl, items, feats, mkt, exclude, asof) if asof else []
    counts = {g: sum(1 for y in graded if y["grade"] == g) for g in "ABC"}
    counts.update(hot=len(excl), universe=len(uni), earn=cnt)
    c.save("us/screen/result.json", {"updated": c.NOW.strftime("%Y-%m-%d %H:%M"), "asof": asof, "counts": counts, "picks": picks,
                                     "items": graded[:60], "hot": excl[:20], "track": trk})
    print(f"등급: {counts}, 추천 {picks}, {time.time() - t0:.0f}초", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
