import sys, os, urllib.request, json
sys.path.insert(0, "scripts")
import screen_us as X
for url in ("https://www.sec.gov/files/company_tickers.json", "https://data.sec.gov/api/xbrl/frames/us-gaap/OperatingIncomeLoss/USD/CY2026Q2.json"):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": X.UA})
        r = urllib.request.urlopen(req, timeout=60); b = r.read()
        print("OK", url, r.status, len(b), b[:150])
    except Exception as e:
        print("ERR", url, type(e).__name__, getattr(e, "code", ""), str(e)[:200])
raw = X.http("https://en.wikipedia.org/wiki/Nasdaq-100")
p = X.Tables(); p.feed(raw.decode("utf-8", "replace"))
for k, v in p.tables.items():
    print("TABLE", k, len(v), v[0][:6] if v else None, v[1][:6] if len(v) > 1 else None)
