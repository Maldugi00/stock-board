#!/usr/bin/env python3
"""모의투자 계좌 전용 도구. 실전 서버로는 절대 보내지 않는다.

지금은 잔고 조회(balance)만 한다. 공개 저장소라 로그에도 계좌번호·금액을 찍지 않는다.
"""
import os
import sys

import collect as c

MOCK = "https://openapivts.koreainvestment.com:29443"      # 모의투자 서버. 이 주소 말고는 쓰지 않는다.
T = "/uapi/domestic-stock/v1/trading/"
CANO = os.environ.get("KIS_ACCOUNT_NO", "").strip()
PRD = os.environ.get("KIS_ACCOUNT_PRD", "").strip()


def mock_token():
    """저장해 둔 토큰이 모의투자용이면 다시 쓰고, 아니면 모의투자 서버에서만 새로 받는다."""
    t = c.read_token()
    if t and t.get("base") == MOCK:
        return t
    status, js = c.http("POST", MOCK + "/oauth2/tokenP", {"content-type": "application/json"},
                        {"grant_type": "client_credentials", "appkey": c.APP_KEY, "appsecret": c.APP_SECRET})
    token = js.get("access_token") if isinstance(js, dict) else None
    if status == 200 and token:
        return {"token": token, "label": "모의투자", "base": MOCK, "exp": c.EPOCH + 3600}
    print(f"모의투자 토큰 발급 실패: HTTP {status}")
    return None


def balance(k):
    js = k.get(T + "inquire-balance", "VTTC8434R",
               {"CANO": CANO, "ACNT_PRDT_CD": PRD, "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}, "잔고 조회")
    if not js:
        return None
    hold = [r for r in js.get("output1") or [] if c.num(r.get("hldg_qty"), int) > 0]
    tot = (js.get("output2") or [{}])[0]
    return {"holdings": [{"code": r.get("pdno"), "qty": c.num(r.get("hldg_qty"), int), "avg": c.num(r.get("pchs_avg_pric")),
                          "eval": c.num(r.get("evlu_amt"))} for r in hold],
            "cash": c.num(tot.get("dnca_tot_amt")), "total": c.num(tot.get("tot_evlu_amt")), "keys": sorted(tot.keys())}


def main():
    if not (c.APP_KEY and c.APP_SECRET):
        print("KIS_APP_KEY / KIS_APP_SECRET 이 없습니다.")
        return 1
    if not (CANO.isdigit() and len(CANO) == 8 and PRD.isdigit() and len(PRD) == 2):
        print(f"계좌 시크릿 형식이 맞지 않습니다: KIS_ACCOUNT_NO 는 숫자 8자리(지금 {len(CANO)}자), KIS_ACCOUNT_PRD 는 숫자 2자리(지금 {len(PRD)}자).")
        return 1
    print(f"::add-mask::{CANO}")
    t = mock_token()
    if not t:
        return 1
    k = c.Kis(t)
    if k.t["base"] != MOCK:
        print("모의투자 서버가 아니어서 중단합니다.")
        return 1
    b = balance(k)
    if k.t["base"] != MOCK:
        print("모의투자 서버가 아니어서 중단합니다.")
        return 1
    if not b:
        print("잔고 조회 실패:", "; ".join(k.errors))
        return 1
    # 금액은 찍지 않는다. 되는지와 모양만 확인한다.
    print(f"잔고 조회 성공 (모의투자). 보유 종목 {len(b['holdings'])}개, 예수금 {'있음' if b['cash'] > 0 else '없음(0원)'}, "
          f"총평가 {'있음' if b['total'] > 0 else '없음(0원)'}, 응답 항목 {len(b['keys'])}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
