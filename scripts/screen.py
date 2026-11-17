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
import zipfile

import collect as c

MASTER = "https://new.real.download.dws.co.kr/common/master/{}_code.mst.zip"
MIN_CAP = 1000            # 억원
OUT = "screen/earnings.json"

KOSPI_W = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1,
           1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
KOSPI_F = {"group": 0, "spac": 19, "bank": 18, "sec": 26, "ins": 28, "halt": 34, "liq": 35, "admin": 36, "settle": 52, "pref": 54, "cap": 65}
KOSDAQ_W = [2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
KOSDAQ_F = {"group": 0, "spac": 14, "bank": 13, "sec": 21, "ins": 23, "halt": 29, "liq": 30, "admin": 31, "settle": 47, "pref": 49, "cap": 59}


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
                     "cap": c.num(g("cap"), int)})
    return rows


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


def fetch_all(corps):
    """{corp: {(y,'1Q'): {rev, op}, ...}}  — 보고서 하나당 100개씩 묶어서 부른다."""
    data, calls = {}, 0
    ids = sorted(set(corps))
    for y, code, q in periods():
        if (y, q) > (c.NOW.year, "4Q") or (y == c.NOW.year and q == "4Q"):
            continue
        got = 0
        for i in range(0, len(ids), 100):
            rows = c.dart("fnlttMultiAcnt.json", {"corp_code": ",".join(ids[i:i + 100]), "bsns_year": str(y), "reprt_code": code})
            calls += 1
            by = {}
            for r in rows or []:
                by.setdefault(r.get("corp_code"), []).append(r)
            for corp, rs in by.items():
                acc = pick(rs)
                if acc:
                    data.setdefault(corp, {})[(y, q)] = acc
                    got += 1
        if not got and y == c.NOW.year:
            pass                                        # 아직 공시되지 않은 보고서
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


def main():
    if not c.DART_KEY:
        print("DART_API_KEY 가 없습니다.")
        return 1
    try:
        uni, total, why = universe()
    except Exception as e:
        print("전체 종목 파일을 받지 못했습니다:", type(e).__name__, e)
        return 1
    print(f"전체 {total}종목 → 기본 거르기 후 {len(uni)}종목  (제외: {why})")
    cmap = c.corp_map()
    corp_of = {r["code"]: cmap.get(r["code"]) for r in uni}
    rep, calls = fetch_all([v for v in corp_of.values() if v])
    items, counts = [], {}
    for r in uni:
        corp = corp_of.get(r["code"])
        qs = quarters(rep.get(corp, {})) if corp else []
        cls, why_, score, det = judge(qs)
        counts[cls] = counts.get(cls, 0) + 1
        items.append({"code": r["code"], "name": r["name"], "mkt": r["mkt"], "cap": r["cap"], "cls": cls, "why": why_, "score": score,
                      "q": [x[0] for x in qs[-6:]], "rev": [x[1] for x in qs[-6:]], "op": [x[2] for x in qs[-6:]],
                      "det": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in det.items()}})
    items.sort(key=lambda x: (-x["score"], -x["cap"]))
    latest = max((x["q"][-1] for x in items if x["q"]), default=None)
    c.save(OUT, {"updated": c.NOW.strftime("%Y-%m-%d %H:%M"), "latest_q": latest, "total": total, "universe": len(uni), "excluded": why,
                 "counts": counts, "items": items})
    print(f"전자공시 호출 {calls}회, 오류 {len(c.DART_ERR)}건 {c.DART_ERR[:3]}")
    print("실적 분류:", {"up": "개선 중", "start": "개선 시작", "watch": "확인 필요", "down": "악화", "nodata": "자료 부족"} and counts)
    for x in items[:15]:
        print(f"  {x['name']}({x['code']}) {x['cls']} {x['score']}점 — {x['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
