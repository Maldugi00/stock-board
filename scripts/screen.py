#!/usr/bin/env python3
"""추천 종목 스크리닝 (R-1: 전 종목 거르기 + 실적 판정).

1) 한국투자증권 전체 종목 파일(코스피·코스닥)로 기본 거르기
   - 보통주만(ETF·ETN·리츠·스팩·우선주 제외), 관리종목·거래정지·정리매매 제외
   - 금융(은행·증권·보험) 제외, 12월 결산만, 시가총액 1,000억 이상
2) 전자공시 다중회사 주요계정(한 번에 100개)으로 최근 분기 실적을 받아
   개선 중 / 개선 시작 / 확인 필요(적자 축소 포함) / 악화 로 나눈다.

결과: data/screen/earnings.json (공개 정보인 공시 실적만 담는다)
"""
import io
import json
import os
import sys
import urllib.request
import time
import zipfile

import collect as c

MASTER = "https://new.real.download.dws.co.kr/common/master/{}_code.mst.zip"
MIN_CAP = 1000            # 억원
OUT = "screen/earnings.json"

KOSPI_W = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1,
           1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
KOSPI_F = {"ind1": 2, "ind2": 3, "group": 0, "spac": 19, "bank": 18, "sec": 26, "ins": 28, "halt": 34, "liq": 35, "admin": 36, "settle": 52, "pref": 54, "cap": 65}
KOSDAQ_W = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
KOSDAQ_F = {"ind1": 2, "ind2": 3, "group": 0, "spac": 14, "bank": 13, "sec": 21, "ins": 23, "halt": 29, "liq": 30, "admin": 31, "settle": 47, "pref": 49, "cap": 59}


def split(s, widths):
    out, i = [], 0
    for w in widths:
        out.append(s[i:i + w].strip())
        i += w
    return out


def master(mkt):
    """전체 종목 파일 → [{code, name, mkt, cap, ...}]"""
    W, F = (KOSPI_W, KOSPI_F) if mkt == "kospi" else (KOSDAQ_W, KOSDAQ_F)
    n2 = sum(W)
    with urllib.request.urlopen(MASTER.format(mkt), timeout=60) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    text = z.read(z.namelist()[0]).decode("cp949", errors="replace")
    rows = []
    for line in text.splitlines():
        if len(line) <= n2:
            continue
        head, tail = line[:-n2], line[-n2:]
        code, name = head[0:9].strip(), head[21:].strip()
        f = split(tail, W)
        g = lambda k: f[F[k]]
        rows.append({"code": code, "name": name, "mkt": "코스피" if mkt == "kospi" else "코스닥", "group": g("group"),
                     "spac": g("spac") == "Y", "fin": "Y" in (g("bank"), g("sec"), g("ins")), "halt": g("halt") == "Y",
                     "liq": g("liq") == "Y", "admin": g("admin") == "Y", "settle": g("settle"), "pref": g("pref"),
                     "cap": c.num(g("cap"), int), "ind": ("P" if mkt == "kospi" else "Q") + (g("ind2") if g("ind2") not in ("", "0000") else g("ind1"))})
    return rows


def industry_names():
    """업종 코드 → 이름 (못 받으면 빈 표)"""
    try:
        with urllib.request.urlopen("https://new.real.download.dws.co.kr/common/master/idxcode.mst.zip", timeout=60) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        out = {}
        for row in z.read(z.namelist()[0]).decode("cp949", errors="replace").splitlines():
            if len(row) > 5:
                mk = "Q" if row[0] == "2" else "P"
                out[mk + row[1:5]] = row[5:45].strip() or row[3:43].strip()
        return out
    except Exception:
        return {}


def universe():
    rows, why = [], {}
    for mkt in ("kospi", "kosdaq"):
        rows += master(mkt)
    keep = []
    for r in rows:
        reason = ("ETF·리츠 등" if r["group"] != "ST" else "스팩" if r["spac"] else "우선주" if r["pref"] not in ("", "0") or not r["code"].endswith("0")
                  else "관리·정지" if (r["admin"] or r["halt"] or r["liq"]) else "금융" if r["fin"] else "12월 결산 아님" if r["settle"] not in ("12", "")
                  else "시총 1,000억 미만" if r["cap"] < MIN_CAP else None)
        if reason:
            why[reason] = why.get(reason, 0) + 1
        else:
            keep.append(r)
    return keep, len(rows), why


