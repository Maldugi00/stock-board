# stock-board

개인용 국내 주식 관제 보드입니다. 조회 전용이며 주문 기능은 없습니다.

- `index.html`, `engine.js` — 사이트 화면과 신호 계산
- `scripts/collect.py` — 한국투자증권 Open API에서 시세·뉴스 제목·전문가 의견을 받아 `data` 가지에 저장
- `config/stocks.json` — 받아올 종목과 섹터 목록
- `.github/workflows/collect.yml` — 평일 장중 10분마다 수집 실행

신호는 일봉 기준 규칙의 계산 결과이며 수익을 보장하지 않습니다.
