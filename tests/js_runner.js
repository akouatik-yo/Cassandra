// Exécute une liste de cas sur le moteur JavaScript et renvoie les résultats en JSON.
// Utilisé par tests/test_js_conformance.py (node >= 18, WebCrypto global).
const E = require("../web/oracle_engine.js");

async function run(c) {
  const a = c.args;
  switch (c.fn) {
    case "canonical": return E.canonicalJson(a[0]);
    case "hash_obj": return E.hashObj(a[0]);
    case "mulberry": { const r = new E.Mulberry32(a[0]); return Array.from({ length: a[1] }, () => r.nextU32()); }
    case "run_forecast": return E.runForecast(a[0], a[1], a[2]);
    case "build_history": return E.buildHistory(a[0], a[1], a[2]);
    case "merkle_root": return E.merkleRoot(a[0]);
    case "verify": return E.verifyEntries(a[0]);
    case "dashboard": return E.dashboard(a[0], a[1]);
    case "tournament": return E.tournament(a[0], a[1]);
    case "score_records": return E.scoreRecords(a[0], a[1]);
    case "shadow": return E.shadowComparison(a[0], a[1]);
    case "reconcile": return E.reconcile(a[0], a[1], a[2]);
    case "stack_weights": return E.stackWeights(a[0], a[1], a[2], a[3], a[4]);
    case "ancestor_id": return E.ancestorId(a[0], a[1]);
    case "extraction_rates": return E.extractionErrorRates(a[0], a[1]);
    default: throw new Error("cas inconnu " + c.fn);
  }
}

let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", async () => {
  const cases = JSON.parse(input);
  const out = [];
  for (const c of cases) {
    try { out.push({ ok: true, value: await run(c) }); } catch (e) { out.push({ ok: false, error: String(e && e.stack || e) }); }
  }
  process.stdout.write(JSON.stringify(out));
});