def periods():
    """실적 판정에 쓸 보고서: 2년 전 1분기부터 가장 최근까지 (오래된 것 → 최근)"""
    out = []
    for y in (c.NOW.year - 2, c.NOW.year - 1, c.NOW.year):
        out += [(y, "11013", "1Q"), (y, "11012", "2Q"), (y, "11014", "3Q"), (y, "11011", "4Q")]
    return out


def pick(rows):
    """한 회사·한 보고서의 행들 → {rev, op} (연결 우선, 없으면 별도)"""
    for fs in ("CFS", "OFS"):
        acc = {}
        for r in rows:
            if r.get("fs_div") != fs:
                continue
            name = (r.get("account_nm") or "").replace(" ", "")
            key = "rev" if name in ("매출액", "수익(매출액)", "영업수익", "매출") else "op" if name.startswith("영업이익") else None
            if key and key not in acc:
                acc[key] = c.num(r.get("thstrm_amount"), int)
        if "rev" in acc and "op" in acc:
            return acc
    return None


START = time.time()
SLOW = []


def fetch_all(corps):
    """{corp: {(y,'1Q'): {rev, op}}}. 지난 보고서는 바뀌지 않으니 받아 둔 것을 다시 쓰고,
    아직 못 받은 회사와 최근 2개 보고서의 '자료 없음' 회사(늦은 공시)만 하루 한 번 다시 묻는다."""
    cache = c.load("state/dart_q.json", {})
    data, calls = {}, 0
    ids = sorted(set(corps))
    plist = [(y, code, q) for y, code, q in periods() if not (y == c.NOW.year and q == "4Q")]
    recent = {f"{y}{q}" for y, _, q in plist[-2:]}
    for y, code, q in plist:
        key = f"{y}{q}"
        ent = cache.setdefault(key, {"asked": {}, "data": {}})
        retry = key in recent and c.EPOCH - ent.get("ts", 0) > 20 * 3600
        need = [x for x in ids if x not in ent["asked"] or (retry and x not in ent["data"])]
        for i in range(0, len(need), 100):
            if time.time() - START > 25 * 60:
                print("시간 초과(25분)로 실적 수집을 멈춥니다. 다음 실행에서 이어 받습니다.", flush=True)
                break
            part = need[i:i + 100]
            t0, ne = time.time(), len(c.DART_ERR)
            rows = c.dart("fnlttMultiAcnt.json", {"corp_code": ",".join(part), "bsns_year": str(y), "reprt_code": code})
            calls += 1
            SLOW.append(time.time() - t0)
            if rows is None and len(c.DART_ERR) > ne:
                continue                                 # 실패한 묶음은 묻지 않은 것으로 남겨 다음에 다시
            by = {}
            for r in rows or []:
                by.setdefault(r.get("corp_code"), []).append(r)
            for corp in part:
                ent["asked"][corp] = 1
                acc = pick(by.get(corp, []))
                if acc:
                    ent["data"][corp] = [acc["rev"], acc["op"]]
        if need:
            ent["ts"] = c.EPOCH
        for corp, (rev, op) in ent["data"].items():
            data.setdefault(corp, {})[(y, q)] = {"rev": rev, "op": op}
    keep = {f"{y}{q}" for y, _, q in plist}
    c.save("state/dart_q.json", {k: v for k, v in cache.items() if k in keep})
    return data, calls


def quarters(rep):
    """보고서별 금액 → 분기 금액 목록 [(라벨, rev, op)] (4분기 = 연간 − 1~3분기)"""
    out = []
    for y, _, q in periods():
        a = rep.get((y, q))
        if not a:
            continue
        rev, op = a["rev"], a["op"]
        if q == "4Q":
            parts = [rep.get((y, p)) for p in ("1Q", "2Q", "3Q")]
            if not all(parts):
                continue
            rev, op = rev - sum(p["rev"] for p in parts), op - sum(p["op"] for p in parts)
        out.append((f"{y % 100}.{q}", rev, op))
    return out


