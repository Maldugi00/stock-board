"""한국투자증권 Open API 조회 시험.

조회(시세·정보) API만 한 번씩 불러 보고, 항목별 성공 여부만 기록한다.
주문·계좌 API는 부르지 않는다. 키와 토큰은 어디에도 출력하지 않는다.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.strftime("%Y%m%d")
HOUR = min(NOW.strftime("%H%M%S"), "153000")  # 장중이면 지금 시각까지, 장 마감 뒤면 15:30까지
DOMAINS = [
    ("모의투자", "https://openapivts.koreainvestment.com:29443"),
    ("실전", "https://openapi.koreainvestment.com:9443"),
]
APP_KEY = os.environ.get("KIS_APP_KEY", "").strip()
APP_SECRET = os.environ.get("KIS_APP_SECRET", "").strip()


def days_ago(n):
    return (NOW - timedelta(days=n)).strftime("%Y%m%d")


def last_weekday():
    d = NOW - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def call(method, url, headers, body=None):
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
    except Exception as e:  # 네트워크 오류 등
        return 0, {"msg1": type(e).__name__}


def get_token():
    for label, base in DOMAINS:
        status, js = call(
            "POST",
            base + "/oauth2/tokenP",
            {"content-type": "application/json"},
            {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
        )
        token = js.get("access_token") if isinstance(js, dict) else None
        if status == 200 and token:
            print(f"::add-mask::{token}")
            return label, base, token
        print(f"토큰 발급 실패 ({label}): HTTP {status} {js.get('error_code', '')} {js.get('error_description', '')}")
        time.sleep(1.2)
    return None, None, None


TESTS = [
    ("종목 현재가", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
    ("종목 분봉 (당일)", "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_HOUR_1": HOUR,
      "FID_PW_DATA_INCU_YN": "Y", "FID_ETC_CLS_CODE": ""}),
    ("종목 분봉 (지난 날짜)", "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", "FHKST03010230",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_HOUR_1": "153000",
      "FID_INPUT_DATE_1": last_weekday(), "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N"}),
    ("종목 일봉", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_DATE_1": days_ago(140),
      "FID_INPUT_DATE_2": TODAY, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}),
    ("ETF 분봉 (KODEX 레버리지)", "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "122630", "FID_INPUT_HOUR_1": HOUR,
      "FID_PW_DATA_INCU_YN": "Y", "FID_ETC_CLS_CODE": ""}),
    ("코스피 지수", "/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
     {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"}),
    ("코스닥 지수", "/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
     {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "1001"}),
    ("지수 분봉 (코스피)", "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice", "FHKUP03500200",
     {"FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0", "FID_INPUT_ISCD": "0001",
      "FID_INPUT_HOUR_1": "60", "FID_PW_DATA_INCU_YN": "N"}),
    ("전문가 의견 (종목)", "/uapi/domestic-stock/v1/quotations/invest-opinion", "FHKST663300C0",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633", "FID_INPUT_ISCD": "005930",
      "FID_INPUT_DATE_1": days_ago(180), "FID_INPUT_DATE_2": TODAY}),
    ("전문가 의견 (증권사별)", "/uapi/domestic-stock/v1/quotations/invest-opbysec", "FHKST663400C0",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16634", "FID_INPUT_ISCD": "005930",
      "FID_DIV_CLS_CODE": "0", "FID_INPUT_DATE_1": days_ago(90), "FID_INPUT_DATE_2": TODAY}),
    ("손익계산서 (분기)", "/uapi/domestic-stock/v1/finance/income-statement", "FHKST66430200",
     {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": "005930"}),
    ("재무비율 (분기)", "/uapi/domestic-stock/v1/finance/financial-ratio", "FHKST66430300",
     {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": "005930"}),
    ("추정 실적", "/uapi/domestic-stock/v1/quotations/estimate-perform", "HHKST668300C0",
     {"SHT_CD": "005930"}),
    ("뉴스 제목", "/uapi/domestic-stock/v1/quotations/news-title", "FHKST01011800",
     {"FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": "005930",
      "FID_TITL_CNTT": "", "FID_INPUT_DATE_1": "", "FID_INPUT_HOUR_1": "", "FID_RANK_SORT_CLS_CODE": "",
      "FID_INPUT_SRNO": ""}),
    ("ETF 구성 종목", "/uapi/etfetn/v1/quotations/inquire-component-stock-price", "FHKST121600C0",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "122630", "FID_COND_SCR_DIV_CODE": "11216"}),
]


def rows(js):
    n = 0
    for k, v in js.items():
        if not k.startswith("output"):
            continue
        if isinstance(v, list):
            n += len(v)
        elif isinstance(v, dict) and v:
            n += 1
    return n


def main():
    out = {"실행 시각": NOW.strftime("%Y-%m-%d %H:%M KST"), "환경": None, "결과": []}
    if not APP_KEY or not APP_SECRET:
        out["오류"] = "KIS_APP_KEY 또는 KIS_APP_SECRET 이 저장소 Secrets 에 없습니다."
    else:
        label, base, token = get_token()
        out["환경"] = label
        if not token:
            out["오류"] = "토큰 발급 실패 (모의투자·실전 주소 모두). 키가 맞는지 확인이 필요합니다."
        else:
            for name, path, tr_id, params in TESTS:
                headers = {"content-type": "application/json; charset=utf-8", "authorization": "Bearer " + token,
                           "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": tr_id, "custtype": "P"}
                status, js = call("GET", base + path + "?" + urllib.parse.urlencode(params), headers)
                js = js if isinstance(js, dict) else {}
                n = rows(js)
                ok = status == 200 and str(js.get("rt_cd")) == "0"
                out["결과"].append({"항목": name, "성공": ok, "데이터 있음": n > 0, "건수": n, "HTTP": status,
                                   "코드": js.get("msg_cd", ""), "메시지": str(js.get("msg1", "")).strip()[:80]})
                print(f"{'O' if ok else 'X'} {name}: HTTP {status}, {n}건, {js.get('msg_cd', '')}")
                time.sleep(0.7)  # 모의투자는 초당 호출 한도가 낮다

    os.makedirs("test", exist_ok=True)
    with open("test/kis-test-result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    lines = ["# 한국투자증권 API 조회 시험 결과", "", f"- 실행: {out['실행 시각']}", f"- 환경: {out['환경'] or '확인 안 됨'}", ""]
    if out.get("오류"):
        lines.append("**오류:** " + out["오류"])
    else:
        lines += ["| 항목 | 결과 | 건수 | 메시지 |", "|---|---|---|---|"]
        for r in out["결과"]:
            mark = "됨" if r["성공"] and r["데이터 있음"] else ("응답은 왔지만 데이터 없음" if r["성공"] else "안 됨")
            lines.append(f"| {r['항목']} | {mark} | {r['건수']} | {r['코드']} {r['메시지']} |")
    with open("test/kis-test-result.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
