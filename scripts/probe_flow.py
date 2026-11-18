"""임시 점검: 투자자별(외국인·기관) 매매동향 API가 모의투자 서버에서 되는지 확인한다. 시장 숫자만 출력."""
import collect as c
t = c.read_token() or c.issue_token()
k = c.Kis(t)
print("서버:", t.get("label"))
D = c.NOW.strftime("%Y%m%d")
tries = [
    ("time", {"FID_INPUT_ISCD": "0001", "FID_INPUT_ISCD_2": "S001"}),
    ("time", {"FID_INPUT_ISCD": "999", "FID_INPUT_ISCD_2": "S001"}),
    ("time", {"FID_INPUT_ISCD": "1001", "FID_INPUT_ISCD_2": "S001"}),
    ("daily", {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001", "FID_INPUT_DATE_1": D, "FID_INPUT_ISCD_1": "KSP", "FID_INPUT_DATE_2": D, "FID_INPUT_ISCD_2": "0001"}),
    ("daily", {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "1001", "FID_INPUT_DATE_1": D, "FID_INPUT_ISCD_1": "KSQ", "FID_INPUT_DATE_2": D, "FID_INPUT_ISCD_2": "1001"}),
    ("stock", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}),
]
for kind, p in tries:
    path, tr = {"time": ("inquire-investor-time-by-market", "FHPTJ04030000"), "daily": ("inquire-investor-daily-by-market", "FHPTJ04040000"),
                "stock": ("inquire-investor", "FHKST01010900")}[kind]
    js = k.get("/uapi/domestic-stock/v1/quotations/" + path, tr, p, kind)
    print("==", kind, p.get("FID_INPUT_ISCD"), "→", "실패 " + (k.errors[-1][:120] if k.errors else "") if js is None else "성공")
    if js:
        out = js.get("output") or js.get("output1") or js.get("output2")
        row = out[0] if isinstance(out, list) and out else out
        if isinstance(row, dict):
            print("   ", {kk: vv for kk, vv in row.items() if any(s in kk for s in ("frgn", "orgn", "prsn", "date", "ntby", "bsop"))})
