/* Oracle Calibré — back-ends de l'interface.
 *
 * ServerBackend : appelle l'API du serveur Python (moteur de référence).
 * LocalBackend  : version hébergée sur claude.ai ; moteur JavaScript conforme,
 *                 LLM via la capacité `sample`, registre via la capacité `db`.
 * L'ingestion JS ci-dessous est le miroir de oracle_calibre/ingestion/pipeline.py :
 * même ordre d'appels, mêmes prompts, même agrégation (donc même rejeu).
 */
(function (root) {
  "use strict";
  const E = root.OracleEngine;

  const nowIso = () => new Date().toISOString();
  const uuid = () => (root.crypto && root.crypto.randomUUID ? root.crypto.randomUUID() :
    "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => { const r = (Math.random() * 16) | 0; return (c === "x" ? r : (r & 3) | 8).toString(16); }));
  const clone = (x) => JSON.parse(JSON.stringify(x));

  // -------------------------------------------------------------- ingestion
  class Session {
    constructor(clients, prompts, cfg, onProgress) {
      this.primary = clients.primary; this.secondary = clients.secondary || null;
      this.prompts = prompts; this.cfg = cfg; this.snapshots = []; this.onProgress = onProgress || (() => {});
    }
    /** Lance plusieurs appels en parallèle ; les rangs et l'ordre des snapshots
     *  sont fixés à la création, comme l'exécution séquentielle en Python. */
    async callMany(specs) {
      const counts = {};
      const planned = specs.map((s) => {
        const role = s.role || "primary";
        const key = s.promptId + "|" + role;
        if (!(key in counts)) counts[key] = this.snapshots.filter((x) => x.prompt_id === s.promptId && x.role === role).length;
        const idx = counts[key]++;
        return { promptId: s.promptId, role, idx, prompt: E.render(this.prompts[s.promptId], s.values) };
      });
      let done = 0;
      this.onProgress(0, planned.length);
      const results = await Promise.all(planned.map(async (p) => {
        const client = p.role === "primary" ? this.primary : this.secondary;
        const text = await client.complete({ prompt_id: p.promptId, prompt: p.prompt, role: p.role, sample_index: p.idx });
        this.onProgress(++done, planned.length);
        return text;
      }));
      const parsed = [];
      for (let i = 0; i < planned.length; i++) {
        const p = planned[i], client = p.role === "primary" ? this.primary : this.secondary, info = client.info();
        const key = `${p.promptId}|${p.role}|${p.idx}|${await E.sha256Hex(p.prompt)}`;
        this.snapshots.push({ request_key: key, prompt_id: p.promptId, role: p.role, sample_index: p.idx, prompt: p.prompt,
          response_text: results[i], model_id: info.model_id, model_version: info.model_version, provider: info.llm_provider });
        parsed.push(E.parseJsonReply(results[i]));
      }
      return parsed;
    }
    async call(promptId, role, values) { return (await this.callMany([{ promptId, role, values }]))[0]; }
  }

  class SampleClient {
    constructor(sample, tier) { this.sample = sample; this.tier = tier; }
    info() { return { llm_provider: "claude.ai (capacité sample)", model_id: "claude.ai:" + this.tier, model_version: "non exposée par la plateforme",
      temperature: null, seed_if_supported: null }; }
    async complete(req) {
      const r = await this.sample(req.prompt, { cache: false, modelTier: this.tier });
      if (r.truncated) throw new Error("réponse du modèle tronquée");
      return r.text;
    }
  }
  class ReplayClient {
    constructor(snaps, info) { this.map = {}; for (const s of snaps) this.map[s.request_key] = s.response_text; this._info = info || {}; }
    info() { return { llm_provider: this._info.llm_provider || "replay", model_id: this._info.model_id || "replay",
      model_version: this._info.model_version || "replay", temperature: null, seed_if_supported: null }; }
    async complete(req) {
      const key = `${req.prompt_id}|${req.role}|${req.sample_index}|${await E.sha256Hex(req.prompt)}`;
      if (!(key in this.map)) throw new Error("rejeu : réponse figée introuvable pour " + req.prompt_id);
      return this.map[key];
    }
  }

  const scenariosText = (sc) => sc.map((s, i) => `${i + 1}. ${s}`).join("\n");

  async function formalize(session, text, now) {
    const f = await session.call("formalize", "primary", { question: text, now });
    const qtype = E.QUESTION_TYPES.includes(f.question_type) ? f.question_type : "binary";
    const policy = E.AMBIGUITY_POLICIES.includes(f.ambiguity_policy) ? f.ambiguity_policy : "cancel";
    const scen = (f.test_scenarios || []).map(String).slice(0, 5);
    const subq = [];
    for (const s of f.subquestions || []) if (E.RELATIONS.includes(s.relation))
      subq.push({ text: s.text || "", resolution_rule: s.resolution_rule || "", relation: s.relation, hard: !!(s.hard || false) });
    const draft = {
      input_text: text, now, question_type: qtype, text: f.text || text, definition: f.definition || "",
      resolution_rule: f.resolution_rule || "", resolution_source: f.resolution_source || "", source_kind: f.source_kind || "other",
      resolution_deadline: f.resolution_deadline || "", horizon: f.horizon || "", ambiguity_policy: policy, domain: f.domain || "other",
      is_atomic: f.is_atomic === undefined ? true : !!f.is_atomic, subquestions: subq, test_scenarios: scen,
      self_negating_risk: !!(f.self_negating_risk || false), self_negating_reason: f.self_negating_reason || "",
    };
    await measureRuleStability(session, draft, true);
    return draft;
  }

  async function measureRuleStability(session, draft, reformulate) {
    const n = session.cfg.ingestion.n_reformulations, scen = draft.test_scenarios, st = scenariosText(scen);
    const specs = [{ promptId: "apply_rule", role: "primary", values: { question: draft.text, rule: draft.resolution_rule, scenarios: st } }];
    if (reformulate) {
      for (let i = 0; i < n; i++) specs.push({ promptId: "reformulate", role: "primary", values: { index: i + 1, question: draft.text, deadline: draft.resolution_deadline, scenarios: st } });
      if (session.secondary) specs.push({ promptId: "reformulate", role: "secondary", values: { index: 1, question: draft.text, deadline: draft.resolution_deadline, scenarios: st } });
    }
    const out = await session.callMany(specs);
    if (reformulate) {
      draft.reformulations = out.slice(1, 1 + n).map((r) => ({ resolution_rule: r.resolution_rule || "", verdicts: E.cleanVerdicts(r.verdicts, scen.length) }));
      draft.second_model_reformulation = session.secondary ? { resolution_rule: out[1 + n].resolution_rule || "",
        verdicts: E.cleanVerdicts(out[1 + n].verdicts, scen.length), model_id: session.secondary.info().model_id } : null;
    }
    const vectors = [E.cleanVerdicts(out[0].verdicts, scen.length)].concat(draft.reformulations.map((r) => r.verdicts));
    const second = draft.second_model_reformulation;
    draft.rule_verdicts = vectors[0];
    draft.resolution_stability_score = E.meanPairwiseAgreement(vectors);
    draft.resolution_inter_model_agreement = second ? E.agreement(second.verdicts, E.majority(vectors)) : null;
    draft.rule_validation_protocol = { n_reformulations: n, n_scenarios: scen.length,
      agreement_metric: "accord moyen par paires des verdicts YES/NO/AMBIGUOUS sur scénarios de test",
      second_model: session.secondary ? session.secondary.info().model_id : null };
  }

  async function applyHumanValidation(session, draft, edits, validated) {
    const ed = {};
    for (const [k, v] of Object.entries(edits || {})) if (v !== null && v !== undefined && v !== "") ed[k] = v;
    let ruleChanged = false, textChanged = false;
    for (const k of ["resolution_rule", "resolution_source", "resolution_deadline", "horizon", "ambiguity_policy", "text"]) {
      if (k in ed && ed[k] !== draft[k]) {
        draft[k] = ed[k];
        ruleChanged = ruleChanged || k === "resolution_rule";
        textChanged = textChanged || k === "text" || k === "resolution_deadline";
      }
    }
    if (ruleChanged || textChanged) await measureRuleStability(session, draft, textChanged);
    draft.rule_edited_by_human = Object.keys(ed).length > 0;
    draft.resolution_human_validated = !!validated;
    return draft;
  }

  async function extractSources(session, draft, sources, now) {
    const k = session.cfg.ingestion.n_extractions, lineage = [], rejected = [], retrieval = [], specs = [], plan = [];
    sources.forEach((src, dIdx) => {
      const pub = src.published_at || "";
      retrieval.push({ title: src.title, url: src.url, published_at: src.published_at, text: src.text });
      if (!pub || pub.slice(0, 10) > now.slice(0, 10) || (pub.length > 10 && pub > now)) {
        rejected.push({ title: src.title, reason: "date de publication absente ou postérieure" }); return;
      }
      for (let r = 0; r < k; r++) {
        plan.push({ dIdx, r });
        specs.push({ promptId: "extract", role: "primary", values: { index: r + 1, question: draft.text, rule: draft.resolution_rule, now,
          title: src.title || "", url: src.url || "", published_at: pub, text: src.text || "" } });
      }
    });
    const outs = specs.length ? await session.callMany(specs) : [];
    const runsByDoc = {};
    plan.forEach((p, i) => { (runsByDoc[p.dIdx] = runsByDoc[p.dIdx] || [])[p.r] = (outs[i].triplets || []); });
    for (const dIdx of Object.keys(runsByDoc).map(Number).sort((a, b) => a - b)) {
      const src = sources[dIdx], runs = runsByDoc[dIdx], seen = {};
      for (let r = 0; r < runs.length; r++) for (const t of runs[r]) {
        const anc = await E.ancestorId(t.primary_source || "", t.date || "");
        const d = ["yes", "no", "neutral"].includes(t.direction) ? t.direction : "neutral";
        const e = (seen[anc] = seen[anc] || { votes: {}, first: {}, runs: new Set() });
        if (!e.runs.has(r)) { e.runs.add(r); e.votes[d] = (e.votes[d] || 0) + 1; if (!(d in e.first)) e.first[d] = t; }
      }
      Object.keys(seen).sort().forEach((anc, j) => {
        const e = seen[anc];
        const [modal, cnt] = Object.entries(e.votes).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0];
        const t = e.first[modal];
        const derived = !!src.derived_from_tool_output || !!t.mentions_forecasting_tool || E.TOOL_MARKERS.test(src.text || "");
        lineage.push({ source_id: `SRC-${String(dIdx + 1).padStart(3, "0")}-${String(j + 1).padStart(2, "0")}`, primary_ancestor_id: anc,
          timestamp: src.published_at, method: `llm_extraction:${session.primary.info().model_id}:k=${k}`, derived_from_tool_output: derived,
          self_consistency_score: cnt / k, direction: modal, evidence_type: t.evidence_type || "other", fact: t.fact || "",
          fact_date: t.date || "", primary_source: t.primary_source || "", document_url: src.url || "", document_title: src.title || "" });
      });
    }
    return { lineage, rejected, retrieval_snapshot_hash: await E.hashObj(retrieval), retrieval };
  }

  // ----------------------------------------------------------------- stockage
  class MemoryStore {
    constructor() { this.entries = []; this.snaps = {}; this.persistent = false; }
    async loadEntries(after = -1) { return clone(this.entries.filter((e) => e.seq > after)); }
    async putEntry(e) { this.entries.push(clone(e)); }
    async putSnapshot(h, obj) { this.snaps[h] = clone(obj); }
    async getSnapshot(h) { if (!(h in this.snaps)) throw new Error("snapshot introuvable " + h); return clone(this.snaps[h]); }
    async withLock(fn) { return fn(); }
  }
  class DbStore {
    constructor(db, ns) { this.db = db; this.ns = ns; this.persistent = true; this.holder = uuid(); }
    col(name) { return this.db.collection(`${this.ns}_${name}`); }
    async loadEntries(after = -1) {
      const out = [];
      let last = after;
      for (;;) {
        const snap = await this.col("ledger").where("seq", ">", last).orderBy("seq").limit(1000).get();
        for (const d of snap.docs) out.push(d.data());
        if (snap.size < 1000) break;
        last = out[out.length - 1].seq;
      }
      return clone(out);
    }
    async putEntry(e) { await this.col("ledger").doc("e" + String(e.seq).padStart(7, "0")).set(clone(e)); }
    async putSnapshot(h, obj) { await this.col("snapshots").doc(h).set({ data: clone(obj) }); }
    async getSnapshot(h) {
      const d = await this.col("snapshots").doc(h).get();
      if (!d.exists) throw new Error("snapshot introuvable " + h);
      const obj = clone(d.data().data);
      if ((await E.hashObj(obj)) !== h) throw new Error("snapshot altéré " + h);
      return obj;
    }
    async withLock(fn) {
      const ref = this.db.doc(`${this.ns}_meta/lock`);
      const got = await ref.acquire({ holder: this.holder, ttlMs: 60000 });
      if (!got.acquired) throw new Error("Le registre est en cours d'écriture dans un autre onglet. Réessayez dans un instant.");
      return fn();
    }
  }

  // ------------------------------------------------------------ orchestration
  function makeQuestion(draft, questionId, links, subIds) {
    return {
      question_id: questionId, text: draft.text, question_type: draft.question_type,
      question_definition_version: draft.question_definition_version || "1.0", horizon: draft.horizon,
      resolution_rule: draft.resolution_rule, resolution_source: draft.resolution_source, resolution_deadline: draft.resolution_deadline,
      ambiguity_policy: draft.ambiguity_policy, resolvability_score: null,
      resolution_stability_score: draft.resolution_stability_score === undefined ? 0 : draft.resolution_stability_score,
      resolution_inter_model_agreement: draft.resolution_inter_model_agreement === undefined ? null : draft.resolution_inter_model_agreement,
      resolution_human_validated: !!draft.resolution_human_validated,
      rule_validation_protocol: draft.rule_validation_protocol || { n_reformulations: 0, agreement_metric: "aucun" },
      linked_questions: links.map((l) => ({ question_id: l.question_id, relation: l.relation, hard: !!l.hard })),
      decomposition: subIds.map((s) => s.question_id), decomposition_detail: subIds,
      definition: draft.definition || "", domain: draft.domain || "other", rule_edited_by_human: !!draft.rule_edited_by_human,
      test_scenarios: draft.test_scenarios || [], rule_verdicts: draft.rule_verdicts || [],
    };
  }
  function engineInput(question, draft, lineage, asOf, market, linked, internal, annotations) {
    return {
      question: { question_id: question.question_id, question_type: question.question_type, horizon: question.horizon,
        domain: draft.domain || "other", resolution_stability_score: question.resolution_stability_score,
        resolution_inter_model_agreement: question.resolution_inter_model_agreement, source_kind: draft.source_kind || "other",
        self_negating_risk: !!draft.self_negating_risk },
      as_of: asOf, lineage, market: market || null, linked, linked_constraints: internal, annotations: annotations || [],
    };
  }
  function linkedInputs(entries, links, beforeSeq) {
    const latest = {};
    for (const e of entries) {
      if (beforeSeq !== null && beforeSeq !== undefined && e.seq >= beforeSeq) break;
      if (e.record_type === "forecast") latest[e.record.question.question_id] = e.record;
    }
    const ids = new Set(links.map((l) => l.question_id)), linked = [], internal = [];
    for (const l of links) {
      const rec = latest[l.question_id];
      if (!rec) throw new E.ValidationError("question liée sans prévision au registre");
      linked.push({ question_id: l.question_id, relation: l.relation, hard: !!l.hard, p: rec.final_output.p_reconciled });
    }
    for (const qid of Array.from(ids).sort())
      for (const l2 of latest[qid].question.linked_questions || [])
        if (ids.has(l2.question_id)) internal.push({ a_id: qid, b_id: l2.question_id, relation: l2.relation, hard: !!l2.hard });
    return [linked, internal];
  }

  class LocalBackend {
    constructor(opts) {
      this.cfg = opts.cfg; this.prompts = opts.prompts; this.build = opts.build; this.sample = opts.sample || null;
      this.store = opts.store; this.ns = opts.ns; this.drafts = {}; this.runtime = "javascript";
    }
    async init() { this.ledger = new E.Ledger(await this.store.loadEntries(), (e) => this.store.putEntry(e)); return this; }
    async refresh() {
      // Registre en ajout seul : on ne relit que les entrées nouvelles (écrites par un autre onglet).
      const fresh = await this.store.loadEntries(this.ledger.length - 1);
      if (fresh.length) this.ledger = new E.Ledger(this.ledger.entries.concat(fresh), (e) => this.store.putEntry(e));
    }
    async info() {
      return { llm: !!this.sample, second_model: !!this.sample, model_id: this.sample ? "claude.ai:default" : null, runtime: "javascript",
        persistent: this.store.persistent, statistical_config_version: this.cfg.statistical_config_version };
    }
    clients(onProgress) {
      if (!this.sample) throw new Error("Claude n'est pas disponible sur cette vue : utilisez la saisie manuelle.");
      return { primary: new SampleClient(this.sample, "default"), secondary: new SampleClient(this.sample, "quick"), onProgress };
    }
    async formalize(text, sources, onProgress) {
      const c = this.clients(onProgress);
      const session = new Session(c, this.prompts, this.cfg, onProgress);
      const draft = await formalize(session, text, nowIso());
      const id = uuid();
      this.drafts[id] = { draft, session, sources: sources || [], lineage: [], retrievalHash: await E.hashObj([]), edits: null, validated: false, override: null };
      return { draft_id: id, draft: clone(draft) };
    }
    async manual(fields) {
      const draft = {
        input_text: fields.text || "", now: nowIso(), question_type: fields.question_type || "binary", text: fields.text || "",
        definition: fields.definition || "", resolution_rule: fields.resolution_rule || "", resolution_source: fields.resolution_source || "",
        source_kind: fields.source_kind || "other", resolution_deadline: fields.resolution_deadline || "", horizon: fields.horizon || "",
        ambiguity_policy: fields.ambiguity_policy || "cancel", domain: fields.domain || "other", subquestions: [], test_scenarios: [],
        self_negating_risk: !!fields.self_negating_risk, self_negating_reason: fields.self_negating_reason || "",
        resolution_stability_score: fields.resolution_stability_score === undefined ? 1 : Number(fields.resolution_stability_score),
        resolution_inter_model_agreement: null, resolution_human_validated: fields.resolution_human_validated === undefined ? true : !!fields.resolution_human_validated,
        rule_validation_protocol: { n_reformulations: 0, agreement_metric: "saisie manuelle, sans LLM" },
      };
      const id = uuid();
      this.drafts[id] = { draft, session: null, sources: [], lineage: [], retrievalHash: await E.hashObj([]), edits: null,
        validated: draft.resolution_human_validated, override: clone(draft) };
      return { draft_id: id, draft: clone(draft) };
    }
    async validate(id, edits, validated, onProgress) {
      const d = this.drafts[id];
      if (!d.session) {
        for (const [k, v] of Object.entries(edits || {})) if (v !== null && v !== undefined && v !== "") d.draft[k] = v;
        d.draft.resolution_human_validated = !!validated; d.override = clone(d.draft); d.validated = validated;
        return { draft: clone(d.draft) };
      }
      d.session.onProgress = onProgress || d.session.onProgress;
      await applyHumanValidation(d.session, d.draft, edits, validated);
      d.edits = edits; d.validated = validated;
      return { draft: clone(d.draft) };
    }
    async commit(id, opts, onProgress) {
      const d = this.drafts[id], draft = d.draft;
      const miss = E.missingResolutionFields(Object.assign({}, draft, { question_definition_version: "1.0" }));
      if (miss.length) throw new E.ValidationError("Enregistrement refusé, champs de résolution manquants : " + miss.join(", "));
      E.isoDurationDays(draft.horizon);
      const links = opts.links || [], asOf = draft.now;
      if (d.sources.length && d.session) {
        d.session.onProgress = onProgress || d.session.onProgress;
        const ex = await extractSources(d.session, draft, d.sources, asOf);
        d.lineage = ex.lineage; d.rejected = ex.rejected; d.retrievalHash = ex.retrieval_snapshot_hash;
      }
      return this.store.withLock(async () => {
        await this.refresh();
        let questionId = uuid();
        if (opts.supersedes) {
          const prev = this.ledger.findForecast(opts.supersedes);
          if (!prev) throw new E.ValidationError("prévision remplacée introuvable");
          questionId = prev.record.question.question_id;
        }
        const subIds = (draft.subquestions || []).map((s, i) => Object.assign({ question_id: questionId + "-sub" + i }, s));
        const question = makeQuestion(draft, questionId, links, subIds);
        const inputs = { kind: "forecast_inputs", input_text: draft.input_text, now: asOf, edits: d.edits, validated: d.validated,
          sources: d.sources, market: opts.market || null, links: question.linked_questions, annotations: opts.annotations || [],
          shadow: opts.shadow || null, draft_override: d.override };
        const rec = await this._forecast(question, draft, d.lineage, d.session, d.retrievalHash, inputs, opts.supersedes || null, opts.shadow || null);
        delete this.drafts[id];
        return rec;
      });
    }
    async _forecast(question, draft, lineage, session, retrievalHash, inputs, supersedes, shadow) {
      const ts = nowIso(), snapHashes = [];
      const info = session ? session.primary.info() : { llm_provider: "none", model_id: "none", model_version: "none", temperature: null, seed_if_supported: null };
      const put = async (obj) => { const h = await E.hashObj(obj); await this.store.putSnapshot(h, obj); return h; };
      for (const s of session ? session.snapshots : []) snapHashes.push(await put(Object.assign({ kind: "llm_output" }, s)));
      const bundleHash = await put({ kind: "llm_bundle", snapshot_hashes: snapHashes });
      const retrHash = await put({ kind: "retrieval", documents: inputs.sources || [], content_hash: retrievalHash });
      const inputsHash = await put(inputs);
      for (const [h, kind] of snapHashes.map((x) => [x, "llm_output"]).concat([[bundleHash, "llm_bundle"], [retrHash, "retrieval"], [inputsHash, "forecast_inputs"]]))
        await this.ledger.appendSnapshot(h, kind, ts);
      const entries = this.ledger.entries, seqNext = entries.length;
      const history = E.buildHistory(entries, this.cfg, seqNext);
      const [linked, internal] = linkedInputs(entries, question.linked_questions, seqNext);
      const inp = engineInput(question, draft, lineage, inputs.now, inputs.market, linked, internal, inputs.annotations);
      const out = E.runForecast(inp, history, this.cfg);
      question.resolvability_score = out.resolvability_score;
      const record = {
        forecast_id: uuid(), supersedes_forecast_id: supersedes, hash_previous: null, timestamp_utc: ts, question,
        reproducibility: Object.assign({}, info, {
          temperature_note: "temperature non transmise : non supportée par la plateforme",
          prompt_hash: await E.sha256Hex(E.canonicalJson(this.prompts)), llm_snapshots_hash: bundleHash, retrieval_snapshot_hash: retrHash,
          inputs_snapshot_hash: inputsHash, engine_input_hash: await E.hashObj(inp), code_commit: this.build.code_commit,
          code_version: this.build.code_version, dependency_lock_hash: this.build.dependency_lock_hash,
          statistical_config_version: this.cfg.statistical_config_version, statistical_config_hash: this.build.config_hash,
          engine_runtime: "javascript" }),
        information_lineage: lineage, extraction_error_rates: out.extraction_error_rates, models_pipeline: out.models_pipeline,
        final_output: Object.assign({}, out.final_output, { self_negating_reason: draft.self_negating_reason || "" }),
        assumptions: [
          "Règle de résolution " + (question.resolution_human_validated ? "validée par l'utilisateur" :
            "NON validée par un humain : l'accord entre reformulations mesure la stabilité, pas la validité"),
          "Sources limitées aux documents fournis, filtrées strictement par date", out.models_pipeline.dependency_assumptions],
      };
      if (shadow) record.shadow = shadow;
      return (await this.ledger.appendForecast(record)).record;
    }
    async replay(fid) {
      const entry = this.ledger.findForecast(fid);
      if (!entry) throw new Error("prévision introuvable");
      const rec = entry.record, seq = entry.seq, repro = rec.reproducibility;
      const inputs = await this.store.getSnapshot(repro.inputs_snapshot_hash);
      const bundle = await this.store.getSnapshot(repro.llm_snapshots_hash);
      const snaps = [];
      for (const h of bundle.snapshot_hashes) snaps.push(await this.store.getSnapshot(h));
      let draft, lineage = [];
      if (inputs.draft_override) draft = clone(inputs.draft_override);
      else {
        const primary = new ReplayClient(snaps.filter((s) => s.role === "primary"), repro);
        const sec = snaps.some((s) => s.role === "secondary") ? new ReplayClient(snaps.filter((s) => s.role === "secondary"), { model_id: "claude.ai:quick" }) : null;
        const session = new Session({ primary, secondary: sec }, this.prompts, this.cfg);
        draft = await formalize(session, inputs.input_text, inputs.now);
        await applyHumanValidation(session, draft, inputs.edits, inputs.validated);
        if (inputs.sources && inputs.sources.length) lineage = (await extractSources(session, draft, inputs.sources, inputs.now)).lineage;
      }
      const entries = this.ledger.entries;
      let first = seq;
      while (first > 0 && entries[first - 1].record_type === "snapshot") first--;
      const history = E.buildHistory(entries, this.cfg, first);
      const question = makeQuestion(draft, rec.question.question_id, inputs.links, []);
      const [linked, internal] = linkedInputs(entries, inputs.links, first);
      const inp = engineInput(question, draft, lineage, inputs.now, inputs.market, linked, internal, inputs.annotations);
      const out = E.runForecast(inp, history, this.cfg), fo = rec.final_output;
      const diffs = { p_raw: Math.abs(out.final_output.p_raw - fo.p_raw), p_reconciled: Math.abs(out.final_output.p_reconciled - fo.p_reconciled),
        w_displayed: Math.abs(out.final_output.w_displayed - fo.w_displayed) };
      return { forecast_id: fid, max_abs_diff: Math.max(diffs.p_raw, diffs.p_reconciled, diffs.w_displayed), diffs,
        engine_input_hash_match: (await E.hashObj(inp)) === repro.engine_input_hash,
        replayed: { p_raw: out.final_output.p_raw, p_reconciled: out.final_output.p_reconciled } };
    }
    async resolve(body) {
      return this.store.withLock(async () => {
        await this.refresh();
        const latest = this.ledger.latestForecast(body.question_id);
        if (!latest) throw new E.ValidationError("question inconnue");
        const rec = { resolution_id: uuid(), question_id: body.question_id, hash_previous: null, timestamp_utc: nowIso(), outcome: body.outcome,
          effective_source: body.effective_source, definition_version_applied: latest.question.question_definition_version,
          ambiguity_policy_applied: body.ambiguity_policy_applied || latest.question.ambiguity_policy,
          resolvability_score_post: Number(body.resolvability_score_post) };
        return (await this.ledger.appendResolution(rec)).record;
      });
    }
    async anchor() {
      return this.store.withLock(async () => {
        await this.refresh();
        if (!this.ledger.length) throw new Error("registre vide : rien à ancrer");
        const covered = this.ledger.length - 1, [root, n] = await this.ledger.merkleRoot(covered);
        return (await this.ledger.appendAnchor({ merkle_root: root, leaf_count: n, covered_seq_max: covered, anchor_method: "pending",
          timestamp_utc: nowIso(), publication_ref: "racine calculée dans la version hébergée : à horodater ou publier à l'extérieur" })).record;
      });
    }
    async importReference(items, source) {
      return this.store.withLock(async () => { await this.refresh(); return (await this.ledger.appendReferenceBatch(items, source, nowIso())).record; });
    }
    async importShadow(platform, data) {
      const items = platform === "manifold" ? E.normalizeManifold(data) : E.normalizeMetaculus(data);
      let created = 0, resolved = 0;
      for (const it of items) {
        if (it.is_resolved || it.community_p === null || it.community_p === undefined) continue;
        const qid = "shadow-" + it.platform + "-" + it.external_id;
        if (this.ledger.latestForecast(qid)) continue;
        const now = nowIso();
        let deadline = it.close_time_iso || (it.close_time_ms ? new Date(it.close_time_ms).toISOString().slice(0, 10) : "");
        const days = deadline ? Math.max(1, E.toDays(deadline) - E.toDays(now)) : 365;
        const draft = { input_text: it.title, now, question_type: "binary", text: it.title, definition: "",
          resolution_rule: (it.resolution_criteria || "").slice(0, 4000) || `Résolution officielle de la plateforme ${it.platform}`,
          resolution_source: it.url || it.platform, source_kind: "market_platform", resolution_deadline: deadline.slice(0, 10) || "inconnue",
          horizon: `P${Math.trunc(days)}D`, ambiguity_policy: "cancel", domain: "other", subquestions: [], resolution_stability_score: 0.5,
          resolution_inter_model_agreement: null, resolution_human_validated: false,
          rule_validation_protocol: { n_reformulations: 0, agreement_metric: "règle de la plateforme" } };
        const shadow = { platform: it.platform, external_id: it.external_id, url: it.url || "", community_p_at_forecast: it.community_p };
        await this.store.withLock(async () => {
          await this.refresh();
          const question = makeQuestion(draft, qid, [], []);
          const inputs = { kind: "forecast_inputs", input_text: it.title, now, edits: null, validated: false, sources: [], market: null,
            links: [], annotations: [], shadow, draft_override: draft };
          await this._forecast(question, draft, [], null, await E.hashObj([]), inputs, null, shadow);
        });
        created++;
      }
      for (const it of items) {
        if (!it.is_resolved || it.resolution === null) continue;
        const qid = "shadow-" + it.platform + "-" + it.external_id;
        if (!this.ledger.latestForecast(qid) || this.ledger.resolutionFor(qid)) continue;
        await this.resolve({ question_id: qid, outcome: it.resolution, effective_source: it.url || it.platform, resolvability_score_post: 0.9 });
        resolved++;
      }
      return { created, resolved, seen: items.length };
    }
    async demo(n, onProgress) {
      const rng = new E.Mulberry32(1729), deadline = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
      for (let i = 0; i < n; i++) {
        const pi = 0.1 + 0.8 * rng.random(), mkt = Math.min(0.97, Math.max(0.03, pi + 0.2 * (rng.random() - 0.5)));
        const y = rng.random() < pi ? 1 : 0;
        const d = await this.manual({ text: `[Démo synthétique] Événement fictif n°${i + 1}`,
          resolution_rule: "Issue tirée au hasard (données synthétiques de démonstration).", resolution_source: "synthetique://demo",
          resolution_deadline: deadline, horizon: "P30D", ambiguity_policy: "cancel", domain: "other", source_kind: "official_statistics" });
        const rec = await this.commit(d.draft_id, { market: { p: mkt, source: "marché synthétique" } });
        await this.resolve({ question_id: rec.question.question_id, outcome: y, effective_source: "synthetique://demo", resolvability_score_post: 0.9 });
        if (onProgress) onProgress(i + 1, n);
      }
      return { created: n };
    }
    async entries() { return this.ledger.entries; }
    async verify() {
      // Relit tout le registre stocké : une altération de la base doit être détectée.
      const all = await this.store.loadEntries();
      this.ledger = new E.Ledger(all, (e) => this.store.putEntry(e));
      return E.verifyEntries(all);
    }
    async dashboard() { return E.dashboard(this.ledger.entries, this.cfg); }
    async tournament() { return E.tournament(this.ledger.entries, this.cfg); }
    async shadowReport() { return E.shadowComparison(this.ledger.entries, this.cfg); }
  }

  class ServerBackend {
    constructor(ns) { this.ns = ns; this.runtime = "python"; }
    async req(method, path, body) {
      const r = await fetch(path + (path.includes("?") ? "&" : "?") + "ns=" + this.ns, { method, headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "erreur serveur");
      return data;
    }
    async init() { return this; }
    async refresh() {}
    async info() { return Object.assign({ persistent: true }, await this.req("GET", "/api/info")); }
    formalize(text, sources) { return this.req("POST", "/api/draft", { text, sources }); }
    manual(fields) { return this.req("POST", "/api/draft/manual", { fields }); }
    validate(id, edits, validated) { return this.req("POST", "/api/draft/validate", { draft_id: id, edits, validated }); }
    commit(id, opts) { return this.req("POST", "/api/draft/commit", Object.assign({ draft_id: id }, opts)); }
    replay(fid) { return this.req("POST", "/api/replay", { forecast_id: fid }); }
    resolve(body) { return this.req("POST", "/api/resolve", body); }
    anchor() { return this.req("POST", "/api/anchor", { method: "pending" }); }
    importReference(items, source) { return this.req("POST", "/api/reference", { items, source }); }
    importShadow(platform, data) { return this.req("POST", "/api/shadow/import", { platform, data }); }
    demo(n) { return this.req("POST", "/api/demo", { n }); }
    entries() { return this.req("GET", "/api/entries"); }
    verify() { return this.req("GET", "/api/verify"); }
    dashboard() { return this.req("GET", "/api/dashboard"); }
    tournament() { return this.req("GET", "/api/tournament"); }
    shadowReport() { return this.req("GET", "/api/shadow"); }
  }

  root.OracleBackends = { LocalBackend, ServerBackend, MemoryStore, DbStore, Session, ReplayClient, formalize, extractSources, applyHumanValidation };
  if (typeof module !== "undefined" && module.exports) module.exports = root.OracleBackends;
})(typeof globalThis !== "undefined" ? globalThis : this);
