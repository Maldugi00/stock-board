import urllib.request, json
H = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36", "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
for url in ("https://api.nasdaq.com/api/company/AAPL/revenue?limit=1", "https://api.nasdaq.com/api/company/JPM/revenue?limit=1", "https://api.nasdaq.com/api/quote/list-type/nasdaq100"):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30); b = r.read()
        print("OK", url[-45:], r.status, len(b)); print("  ", b[:1800].decode("utf-8", "replace"))
    except Exception as e:
        print("ERR", url[-45:], getattr(e, "code", ""), str(e)[:150])
