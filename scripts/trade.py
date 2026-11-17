#!/usr/bin/env python3
"""모의투자 계좌 전용 자동 운영. 실전 서버로는 절대 보내지 않는다.

수집(collect.py)이 끝난 뒤 같은 작업 안에서 불린다. 시각에 따라 하는 일이 다르다.
  - 평일 15:40 이후, 오늘 일봉이 확정되면: 내일 아침 주문할 계획(plan)을 만든다.
  - 평일 08:30~10:30: 전날 계획대로 시장가 주문을 넣는다(config 의 orders 가 false 면 기록만 = 연습 모드).
  - 그 밖에는: 잔고와 체결을 확인해 수익률을 갱신한다.

공개 저장소라 계좌번호·금액·수량은 파일에도 로그에도 남기지 않는다. 비율(수익률)과 가격만 남긴다.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import collect as c

MOCK = "https://openapivts.koreainvestment.com:29443"      # 모의투자 서버. 이 주소 말고는 쓰지 않는다.
T = "/uapi/domestic-stock/v1/trading/"
CANO = os.environ.get("KIS_ACCOUNT_NO", "").strip()
PRD = os.environ.get("KIS_ACCOUNT_PRD", "").strip()
DATA = os.environ.get("DATA_DIR", "data")
STATE = os.path.join(DATA, "trade", "state.json")
HERE = os.path.dirname(os.path.abspath(__file__))
HM = c.NOW.strftime("%H%M")
TODAY_ISO = c.NOW.strftime("%Y-%m-%d")
WEEKDAY = c.NOW.weekday() < 5
COST = {"stock": (0.00014, 0.002), "etf": (0.00014, 0.0)}   # (수수료, 매도세) — 엔진과 같은 값


def log(*a):
    print("[모의계좌]", *a)


def mock_token():
    t = c.read_token()
    if t and t.get("base") == MOCK:
        return t
    status, js = c.http("POST", MOCK + "/oauth2/tokenP", {"content-type": "application/json"},
                        {"grant_type": "client_credentials", "appkey": c.APP_KEY, "appsecret": c.APP_SECRET})
    token = js.get("access_token") if isinstance(js, dict) else None
    if status == 200 and token:
        return {"token": token, "label": "모의투자", "base": MOCK, "exp": c.EPOCH + 3600}
    log(f"모의투자 토큰 발급 실패: HTTP {status}")
    return None


def post(k, path, tr_id, body):
    """주문용 POST. 모의투자 서버가 아니면 보내지 않는다."""
    if k.t.get("base") != MOCK:
        raise SystemExit("모의투자 서버가 아니어서 주문을 막았습니다.")
    k.calls += 1
    headers = {"content-type": "application/json; charset=utf-8", "authorization": "Bearer " + k.t["token"],
               "appkey": c.APP_KEY, "appsecret": c.APP_SECRET, "tr_id": tr_id, "custtype": "P"}
    status, js = c.http("POST", MOCK + path, headers, body)
    js = js if isinstance(js, dict) else {}
    ok = status == 200 and str(js.get("rt_cd")) == "0"
    return ok, js, status


def balance(k):
    js = k.get(T + "inquire-balance", "VTTC8434R",
               {"CANO": CANO, "ACNT_PRDT_CD": PRD, "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}, "잔고 조회")
    if not js:
        return None
    hold = {}
    for r in js.get("output1") or []:
        q = c.num(r.get("hldg_qty"), int)
        if q > 0:
            hold[r.get("pdno")] = {"qty": q, "avg": c.num(r.get("pchs_avg_pric")), "px": c.num(r.get("prpr"))}
    return hold


def fills_today(k):
    d = c.NOW.strftime("%Y%m%d")
    js = k.get(T + "inquire-daily-ccld", "VTTC0081R",
               {"CANO": CANO, "ACNT_PRDT_CD": PRD, "INQR_STRT_DT": d, "INQR_END_DT": d, "SLL_BUY_DVSN_CD": "00", "PDNO": "",
                "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_3": "00", "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_1": "",
                "EXCG_ID_DVSN_CD": "KRX", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}, "체결 조회")
    out = {}
    for r in (js or {}).get("output1") or []:
        out[str(r.get("odno", "")).lstrip("0")] = {"filled": c.num(r.get("tot_ccld_qty"), int), "ordered": c.num(r.get("ord_qty"), int),
                                                     "avg": c.num(r.get("avg_prvs")), "rejected": c.num(r.get("rjct_qty"), int)}
    return out


def order(k, code, side, qty):
    body = {"CANO": CANO, "ACNT_PRDT_CD": PRD, "PDNO": code, "ORD_DVSN": "01", "ORD_QTY": str(int(qty)), "ORD_UNPR": "0",
            "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if side == "sell" else "", "CNDT_PRIC": ""}
    ok, js, status = post(k, T + "order-cash", "VTTC0011U" if side == "sell" else "VTTC0012U", body)
    out = js.get("output") or {}
    return ok, str(out.get("ODNO", "")).lstrip("0"), f"{js.get('msg_cd', status)} {js.get('msg1', '')}".strip()


def signals(codes, mode, entries):
    p = subprocess.run(["node", os.path.join(HERE, "signals.js"), json.dumps({"codes": codes, "mode": mode, "entries": entries})],
                       capture_output=True, text=True, env=dict(os.environ, DATA_DIR=DATA), timeout=60)
    if p.returncode != 0:
        log("신호 계산 실패:", p.stderr.strip()[:300])
        return None
    return {r["code"]: r for r in json.loads(p.stdout)}


def last_bar(code):
    try:
        d = json.load(open(os.path.join(DATA, "stocks", code + ".json"), encoding="utf-8"))
        r = d["daily"][-1]
        return str(r[0]), r[4]
    except (OSError, ValueError, KeyError, IndexError):
        return "", 0


def main():
    cfg = json.load(open(c.CONFIG, encoding="utf-8"))
    tc = cfg.get("trade") or {}
    names = {s["code"]: s for s in cfg["stocks"]}
    if not tc.get("enabled"):
        log("꺼져 있습니다(config trade.enabled=false).")
        return 0
    if not (CANO.isdigit() and len(CANO) == 8 and PRD.isdigit() and len(PRD) == 2):
        log("계좌 시크릿이 없거나 형식이 맞지 않아 건너뜁니다.")
        return 0
    print(f"::add-mask::{CANO}")
    codes, mode, cap, live = tc["codes"], tc.get("mode", "trend"), float(tc.get("cap", 2500000)), bool(tc.get("orders"))
    st = c.load("trade/state.json", {})
    st.setdefault("start", TODAY_ISO)
    st.setdefault("slots", {})
    st.setdefault("orders", [])
    for code in codes:
        st["slots"].setdefault(code, {"mult": 1.0, "entry_date": None, "entry_px": None})
    st["mode"], st["live"] = mode, live

    t = mock_token()
    if not t:
        return 0
    k = c.Kis(t)
    hold = balance(k)
    if hold is None:
        log("잔고 조회 실패:", "; ".join(k.errors))
        return 0

    for o in st["orders"]:                       # 지난날 보낸 주문이 끝내 체결 확인이 안 되면 정리
        if o.get("status") == "sent" and o.get("sent_day") != TODAY_ISO:
            o["status"] = "expired"
    # 1) 보낸 주문의 체결 확인 → 진입가·누적 수익 배수 갱신
    pend = [o for o in st["orders"] if o.get("status") == "sent" and o.get("sent_day") == TODAY_ISO]
    if pend:
        fl = fills_today(k)
        for o in pend:
            f = fl.get(o.get("odno", ""))
            if not f:
                continue
            if f["filled"] > 0 and f["filled"] >= f["ordered"]:
                o["status"], o["fill_px"] = "filled", f["avg"]
                s = st["slots"][o["code"]]
                if o["side"] == "buy":
                    s["entry_date"], s["entry_px"] = TODAY_ISO, f["avg"]
                elif s.get("entry_px"):
                    fee, tax = COST.get(names[o["code"]]["type"], COST["stock"])
                    o["ret"] = f["avg"] * (1 - fee - tax) / (s["entry_px"] * (1 + fee)) - 1
                    s["mult"] = s["mult"] * (1 + o["ret"])
                    s["entry_date"], s["entry_px"] = None, None
            elif f["rejected"] > 0:
                o["status"] = "rejected"
    # 계좌에 있는데 진입 기록이 없으면(직접 산 경우 등) 평균단가로 맞추고, 계좌에 없는데 기록만 있으면 지운다
    for code in codes:
        s = st["slots"][code]
        if code in hold and not s.get("entry_px"):
            s["entry_date"], s["entry_px"] = s.get("entry_date") or TODAY_ISO, hold[code]["avg"]
        if code not in hold and s.get("entry_px") and not any(o.get("status") == "sent" and o["code"] == code for o in st["orders"]):
            s["entry_date"], s["entry_px"] = None, None

    # 2) 아침: 전날 계획대로 주문 (중복 방지: 같은 계획·종목·방향은 한 번만)
    plan = st.get("plan") or {}
    if WEEKDAY and "0830" <= HM < "1030" and plan.get("items") and plan.get("date") != TODAY_ISO:
        for it in plan["items"]:
            oid = f"{plan['date']}-{it['code']}-{it['side']}"
            o = next((x for x in st["orders"] if x["id"] == oid), None)
            if o and o["status"] in ("sent", "filled", "paper", "rejected", "skipped", "failed"):
                continue
            if not o:
                o = {"id": oid, "plan_date": plan["date"], "code": it["code"], "side": it["side"], "why": it.get("why", ""), "tries": 0}
                st["orders"].append(o)
            code, side = it["code"], it["side"]
            if side == "buy" and code in hold:
                o.update(status="skipped", msg="이미 보유 중")
                continue
            if side == "sell" and code not in hold:
                o.update(status="skipped", msg="보유하지 않음")
                continue
            qty = hold[code]["qty"] if side == "sell" else int(cap // (max(it.get("ref", 0), 1) * 1.05))
            if qty <= 0:
                o.update(status="skipped", msg="한도 안에서 살 수 있는 수량이 없음")
                continue
            if not live:
                o.update(status="paper", sent_day=TODAY_ISO, msg="연습 모드: 주문하지 않고 기록만 함")
                continue
            ok, odno, msg = order(k, code, side, qty)
            o["tries"] += 1
            o["msg"] = msg[:80]
            if ok:
                o.update(status="sent", odno=odno, sent_day=TODAY_ISO, sent_at=c.NOW.strftime("%H:%M"))
                log(f"{names[code]['name']} {'매수' if side == 'buy' else '매도'} 주문 접수")
            else:
                o["status"] = "failed" if o["tries"] >= 6 else "retry"
                log(f"{names[code]['name']} 주문 실패({o['tries']}회): {msg[:80]}")

    # 3) 마감 뒤: 오늘 일봉이 확정되면 내일 아침 계획을 만든다
    if WEEKDAY and HM >= "1540":
        dates = {last_bar(code)[0] for code in codes}
        today = c.NOW.strftime("%Y%m%d")
        if dates == {today} and plan.get("date") != TODAY_ISO:
            entries = {code: (st["slots"][code]["entry_date"] if code in hold else None) for code in codes}
            sig = signals(codes, mode, entries)
            if sig:
                items, notes = [], {}
                for code in codes:
                    r = sig.get(code, {})
                    if r.get("action"):
                        items.append({"code": code, "side": r["action"], "why": r["why"], "ref": r["close"]})
                    notes[code] = {"why": r.get("why") or r.get("error", ""), "stop": r.get("stop"), "mok": r.get("mok")}
                st["plan"] = {"date": TODAY_ISO, "made_at": c.NOW.strftime("%H:%M"), "items": items, "notes": notes}
                log(f"내일 계획: {len(items)}건")

    # 4) 공개용 상태: 비율만
    rows, vals = [], []
    for code in codes:
        s, h = st["slots"][code], hold.get(code)
        cur = (h["px"] / s["entry_px"]) if (h and s.get("entry_px")) else 1.0
        v = s["mult"] * cur
        vals.append(v)
        rows.append({"code": code, "name": names[code]["name"], "held": bool(h), "entry_date": s.get("entry_date"),
                     "entry_px": s.get("entry_px"), "open_ret": (cur - 1) if h else None, "slot_ret": v - 1})
    st["status"] = {"updated": c.NOW.strftime("%Y-%m-%d %H:%M"), "total_ret": sum(vals) / len(vals) - 1 if vals else 0,
                    "rows": rows, "held": sum(1 for r in rows if r["held"])}
    st["orders"] = st["orders"][-60:]
    c.save("trade/state.json", st)
    log(f"상태 갱신 (보유 {st['status']['held']}종목, 호출 {k.calls}회{', 오류 ' + str(len(k.errors)) + '건' if k.errors else ''})")
    return 0


def probe():
    """주문 형식 점검: 하한가보다 훨씬 낮은 1원 지정가 1주 매수를 보내 거부 사유만 확인한다(체결될 수 없음)."""
    if not (CANO.isdigit() and len(CANO) == 8 and PRD.isdigit() and len(PRD) == 2):
        log("계좌 시크릿 형식이 맞지 않습니다.")
        return 1
    print(f"::add-mask::{CANO}")
    t = mock_token()
    if not t:
        return 1
    k = c.Kis(t)
    hold = balance(k)
    log("잔고 조회", "성공" if hold is not None else "실패 " + "; ".join(k.errors))
    body = {"CANO": CANO, "ACNT_PRDT_CD": PRD, "PDNO": "005930", "ORD_DVSN": "00", "ORD_QTY": "1", "ORD_UNPR": "1",
            "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "", "CNDT_PRIC": ""}
    ok, js, status = post(k, T + "order-cash", "VTTC0012U", body)
    log(f"주문 점검 응답: 접수={'예' if ok else '아니오'} HTTP {status} {js.get('msg_cd', '')} {js.get('msg1', '')}")
    if ok:   # 혹시 접수되면 바로 취소한다
        odno = str((js.get("output") or {}).get("ODNO", ""))
        ok2, js2, _ = post(k, T + "order-rvsecncl", "VTTC0013U",
                           {"CANO": CANO, "ACNT_PRDT_CD": PRD, "KRX_FWDG_ORD_ORGNO": (js.get("output") or {}).get("KRX_FWDG_ORD_ORGNO", ""),
                            "ORGN_ODNO": odno, "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": "0", "ORD_UNPR": "0",
                            "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX"})
        log(f"취소: {'성공' if ok2 else '실패'} {js2.get('msg_cd', '')} {js2.get('msg1', '')}")
    f = fills_today(k)
    log("체결 조회", "성공" if not any("체결" in e for e in k.errors) else "실패", f"(오늘 주문 {len(f)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(probe() if "probe" in sys.argv[1:] else main())
