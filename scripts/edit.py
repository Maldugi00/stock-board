#!/usr/bin/env python3
"""사이트의 편집 버튼으로 들어온 변경을 config/stocks.json 에 반영한다.

입력(환경변수 OPS): JSON 배열. 항목 예
  {"op": "add",    "code": "294870", "name": "IPARK현대산업개발", "group": "watch"}
  {"op": "group",  "code": "005930", "group": "watch"}
  {"op": "remove", "code": "035420"}
  {"op": "mode",   "code": "005930", "mode": "trend"}
  미국 종목은 "m": "us" 를 붙인다 → config/us.json
  {"m": "us", "op": "add", "code": "NVDA", "ex": "NAS", "name": "엔비디아", "group": "mine"}
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
USCFG = os.path.join(ROOT, "config", "us.json")
USCODE = re.compile(r"^[A-Z][A-Z0-9/]{0,9}$")
MAX_STOCKS = 30


def apply_us(ops):
    cfg = json.load(open(USCFG, encoding="utf-8"))
    by = {s["code"]: s for s in cfg["stocks"]}
    done = []
    for o in ops:
        op, code = o.get("op"), str(o.get("code", ""))
        if not USCODE.match(code):
            print("건너뜀: 미국 티커 형식이 아님", code[:12])
            continue
        s = by.get(code)
        if op == "add":
            g = o.get("group") if o.get("group") in ("mine", "watch") else "watch"
            ex = o.get("ex") if o.get("ex") in ("NAS", "NYS", "AMS") else "NAS"
            name = re.sub(r"[\x00-\x1f<>\"'`]", "", str(o.get("name", "")))[:40].strip() or code
            if s:
                s["group"] = g
            elif len(cfg["stocks"]) >= MAX_STOCKS:
                print("건너뜀: 미국 종목이 너무 많음", code)
                continue
            else:
                s = {"code": code, "ex": ex, "name": name, "group": g}
                cfg["stocks"].append(s)
                by[code] = s
            done.append(f"미국 추가 {code}→{g}")
        elif op == "group" and s and o.get("group") in ("mine", "watch"):
            s["group"] = o["group"]
            done.append(f"미국 그룹 {code}→{o['group']}")
        elif op == "remove" and s:
            cfg["stocks"] = [x for x in cfg["stocks"] if x["code"] != code]
            by.pop(code, None)
            done.append(f"미국 삭제 {code}")
        elif op == "mode" and s and o.get("mode") in ("def", "trend"):
            s["mode"] = o["mode"]
            done.append(f"미국 방식 {code}→{o['mode']}")
        else:
            print("건너뜀(미국):", op, code)
    if done:
        with open(USCFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return done


def mark(market):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"{market}=1\n")


def main():
    try:
        ops = json.loads(os.environ.get("OPS") or "[]")
    except ValueError:
        print("변경 내용(JSON)을 읽지 못했습니다.")
        return 1
    if not isinstance(ops, list) or not ops or len(ops) > 40:
        print("변경 내용이 비었거나 너무 많습니다.")
        return 1
    us_ops = [o for o in ops if isinstance(o, dict) and o.get("m") == "us"]
    ops = [o for o in ops if not (isinstance(o, dict) and o.get("m") == "us")]
    if us_ops:
        got = apply_us(us_ops)
        if got:
            mark("us")
            print("반영:", ", ".join(got))
    if not ops:
        return 0
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
    mark("kr")
    print("반영:", ", ".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
