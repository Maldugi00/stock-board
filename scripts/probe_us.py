"""임시 점검: 미국 시세 API 응답 모양과 지수·국채 코드 확인 (저장하지 않음)."""
import io, sys, zipfile, urllib.request
sys.path.insert(0, "scripts")
import collect as C

t = C.read_token() or C.issue_token()
k = C.Kis(t)
print("env", t["label"])
P = "/uapi/overseas-price/v1/quotations/"
for ex, sy in (("NAS", "AAPL"), ("AMS", "SPY"), ("NYS", "BRK/B"), ("NYS", "BRK.B")):
    js = k.get(P + "price", "HHDFS00000300", {"AUTH": "", "EXCD": ex, "SYMB": sy}, f"price {sy}")
    print("PRICE", sy, (js or {}).get("output"))
js = k.get(P + "dailyprice", "HHDFS76240000", {"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL", "GUBN": "0", "BYMD": "", "MODP": "1"}, "daily")
o2 = (js or {}).get("output2") or []
print("DAILY n", len(o2), o2[:2], o2[-1:] if o2 else None, (js or {}).get("output1"))
js = k.get(P + "dailyprice", "HHDFS76240000", {"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL", "GUBN": "0", "BYMD": "20250101", "MODP": "1"}, "daily2")
o2 = (js or {}).get("output2") or []
print("DAILY2 n", len(o2), o2[:1], o2[-1:] if o2 else None)
for code in ("SPX", ".SPX", "COMP", ".IXIC", "NDX", ".NDX", ".DJI", "SOX", "VIX", ".VIX"):
    js = k.get(P + "inquire-daily-chartprice", "FHKST03030100", {"FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": C.days_ago(10), "FID_INPUT_DATE_2": C.TODAY, "FID_PERIOD_DIV_CODE": "D"}, code)
    o = (js or {}).get("output2") or []
    print("IDX", code, (js or {}).get("output1", {}).get("hts_kor_isnm"), o[:1])
try:
    raw = urllib.request.urlopen("https://new.real.download.dws.co.kr/common/master/frgn_code.mst.zip", timeout=30).read()
    z = zipfile.ZipFile(io.BytesIO(raw)); txt = z.read(z.namelist()[0]).decode("cp949", "replace")
    keys = ("S&P", "NASDAQ", "TREAS", "T-NOTE", "10Y", "국채", "미국", "US ", "VIX")
    for line in txt.splitlines():
        if any(x in line.upper() for x in keys):
            print("MST", line[:90])
except Exception as e:
    print("mst err", e)
for code in ("Y0202", "Y0201", "Y0203", "US10Y", "TNX", ".TNX", "Y0101"):
    js = k.get(P + "inquire-daily-chartprice", "FHKST03030100", {"FID_COND_MRKT_DIV_CODE": "I", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": C.days_ago(10), "FID_INPUT_DATE_2": C.TODAY, "FID_PERIOD_DIV_CODE": "D"}, code)
    print("BOND", code, (js or {}).get("output1", {}).get("hts_kor_isnm"), ((js or {}).get("output2") or [])[:1])
try:
    raw = urllib.request.urlopen("https://new.real.download.dws.co.kr/common/master/nasmst.cod.zip", timeout=30).read()
    z = zipfile.ZipFile(io.BytesIO(raw)); txt = z.read(z.namelist()[0]).decode("cp949", "replace").splitlines()
    print("NASMST", len(txt)); [print("  ", l.split("\t")[:10]) for l in txt[:3]]
except Exception as e:
    print("nas err", e)
print("errors", k.errors)
