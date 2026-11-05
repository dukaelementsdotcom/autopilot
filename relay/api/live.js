// Live relay for the Research Machine. Runs in Frankfurt (fra1), where Binance serves its public market data normally.
// It only READS public data (no keys, no accounts, no orders) and returns the last ~3 days for the requested coins:
//   k: hourly candles  [openTime, open, high, low, close, volume, takerBuyVolume]   (unfinished current hour removed)
//   m: 5-minute positioning readings, the same series as Binance's public archive "metrics" files:
//      [time, openInterestValue, crowdLongShortRatio, topTraderAccountRatio, topTraderPositionRatio, takerBuySellRatio]
// Responses are cached by Vercel's CDN for 5 minutes, so repeated calls never hammer Binance.
const BASE = "https://fapi.binance.com";
const SERIES = {
  oi: ["/futures/data/openInterestHist", (x) => +x.sumOpenInterestValue],
  glob: ["/futures/data/globalLongShortAccountRatio", (x) => +x.longShortRatio],
  topAcc: ["/futures/data/topLongShortAccountRatio", (x) => +x.longShortRatio],
  topPos: ["/futures/data/topLongShortPositionRatio", (x) => +x.longShortRatio],
  tk: ["/futures/data/takerlongshortRatio", (x) => +x.buySellRatio],
};

async function get(path, params, tries = 3) {
  const url = BASE + path + "?" + new URLSearchParams(params).toString();
  for (let i = 0; i < tries; i++) {
    try {
      const ctl = new AbortController(); const tm = setTimeout(() => ctl.abort(), 12000);
      const r = await fetch(url, { signal: ctl.signal, headers: { "User-Agent": "research-machine-relay/1.0" } });
      clearTimeout(tm);
      if (r.ok) return await r.json();
      if (r.status === 400) return [];               // e.g. symbol not listed
      await new Promise((s) => setTimeout(s, 400 * (i + 1)));
    } catch (e) { await new Promise((s) => setTimeout(s, 400 * (i + 1))); }
  }
  throw new Error("failed " + path + " " + params.symbol);
}

async function pages(path, symbol, n = 2) {            // 5-minute readings: 500 per page, newest first -> ~83 hours
  let out = [], end = undefined;
  for (let p = 0; p < n; p++) {
    const params = { symbol, period: "5m", limit: 500 }; if (end) params.endTime = end;
    const rows = await get(path, params);
    if (!rows.length) break;
    out = rows.concat(out); end = Math.min(...rows.map((x) => +x.timestamp)) - 1;
  }
  return out;
}

async function coin(c) {
  const symbol = c + "USDT";
  const k = await get("/fapi/v1/klines", { symbol, interval: "1h", limit: 96 });
  const now = Date.now();
  const kk = k.filter((x) => +x[6] < now).map((x) => [Math.floor(+x[0] / 1000), +x[1], +x[2], +x[3], +x[4], +x[5], +x[9]]);
  const m = {};
  for (const [name, [path, pick]] of Object.entries(SERIES)) {
    for (const x of await pages(path, symbol)) {
      const t = Math.floor(+x.timestamp / 1000); (m[t] = m[t] || {})[name] = pick(x);
    }
  }
  const mm = Object.keys(m).map(Number).sort((a, b) => a - b).map((t) => [t, m[t].oi ?? null, m[t].glob ?? null, m[t].topAcc ?? null, m[t].topPos ?? null, m[t].tk ?? null]);
  return { k: kk, m: mm };
}

module.exports = async (req, res) => {
  const coins = String((req.query && req.query.coins) || "BTC").toUpperCase().split(",").filter((c) => /^[A-Z0-9]{2,12}$/.test(c)).slice(0, 60);
  const out = { at: Math.floor(Date.now() / 1000), region: process.env.VERCEL_REGION || "?", coins: {}, errors: {} };
  let i = 0;
  async function worker() {                            // at most 4 coins at once (Binance rate limits)
    while (i < coins.length) { const c = coins[i++]; try { out.coins[c] = await coin(c); } catch (e) { out.errors[c] = String(e.message || e).slice(0, 120); } }
  }
  await Promise.all([worker(), worker(), worker(), worker()]);
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=60");
  res.setHeader("Content-Type", "application/json");
  res.status(200).send(JSON.stringify(out));
};