def judge(qs):
    """분기 실적 → (분류, 이유, 점수 0~50, 세부)"""
    if len(qs) < 6:
        return "nodata", "분기 실적이 6개 미만", 0, {}
    lab, rev, op = [q[0] for q in qs], [q[1] for q in qs], [q[2] for q in qs]
    L = len(qs) - 1
    if lab[L][:2] != lab[L - 4][:2] and False:
        pass
    def yoy_up(i):
        if i - 4 < 0:
            return None
        a, b = op[i], op[i - 4]
        return a > b and (a > 0)
    def growth(i):
        b = op[i - 4]
        return (op[i] - b) / abs(b) if b else None
    opm = [(op[i] / rev[i]) if rev[i] else None for i in range(len(qs))]
    turn = op[L] > 0 >= op[L - 4]
    g = growth(L)
    streak = 0
    for i in range(L, 3, -1):
        if yoy_up(i) and (op[i] > 0 >= op[i - 4] or (growth(i) or 0) >= 0.10):
            streak += 1
        else:
            break
    margin_up = all(opm[i] is not None for i in (L, L - 1, L - 2)) and opm[L] > opm[L - 1] > opm[L - 2]
    ry = [(rev[i] / rev[i - 4] - 1) if i >= 4 and rev[i - 4] else None for i in range(len(qs))]
    rev_accel = ry[L] is not None and ry[L - 1] is not None and ry[L] > ry[L - 1] and ry[L] > 0
    det = {"growth": g, "turn": turn, "streak": streak, "margin_up": margin_up, "rev_yoy": ry[L], "rev_accel": rev_accel,
           "opm": opm[L]}
    collapse = op[L - 1] > 0 and op[L] < 0.6 * op[L - 1]
    if streak >= 1 and not collapse:
        cls = "up" if streak >= 2 else "start"
        score = min(20, 10 + (10 if turn else min(10, (g or 0) * 20))) + min(10, streak * 4) + (10 if margin_up else 0) + (10 if rev_accel else 0)
        why = ("흑자전환" if turn else f"영업이익 전년비 {g * 100:+.0f}%") + (f", {streak}분기 연속 개선" if streak >= 2 else ", 이번 분기부터 개선") + \
              (", 이익률 2분기 연속 상승" if margin_up else "") + (", 매출 증가 가속" if rev_accel else "")
        return cls, why, round(score), det
    worse2 = op[L] < op[L - 4] and op[L - 1] < op[L - 5]
    to_loss = op[L] <= 0 < op[L - 4]
    if worse2 or to_loss:
        return "down", ("적자전환" if to_loss else "영업이익 2분기 연속 전년보다 감소"), 0, det
    if op[L] < 0 and op[L] > op[L - 4]:
        return "watch", "적자 폭이 줄어드는 중 (실적 확인 필요)", 5, det
    return "watch", "뚜렷한 개선·악화 없음 (실적 확인 필요)", 0, det


# ───────────────────────── R-2: 주가 판정 ─────────────────────────
PX = "state/screen_px.json"
WIN_FULL = ((140, 0), (280, 141), (420, 281))      # 약 1년 반 (120일선과 1년 상대강도에 충분)


def sma(a, n, i):
    return sum(a[i - n + 1:i + 1]) / n if i >= n - 1 else None


def price_data(k, codes, budget_end):
    """후보 종목 일봉 (날짜, 고가, 저가, 종가, 거래량). 받아 둔 것은 최근 구간만 이어 받는다."""
    px = c.load(PX, {})
    fresh = 0
    for code in codes:
        if time.time() > budget_end:
            print("시간 제한으로 일봉 받기를 멈춥니다. 다음 실행에서 이어 받습니다.", flush=True)
            break
        ent = px.get(code) or {}
        rows = ent.get("rows") or []
        if c.EPOCH - ent.get("ts", 0) < 10 * 3600 and rows:
            continue
        full = len(rows) < 200 or c.EPOCH - ent.get("full", 0) > 30 * 86400
        got = c.daily(k, code, windows=WIN_FULL if full else ((30, 0),))
        if not got:
            continue
        new = [[r[0], r[2], r[3], r[4], r[5]] for r in got]
        if not full:
            old = {r[0]: r for r in rows}
            # 겹치는 날의 종가가 1% 넘게 다르면 수정주가가 바뀐 것(분할 등) → 다음에 전체를 다시 받는다
            bad = any(abs(old[r[0]][3] - r[3]) > 0.01 * r[3] for r in new if r[0] in old and r[3])
            if bad:
                ent["full"] = 0
            old.update({r[0]: r for r in new})
            new = [old[d] for d in sorted(old)]
        else:
            ent["full"] = c.EPOCH
        ent["rows"], ent["ts"] = new[-400:], c.EPOCH
        px[code] = ent
        fresh += 1
    keep = set(codes)
    c.save(PX, {k2: v for k2, v in px.items() if k2 in keep})
    return {code: px[code]["rows"] for code in codes if code in px}, fresh


