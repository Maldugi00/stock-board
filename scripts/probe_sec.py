import sys, urllib.request, re, json, gzip
sys.path.insert(0, "scripts")
import screen_us as X
H = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36", "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
for url in ("https://api.nasdaq.com/api/company/AAPL/financials?frequency=2", "https://api.nasdaq.com/api/quote/NVDA/info?assetclass=stocks"):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30); b = r.read()
        print("OK", url[-50:], r.status, len(b)); j = json.loads(b); d = (j.get("data") or {})
        print("  keys", list(d.keys())[:8]); print("  ", json.dumps(d)[:700])
    except Exception as e:
        print("ERR", url[-50:], getattr(e, "code", ""), str(e)[:150])
raw = X.http("https://en.wikipedia.org/wiki/Nasdaq-100").decode("utf-8", "replace")
for key in ("Apple Inc", "constituents", "Components", "wikitable sortable"):
    i = raw.find(key); print("KEY", key, i, raw[max(0, i-300):i+400].replace("\n", " ") if i >= 0 else "")
