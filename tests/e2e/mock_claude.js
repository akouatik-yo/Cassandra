// Simulation de l'environnement claude.ai pour tester la version hébergée hors ligne :
// `sample` répond de façon déterministe (même contrat que tests/conftest.py::scripted_llm),
// `db` est une base de documents en mémoire avec where/orderBy/limit/get/set/acquire.
(function () {
  const docs = new Map();
  window.__mockDocs = docs;
  window.__sampleCalls = 0;
  const snap = (path) => ({ id: path.split("/").pop(), exists: docs.has(path), data: () => (docs.has(path) ? JSON.parse(JSON.stringify(docs.get(path))) : undefined), metadata: { fromCache: false, hasPendingWrites: false } });
  function docRef(path) {
    return { id: path.split("/").pop(), path,
      get: async () => snap(path),
      set: async (d) => { docs.set(path, JSON.parse(JSON.stringify(d))); },
      update: async (d) => { docs.set(path, Object.assign(docs.get(path) || {}, d)); },
      delete: async () => { docs.delete(path); },
      acquire: async () => ({ acquired: true, version: 1 }) };
  }
  function query(col, filters, order, lim) {
    return {
      where: (f, op, v) => query(col, filters.concat([[f, op, v]]), order, lim),
      orderBy: (f, dir) => query(col, filters, [f, dir || "asc"], lim),
      limit: (n) => query(col, filters, order, n),
      get: async () => {
        let rows = [];
        for (const [p, d] of docs) if (p.startsWith(col + "/") && p.split("/").length === col.split("/").length + 1) rows.push([p, d]);
        rows = rows.filter(([, d]) => filters.every(([f, op, v]) => (op === ">" ? d[f] > v : op === "==" ? d[f] === v : true)));
        rows.sort((a, b) => (order ? (a[1][order[0]] - b[1][order[0]]) * (order[1] === "desc" ? -1 : 1) : a[0] < b[0] ? -1 : 1));
        if (lim) rows = rows.slice(0, lim);
        const ds = rows.map(([p]) => snap(p));
        return { docs: ds, size: ds.length, empty: !ds.length, docChanges: () => [], metadata: {} };
      },
      doc: (id) => docRef(col + "/" + id),
    };
  }
  const db = { doc: docRef, collection: (c) => query(c, [], null, null) };

  function reply(prompt, promptId) {
    if (promptId === "formalize") {
      const q = prompt.split("Question from the user: ")[1].split("\n")[0];
      return { question_type: "binary", text: q, definition: "Termes au sens usuel.",
        resolution_rule: "OUI si l'événement est confirmé par la source officielle avant l'échéance.",
        resolution_source: "https://example.org/officiel", source_kind: "official_statistics", resolution_deadline: "2027-01-02",
        horizon: "P90D", ambiguity_policy: "cancel", domain: "economics", is_atomic: false,
        subquestions: [{ text: "Sous-question test", resolution_rule: "règle", relation: "implies", hard: true }],
        test_scenarios: ["Confirmé le 1er décembre", "Infirmé", "Confirmé après l'échéance", "Source muette", "Confirmé par une source non officielle"],
        self_negating_risk: true, self_negating_reason: "Test du signalement." };
    }
    if (promptId === "apply_rule") return { verdicts: ["YES", "NO", "NO", "NO", "AMBIGUOUS"] };
    if (promptId === "reformulate") {
      const idx = Number(prompt.split("Independent attempt number ")[1].split(".")[0]);
      return { resolution_rule: "Règle reformulée " + idx, verdicts: idx % 2 ? ["YES", "NO", "NO", "AMBIGUOUS", "NO"] : ["YES", "NO", "NO", "NO", "AMBIGUOUS"] };
    }
    if (promptId === "extract") {
      const text = prompt.split("<<<\n")[1].split("\n>>>")[0];
      const run = Number(prompt.split("Extraction run ")[1].split(".")[0]);
      let lines = text.split("\n").filter((l) => l.split("|").length === 5);
      if (run === 3 && lines.length > 1) lines = lines.slice(0, -1);
      return { triplets: lines.map((l) => { const [fact, date, src, d, t] = l.split("|");
        return { fact, date, primary_source: src, direction: d, evidence_type: t, mentions_forecasting_tool: false }; }) };
    }
    throw new Error("prompt inconnu");
  }
  async function sample(input, opts) {
    window.__sampleCalls++;
    await new Promise((r) => setTimeout(r, 20));
    const id = input.startsWith("You formalize") ? "formalize" : input.startsWith("You independently") ? "reformulate"
      : input.startsWith("Apply this") ? "apply_rule" : "extract";
    const out = reply(input, id);
    if (opts && opts.modelTier === "quick" && id === "reformulate") out.verdicts = ["YES", "NO", "AMBIGUOUS", "NO", "NO"];
    return { text: JSON.stringify(out), truncated: false };
  }
  window.claude = { use: async (name) => (name === "sample" ? sample : name === "db" ? db : null) };
})();
