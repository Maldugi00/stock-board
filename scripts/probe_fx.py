"""임시 점검: 환율 조회가 모의투자 서버에서 되는지 확인 (시세 숫자만 출력)."""
import collect as c
from datetime import timedelta
k = c.Kis(c.read_token() or c.issue_token())
d2 = c.NOW.strftime("%Y%m%d"); d1 = (c.NOW - timedelta(days=20)).strftime("%Y%m%d")
for mk, code in (("X", "FX@KRW"), ("KX", "FX@KRW"), ("X", "FX@KRWKFTC"), ("N", ".DJI"), ("N", "SOX")):
    js = k.get("/uapi/overseas-price/v1/quotations/inquire-daily-chartprice", "FHKST03030100",
               {"FID_COND_MRKT_DIV_CODE": mk, "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d2, "FID_PERIOD_DIV_CODE": "D"}, "fx")
    o1 = (js or {}).get("output1") or {}; o2 = (js or {}).get("output2") or []
    print("== daily", mk, code, "실패 " + (k.errors[-1][:100] if k.errors else "") if js is None else "성공",
          {kk: o1.get(kk) for kk in ("hts_kor_isnm", "ovrs_nmix_prpr", "ovrs_nmix_prdy_vrss", "prdy_ctrt", "stck_bsop_date")}, o2[:2])
for mk in ("KX", "X"):
    js = k.get("/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice", "FHKST03030200",
               {"FID_COND_MRKT_DIV_CODE": mk, "FID_INPUT_ISCD": "FX@KRW", "FID_HOUR_CLS_CODE": "0", "FID_PW_DATA_INCU_YN": "N"}, "fx-time")
    print("== time", mk, "실패 " + (k.errors[-1][:100] if k.errors else "") if js is None else "성공", str((js or {}).get("output1"))[:200], str(((js or {}).get("output2") or [])[:2])[:200])
