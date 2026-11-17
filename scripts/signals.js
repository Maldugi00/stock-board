// 모의계좌용 신호 계산. 사이트와 똑같은 engine.js 를 써서 계산이 어긋나지 않게 한다.
// 입력: 환경변수 DATA_DIR, 인자 1개(JSON) {codes:[...], mode:'trend'|'def', entries:{code: 'YYYY-MM-DD'|null}}
// 출력: [{code, date, close, action:'buy'|'sell'|null, why, stop, mok}]
const fs = require('fs'), path = require('path');
const K = require(path.join(__dirname, '..', 'engine.js'));
const D = process.env.DATA_DIR || 'data';
const arg = JSON.parse(process.argv[2] || '{}');
const ymd = d => { d = String(d); return d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8); };
let mok = null;
try { const m = JSON.parse(fs.readFileSync(path.join(D, 'market_daily.json'))); if (m.rows && m.rows.length > 80) mok = K.marketOk(m.rows.map(r => [ymd(r[0]), r[1]])); } catch (e) {}
const P = arg.mode === 'def' ? K.P0 : K.P1;
const out = (arg.codes || []).map(code => {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(D, 'stocks', code + '.json')));
    const rows = (d.daily || []).map(r => [ymd(r[0]), r[1], r[2], r[3], r[4], r[5]]);
    const ind = K.indicators(rows, P), L = ind.n - 1;
    if (L < 60) return { code, error: '일봉 부족' };
    const day = ind.d[L], c = ind.c[L], okMkt = !mok || mok[day] !== false, entry = (arg.entries || {})[code];
    const r = { code, date: day, close: c, action: null, why: '', stop: null, mok: okMkt };
    if (!entry) {                                        // 보유하지 않음 → 매수 조건 확인 (기록장 simSlot 과 같은 규칙)
      if (ind.buy[L]) { if (okMkt) { r.action = 'buy'; r.why = ind.bt[L]; } else r.why = ind.bt[L] + ' 조건 충족, 시장 약세로 보류'; }
    } else {                                             // 보유 중 → 들어간 날부터의 최고가로 손절선
      let hi = 0; for (let i = 0; i <= L; i++) if (ind.d[i] >= entry) hi = Math.max(hi, ind.h[i]);
      r.stop = hi - P.atr_mult * ind.atr[L];
      if (c < r.stop) { r.action = 'sell'; r.why = '트레일링 스탑'; }
      else if (!P.no_trend_exit && c < ind.s20[L] && ind.mh[L] < 0) { r.action = 'sell'; r.why = '추세 훼손'; }
    }
    return r;
  } catch (e) { return { code, error: String(e.message || e) }; }
});
process.stdout.write(JSON.stringify(out));
