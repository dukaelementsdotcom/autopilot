// The same live relay as a tiny always-free web service (e.g. Render, region Frankfurt). No dependencies.
// GET /api/live?coins=BTC,ETH&pages=1&kl=72  ->  same JSON as the Vercel relay.
const http = require("http");
const handler = require("./live.js");
http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://x");
  if (u.pathname === "/health") { res.end("ok"); return; }
  if (u.pathname !== "/api/live") { res.statusCode = 404; res.end("not found"); return; }
  req.query = Object.fromEntries(u.searchParams);
  try { await handler(req, res); } catch (e) { res.statusCode = 500; res.end(JSON.stringify({ error: String(e) })); }
}).listen(process.env.PORT || 10000);