def features(rows, mkt):
    """일봉 → 변화 신호. rows: [날짜, 고, 저, 종, 거래량], mkt: {날짜: 시장 종가}"""
    if len(rows) < 130:
        return None
    d = [r[0] for r in rows]; h = [r[1] for r in rows]; l = [r[2] for r in rows]; cl = [r[3] for r in rows]; v = [r[4] for r in rows]
    t = len(rows) - 1
    ma20, ma60, ma120 = sma(cl, 20, t), sma(cl, 60, t), sma(cl, 120, t)
    ma60_10, ma60_20 = sma(cl, 60, t - 10), sma(cl, 60, t - 20)
    slope = ma60 / ma60_10 - 1
    slope_prev = ma60_10 / ma60_20 - 1
    cross = next((t - i for i in range(t, t - 20, -1) if sma(cl, 20, i) > sma(cl, 60, i) and sma(cl, 20, i - 1) <= sma(cl, 60, i - 1)), None)
    r20, r60 = cl[t] / cl[t - 20] - 1, cl[t] / cl[t - 60] - 1
    ext60 = cl[t] / ma60 - 1
    hi60, lo60 = max(h[t - 59:t + 1]), min(l[t - 59:t + 1])
    range60 = hi60 / lo60 - 1
    tr = [max(h[i] - l[i], abs(h[i] - cl[i - 1]), abs(l[i] - cl[i - 1])) / cl[i - 1] for i in range(1, t + 1)]
    atr_now = sum(tr[-20:]) / 20
    atr_then = sum(tr[-120:-60]) / 60
    vc = atr_now / atr_then if atr_then else 1
    g = [max(cl[i] - cl[i - 1], 0) for i in range(t - 13, t + 1)]; ls = [max(cl[i - 1] - cl[i], 0) for i in range(t - 13, t + 1)]
    rsi = 100 - 100 / (1 + (sum(g) / sum(ls))) if sum(ls) else 100
    upv = sum(v[i] for i in range(t - 19, t + 1) if cl[i] > cl[i - 1]); dnv = sum(v[i] for i in range(t - 19, t + 1) if cl[i] < cl[i - 1])
    acc = upv / dnv if dnv else 2
    val20 = sum(cl[i] * v[i] for i in range(t - 19, t + 1)) / 20 / 1e8          # 하루 평균 거래대금(억)
    rs = rs_prev = None
    if d[t] in mkt and d[t - 60] in mkt and d[t - 20] in mkt and d[t - 80] in mkt:
        rs = (cl[t] / cl[t - 60]) / (mkt[d[t]] / mkt[d[t - 60]])
        rs_prev = (cl[t - 20] / cl[t - 80]) / (mkt[d[t - 20]] / mkt[d[t - 80]])
    return {"date": d[t], "close": cl[t], "ma20": ma20, "ma60": ma60, "ma120": ma120, "slope": slope, "slope_prev": slope_prev, "cross": cross,
            "r20": r20, "r60": r60, "ext60": ext60, "range60": range60, "vc": vc, "rsi": rsi, "acc": acc, "val20": val20,
            "rs": rs, "rs_up": (rs is not None and rs_prev is not None and rs > rs_prev), "spark": cl[-120:]}


def price_state(f):
    """→ (상태, 이유)  상태: hot(과열) / early(상승 초기) / base(횡보·바닥) / down(하락) / other"""
    if f["r20"] > 0.30 or f["ext60"] > 0.40 or f["r60"] > 0.80 or f["rsi"] > 75:
        why = ("20일 " + f"{f['r20'] * 100:+.0f}%") if f["r20"] > 0.30 else ("60일선보다 " + f"{f['ext60'] * 100:+.0f}% 위") if f["ext60"] > 0.40 else \
              ("60일 " + f"{f['r60'] * 100:+.0f}%") if f["r60"] > 0.80 else f"RSI {f['rsi']:.0f}"
        return "hot", "이미 많이 오름 (" + why + ")"
    up_turn = f["slope"] > 0 and (f["slope_prev"] <= 0.01 or (f["cross"] is not None))
    if f["close"] > f["ma60"] and f["ma20"] > f["ma60"] and up_turn and f["ext60"] <= 0.20:
        return "early", ("60일선이 막 위로 돌아섬" if f["slope_prev"] <= 0.01 else f"20일선이 60일선을 {f['cross']}일 전 상향 돌파")
    if f["range60"] <= 0.25 and abs(f["slope"]) <= 0.02 and f["close"] >= f["ma120"] * 0.85:
        return "base", f"60일 동안 {f['range60'] * 100:.0f}% 범위에서 옆걸음"
    if f["close"] < f["ma60"] and f["slope"] < 0:
        return "down", "60일선 아래에서 내려가는 중"
    return "other", "뚜렷한 자리 없음"


