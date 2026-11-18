#!/usr/bin/env python3
"""사이트의 편집 버튼으로 들어온 변경을 config/stocks.json 에 반영한다.

입력(환경변수 OPS): JSON 배열. 항목 예
  {"op": "add",    "code": "294870", "name": "IPARK현대산업개발", "group": "watch"}
  {"op": "group",  "code": "005930", "group": "watch"}
  {"op": "remove", "code": "035420"}
  {"op": "mode",   "code": "005930", "mode": "trend"}
모의계좌가 운영 중인 종목은 지워도 데이터 수집이 끊기지 않게 'trade'(화면에 안 보임) 그룹으로만 바꾼다.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "stocks.json")
TRADE = os.path.join(ROOT, "config", "trade.json")
CODE = re.compile(r"^[0-9A-Z]{6}$")
MAX_STOCKS = 30


def main():
    try:
        ops = json.loads(os.environ.get("OPS") or "[]")
    except ValueError:
        print("변경 내용(JSON)을 읽지 못했습니다.")
        return 1
    if not isinstance(ops, list) or not ops or len(ops) > 40:
        print("변경 내용이 비었거나 너무 많습니다.")
        return 1
    cfg = json.load(open(CFG, encoding="utf-8"))
    try:
        traded = set(json.load(open(TRADE, encoding="utf-8")).get("codes", []))
    except (OSError, ValueError):
        traded = set()
    by = {s["code"]: s for s in cfg["stocks"]}
    done = []
    for o in ops:
        if not isinstance(o, dict):
            continue
        op, code = o.get("op"), str(o.get("code", ""))
        if not CODE.match(code):
            print("건너뜀: 종목코드 형식이 아님", code[:10])
            continue
        s = by.get(code)
        if op == "add":
            g = o.get("group") if o.get("group") in ("mine", "watch") else "watch"
            name = re.sub(r"[\x00-\x1f<>\"'`]", "", str(o.get("name", "")))[:40].strip() or code
            if s:
                s["group"] = g
            elif len(cfg["stocks"]) >= MAX_STOCKS:
                print("건너뜀: 종목이 너무 많음", code)
                continue
            else:
                s = {"code": code, "name": name, "type": "stock", "group": g}
                cfg["stocks"].append(s)
                by[code] = s
            done.append(f"추가 {code}→{g}")
        elif op == "group" and s and o.get("group") in ("mine", "watch"):
            s["group"] = o["group"]
            done.append(f"그룹 {code}→{o['group']}")
        elif op == "remove" and s:
            if code in traded:
                s["group"] = "trade"                       # 모의계좌 운영 종목: 화면에서만 빠지고 수집은 계속
                done.append(f"숨김(모의계좌) {code}")
            else:
                cfg["stocks"] = [x for x in cfg["stocks"] if x["code"] != code]
                by.pop(code, None)
                done.append(f"삭제 {code}")
        elif op == "mode" and s and o.get("mode") in ("def", "trend"):
            s["mode"] = o["mode"]
            done.append(f"방식 {code}→{o['mode']}")
        else:
            print("건너뜀:", op, code)
    if not done:
        print("바뀐 것이 없습니다.")
        return 0
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("반영:", ", ".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
