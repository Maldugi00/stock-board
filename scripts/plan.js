// 모의계좌용 신호 계산. 사이트와 같은 engine.js 를 그대로 쓴다.
// 사용: node scripts/plan.js <data 폴더> <종목코드,...> <def|trend>   → 표준출력에 JSON
const fs = require('fs'), path = require('path');
global.window = {};
eval(fs.readFileSync(path.join(__dirname, '..', 'engine.js'), 'utf8'));
const K = window.KRT, [dir, codes, mode] = process.argv.slice(2);
const P = mode === 'trend' ? K.P1 : K.P0;
const ymd = d => { d = String(d); return d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8); };
const read = f => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
let mkt = null, mktDate = null;
try { const m = read('market_daily.json'); mkt = K.marketOk(m.rows.map(r => [ymd(r[0]), r[1]])); mktDate = ymd(m.rows[m.rows.length - 1][0]); } catch (e) {}
const out = codes.split(',').map(code => {
  try {
    const d = read('stocks/' + code + '.json'), q = d.quote || {};
    const rows = (d.daily || []).map(r => [ymd(r[0]), r[1], r[2], r[3], r[4], r[5]]);
    const today = ymd(d.date), last = rows[rows.length - 1];
    if (q.price && last && last[0] === today) { last[4] = q.price; last[2] = Math.max(last[2], q.high || 0, q.price); last[3] = Math.min(last[3], q.low || q.price, q.price); if (q.vol) last[5] = q.vol; }
    const ind = K.indicators(rows, P), L = ind.n - 1;
    return { code, name: d.name, date: ind.d[L], today, closed: (d.ts || {}).closed === d.date, open_today: (d.m1 || []).length > 0,
      close: ind.c[L], high: ind.h[L], atr: ind.atr[L], buy: !!ind.buy[L], why: ind.bt[L] || '', up: !!ind.up[L],
      mok: mkt ? mkt[ind.d[L]] !== false : true, mkt_date: mktDate, price: q.price || ind.c[L], atr_mult: P.atr_mult };
  } catch (e) { return { code, error: String(e.message || e) }; }
});
process.stdout.write(JSON.stringify(out));