def price_score(f, st):
    """오르기 전에 나타나는 변화에 점수 (0~50)"""
    sig, sc = [], 0
    if st == "early":
        sc += 12; sig.append("추세 전환 시작")
    if f["rs_up"]:
        sc += 10; sig.append("시장 대비 상대강도 개선")
    if f["vc"] < 0.8:
        sc += 10; sig.append(f"출렁임 축소 ({f['vc']:.2f}배)")
    if f["acc"] > 1.3 and f["r20"] < 0.10:
        sc += 10; sig.append("주가는 제자리인데 오르는 날 거래가 많음 (매집 흔적)")
    sc += max(0, 8 - int(max(f["ext60"], 0) * 40))          # 60일선에 가까울수록 (덜 오른 상태일수록) 가점
    return min(50, sc), sig


def grade_all(items, prices, mkt, names, exclude):
    feats = {code: features(rows, mkt) for code, rows in prices.items()}
    # 업종 흐름: 같은 업종 안에서 실적 개선 비율과 상대강도 중간값
    groups = {}
    for x in items:
        groups.setdefault(x.get("ind"), []).append(x)
    ind_ok = {}
    for key, xs in groups.items():
        known = [x for x in xs if x["cls"] != "nodata"]
        share = sum(1 for x in known if x["cls"] in ("up", "start")) / len(known) if known else 0
        rss = sorted(feats[x["code"]]["rs"] for x in xs if feats.get(x["code"]) and feats[x["code"]]["rs"] is not None)
        med = rss[len(rss) // 2] if rss else None
        ind_ok[key] = {"share": share, "rs": med, "n": len(xs), "strong": len(known) >= 3 and share >= 0.4 and (med or 0) > 1.0,
                       "name": names.get(key, key)}
    out, excl = [], []
    for x in items:
        if x["code"] in exclude:
            continue
        if x["cls"] == "down":
            continue
        f = feats.get(x["code"])
        if not f or f["val20"] < 10:                        # 일봉 부족·거래대금 하루 10억 미만은 판정하지 않음
            continue
        st, pwhy = price_state(f)
        ind = ind_ok.get(x.get("ind"), {})
        grade = None
        if st == "hot":
            excl.append({"code": x["code"], "name": x["name"], "why": pwhy, "ewhy": x["why"]})
            continue
        if x["cls"] in ("up", "start") and st == "early":
            grade = "A"
        elif x["cls"] in ("up", "start") and st == "base":
            grade = "B"
        elif x["cls"] == "watch" and ind.get("strong") and st in ("early", "base"):
            grade = "C"
        if not grade:
            continue
        ps, sig = price_score(f, st)
        out.append({"code": x["code"], "name": x["name"], "mkt": x["mkt"], "cap": x["cap"], "grade": grade, "score": x["score"] + ps,
                    "escore": x["score"], "pscore": ps, "ecls": x["cls"], "ewhy": x["why"], "pstate": st, "pwhy": pwhy, "signals": sig,
                    "ind": ind.get("name"), "ind_share": round(ind.get("share", 0), 2),
                    "q": x["q"], "op": x["op"], "rev": x["rev"],
                    "f": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items() if k != "spark"}, "spark": f["spark"]})
    order = {"A": 0, "B": 1, "C": 2}
    out.sort(key=lambda y: (order[y["grade"]], -y["score"]))
    return out, excl, feats


