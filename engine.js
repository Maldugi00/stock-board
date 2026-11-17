/* 4종목 매매 — 계산 엔진 (화면과 분리된 순수 계산)
 * kr_timing.py 와 같은 규칙: 일봉 종가에서 신호 → 다음 거래일 시가 체결
 *   [추세 필터] 종가 > 60일선 AND 20일선 > 60일선
 *   [매수 ①] 눌림목: RSI(14)가 45 아래에서 위로 + MACD 히스토그램 증가
 *   [매수 ②] 돌파: 직전 20일 고점 돌파 + 거래량 20일 평균의 1.5배 이상
 *   [매도 ①] 트레일링 스탑: 종가 < 보유 중 최고가 − 3×ATR(14)
 *   [매도 ②] 추세 훼손: 종가 < 20일선 AND MACD 히스토그램 < 0
 */
(function (root) {
  'use strict';

  // 한국투자증권 온라인 기준(편도 수수료율 / 매도 시 거래세). ETF는 매도세 없음.
  var TICKERS = [
    { code: '005930', name: '삼성전자', short: '삼성전자', fee: 0.000140527, tax: 0.0020 },
    { code: '000660', name: 'SK하이닉스', short: '하이닉스', fee: 0.000140527, tax: 0.0020 },
    { code: '122630', name: 'KODEX 레버리지', short: '레버리지', fee: 0.000146527, tax: 0.0 },
    { code: '494310', name: 'KODEX 반도체레버리지', short: '반도체레버', fee: 0.000146527, tax: 0.0 }
  ];
  var MARKET = { code: '069500', name: 'KOSPI200 (KODEX 200)' };
  // 섹터는 대표 ETF의 가격 흐름으로 봅니다.
  var SECTORS = [
    { code: '091160', name: '반도체' },
    { code: '305720', name: '2차전지' },
    { code: '091180', name: '자동차' },
    { code: '091170', name: '은행' },
    { code: '102970', name: '증권' },
    { code: '244580', name: '바이오' },
    { code: '117460', name: '에너지화학' },
    { code: '117680', name: '철강' },
    { code: '117700', name: '건설' },
    { code: '466920', name: '조선' },
    { code: '449450', name: '방산' },
    { code: '229200', name: '코스닥150' }
  ];
  var SLIP = 0.0005;
  var P0 = { atr_mult: 3.0, rsi_entry: 45, vol_mult: 1.5, breakout_n: 20 };
  var TRACK0 = { start: '2026-10-28', end: '2026-11-27', shares: { '005930': 30, '000660': 2, '122630': 40, '494310': 15 } };

  // 거래시간 (분 단위, KST). 수능일은 1시간 늦게 열고 닫습니다.
  var SHIFT = { '2026-11-19': 60 };
  var HOLIDAYS = { '2026-12-25': 1, '2026-12-31': 1, '2027-01-01': 1 };

  function tickerOf(code) {
    for (var i = 0; i < TICKERS.length; i++) if (TICKERS[i].code === code) return TICKERS[i];
    return { code: code, name: code, short: code, fee: 0.000140527, tax: 0.0020 };
  }

  // ───────── 날짜·시간 ─────────
  function pad2(x) { return (x < 10 ? '0' : '') + x; }
  function dayNum(s) { return Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / 86400000; }
  function dayStr(n) { var d = new Date(n * 86400000); return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()); }
  function addDays(s, k) { return dayStr(dayNum(s) + k); }
  function dow(s) { return new Date(dayNum(s) * 86400000).getUTCDay(); }
  var DOW_KO = ['일', '월', '화', '수', '목', '금', '토'];
  function mdLabel(s) { return (+s.slice(5, 7)) + '/' + (+s.slice(8, 10)) + '(' + DOW_KO[dow(s)] + ')'; }
  function hm(min) { return pad2(Math.floor(min / 60)) + ':' + pad2(min % 60); }
  /** 현재 한국시간. ms를 주면 그 시각 기준. */
  function kstNow(ms) {
    var t = new Date((ms == null ? Date.now() : ms) + 9 * 3600000);
    return { date: t.getUTCFullYear() + '-' + pad2(t.getUTCMonth() + 1) + '-' + pad2(t.getUTCDate()), min: t.getUTCHours() * 60 + t.getUTCMinutes(), ms: (ms == null ? Date.now() : ms) };
  }
  function session(date) { var s = SHIFT[date] || 0; return { open: 540 + s, close: 930 + s, shift: s }; }
  function isTradingDay(date) { var w = dow(date); return w !== 0 && w !== 6 && !HOLIDAYS[date]; }
  function nextTradingDay(date) { var d = addDays(date, 1); while (!isTradingDay(d)) d = addDays(d, 1); return d; }
  /** 'holiday' | 'pre' | 'open' | 'closed' */
  function marketState(now) {
    if (!isTradingDay(now.date)) return 'holiday';
    var s = session(now.date);
    return now.min < s.open ? 'pre' : (now.min < s.close ? 'open' : 'closed');
  }
  /** 마지막 일봉이 "오늘 진행 중인 봉"인지 */
  function isLiveBar(rows, now) {
    if (!rows.length) return false;
    return rows[rows.length - 1][0] === now.date && now.min < session(now.date).close;
  }

  // ───────── 지표 ─────────
  function sma(a, n) {
    var out = new Array(a.length), s = 0;
    for (var i = 0; i < a.length; i++) { s += a[i]; if (i >= n) s -= a[i - n]; out[i] = i >= n - 1 ? s / n : NaN; }
    return out;
  }
  function ewmFrom(a, alpha, start) {
    var out = new Array(a.length);
    for (var i = 0; i < a.length; i++) out[i] = i < start ? NaN : (i === start ? a[i] : out[i - 1] * (1 - alpha) + a[i] * alpha);
    return out;
  }
  function cleanRows(rows) {
    var seen = {}, out = [];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (!(r[1] > 0 && r[2] > 0 && r[3] > 0 && r[4] > 0) || !(r[5] >= 0)) continue;   // 거래정지·결측 제외
      seen[r[0]] = r;
    }
    Object.keys(seen).sort().forEach(function (k) { out.push(seen[k]); });
    return out;
  }

  /** rows: [[YYYY-MM-DD, 시가, 고가, 저가, 종가, 거래량], ...] (과거→최근) */
  function indicators(rows, p) {
    p = p || P0;
    var n = rows.length, i, d = [], o = [], h = [], l = [], c = [], v = [];
    for (i = 0; i < n; i++) { d.push(rows[i][0]); o.push(rows[i][1]); h.push(rows[i][2]); l.push(rows[i][3]); c.push(rows[i][4]); v.push(rows[i][5]); }
    var s20 = sma(c, 20), s60 = sma(c, 60), vm = sma(v, 20);
    var up = [NaN], dn = [NaN];
    for (i = 1; i < n; i++) { var x = c[i] - c[i - 1]; up.push(Math.max(x, 0)); dn.push(Math.max(-x, 0)); }
    var au = ewmFrom(up, 1 / 14, 1), ad = ewmFrom(dn, 1 / 14, 1), rsi = [];
    for (i = 0; i < n; i++) rsi.push(i < 1 || ad[i] === 0 ? NaN : 100 - 100 / (1 + au[i] / ad[i]));
    var e12 = ewmFrom(c, 2 / 13, 0), e26 = ewmFrom(c, 2 / 27, 0), macd = [];
    for (i = 0; i < n; i++) macd.push(e12[i] - e26[i]);
    var sig = ewmFrom(macd, 2 / 10, 0), mh = [];
    for (i = 0; i < n; i++) mh.push(macd[i] - sig[i]);
    var tr = [];
    for (i = 0; i < n; i++) tr.push(i ? Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])) : h[i] - l[i]);
    var atr = ewmFrom(tr, 1 / 14, 0);
    var bn = Math.round(p.breakout_n), hn = [], vr = [];
    for (i = 0; i < n; i++) {
      if (i < bn) hn.push(NaN); else { var m = -Infinity; for (var k = i - bn; k < i; k++) if (h[k] > m) m = h[k]; hn.push(m); }
      vr.push(v[i] / vm[i]);
    }
    var upt = [], buy = [], bt = [];
    for (i = 0; i < n; i++) {
      var ut = c[i] > s60[i] && s20[i] > s60[i];
      var pb = ut && i > 0 && rsi[i - 1] < p.rsi_entry && rsi[i] >= p.rsi_entry && mh[i] > mh[i - 1];
      var bo = ut && c[i] > hn[i] && vr[i] >= p.vol_mult;
      upt.push(!!ut); buy.push(!!(pb || bo)); bt.push(pb ? '눌림목' : (bo ? '돌파' : ''));
    }
    // pandas dropna 와 같게: 지표가 비어 있는 날은 제외
    var keep = [];
    for (i = 0; i < n; i++) if (!(isNaN(s60[i]) || isNaN(rsi[i]) || isNaN(atr[i]) || isNaN(hn[i]) || isNaN(vr[i]))) keep.push(i);
    function pick(a) { return keep.map(function (j) { return a[j]; }); }
    return { n: keep.length, d: pick(d), o: pick(o), h: pick(h), l: pick(l), c: pick(c), v: pick(v), s20: pick(s20), s60: pick(s60), rsi: pick(rsi),
      mh: pick(mh), atr: pick(atr), vr: pick(vr), hn: pick(hn), up: pick(upt), buy: pick(buy), bt: pick(bt), e12: pick(e12), e26: pick(e26), sig: pick(sig) };
  }
  var IND_KEYS = ['d', 'o', 'h', 'l', 'c', 'v', 's20', 's60', 'rsi', 'mh', 'atr', 'vr', 'hn', 'up', 'buy', 'bt', 'e12', 'e26', 'sig'];
  function sliceInd(ind, from, to) {
    var out = {};
    IND_KEYS.forEach(function (k) { out[k] = ind[k].slice(from, to); });
    out.n = out.d.length;
    return out;
  }
  /** 마지막 날짜에서 years 년 이전은 잘라냄 (백테스트 구간) */
  function cutYears(ind, years) {
    if (!ind.n) return ind;
    var cut = dayNum(ind.d[ind.n - 1]) - Math.floor(365 * years), i = 0;
    while (i < ind.n && dayNum(ind.d[i]) < cut) i++;
    return sliceInd(ind, i);
  }

  // ───────── 백테스트 ─────────
  /** exec: 'open'(기본) = 신호 다음 거래일 시가 체결, 'close' = 신호가 난 날 종가 체결 */
  function backtest(ind, cost, p, exec) {
    p = p || P0; exec = exec || 'open';
    var fee = cost.fee, tax = cost.tax, n = ind.n, o = ind.o, h = ind.h, c = ind.c;
    var cash = 1, sh = 0, hi = 0, ep = 0, ei = 0, eqB = 1, pend = null, reason = '', etype = '';
    var eq = [], trades = [], marks = [];
    function doBuy(i, px0, hi0, why) {
      ep = px0 * (1 + SLIP); eqB = cash; sh = cash * (1 - fee) / ep; cash = 0; hi = hi0; ei = i; etype = why;
      marks.push({ i: i, k: 'B', px: ep, why: why });
    }
    function doSell(i, px0, why) {
      var xp = px0 * (1 - SLIP);
      cash = sh * xp * (1 - fee - tax);
      trades.push({ in: ind.d[ei], out: ind.d[i], inPx: ep, outPx: xp, ret: cash / eqB - 1, days: i - ei, type: etype, why: why });
      marks.push({ i: i, k: 'S', px: xp, why: why });
      sh = 0;
    }
    for (var i = 0; i < n; i++) {
      if (exec === 'open') {
        if (pend === 'buy' && sh === 0) doBuy(i, o[i], h[i], reason);
        else if (pend === 'sell' && sh > 0) doSell(i, o[i], reason);
      }
      pend = null;
      if (sh > 0) hi = Math.max(hi, h[i]);
      if (sh === 0 && ind.buy[i]) { pend = 'buy'; reason = ind.bt[i]; }
      else if (sh > 0) {
        if (c[i] < hi - p.atr_mult * ind.atr[i]) { pend = 'sell'; reason = '트레일링 스탑'; }
        else if (c[i] < ind.s20[i] && ind.mh[i] < 0) { pend = 'sell'; reason = '추세 훼손'; }
      }
      if (exec === 'close' && pend) {
        if (pend === 'buy') doBuy(i, c[i], c[i], reason); else doSell(i, c[i], reason);
        pend = null;
      }
      eq.push(cash + sh * c[i]);
    }
    var holding = sh > 0;
    return { eq: eq, trades: trades, marks: marks,
      st: { holding: holding, pending: pend, reason: reason, highest: hi, stop: holding ? hi - p.atr_mult * ind.atr[n - 1] : null } };
  }
  function stats(ind, eq, trades) {
    var n = ind.n, years = Math.max((dayNum(ind.d[n - 1]) - dayNum(ind.d[0])) / 365.25, 1e-9), pk = 0, mdd = 0, pk2 = 0, bhm = 0, i;
    for (i = 0; i < n; i++) { pk = Math.max(pk, eq[i]); mdd = Math.min(mdd, eq[i] / pk - 1); pk2 = Math.max(pk2, ind.c[i]); bhm = Math.min(bhm, ind.c[i] / pk2 - 1); }
    var s = { total: eq[n - 1] / eq[0] - 1, cagr: Math.pow(eq[n - 1] / eq[0], 1 / years) - 1, mdd: mdd, bh: ind.c[n - 1] / ind.c[0] - 1, bhMdd: bhm,
      years: years, n: trades.length, win: NaN, avgW: NaN, avgL: NaN, hold: NaN };
    if (trades.length) {
      var w = trades.filter(function (t) { return t.ret > 0; }), lo = trades.filter(function (t) { return !(t.ret > 0); });
      var mean = function (a, f) { return a.length ? a.reduce(function (x, t) { return x + f(t); }, 0) / a.length : NaN; };
      s.win = w.length / trades.length; s.avgW = mean(w, function (t) { return t.ret; }); s.avgL = mean(lo, function (t) { return t.ret; });
      s.hold = mean(trades, function (t) { return t.days; });
    }
    return s;
  }

  // ───────── 신호 해석 ─────────
  function won(x) { return Math.round(x).toLocaleString('ko-KR'); }
  function pct(x, dgt) { if (x == null || isNaN(x)) return '-'; return (x >= 0 ? '+' : '') + (x * 100).toFixed(dgt == null ? 1 : dgt) + '%'; }

  /** 이미 보유 중인 계좌 기준 (최근 20일 고점 기준 트레일링 스탑) */
  function holderView(ind, p) {
    p = p || P0;
    var L = ind.n - 1, hi = -Infinity;
    for (var k = Math.max(0, L - 19); k <= L; k++) if (ind.h[k] > hi) hi = ind.h[k];
    var stop = hi - p.atr_mult * ind.atr[L], c = ind.c[L];
    if (c < stop) return { sell: true, stop: stop, hi: hi, why: '트레일링 스탑 이탈', text: '트레일링 스탑 이탈 (20일 고점 ' + won(hi) + ' − ' + p.atr_mult + '×ATR = ' + won(stop) + ' > 종가 ' + won(c) + ')' };
    if (c < ind.s20[L] && ind.mh[L] < 0) return { sell: true, stop: stop, hi: hi, why: '추세 훼손', text: '추세 훼손 (종가가 20일선 아래 + MACD 히스토그램 음수)' };
    return { sell: false, stop: stop, hi: hi, why: '', text: '청산 조건 없음 (트레일링 스탑 ' + won(stop) + ', 종가 ' + won(c) + ')' };
  }
  function signalText(ind, st, p) {
    p = p || P0;
    var L = ind.n - 1, c = ind.c[L], hv = holderView(ind, p);
    if (st.pending === 'buy') {
      var w = st.reason === '눌림목'
        ? '눌림목 — RSI ' + ind.rsi[L - 1].toFixed(0) + '→' + ind.rsi[L].toFixed(0) + '로 ' + p.rsi_entry + ' 상향 돌파, MACD 히스토그램 증가, 상승 추세 유지'
        : '돌파 — 종가가 직전 ' + Math.round(p.breakout_n) + '일 고점 돌파, 거래량 ' + ind.vr[L].toFixed(1) + '배, 상승 추세 유지';
      return { status: '매수 신호', cls: 'b', base: w + ' → 다음 거래일 시가 진입 후보 (참고 손절선 ' + won(c - p.atr_mult * ind.atr[L]) + ')', hold: null, hv: hv };
    }
    if (st.pending === 'sell') {
      var w2 = st.reason === '트레일링 스탑' ? '트레일링 스탑 — 종가가 (최고가 − ' + p.atr_mult + '×ATR) 아래로 마감' : '추세 훼손 — 종가가 20일선 아래 + MACD 히스토그램 음수';
      return { status: '매도 신호', cls: 's', base: w2 + ' → 다음 거래일 시가 청산 후보', hold: null, hv: hv };
    }
    if (st.holding) return { status: '보유 유지', cls: 'h', base: '청산 조건 미발생 — 트레일링 스탑 ' + won(st.stop) + ' (종가 ' + won(c) + ', 20일선 ' + won(ind.s20[L]) + ')', hold: null, hv: hv };
    var base = ind.up[L]
      ? '상승 추세이나 눌림목·돌파 진입 조건 미충족 (RSI ' + ind.rsi[L].toFixed(0) + ', 거래량 ' + ind.vr[L].toFixed(1) + '배)'
      : '추세 필터 미충족 — 종가 ' + won(c) + ' / 20일선 ' + won(ind.s20[L]) + ' / 60일선 ' + won(ind.s60[L]) + ' (종가>60일선, 20일선>60일선이어야 매수 검토)';
    return { status: hv.sell ? '관망·보유 시 매도' : '관망', cls: hv.sell ? 'w' : '', base: base, hold: (hv.sell ? '매도 구간 — ' : '유지 — ') + hv.text, hv: hv };
  }

  /** 한 종목 분석: 3년 백테스트 + 현재 신호 */
  function analyze(rows, code, p, years) {
    p = p || P0;
    var cost = tickerOf(code), full = indicators(rows, p), ind = cutYears(full, years || 3);
    if (ind.n < 30) throw new Error('데이터가 너무 짧습니다 (' + ind.n + '일)');
    var bt = backtest(ind, cost, p), s = stats(ind, bt.eq, bt.trades), sg = signalText(ind, bt.st, p);
    return { code: code, cost: cost, full: full, ind: ind, eq: bt.eq, trades: bt.trades, marks: bt.marks, st: bt.st, s: s, sig: sg };
  }

  /** 오늘 종가가 이 값 아래로 끝나면 보유분 매도 신호가 되는 가격.
   *  ind 의 마지막 봉이 오늘(진행 중)이라고 보고 계산. highest 를 주면 그 고점 기준(추적 계좌), 없으면 최근 20일 고점. */
  function sellLine(ind, p, highest) {
    p = p || P0;
    var L = ind.n - 1;
    if (L < 20) return null;
    var hi = highest;
    if (hi == null) { hi = -Infinity; for (var k = Math.max(0, L - 19); k <= L; k++) if (ind.h[k] > hi) hi = ind.h[k]; }
    else hi = Math.max(hi, ind.h[L]);
    var stop = hi - p.atr_mult * ind.atr[L];
    var k12 = 2 / 13, k26 = 2 / 27;
    var cM = (ind.sig[L - 1] - (1 - k12) * ind.e12[L - 1] + (1 - k26) * ind.e26[L - 1]) / (k12 - k26);   // 종가 < cM 이면 MACD 히스토그램 음수
    var s19 = 0; for (var j = L - 19; j < L; j++) s19 += ind.c[j];
    var cS = s19 / 19;                                                                                    // 종가 < cS 이면 20일선 아래
    var trend = Math.min(cM, cS);
    return { line: Math.max(stop, trend), stop: stop, trend: trend, which: stop >= trend ? '트레일링 스탑' : '추세 훼손' };
  }

  // ───────── 한 달 운영 추적 (프로그램 신호대로 vs 유지) ─────────
  function trackTicker(ind, shares0, cost, p, start) {
    p = p || P0;
    var pos = ind.d.indexOf(start);
    if (pos < 0) throw new Error('기준일(' + start + ') 시세가 없습니다');
    var fee = cost.fee, tax = cost.tax, o = ind.o, h = ind.h, c = ind.c;
    var cash = 0, sh = Math.round(shares0), hi = -Infinity, pend = null, reason = '', lastSell = null;
    for (var k = Math.max(0, pos - 19); k <= pos; k++) if (h[k] > hi) hi = h[k];
    var dates = [], hold = [], strat = [], shs = [], trades = [];
    for (var i = pos; i < ind.n; i++) {
      if (i > pos && pend) {
        if (pend === 'buy' && sh === 0) {
          var px = o[i] * (1 + SLIP), q = Math.floor(cash / (px * (1 + fee)));
          if (q > 0) { cash -= q * px * (1 + fee); sh = q; hi = h[i]; trades.push({ d: ind.d[i], act: '매수', n: q, px: px, why: reason }); }
        } else if (pend === 'sell' && sh > 0) {
          var xp = o[i] * (1 - SLIP);
          cash += sh * xp * (1 - fee - tax);
          trades.push({ d: ind.d[i], act: '매도', n: sh, px: xp, why: reason });
          lastSell = { d: ind.d[i], px: xp }; sh = 0;
        }
        pend = null;
      }
      if (sh > 0 && i > pos) hi = Math.max(hi, h[i]);
      dates.push(ind.d[i]); hold.push(shares0 * c[i]); strat.push(cash + sh * c[i]); shs.push(sh);
      pend = null;
      if (sh === 0 && ind.buy[i] && cash > 0) { pend = 'buy'; reason = ind.bt[i]; }
      else if (sh > 0) {
        if (c[i] < hi - p.atr_mult * ind.atr[i]) { pend = 'sell'; reason = '트레일링 스탑'; }
        else if (c[i] < ind.s20[i] && ind.mh[i] < 0) { pend = 'sell'; reason = '추세 훼손'; }
      }
    }
    return { dates: dates, hold: hold, strat: strat, sh: shs, trades: trades, pending: pend, reason: reason, cash: cash, close: c[ind.n - 1], highest: hi, lastSell: lastSell, shares0: shares0 };
  }
  /** indBy: {code: indicators(전체 기간)} */
  function computeTrack(indBy, cfg, p) {
    cfg = cfg || TRACK0;
    var per = {}, order = TICKERS.map(function (t) { return t.code; }), codes = order.concat(Object.keys(cfg.shares).filter(function (k) { return order.indexOf(k) < 0; })).filter(function (k) { return cfg.shares[k] > 0; });
    codes.forEach(function (code) {
      if (!indBy[code]) throw new Error(tickerOf(code).name + ' 시세를 가져오지 못했습니다');
      per[code] = trackTicker(indBy[code], cfg.shares[code], tickerOf(code), p, cfg.start);
    });
    var dates = per[codes[0]].dates.filter(function (d) { return codes.every(function (k) { return per[k].dates.indexOf(d) >= 0; }); });
    var hold = dates.map(function () { return 0; }), strat = hold.slice();
    codes.forEach(function (k) { dates.forEach(function (d, j) { var x = per[k].dates.indexOf(d); hold[j] += per[k].hold[x]; strat[j] += per[k].strat[x]; }); });
    var trades = [];
    codes.forEach(function (k) { per[k].trades.forEach(function (t) { trades.push({ d: t.d, name: tickerOf(k).name, code: k, act: t.act, n: t.n, px: t.px, why: t.why }); }); });
    trades.sort(function (a, b) { return a.d < b.d ? -1 : a.d > b.d ? 1 : (a.name < b.name ? -1 : a.name > b.name ? 1 : 0); });
    return { dates: dates, hold: hold, strat: strat, base: hold[0], per: per, codes: codes, trades: trades, start: cfg.start, end: cfg.end, done: dates[dates.length - 1] >= cfg.end };
  }

  // ───────── 섹터·시장 상태 ─────────
  function trendState(rows) {
    var n = rows.length;
    if (n < 61) return null;
    var c = rows.map(function (r) { return r[4]; }), L = n - 1, s20 = sma(c, 20)[L], s60 = sma(c, 60)[L];
    var up = [NaN], dn = [NaN], i;
    for (i = 1; i < n; i++) { var x = c[i] - c[i - 1]; up.push(Math.max(x, 0)); dn.push(Math.max(-x, 0)); }
    var au = ewmFrom(up, 1 / 14, 1)[L], ad = ewmFrom(dn, 1 / 14, 1)[L], rsi = ad === 0 ? 100 : 100 - 100 / (1 + au / ad);
    var ret = function (k) { return L - k >= 0 ? c[L] / c[L - k] - 1 : NaN; };
    var state, key, outlook;
    if (c[L] > s60 && s20 > s60) {
      if (c[L] >= s20) { state = '상승 추세'; key = 'up'; outlook = rsi >= 70 ? '추세는 살아 있으나 단기 과열(RSI ' + rsi.toFixed(0) + ') — 눌림 가능성' : '20·60일선 위 — 추세가 이어지는 구간'; }
      else { state = '상승 중 조정'; key = 'dip'; outlook = '20일선 아래로 눌림 — 60일선(' + won(s60) + ') 지지 여부가 관건'; }
    } else if (c[L] > s20) { state = '반등 시도'; key = 'reb'; outlook = '20일선은 회복 — 60일선(' + won(s60) + ') 회복 전까지는 반등으로 봄'; }
    else if (c[L] > s60) { state = '조정'; key = 'dip'; outlook = '20일선 아래 — 60일선(' + won(s60) + ') 지지 여부가 관건'; }
    else { state = '하락 추세'; key = 'down'; outlook = rsi <= 30 ? '20·60일선 아래 + 과매도(RSI ' + rsi.toFixed(0) + ') — 기술적 반등은 가능하나 추세는 약세' : '20·60일선 아래 — 20일선(' + won(s20) + ') 회복 전까지 약세 구간'; }
    return { date: rows[L][0], close: c[L], r1: ret(1), r5: ret(5), r20: ret(20), r60: ret(60), s20: s20, s60: s60, rsi: rsi, state: state, key: key, outlook: outlook };
  }
  function sectorBoard(rowsBy, marketRows) {
    var mk = marketRows ? trendState(marketRows) : null, list = [];
    SECTORS.forEach(function (s) {
      var t = rowsBy[s.code] ? trendState(rowsBy[s.code]) : null;
      if (t) { t.code = s.code; t.name = s.name; t.rel20 = mk ? t.r20 - mk.r20 : NaN; list.push(t); }
    });
    list.sort(function (a, b) { return b.r20 - a.r20; });
    var above20 = list.filter(function (t) { return t.close > t.s20; }).length, upN = list.filter(function (t) { return t.key === 'up'; }).length, downN = list.filter(function (t) { return t.key === 'down'; }).length;
    var byDay = list.slice().sort(function (a, b) { return b.r1 - a.r1; });
    var lines = [];
    if (list.length) {
      var br = above20 / list.length;
      lines.push(list.length + '개 섹터 중 ' + above20 + '개가 20일선 위 — ' + (br >= 0.67 ? '시장 전반이 고르게 강한 편' : br >= 0.34 ? '강한 섹터와 약한 섹터가 갈리는 장' : '대부분 섹터가 약세 구간'));
      lines.push('20일 기준 강세: ' + list.slice(0, 3).map(function (t) { return t.name + ' ' + pct(t.r20); }).join(', ') + ' · 약세: ' + list.slice(-3).reverse().map(function (t) { return t.name + ' ' + pct(t.r20); }).join(', '));
      var semi = list.filter(function (t) { return t.code === '091160'; })[0];
      if (semi) lines.push('반도체(보유 종목과 직결): ' + semi.state + ' · 20일 ' + pct(semi.r20) + (mk ? ' (시장 대비 ' + pct(semi.rel20) + 'p)' : '') + ' — ' + semi.outlook);
      if (mk) lines.push('시장(KOSPI200): ' + mk.state + ' · 20일 ' + pct(mk.r20) + ' — ' + mk.outlook);
    }
    return { market: mk, list: list, byDay: byDay, above20: above20, upN: upN, downN: downN, lines: lines };
  }

  // ───────── 장마감 체결 vs 장시작 체결 비교 ─────────
  function execCompare(rows, code, p, years) {
    p = p || P0;
    var cost = tickerOf(code), ind = cutYears(indicators(rows, p), years || 3);
    var A = backtest(ind, cost, p, 'open'), B = backtest(ind, cost, p, 'close');
    var sA = stats(ind, A.eq, A.trades), sB = stats(ind, B.eq, B.trades);
    var bg = [], sg = [];
    A.marks.forEach(function (m) { if (m.i > 0) (m.k === 'B' ? bg : sg).push(ind.o[m.i] / ind.c[m.i - 1] - 1); });
    var mean = function (a) { return a.length ? a.reduce(function (x, y) { return x + y; }, 0) / a.length : NaN; };
    var on = [], id = [];
    for (var i = 1; i < ind.n; i++) { on.push(ind.o[i] / ind.c[i - 1] - 1); id.push(ind.c[i] / ind.o[i] - 1); }
    return { code: code, name: cost.name, open: sA, close: sB, buyGap: mean(bg), buyN: bg.length, sellGap: mean(sg), sellN: sg.length,
      gapAbs: mean(on.map(Math.abs)), dayAbs: mean(id.map(Math.abs)), from: ind.d[0], to: ind.d[ind.n - 1] };
  }

  // ───────── 브리핑 (장 시작 · 장중 · 마감) ─────────
  /** 종목 하나의 현재 사실들. rows = 일봉 전체, now = kstNow(), tr = 추적 결과(없어도 됨) */
  function tickerFacts(code, rows, now, p, tr) {
    p = p || P0;
    var tk = tickerOf(code), live = isLiveBar(rows, now), all = indicators(rows, p), conf = live ? indicators(rows.slice(0, -1), p) : all;
    var L = all.n - 1, cur = all.c[L], prev = L > 0 ? all.c[L - 1] : NaN;
    var f = { code: code, name: tk.name, short: tk.short, live: live, date: all.d[L], cur: cur, prev: prev, chg: cur / prev - 1, open: all.o[L],
      gap: all.o[L] / prev - 1, s20: all.s20[L], s60: all.s60[L], rsi: all.rsi[L], up: all.up[L],
      confDate: conf.d[conf.n - 1], hvConf: holderView(conf, p), hvNow: holderView(all, p), buyConf: conf.buy[conf.n - 1] ? conf.bt[conf.n - 1] : '', buyNow: all.buy[L] ? all.bt[L] : '',
      needS60: all.s60[L] / cur - 1 };
    f.lineHold = sellLine(all, p, null);
    if (tr && tr.per[code]) {
      var q = tr.per[code];
      f.tr = { sh: q.sh[q.sh.length - 1], cash: q.cash, pending: q.pending, reason: q.reason, lastSell: q.lastSell, shares0: q.shares0,
        holdRet: q.hold[q.hold.length - 1] / q.hold[0] - 1, stratRet: q.strat[q.strat.length - 1] / q.hold[0] - 1 };
      if (f.tr.sh > 0) f.lineTrack = sellLine(all, p, q.highest);
      // 확정(전일) 기준으로 추적 계좌가 오늘 시가에 할 일
      if (live) {
        var cfgConf = null;
        try { cfgConf = trackTicker(conf, q.shares0, tk, p, tr.start); } catch (e) { cfgConf = null; }
        f.trConf = cfgConf ? { sh: cfgConf.sh[cfgConf.sh.length - 1], pending: cfgConf.pending, reason: cfgConf.reason } : null;
      } else f.trConf = { sh: f.tr.sh, pending: f.tr.pending, reason: f.tr.reason };
    }
    return f;
  }

  function judge(f) {
    // 관리(전략 계좌)와 유지(그대로 보유)를 숫자로 비교한 한 줄
    if (!f.tr) return '';
    if (f.tr.sh > 0) {
      var ln = f.lineTrack;
      return '관리·유지 모두 보유 중이라 차이 없음' + (ln ? ' · 매도 기준가 ' + won(ln.line) + ' (현재가 대비 ' + pct(ln.line / f.cur - 1) + ')' : '');
    }
    if (f.tr.lastSell) {
      var d = f.cur / f.tr.lastSell.px - 1;
      return d < 0 ? '관리 우위 — ' + mdLabel(f.tr.lastSell.d) + ' ' + won(f.tr.lastSell.px) + '에 판 뒤 ' + pct(d) + ', 그만큼 하락을 피함'
        : '유지 우위 — ' + mdLabel(f.tr.lastSell.d) + ' ' + won(f.tr.lastSell.px) + '에 판 뒤 ' + pct(d) + ' 반등, 재매수 조건은 아직 미충족';
    }
    return '관리 계좌는 현금 대기';
  }
  function todoOpen(f) {
    var t = f.trConf;
    if (!t) return f.hvConf.sell ? '보유 시 매도 구간 (' + f.hvConf.why + ')' : (f.buyConf ? '매수 신호(' + f.buyConf + ')' : '신호 없음');
    if (t.pending === 'sell') return '시가 매도 (' + t.reason + ')';
    if (t.pending === 'buy') return '시가 매수 (' + t.reason + ')';
    return t.sh > 0 ? '할 일 없음 — 보유 유지' : '할 일 없음 — 현금 대기(매수 조건 미충족)';
  }
  function buyDistance(f) {
    if (f.cur > f.s60) return f.s20 > f.s60 ? '추세 필터 통과 — 눌림목·돌파 신호 대기' : '종가는 60일선 위, 20일선이 60일선을 넘어야 매수 검토';
    return '매수 검토까지 60일선 ' + won(f.s60) + ' (' + pct(f.needS60) + ' 필요)';
  }

  /** slot: 'open' | 'mid' | 'close'.  ctx: {now, facts:[...], track, board, us:[{name, chg, date}]} */
  function briefing(slot, ctx) {
    var now = ctx.now, facts = ctx.facts || [], tr = ctx.track, board = ctx.board, mk = board && board.market, sec = [], add = function (h, lines) { if (lines.length) sec.push({ h: h, lines: lines }); };
    var names = { open: '장 시작 브리핑', mid: '장중 브리핑', close: '장 마감 브리핑' };
    var title = names[slot] + ' · ' + mdLabel(now.date) + (slot === 'mid' ? ' ' + hm(now.min) : '');
    var anyLive = facts.some(function (f) { return f.live; }), dataDate = facts.length ? facts[0].date : '';
    var note = '';
    if (slot === 'open') note = '어제까지의 종가로 확정된 신호 기준이에요.';
    if (slot === 'mid') note = anyLive ? '장중 가격 기준 잠정치예요. 신호는 종가에서 확정돼요.' : '오늘 장중 시세가 아직 없어 최근 종가(' + mdLabel(dataDate) + ') 기준이에요.';
    if (slot === 'close') note = anyLive ? '아직 장이 끝나지 않아 잠정치예요.' : mdLabel(dataDate) + ' 종가로 확정된 신호예요.';

    // 시장
    var m = [];
    if (slot === 'open' && ctx.us && ctx.us.length) m.push('밤사이 미국: ' + ctx.us.map(function (u) { return u.name + ' ' + pct(u.chg); }).join(' · '));
    if (mk) m.push('KOSPI200 ' + (slot === 'open' ? '' : pct(mk.r1) + ' · ') + mk.state + ' (20일 ' + pct(mk.r20) + ')');
    if (board && board.list.length) {
      if (slot === 'open') m.push('20일 강세 섹터: ' + board.list.slice(0, 2).map(function (t) { return t.name; }).join(', ') + ' · 약세: ' + board.list.slice(-2).reverse().map(function (t) { return t.name; }).join(', '));
      else m.push('오늘 강세: ' + board.byDay.slice(0, 2).map(function (t) { return t.name + ' ' + pct(t.r1); }).join(', ') + ' · 약세: ' + board.byDay.slice(-2).reverse().map(function (t) { return t.name + ' ' + pct(t.r1); }).join(', '));
    }
    add('시장', m);

    // 종목
    var t = [];
    facts.forEach(function (f) {
      if (slot === 'open') {
        var g = f.live ? ' · 시가 갭 ' + pct(f.gap) : '';
        t.push(f.short + ': ' + todoOpen(f) + g);
      } else if (slot === 'mid') {
        var s = f.short + ' ' + won(f.cur) + ' (' + pct(f.chg) + ')';
        if (f.tr && f.tr.sh > 0 && f.lineTrack) s += ' — 종가 ' + won(f.lineTrack.line) + ' 아래면 매도 신호(' + f.lineTrack.which + '), 지금은 ' + (f.cur < f.lineTrack.line ? '아래' : '위 ' + pct(f.cur / f.lineTrack.line - 1));
        else if (f.tr) s += ' — 관리: 현금 · ' + buyDistance(f);
        else s += ' — ' + (f.hvNow.sell ? '보유 시 매도 구간' : '보유 시 유지');
        t.push(s);
      } else {
        var a;
        if (f.tr) a = f.tr.pending === 'sell' ? '매도 신호 (' + f.tr.reason + ')' : f.tr.pending === 'buy' ? '매수 신호 (' + f.tr.reason + ')' : (f.tr.sh > 0 ? '보유 유지' : '현금 대기');
        else a = f.hvNow.sell ? '보유 시 매도 구간' : (f.buyNow ? '매수 신호(' + f.buyNow + ')' : '신호 없음');
        t.push(f.short + ' ' + won(f.cur) + ' (' + pct(f.chg) + ') — 관리: ' + a + (f.tr && f.tr.sh === 0 && f.hvNow.sell ? ' · 유지 계좌는 매도 구간' : ''));
      }
    });
    add(slot === 'open' ? '오늘 시가에 할 일 (관리 계좌)' : slot === 'mid' ? '종목별 현재' : '종목별 마감 신호', t);

    // 관리 vs 유지
    var cmp = [];
    if (tr) {
      var n = tr.dates.length, hr = tr.hold[n - 1] / tr.base - 1, sr = tr.strat[n - 1] / tr.base - 1;
      cmp.push('누적(' + mdLabel(tr.start) + '부터): 관리 ' + pct(sr, 2) + ' · 유지 ' + pct(hr, 2) + ' → ' + (sr >= hr ? '관리가 ' : '유지가 ') + Math.abs((sr - hr) * 100).toFixed(2) + '%p 앞섬');
      if (n >= 2 && slot !== 'open') {
        var dh = tr.hold[n - 1] / tr.hold[n - 2] - 1, ds = tr.strat[n - 1] / tr.strat[n - 2] - 1;
        cmp.push('오늘 하루: 관리 ' + pct(ds, 2) + ' · 유지 ' + pct(dh, 2));
      }
      facts.forEach(function (f) { var j = judge(f); if (j && f.tr && f.tr.sh === 0) cmp.push(f.short + ': ' + j); });
    }
    add('관리 중 vs 유지 중', cmp);

    // 판단
    var jd = [];
    if (mk) {
      if (mk.key === 'down') jd.push('시장이 하락 추세라 레버리지 ETF는 손실이 지수의 2배보다 커지기 쉬운 구간이에요. 현금 비중이 방어에 유리했어요.');
      else if (mk.key === 'up') jd.push('시장이 상승 추세예요. 이런 구간에서는 그대로 보유한 쪽이 유리한 경우가 많고, 관리 계좌는 재진입 신호가 늦을 수 있어요.');
      else jd.push('시장이 방향을 정하지 못한 구간이에요. 신호가 자주 바뀔 수 있어 매매 횟수와 비용을 함께 봐야 해요.');
    }
    if (slot === 'close') jd.push('신호대로 실행한다면: 장후 종가매매 15:40~16:00(오늘 종가로 체결) 또는 내일 시가.');
    if (slot === 'open') jd.push('시가는 밤사이 해외 변동이 한꺼번에 반영돼 갭이 클 수 있어요. 지정가 사용을 고려하세요.');
    add('판단', jd);

    var text = sec.map(function (s) { return '[' + s.h + ']\n' + s.lines.join('\n'); }).join('\n');
    var short = (t.slice(0, 4).join(' / ')).slice(0, 180);
    return { slot: slot, title: title, note: note, sections: sec, text: text, short: short };
  }
  /** 지금 시각에 맞는 브리핑 종류 */
  function slotNow(now) {
    var st = marketState(now), s = session(now.date);
    if (st === 'open') return now.min < s.open + 60 ? 'open' : 'mid';
    if (st === 'closed') return 'close';
    if (st === 'pre') return 'open';
    return 'close';
  }

  var api = { TICKERS: TICKERS, MARKET: MARKET, SECTORS: SECTORS, SLIP: SLIP, P0: P0, TRACK0: TRACK0, tickerOf: tickerOf,
    dayNum: dayNum, dayStr: dayStr, addDays: addDays, dow: dow, mdLabel: mdLabel, hm: hm, kstNow: kstNow, session: session, isTradingDay: isTradingDay,
    nextTradingDay: nextTradingDay, marketState: marketState, isLiveBar: isLiveBar, sma: sma, cleanRows: cleanRows, indicators: indicators, cutYears: cutYears,
    backtest: backtest, stats: stats, holderView: holderView, signalText: signalText, analyze: analyze, sellLine: sellLine, trackTicker: trackTicker,
    computeTrack: computeTrack, trendState: trendState, sectorBoard: sectorBoard, execCompare: execCompare, tickerFacts: tickerFacts, briefing: briefing,
    slotNow: slotNow, buyDistance: buyDistance, todoOpen: todoOpen, judge: judge, won: won, pct: pct };
  root.KRT = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
