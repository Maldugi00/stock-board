#!/usr/bin/env python3
"""모의투자 계좌 전용 자동 주문. 실전 서버로는 절대 보내지 않는다.

  python3 scripts/trade.py balance   잔고 조회 시험 (주문 없음)
  python3 scripts/trade.py run       수집 직후 매번 실행: 체결 확인 → 아침 주문 → 마감 뒤 내일 계획 → 공개용 요약

규칙
- 주소는 모의투자 서버(MOCK)로 고정. 토큰도 거기서만 받는다.
- config/trade.json 의 종목만, 종목당 하루 한 번, 종목당 per_stock 원 이하.
- 신호는 장 마감 뒤 확정 일봉으로 계산하고, 다음 거래일 장이 열리면 시장가로 주문한다.
- 공개 저장소라 수량·금액은 로그와 공개 파일에 남기지 않는다. 장부는 암호화해 state/ledger.enc 에 둔다.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse

import collect as c

MOCK = "https://openapivts.koreainvestment.com:29443"      # 모의투자 서버. 이 주소 말고는 쓰지 않는다.
T = "/uapi/domestic-stock/v1/trading/"
CANO = os.environ.get("KIS_ACCOUNT_NO", "").strip()
PRD = os.environ.get("KIS_ACCOUNT_PRD", "").strip()
LEDGER = os.path.join(c.ROOT, "state", "ledger.enc")
PUBLIC = "trade/public.json"
HM = c.NOW.hour * 100 + c.NOW.minute
TODAY = c.TODAY                                            # YYYYMMDD
D = lambda s: f"{s[:4]}-{s[4:6]}-{s[6:]}"


class Mock:
    """모의투자 서버만 부르는 클라이언트."""

    def __init__(self):
        self.token = None
        self.errors = []

    def auth(self, force=False):
        t = None if force else c.read_token()
        if t and t.get("base") == MOCK:
            self.token = t["token"]
        else:
            status, js = c.http("POST", MOCK + "/oauth2/tokenP", {"content-type": "application/json"},
                                {"grant_type": "client_credentials", "appkey": c.APP_KEY, "appsecret": c.APP_SECRET})
            self.token = js.get("access_token") if isinstance(js, dict) else None
            if not self.token:
                print(f"모의투자 토큰 발급 실패: HTTP {status}")
                return False
        print(f"::add-mask::{self.token}")
        return True

    def call(self, method, path, tr_id, data, what):
        for attempt in range(3):
            time.sleep(c.GAP)
            headers = {"content-type": "application/json; charset=utf-8", "authorization": "Bearer " + self.token,
                       "appkey": c.APP_KEY, "appsecret": c.APP_SECRET, "tr_id": tr_id, "custtype": "P"}
            if method == "GET":
                status, js = c.http("GET", MOCK + path + "?" + urllib.parse.urlencode(data), headers)
            else:
                status, js = c.http("POST", MOCK + path, headers, data)
            js = js if isinstance(js, dict) else {}
            if status == 200 and str(js.get("rt_cd")) == "0":
                return js
            code = js.get("msg_cd", "")
            if code in ("EGW00123", "EGW00121") and attempt == 0 and self.auth(force=True):
                continue
            if method == "GET" and (code == "EGW00201" or status in (0, 500, 502, 503)):   # 조회만 다시 시도. 주문은 중복 위험이 있어 다시 보내지 않는다.
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        self.errors.append(f"{what}: HTTP {status} {js.get('msg_cd', '')} {str(js.get('msg1', '')).strip()[:60]}")
        return None

    def balance(self):
        js = self.call("GET", T + "inquire-balance", "VTTC8434R",
                       {"CANO": CANO, "ACNT_PRDT_CD": PRD, "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
                        "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                        "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}, "잔고 조회")
        if not js:
            return None
        tot = (js.get("output2") or [{}])[0]
        return {"hold": {r.get("pdno"): c.num(r.get("hldg_qty"), int) for r in js.get("output1") or [] if c.num(r.get("hldg_qty"), int) > 0},
                "cash": c.num(tot.get("dnca_tot_amt")), "total": c.num(tot.get("tot_evlu_amt"))}

    def order(self, code, side, qty):
        js = self.call("POST", T + "order-cash", "VTTC0012U" if side == "buy" else "VTTC0011U",
                       {"CANO": CANO, "ACNT_PRDT_CD": PRD, "PDNO": code, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0",
                        "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if side == "sell" else "", "CNDT_PRIC": ""}, "주문")
        return (js.get("output") or {}).get("ODNO") if js else None

    def fills(self):
        """오늘 체결 내역 → {주문번호: (체결수량, 체결금액)}"""
        js = self.call("GET", T + "inquire-daily-ccld", "VTTC0081R",
                       {"CANO": CANO, "ACNT_PRDT_CD": PRD, "INQR_STRT_DT": TODAY, "INQR_END_DT": TODAY, "SLL_BUY_DVSN_CD": "00", "PDNO": "",
                        "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_3": "00", "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_1": "",
                        "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}, "체결 조회")
        if not js:
            return None
        return {str(r.get("odno", "")).lstrip("0"): (c.num(r.get("tot_ccld_qty"), int), c.num(r.get("tot_ccld_amt"))) for r in js.get("output1") or []}


def load_ledger():
    try:
        dec = c._openssl(["-d"], open(LEDGER, "rb").read())
        return json.loads(dec.decode()) if dec else None
    except (OSError, ValueError):
        return None


def save_ledger(led):
    enc = c._openssl([], json.dumps(led, ensure_ascii=False).encode())
    if enc:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        open(LEDGER, "wb").write(enc)


def signals(cfg):
    try:
        out = subprocess.run(["node", os.path.join(os.path.dirname(__file__), "plan.js"), c.ROOT, ",".join(cfg["codes"]), cfg.get("mode", "trend")],
                             capture_output=True, text=True, timeout=60)
        return {s["code"]: s for s in json.loads(out.stdout)} if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def log(led, code, name, act, why, price=None, ret=None, note=None):
    led["log"].append({"d": D(TODAY), "t": c.NOW.strftime("%H:%M"), "code": code, "name": name, "act": act, "why": why, "px": price, "ret": ret, "note": note})
    led["log"] = led["log"][-60:]


def run(cfg, m):
    led = load_ledger()
    sig = signals(cfg)
    if sig is None:
        print("신호 계산 실패 (node)")
        return 1
    bal = m.balance()
    if bal is None:
        print("잔고 조회 실패:", "; ".join(m.errors))
        return 1
    if led is None:                                         # 첫 실행: 장부를 만든다
        led = {"start": D(TODAY), "total0": bal["total"], "slots": {}, "plan": None, "planned_on": None, "orders": {}, "log": [], "live": bool(cfg.get("live"))}
        print("장부를 새로 만들었습니다.")
    for code in cfg["codes"]:
        led["slots"].setdefault(code, {"cash": float(cfg["per_stock"]), "sh": 0, "hi": 0.0, "cost": 0.0})
    live = bool(cfg.get("live"))
    today_orders = led["orders"].setdefault(TODAY, {})
    led["orders"] = {d: v for d, v in led["orders"].items() if d >= c.days_ago(10)}

    # 1) 체결 확인: 오늘 보낸 주문이 체결됐으면 장부에 반영
    pending = [o for o in today_orders.values() if o.get("status") == "sent"]
    if pending:
        f = m.fills()
        for code, o in today_orders.items():
            if o.get("status") != "sent" or f is None:
                continue
            qty, amt = f.get(str(o.get("odno", "")).lstrip("0"), (0, 0.0))
            if qty <= 0:
                continue
            s, name = led["slots"][code], (sig.get(code) or {}).get("name", code)
            px = amt / qty if qty else None
            if o["side"] == "buy":
                s["sh"], s["cash"], s["cost"], s["hi"] = qty, s["cash"] - amt, amt, 0.0
                log(led, code, name, "매수 체결", o["why"], px)
            else:
                ret = amt / s["cost"] - 1 if s.get("cost") else None
                s["cash"], s["sh"], s["cost"], s["hi"] = s["cash"] + amt, 0, 0.0, 0.0
                log(led, code, name, "매도 체결", o["why"], px, ret)
            o["status"] = "filled"
            print(f"체결 반영: {name} {'매수' if o['side'] == 'buy' else '매도'}")

    # 2) 아침 주문: 어제(이전 거래일) 마감 뒤 세운 계획을, 오늘 장이 열려 있으면 시장가로
    plan = led.get("plan")
    if plan and plan["date"] < TODAY and not plan.get("done") and 900 <= HM < 1500:
        if plan["date"] < c.days_ago(6):
            plan["done"] = "expired"
        else:
            left = 0
            for it in plan["items"]:
                code, side, s, g = it["code"], it["side"], led["slots"][it["code"]], sig.get(it["code"]) or {}
                name = g.get("name", code)
                if code not in cfg["codes"] or code in today_orders:
                    continue
                if not (g.get("today") == D(TODAY) and g.get("open_today")):      # 오늘 분봉이 아직 없으면 장이 안 열린 것 → 다음 실행에서 다시
                    left += 1
                    continue
                held = bal["hold"].get(code, 0)
                if side == "buy":
                    if held > 0 or s["sh"] > 0:
                        today_orders[code] = {"side": side, "status": "skip", "why": it["why"]}
                        continue
                    qty = int(min(s["cash"], float(cfg["per_stock"])) // (g["price"] * 1.01))
                    if qty < 1:
                        today_orders[code] = {"side": side, "status": "skip", "why": it["why"]}
                        log(led, code, name, "매수 건너뜀", it["why"], note="종목 예산으로 1주를 살 수 없어요")
                        continue
                else:
                    qty = min(s["sh"], held) if live else s["sh"]
                    if qty < 1:
                        today_orders[code] = {"side": side, "status": "skip", "why": it["why"]}
                        continue
                if not live:                                 # 연습 모드: 보내지 않고 지금 가격에 체결된 것으로 적는다
                    amt = qty * g["price"]
                    if side == "buy":
                        s["sh"], s["cash"], s["cost"], s["hi"] = qty, s["cash"] - amt, amt, 0.0
                        log(led, code, name, "매수 (연습)", it["why"], g["price"])
                    else:
                        ret = amt / s["cost"] - 1 if s.get("cost") else None
                        s["cash"], s["sh"], s["cost"], s["hi"] = s["cash"] + amt, 0, 0.0, 0.0
                        log(led, code, name, "매도 (연습)", it["why"], g["price"], ret)
                    today_orders[code] = {"side": side, "status": "filled", "why": it["why"]}
                    continue
                odno = m.order(code, side, qty)
                if odno:
                    today_orders[code] = {"side": side, "status": "sent", "odno": odno, "why": it["why"]}
                    log(led, code, name, "매수 주문" if side == "buy" else "매도 주문", it["why"])
                    print(f"주문 전송: {name} {'매수' if side == 'buy' else '매도'}")
                else:
                    it["tries"] = it.get("tries", 0) + 1
                    err = m.errors[-1] if m.errors else "알 수 없는 오류"
                    print(f"주문 실패: {name} — {err}")
                    if it["tries"] >= 3:
                        today_orders[code] = {"side": side, "status": "failed", "why": it["why"]}
                        log(led, code, name, "주문 실패", it["why"], note=err[:80])
                    else:
                        left += 1
            if not left:
                plan["done"] = TODAY

    # 3) 마감 뒤: 확정 일봉으로 내일 할 일을 정한다 (하루 한 번)
    ready = [g for g in sig.values() if not g.get("error") and g.get("closed") and g.get("date") == D(TODAY) and g.get("open_today")]
    if HM >= 1540 and led.get("planned_on") != TODAY and len(ready) == len(cfg["codes"]):
        items = []
        for code in cfg["codes"]:
            g, s = sig[code], led["slots"][code]
            if s["sh"] > 0:
                s["hi"] = max(s.get("hi") or 0.0, g["high"])
                if g["close"] < s["hi"] - g["atr_mult"] * g["atr"]:
                    items.append({"code": code, "side": "sell", "why": "트레일링 스탑"})
            elif g["buy"] and g["mok"]:
                items.append({"code": code, "side": "buy", "why": g["why"]})
        led["plan"] = {"date": TODAY, "items": items}
        led["planned_on"] = TODAY
        led["sig"] = {code: {"buy": sig[code]["buy"], "mok": sig[code]["mok"], "up": sig[code]["up"]} for code in cfg["codes"]}
        print(f"내일 계획: 주문 {len(items)}건")

    # 4) 공개용 요약: 수익률과 상태만 (수량·금액 없음)
    budget = float(cfg["budget"])
    if live and led.get("total0"):
        ret = (bal["total"] - led["total0"]) / budget
    else:
        ret = sum(s["cash"] + s["sh"] * (sig.get(code) or {}).get("price", 0) for code, s in led["slots"].items() if code in cfg["codes"]) / budget - 1
    slots = []
    for code in cfg["codes"]:
        s, g = led["slots"][code], sig.get(code) or {}
        value = s["cash"] + s["sh"] * g.get("price", 0)
        pl = next((it for it in (led.get("plan") or {}).get("items", []) if it["code"] == code), None) if not (led.get("plan") or {}).get("done") else None
        od = today_orders.get(code)
        gs = (led.get("sig") or {}).get(code) or {}
        slots.append({"code": code, "name": g.get("name", code), "state": "보유" if s["sh"] > 0 else "현금", "ret": round(value / float(cfg["per_stock"]) - 1, 4),
                      "pos": round(s["sh"] * g.get("price", 0) / s["cost"] - 1, 4) if s["sh"] > 0 and s.get("cost") else None,
                      "plan": {"side": pl["side"], "why": pl["why"]} if pl else None,
                      "order": {"side": od["side"], "status": od["status"]} if od else None,
                      "blocked": bool(gs.get("buy") and not gs.get("mok") and s["sh"] == 0)})
    pub = {"updated": c.NOW.strftime("%Y-%m-%d %H:%M"), "live": live, "start": led["start"], "mode": cfg.get("mode", "trend"), "ret": round(ret, 4),
           "planned_on": D(led["planned_on"]) if led.get("planned_on") else None, "slots": slots,
           "log": [{k: v for k, v in e.items() if v is not None} for e in led["log"][-12:]][::-1],
           "errors": [e[:100] for e in m.errors][-3:]}
    c.save(PUBLIC, pub)
    save_ledger(led)
    print(f"모의계좌 처리 완료 ({'실제 모의 주문' if live else '연습 모드'}). 오류 {len(m.errors)}건")
    return 0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "balance"
    if not (c.APP_KEY and c.APP_SECRET):
        print("KIS_APP_KEY / KIS_APP_SECRET 이 없습니다.")
        return 1
    if not (CANO.isdigit() and len(CANO) == 8 and PRD.isdigit() and len(PRD) == 2):
        print(f"계좌 시크릿 형식이 맞지 않습니다: KIS_ACCOUNT_NO 는 숫자 8자리(지금 {len(CANO)}자), KIS_ACCOUNT_PRD 는 숫자 2자리(지금 {len(PRD)}자).")
        return 1
    print(f"::add-mask::{CANO}")
    m = Mock()
    if not m.auth():
        return 1
    if mode == "balance":
        b = m.balance()
        if not b:
            print("잔고 조회 실패:", "; ".join(m.errors))
            return 1
        print(f"잔고 조회 성공 (모의투자). 보유 종목 {len(b['hold'])}개, 예수금 {'있음' if b['cash'] > 0 else '없음(0원)'}, 총평가 {'있음' if b['total'] > 0 else '없음(0원)'}")
        return 0
    cfg = json.load(open(os.environ.get("TRADE_CONFIG", "config/trade.json"), encoding="utf-8"))
    if not cfg.get("enabled"):
        old = c.load(PUBLIC, {})
        old.update({"updated": c.NOW.strftime("%Y-%m-%d %H:%M"), "paused": True})
        c.save(PUBLIC, old)
        print("모의계좌 자동 주문이 꺼져 있습니다 (config/trade.json).")
        return 0
    return run(cfg, m)


if __name__ == "__main__":
    sys.exit(main())