def main():
    if not c.DART_KEY:
        print("DART_API_KEY 가 없습니다.")
        return 1
    t0 = time.time()
    try:
        uni, total, why = universe()
        print(f"전체 종목 파일 {time.time() - t0:.1f}초", flush=True)
    except Exception as e:
        print("전체 종목 파일을 받지 못했습니다:", type(e).__name__, e)
        return 1
    print(f"전체 {total}종목 → 기본 거르기 후 {len(uni)}종목  (제외: {why})")
    t0 = time.time()
    cmap = c.corp_map()
    print(f"고유번호 표 {len(cmap)}개, {time.time() - t0:.1f}초", flush=True)
    corp_of = {r["code"]: cmap.get(r["code"]) for r in uni}
    rep, calls = fetch_all([v for v in corp_of.values() if v])
    items, counts = [], {}
    for r in uni:
        corp = corp_of.get(r["code"])
        qs = quarters(rep.get(corp, {})) if corp else []
        cls, why_, score, det = judge(qs)
        counts[cls] = counts.get(cls, 0) + 1
        items.append({"code": r["code"], "name": r["name"], "mkt": r["mkt"], "cap": r["cap"], "ind": r.get("ind"), "cls": cls, "why": why_, "score": score,
                      "q": [x[0] for x in qs[-6:]], "rev": [x[1] for x in qs[-6:]], "op": [x[2] for x in qs[-6:]],
                      "det": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in det.items()}})
    items.sort(key=lambda x: (-x["score"], -x["cap"]))
    latest = max((x["q"][-1] for x in items if x["q"]), default=None)
    c.save(OUT, {"updated": c.NOW.strftime("%Y-%m-%d %H:%M"), "latest_q": latest, "total": total, "universe": len(uni), "excluded": why,
                 "counts": counts, "items": items})
    if SLOW:
        print(f"전자공시 평균 {sum(SLOW) / len(SLOW):.1f}초, 최장 {max(SLOW):.1f}초")
    # ── R-2 ──
    cfg = json.load(open(c.CONFIG, encoding="utf-8"))
    exclude = {s2["code"] for s2 in cfg.get("stocks", [])}           # 내 종목·관심 종목은 추천에서 뺀다
    cands = [x for x in items if x["cls"] in ("up", "start", "watch") and x["code"] not in exclude]
    cands.sort(key=lambda x: -x["cap"])
    t = c.read_token() or c.issue_token()
    if not t:
        print("한투 토큰을 받지 못해 주가 판정을 건너뜁니다.")
        return 0
    k = c.Kis(t)
    prices, fresh = price_data(k, [x["code"] for x in cands], START + 75 * 60)
    try:
        md = json.load(open(os.path.join(c.ROOT, "market_daily.json"), encoding="utf-8"))
        mkt = {r[0]: r[1] for r in md.get("rows", [])}
    except (OSError, ValueError):
        mkt = {}
    names = industry_names()
    graded, excl, feats = grade_all(items, prices, mkt, names, exclude)
    cnt = {g2: sum(1 for y in graded if y["grade"] == g2) for g2 in "ABC"}
    picks = [y["code"] for y in graded if y["grade"] == "A"][:3]
    if len(picks) < 3:
        picks += [y["code"] for y in graded if y["grade"] == "B"][:3 - len(picks)]
    asof = max((f2["date"] for f2 in feats.values() if f2), default=None)
    c.save("screen/result.json", {"updated": c.NOW.strftime("%Y-%m-%d %H:%M"), "asof": asof, "latest_q": latest, "universe": len(uni),
                                  "priced": len(prices), "counts": dict(cnt, hot=len(excl)), "picks": picks, "exclude_mine": sorted(exclude),
                                  "items": graded, "hot": excl[:40]})
    hist = c.load("screen/history.json", {})
    if asof:
        hist[str(asof)] = picks
    c.save("screen/history.json", dict(sorted(hist.items())[-120:]))
    print(f"일봉: 후보 {len(cands)}종목 중 {len(prices)}종목 보유 (이번에 받은 것 {fresh}), 한투 호출 {k.calls}회, 오류 {len(k.errors)}건")
    print(f"등급: A {cnt['A']} · B {cnt['B']} · C {cnt['C']} · 과열 제외 {len(excl)}  기준일 {asof}")
    for y in graded[:12]:
        print(f"  [{y['grade']}] {y['name']}({y['code']}) {y['score']}점 — {y['ewhy']} / {y['pwhy']} / {', '.join(y['signals'])}")
    print("추천:", [next(y['name'] for y in graded if y['code'] == p2) for p2 in picks])
    print(f"전자공시 호출 {calls}회, 오류 {len(c.DART_ERR)}건 {c.DART_ERR[:3]}")
    print("실적 분류:", {"up": "개선 중", "start": "개선 시작", "watch": "확인 필요", "down": "악화", "nodata": "자료 부족"} and counts)
    for x in items[:15]:
        print(f"  {x['name']}({x['code']}) {x['cls']} {x['score']}점 — {x['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
