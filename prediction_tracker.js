// Nightly forward test of the Console Live / Player Spotlight win-chance model (scout.js in the civ-lookup repo).
//
// Takes ranked console games (1v1 and team) that started after the model was fitted, predicts each from what was known at its
// start (ratings, civs, map and, for 1v1, each player's earlier games), and compares with the result. Rows are appended to
// data/prediction-eval.json together with a summary. A game is predicted once, the first night it is seen finished.
//
//   node prediction_tracker.js [scout.js path] [max players to sample]
//
// Accuracy is reported three ways: the hit rate (the favoured side won), calibration (when it says 70%, the side wins about
// 70% of the time) and log-loss / Brier score against a rating-only prediction and a coin flip.
const fs = require('fs'), vm = require('vm'), crypto = require('crypto');
const SCOUT_JS = process.argv[2] || '../civ-lookup/scout.js', MAX_PLAYERS = Number(process.argv[3] || 700);
const CUT = '2026-10-23T02:00:00Z', END = Date.parse('2026-10-31T00:00:00Z'), FILE = 'data/prediction-eval.json';
if (Date.now() > END) { console.log('Test window is over (ends 2026-10-31); nothing to do. Disable the workflow.'); process.exit(0); }

const UA = { 'User-Agent': 'AoE2DataPipeline/1.0' };
const uaFetch = (u, o = {}) => fetch(u, Object.assign({ headers: UA }, o));
const ctx = vm.createContext({ console, fetch: uaFetch, setTimeout, Date, Math, JSON, Map, Set, Promise, URLSearchParams,
  document: { createElement: () => ({}), head: { appendChild() {} } } });
const src = fs.readFileSync(SCOUT_JS, 'utf8');
const ENGINE = crypto.createHash('sha1').update(src).digest('hex').slice(0, 8);
vm.runInContext(src, ctx);
vm.runInContext(`var esc = s => String(s == null ? '' : s); var API = 'https://data.aoe2companion.com/api'; var platIcon = () => ''; var queueRender = () => {};`, ctx);
const run = code => vm.runInContext(code, ctx);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// the API answers 403/429 "too many requests" when asked too fast: pause and retry
async function getJson(url) {
  for (let a = 0; a < 5; a++) {
    let r = null;
    try { r = await run(`histGet(${JSON.stringify(url)})`); } catch (e) { /* a dropped connection ("fetch failed") is retried like a rate limit */ }
    if (r && r.ok) { try { return await r.json(); } catch (e) { return null; } }
    await sleep(4000 * (a + 1));
  }
  return null;
}

function summarize(rows) {
  const ll = (p, y) => { p = Math.min(Math.max(p, 1e-6), 1 - 1e-6); return -(y ? Math.log(p) : Math.log(1 - p)); };
  const wilson = (k, n) => { if (!n) return [0, 0]; const z = 1.96, ph = k / n, den = 1 + z * z / n, c = (ph + z * z / (2 * n)) / den, h = z * Math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den; return [c - h, c + h]; };
  const one = rs => {
    const n = rs.length; if (!n) return { n: 0 };
    const dec = rs.filter(r => Math.abs(r.p - .5) > 1e-9), hit = dec.filter(r => (r.p > .5) === !!r.won).length;
    const rn = rs.filter(r => r.dr !== 0), rhit = rn.filter(r => (r.dr > 0) === !!r.won).length;
    const bands = [[.5, .55], [.55, .6], [.6, .65], [.65, .7], [.7, .8], [.8, 1.01]].map(([a, b]) => {
      const g = rs.map(r => [Math.max(r.p, 1 - r.p), r.p >= .5 ? r.won : 1 - r.won]).filter(x => x[0] >= a && x[0] < b);
      return { from: a, to: Math.min(b, 1), n: g.length, predicted: g.length ? g.reduce((s, x) => s + x[0], 0) / g.length : null, actual: g.length ? g.reduce((s, x) => s + x[1], 0) / g.length : null };
    });
    return { n, hit: hit, decided: dec.length, hitRate: hit / dec.length, hitRange: wilson(hit, dec.length), ratingHitRate: rhit / rn.length,
      logLoss: rs.reduce((s, r) => s + ll(r.p, r.won), 0) / n, logLossRatingOnly: rs.reduce((s, r) => s + ll(r.pr, r.won), 0) / n,
      brier: rs.reduce((s, r) => s + (r.p - r.won) ** 2, 0) / n, brierRatingOnly: rs.reduce((s, r) => s + (r.pr - r.won) ** 2, 0) / n, calibration: bands };
  };
  const days = [...new Set(rows.map(r => r.t.slice(0, 10)))].sort();
  return { all: one(rows), '1v1': one(rows.filter(r => r.mode === '1v1')), team: one(rows.filter(r => r.mode === 'team')),
    daily: days.map(d => { const o = one(rows.filter(r => r.t.slice(0, 10) === d)); return { date: d, n: o.n, hitRate: o.hitRate, logLoss: o.logLoss, logLossRatingOnly: o.logLossRatingOnly }; }) };
}

