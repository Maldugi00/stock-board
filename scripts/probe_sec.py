import sys, urllib.request, re
sys.path.insert(0, "scripts")
import screen_us as X
for ua in ("Maldugi00 maldugi00@users.noreply.github.com", "StockBoard Maldugi00 maldugi00@users.noreply.github.com", "Mozilla/5.0 (compatible; StockBoard/1.0; +maldugi00@users.noreply.github.com)"):
    for url in ("https://www.sec.gov/files/company_tickers.json", "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues.json"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate", "Host": url.split("/")[2]})
            r = urllib.request.urlopen(req, timeout=60); print("OK", ua[:20], url[-40:], r.status)
        except Exception as e:
            print("ERR", ua[:20], url[-40:], getattr(e, "code", ""), (e.read()[:200] if hasattr(e, "read") else b""))
raw = X.http("https://en.wikipedia.org/wiki/Nasdaq-100").decode("utf-8", "replace")
i = raw.find(">AAPL<"); print("AAPL at", i, len(raw))
print(raw[max(0, i - 1500):i + 200].replace("\n", " ")[-1700:])
print("tables", len(re.findall("<table", raw)))
