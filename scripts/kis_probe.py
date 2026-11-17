"""응답 모양 확인용 (조회만). 각 항목의 첫 줄만 test/kis-probe.json 에 남긴다. 키·토큰은 남기지 않는다."""
import json, os, sys, time, urllib.parse
sys.path.insert(0, os.path.dirname(__file__))
import kis_test as k

FIN = "/uapi/domestic-stock/v1/finance/"
EXTRA = [
    ("손익계산서 A(예제 그대로, 연간)", FIN + "income-statement", "FHKST66430200", {"FID_DIV_CLS_CODE": "0", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": "005930"}),
    ("손익계산서 B(대문자, 분기)", FIN + "income-statement", "FHKST66430200", {"FID_DIV_CLS_CODE": "1", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
    ("재무비율 A(예제 그대로, 연간)", FIN + "financial-ratio", "FHKST66430300", {"FID_DIV_CLS_CODE": "0", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": "005930"}),
    ("재무비율 B(대문자, 분기)", FIN + "financial-ratio", "FHKST66430300", {"FID_DIV_CLS_CODE": "1", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
    ("대차대조표 B(대문자, 분기)", FIN + "balance-sheet", "FHKST66430100", {"FID_DIV_CLS_CODE": "1", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
    ("코스피200 지수", "/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000", {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "2001"}),
    ("당일 분봉 2쪽째(09:30 기준)", "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930", "FID_INPUT_HOUR_1": "093000", "FID_PW_DATA_INCU_YN": "Y", "FID_ETC_CLS_CODE": ""}),
]

def head(v):
    if isinstance(v, list):
        return {"건수": len(v), "첫 줄": v[0] if v else None, "끝 줄": v[-1] if len(v) > 1 else None}
    return v

def main():
    label, base, token = k.get_token()
    out = {"환경": label, "항목": {}}
    if token:
        for name, path, tr_id, params in k.TESTS + EXTRA:
            headers = {"content-type": "application/json; charset=utf-8", "authorization": "Bearer " + token,
                       "appkey": k.APP_KEY, "appsecret": k.APP_SECRET, "tr_id": tr_id, "custtype": "P"}
            status, js = k.call("GET", base + path + "?" + urllib.parse.urlencode(params), headers)
            js = js if isinstance(js, dict) else {}
            out["항목"][name] = {"HTTP": status, "rt_cd": js.get("rt_cd"), "msg_cd": js.get("msg_cd"), "msg1": js.get("msg1"),
                                **{kk: head(v) for kk, v in js.items() if kk.startswith("output")}}
            print(name, status, js.get("msg_cd"))
            time.sleep(1.1)
    os.makedirs("test", exist_ok=True)
    json.dump(out, open("test/kis-probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

main()