async function main() {
  const prev = fs.existsSync(FILE) ? JSON.parse(fs.readFileSync(FILE, 'utf8')) : { cut: CUT, rows: [] };
  const seen = new Set(prev.rows.map(r => r.id));
  const roster = JSON.parse(fs.readFileSync('data/console-roster.json', 'utf8'));
  const ci = Object.fromEntries(roster.cols.map((c, i) => [c, i]));
  const since = new Date(Date.now() - 3 * 864e5).toISOString().slice(0, 10);
  let cand = roster.players.filter(p => (p[ci.last1v1] || '') >= since || (p[ci.lastTeam] || '') >= since);
  let seed = Math.floor(Date.now() / 864e5); const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  cand = cand.map(p => [rnd(), p]).sort((a, b) => a[0] - b[0]).map(x => x[1]).slice(0, MAX_PLAYERS);
  console.log(`engine ${ENGINE}: ${cand.length} active roster players sampled, ${prev.rows.length} games already scored`);
  run(`loadCivStats()`); await sleep(5000);

  const games = new Map();
  for (const lb of ['rm_1v1_console', 'rm_team_console']) {
    for (const p of cand) {
      if (lb === 'rm_1v1_console' ? !((p[ci.last1v1] || '') >= since) : !((p[ci.lastTeam] || '') >= since)) continue;
      const j = await getJson(`https://data.aoe2companion.com/api/matches?profile_ids=${p[0]}&leaderboard_ids=${lb}&per_page=100&page=1`);
      if (!j) continue;
      for (const m of (j.matches || j)) {
        if (!m.started || m.started < CUT || m.abandoned || !m.finished || seen.has(m.matchId)) continue;
        const ps = m.teams.flatMap(t => t.players);
        if (ps.some(q => q.won == null || !(q.rating > 0))) continue;
        games.set(m.matchId, m);
      }
    }
    console.log(lb, 'new games so far', games.size);
  }
  const need = new Set();
  for (const m of games.values()) if (m.leaderboardId === 'rm_1v1_console') m.teams.flatMap(t => t.players).forEach(q => need.add(q.profileId));
  console.log('player histories to load', need.size);
  const H = new Map(); let n = 0;
  for (const id of need) {
    try { H.set(id, await run(`histFetch(${id}, 'rm_1v1_console')`)); } catch (e) { await sleep(3000); }
    if (++n % 100 === 0) console.log('histories', n);
  }
  const rows = [];
  for (const m of games.values()) {
    const teams = m.teams.filter(t => t.players.length);
    if (teams.length !== 2 || teams[0].players.length !== teams[1].players.length) continue;
    const t0 = Date.parse(m.started), a = teams[0].players, b = teams[1].players;
    const won = a.some(q => q.won) ? 1 : 0, dr = a.reduce((s, q) => s + q.rating, 0) / a.length - b.reduce((s, q) => s + q.rating, 0) / b.length;
    let o, mode;
    if (m.leaderboardId === 'rm_1v1_console') {
      mode = '1v1';
      const f = h => h ? { games: h.games.filter(x => x.ts && Date.parse(x.ts) < t0) } : null, ha = f(H.get(a[0].profileId)), hb = f(H.get(b[0].profileId));
      o = run(`oddsFor('1v1', ${JSON.stringify(a[0])}, ${JSON.stringify(b[0])}, ${JSON.stringify(ha && hb ? ha : null)}, ${JSON.stringify(ha && hb ? hb : null)}, ${JSON.stringify(m.mapName || '')})`);
    } else { mode = 'team'; o = run(`oddsTeam(${JSON.stringify(a)}, ${JSON.stringify(b)})`); }
    rows.push({ id: m.matchId, t: m.started, mode, map: m.mapName || '', p: +o.p.toFixed(4), pr: +o.steps[0].p.toFixed(4), dr: Math.round(dr), won, v: ENGINE });
  }
  const all = prev.rows.concat(rows).sort((x, y) => x.t < y.t ? -1 : 1);
  fs.mkdirSync('data', { recursive: true });
  fs.writeFileSync(FILE, JSON.stringify({ cut: CUT, updated: new Date().toISOString(), engines: [...new Set(all.map(r => r.v))], summary: summarize(all), rows: all }));
  console.log(`scored ${rows.length} new games; ${all.length} in total`);
}
main().catch(e => {
  // an annotation shows up in the run's check data, which can be read without logging in
  console.log('::error::prediction tracker failed: ' + String((e && e.stack) || e).split(String.fromCharCode(10)).join(' | ').slice(0, 900));
  process.exit(1);
});
