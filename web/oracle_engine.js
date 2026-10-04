/* Oracle Calibré — portage JavaScript du moteur, du registre et de l'évaluation.
 *
 * Miroir fonction par fonction du paquet Python oracle_calibre (référence).
 * Le test tests/test_js_conformance.py exécute les deux implémentations sur
 * les mêmes entrées et exige un écart < 1e-9 ; la sérialisation canonique et
 * les hash sont identiques à l'octet près. Aucun appel LLM dans ce fichier.
 */
(function (root) {
  "use strict";

  // ------------------------------------------------------------ canonique
  const GENESIS_HASH = "0".repeat(64);

  function canonNum(x) {
    if (!Number.isFinite(x)) throw new Error("nombre non fini interdit dans un enregistrement");
    if (Number.isInteger(x) && Math.abs(x) < 1e16) return String(x === 0 ? 0 : x);
    const e = x.toExponential(); // chiffres les plus courts, comme repr() en Python
    let [mant, exp] = e.split("e");
    const ex = parseInt(exp, 10);
    if (ex >= -4 && ex < 16) {
      const neg = mant[0] === "-";
      const digits = mant.replace("-", "").replace(".", "");
      let s;
      if (ex >= 0) {
        if (digits.length <= ex + 1) s = digits + "0".repeat(ex + 1 - digits.length) + ".0";
        else s = digits.slice(0, ex + 1) + "." + digits.slice(ex + 1);
      } else {
        s = "0." + "0".repeat(-ex - 1) + digits;
      }
      return (neg ? "-" : "") + s;
    }
    const sign = ex < 0 ? "-" : "+";
    const a = Math.abs(ex);
    return mant + "e" + sign + (a < 10 ? "0" + a : String(a));
  }

  function canonStr(s) {
    let out = '"';
    for (const ch of s) {
      const o = ch.codePointAt(0);
      if (ch === '"') out += '\\"';
      else if (ch === "\\") out += "\\\\";
      else if (ch === "\n") out += "\\n";
      else if (ch === "\r") out += "\\r";
      else if (ch === "\t") out += "\\t";
      else if (ch === "\b") out += "\\b";
      else if (ch === "\f") out += "\\f";
      else if (o < 0x20) out += "\\u" + o.toString(16).padStart(4, "0");
      else out += ch;
    }
    return out + '"';
  }

  function canonicalJson(obj) {
    if (obj === null || obj === undefined) return "null";
    if (obj === true) return "true";
    if (obj === false) return "false";
    if (typeof obj === "number") return canonNum(obj);
    if (typeof obj === "string") return canonStr(obj);
    if (Array.isArray(obj)) return "[" + obj.map(canonicalJson).join(",") + "]";
    if (typeof obj === "object") {
      const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
      return "{" + keys.map((k) => canonStr(k) + ":" + canonicalJson(obj[k])).join(",") + "}";
    }
    throw new Error("type non sérialisable : " + typeof obj);
  }

  function subtle() {
    const c = (typeof globalThis !== "undefined" && globalThis.crypto) || null;
    if (!c || !c.subtle) throw new Error("WebCrypto indisponible");
    return c.subtle;
  }
  function hex(buf) {
    return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  function hexToBytes(h) {
    const out = new Uint8Array(h.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(h.substr(2 * i, 2), 16);
    return out;
  }
  async function sha256Bytes(bytes) {
    return new Uint8Array(await subtle().digest("SHA-256", bytes));
  }
  async function sha256Hex(data) {
    const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
    return hex(await subtle().digest("SHA-256", bytes));
  }
  const hashObj = (obj) => sha256Hex(canonicalJson(obj));

  class Mulberry32 {
    constructor(seed) { this.state = seed >>> 0; }
    nextU32() {
      this.state = (this.state + 0x6d2b79f5) >>> 0;
      let t = this.state;
      t = Math.imul(t ^ (t >>> 15), t | 1) >>> 0;
      t = (t ^ ((t + Math.imul(t ^ (t >>> 7), t | 61)) >>> 0)) >>> 0;
      return (t ^ (t >>> 14)) >>> 0;
    }
    random() { return this.nextU32() / 4294967296; }
    randint(n) { return Math.floor(this.random() * n); }
  }

  // ---------------------------------------------------------------- maths
  const EPS_P = 1e-6;
  const LANCZOS = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
    -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7];
  const clip = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
  const clipP = (p, eps = EPS_P) => clip(p, eps, 1 - eps);
  function logit(p) { p = clipP(p); return Math.log(p / (1 - p)); }
  function sigmoid(z) {
    if (z >= 0) { const e = Math.exp(-z); return 1 / (1 + e); }
    const e = Math.exp(z); return e / (1 + e);
  }
  const probitSigmoid = (mu, v) => sigmoid(mu / Math.sqrt(1 + (Math.PI * v) / 8));
  function lgamma(x) {
    if (x < 0.5) return Math.log(Math.PI / Math.abs(Math.sin(Math.PI * x))) - lgamma(1 - x);
    x -= 1;
    let a = LANCZOS[0];
    const t = x + 7.5;
    for (let i = 1; i < 9; i++) a += LANCZOS[i] / (x + i);
    return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
  }
  function betacf(a, b, x) {
    const fpmin = 1e-300, qab = a + b, qap = a + 1, qam = a - 1;
    let c = 1, d = 1 - (qab * x) / qap;
    if (Math.abs(d) < fpmin) d = fpmin;
    d = 1 / d;
    let h = d;
    for (let m = 1; m <= 300; m++) {
      const m2 = 2 * m;
      let aa = (m * (b - m) * x) / ((qam + m2) * (a + m2));
      d = 1 + aa * d; if (Math.abs(d) < fpmin) d = fpmin;
      c = 1 + aa / c; if (Math.abs(c) < fpmin) c = fpmin;
      d = 1 / d; h *= d * c;
      aa = (-(a + m) * (qab + m) * x) / ((a + m2) * (qap + m2));
      d = 1 + aa * d; if (Math.abs(d) < fpmin) d = fpmin;
      c = 1 + aa / c; if (Math.abs(c) < fpmin) c = fpmin;
      d = 1 / d;
      const dl = d * c;
      h *= dl;
      if (Math.abs(dl - 1) < 1e-15) break;
    }
    return h;
  }
  function betainc(a, b, x) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    const lbt = lgamma(a + b) - lgamma(a) - lgamma(b) + a * Math.log(x) + b * Math.log(1 - x);
    const bt = Math.exp(lbt);
    if (x < (a + 1) / (a + b + 2)) return (bt * betacf(a, b, x)) / a;
    return 1 - (bt * betacf(b, a, 1 - x)) / b;
  }
  function betaPpf(q, a, b) {
    let lo = 0, hi = 1;
    for (let i = 0; i < 80; i++) {
      const mid = 0.5 * (lo + hi);
      if (betainc(a, b, mid) < q) lo = mid; else hi = mid;
    }
    return 0.5 * (lo + hi);
  }
  function erfc(x) {
    const z = Math.abs(x), t = 1 / (1 + 0.5 * z);
    const r = t * Math.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (
      0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))));
    return x >= 0 ? r : 2 - r;
  }
  const normCdf = (x) => 0.5 * erfc(-x / Math.sqrt(2));
  function solveLinear(a, b) {
    const n = b.length;
    const m = a.map((row, i) => row.slice().concat([b[i]]));
    for (let col = 0; col < n; col++) {
      let piv = col;
      for (let r = col + 1; r < n; r++) if (Math.abs(m[r][col]) > Math.abs(m[piv][col])) piv = r;
      if (Math.abs(m[piv][col]) < 1e-300) throw new Error("système singulier");
      if (piv !== col) { const tmp = m[col]; m[col] = m[piv]; m[piv] = tmp; }
      for (let r = col + 1; r < n; r++) {
        const f = m[r][col] / m[col][col];
        if (f !== 0) for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
      }
    }
    const x = new Array(n).fill(0);
    for (let r = n - 1; r >= 0; r--) {
      let s = m[r][n];
      for (let c = r + 1; c < n; c++) s -= m[r][c] * x[c];
      x[r] = s / m[r][r];
    }
    return x;
  }
  function invert(a) {
    const n = a.length;
    const cols = [];
    for (let j = 0; j < n; j++) cols.push(solveLinear(a, a.map((_, i) => (i === j ? 1 : 0))));
    return a.map((_, i) => cols.map((col) => col[i]));
  }
  function bernoulliLoglik(p, y) { p = clipP(p); return y === 1 ? Math.log(p) : Math.log(1 - p); }

  // --------------------------------------------------------------- schéma
  const QUESTION_TYPES = ["binary", "categorical", "continuous", "time_to_event"];
  const AMBIGUITY_POLICIES = ["cancel", "freeze_old", "adopt_new"];
  const RELATIONS = ["implies", "implied_by", "exclusive", "partition_member"];
  const DISTRIBUTION_TYPES = { bernoulli: ["p"], categorical: ["probs"], quantile: ["levels", "values"], survival: ["times", "survival"] };
  const DISTRIBUTION_FOR_QUESTION = { binary: "bernoulli", categorical: "categorical", continuous: "quantile", time_to_event: "survival" };
  const MANDATORY_RESOLUTION_FIELDS = ["question_type", "question_definition_version", "resolution_rule",
    "resolution_source", "resolution_deadline", "ambiguity_policy"];
  const FORECAST_TOP_FIELDS = ["forecast_id", "supersedes_forecast_id", "hash_previous", "timestamp_utc", "question",
    "reproducibility", "information_lineage", "extraction_error_rates", "models_pipeline", "final_output"];
  const QUESTION_FIELDS = ["question_id", "text", "question_type", "question_definition_version", "horizon",
    "resolution_rule", "resolution_source", "resolution_deadline", "ambiguity_policy", "resolvability_score",
    "resolution_stability_score", "resolution_inter_model_agreement", "resolution_human_validated",
    "rule_validation_protocol", "linked_questions", "decomposition"];
  const REPRO_FIELDS = ["llm_provider", "model_id", "model_version", "prompt_hash", "temperature", "seed_if_supported",
    "llm_snapshots_hash", "retrieval_snapshot_hash", "code_commit", "dependency_lock_hash", "statistical_config_version"];
  const LINEAGE_FIELDS = ["source_id", "primary_ancestor_id", "timestamp", "method", "derived_from_tool_output", "self_consistency_score"];
  const PIPELINE_FIELDS = ["base_rate", "component_forecasts", "stacking_weights", "dirichlet_hyperparameter_id",
    "systematic_bias_variance", "dependency_assumptions"];
  const FINAL_FIELDS = ["p_raw", "p_reconciled", "predictive_distribution", "epistemic_uncertainty", "w_displayed", "self_negating_risk"];
  const RESOLUTION_FIELDS = ["resolution_id", "question_id", "hash_previous", "timestamp_utc", "outcome",
    "effective_source", "definition_version_applied", "ambiguity_policy_applied", "resolvability_score_post"];

  class ValidationError extends Error {}
  class ImmutableRecordError extends Error {}
  const has = (o, k) => o !== null && typeof o === "object" && Object.prototype.hasOwnProperty.call(o, k);
  const blank = (v) => v === null || v === undefined || (typeof v === "string" && !v.trim());
  const missingResolutionFields = (q) => MANDATORY_RESOLUTION_FIELDS.filter((f) => blank(q[f]));

  function validateDistribution(d) {
    if (!d || !DISTRIBUTION_TYPES[d.type]) throw new ValidationError("predictive_distribution.type inconnu");
    for (const f of DISTRIBUTION_TYPES[d.type]) if (!has(d, f)) throw new ValidationError("predictive_distribution." + f + " manquant");
    if (d.type === "bernoulli" && !(d.p >= 0 && d.p <= 1)) throw new ValidationError("p hors de [0, 1]");
  }
  function validateForecastRecord(rec) {
    const errors = FORECAST_TOP_FIELDS.filter((f) => !has(rec, f));
    const q = rec.question || {};
    const missing = missingResolutionFields(q);
    if (missing.length) errors.push("champs de résolution obligatoires manquants : " + missing.join(", "));
    QUESTION_FIELDS.forEach((f) => { if (!has(q, f)) errors.push("question." + f); });
    if (!QUESTION_TYPES.includes(q.question_type) && !missing.includes("question_type")) errors.push("question_type invalide");
    if (!AMBIGUITY_POLICIES.includes(q.ambiguity_policy) && !missing.includes("ambiguity_policy")) errors.push("ambiguity_policy invalide");
    for (const l of q.linked_questions || []) if (!RELATIONS.includes(l.relation) || typeof l.hard !== "boolean") errors.push("lien invalide");
    REPRO_FIELDS.forEach((f) => { if (!has(rec.reproducibility || {}, f)) errors.push("reproducibility." + f); });
    (rec.information_lineage || []).forEach((s, i) => LINEAGE_FIELDS.forEach((f) => { if (!has(s, f)) errors.push(`information_lineage[${i}].${f}`); }));
    PIPELINE_FIELDS.forEach((f) => { if (!has(rec.models_pipeline || {}, f)) errors.push("models_pipeline." + f); });
    FINAL_FIELDS.forEach((f) => { if (!has(rec.final_output || {}, f)) errors.push("final_output." + f); });
    if (errors.length) throw new ValidationError("ForecastRecord refusé — " + errors.join(" ; "));
    validateDistribution(rec.final_output.predictive_distribution);
    if (rec.final_output.predictive_distribution.type !== DISTRIBUTION_FOR_QUESTION[q.question_type])
      throw new ValidationError("distribution incompatible avec le type de question");
  }
  function validateResolutionRecord(rec) {
    const errors = RESOLUTION_FIELDS.filter((f) => !has(rec, f) || (f !== "hash_previous" && blank(rec[f])));
    if (!AMBIGUITY_POLICIES.includes(rec.ambiguity_policy_applied)) errors.push("ambiguity_policy_applied invalide");
    if (errors.length) throw new ValidationError("ResolutionRecord refusé — " + errors.join(", "));
  }
  function horizonBucket(days, buckets) {
    for (const b of buckets) if (days <= b) return "le" + Math.trunc(b);
    return "gt" + Math.trunc(buckets[buckets.length - 1]);
  }
  function isoDurationDays(dur) {
    const s = String(dur || "").trim().toUpperCase();
    if (!s.startsWith("P")) throw new Error("durée ISO-8601 invalide : " + dur);
    const ud = { Y: 365.25, M: 30.4375, W: 7, D: 1 }, ut = { H: 1 / 24, M: 1 / 1440, S: 1 / 86400 };
    let days = 0, num = "", inTime = false;
    for (const ch of s.slice(1)) {
      if (ch === "T") inTime = true;
      else if ((ch >= "0" && ch <= "9") || ch === ".") num += ch;
      else {
        const table = inTime ? ut : ud;
        if (!(ch in table) || !num) throw new Error("durée ISO-8601 invalide : " + dur);
        days += parseFloat(num) * table[ch];
        num = "";
      }
    }
    return days;
  }
  function toDays(ts) {
    let s = String(ts).trim();
    if (/T\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(s)) s += "Z";
    const ms = Date.parse(s);
    if (Number.isNaN(ms)) throw new Error("horodatage invalide : " + ts);
    return ms / 86400000;
  }

  // --------------------------------------------------------- historique
  function buildHistory(entries, cfg, beforeSeq) {
    const buckets = cfg.horizon_buckets_days;
    const forecasts = {}, resolutions = {}, reference = [];
    for (const e of entries) {
      if (beforeSeq !== undefined && beforeSeq !== null && e.seq >= beforeSeq) break;
      const rec = e.record, rt = e.record_type;
      if (rt === "forecast") (forecasts[rec.question.question_id] = forecasts[rec.question.question_id] || []).push({ seq: e.seq, rec });
      else if (rt === "resolution") resolutions[rec.question_id] = { seq: e.seq, rec };
      else if (rt === "reference_batch")
        for (const it of rec.items)
          reference.push({ question_type: it.question_type, bucket: horizonBucket(Number(it.horizon_days), buckets),
            domain: it.domain || "general", y: Math.trunc(Number(it.outcome)), t: toDays(it.resolved_at) });
    }
    const observations = [], stats = {}, latest = {};
    for (const qid of Object.keys(forecasts).sort()) {
      const flist = forecasts[qid];
      latest[qid] = flist[flist.length - 1].rec;
      const res = resolutions[qid];
      if (!res) continue;
      const q = flist[0].rec.question;
      const ck = q.question_type + "|" + horizonBucket(isoDurationDays(q.horizon), buckets);
      const st = (stats[ck] = stats[ck] || { n: 0, cancelled: 0 });
      st.n += 1;
      const outcome = res.rec.outcome;
      if (outcome === "cancelled") { st.cancelled += 1; continue; }
      if (q.question_type !== "binary") continue;
      const tRes = toDays(res.rec.timestamp_utc);
      const prior = flist.filter((f) => f.seq < res.seq);
      if (!prior.length) continue;
      const fr = prior[prior.length - 1].rec;
      const mp = fr.models_pipeline;
      const comps = {};
      for (const c of mp.component_forecasts) comps[c.component] = c.p;
      const y = Math.trunc(Number(outcome));
      const domain = mp.domain || "general";
      observations.push({ question_id: qid, forecast_id: fr.forecast_id, t: tRes, y, class_key: ck, domain,
        quality: Number(res.rec.resolvability_score_post), components: comps, base_rate_p: mp.base_rate.p,
        mu_logit: mp.bayes_detail && mp.bayes_detail.mu_logit !== undefined ? mp.bayes_detail.mu_logit : null,
        features: mp.evidence_features || {} });
      reference.push({ question_type: q.question_type, bucket: ck.split("|")[1], domain, y, t: tRes });
    }
    const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
    observations.sort((a, b) => a.t - b.t || cmpStr(a.question_id, b.question_id));
    reference.sort((a, b) => a.t - b.t || cmpStr(a.question_type, b.question_type) || cmpStr(a.bucket, b.bucket) ||
      cmpStr(a.domain, b.domain) || a.y - b.y);
    return { observations, reference, resolution_stats: stats, latest };
  }

  // ---------------------------------------------------------- taux de base
  function baseRate(reference, qtype, bucket, domain, asOf, cfg) {
    const [a0, b0] = cfg.base_rate.global_prior;
    const m = cfg.base_rate.level_pseudo_obs, w0 = cfg.base_rate.widening_pseudo_obs;
    const items = reference.filter((r) => r.t <= asOf && r.question_type === qtype);
    let sg = 0; for (const r of items) sg += r.y;
    const g = (sg + a0) / (items.length + a0 + b0);
    const l1 = items.filter((r) => r.bucket === bucket);
    let s1 = 0; for (const r of l1) s1 += r.y;
    const r1 = (s1 + m * g) / (l1.length + m);
    const l2 = l1.filter((r) => r.domain === domain);
    let s2 = 0; for (const r of l2) s2 += r.y;
    const r2 = (s2 + m * r1) / (l2.length + m);
    const conc = l2.length + m;
    const pd = (r2 * conc + 0.5 * w0) / (conc + w0);
    return { reference_class: `${qtype}|${bucket}|${domain}`, p: r2, p_diffuse: pd,
      variance: (pd * (1 - pd)) / (conc + w0 + 1),
      n: { global: items.length, type_horizon: l1.length, domain: l2.length },
      levels: { global: g, type_horizon: r1, domain: r2 } };
  }

  // -------------------------------------------------------------- stacking
  function emWeights(rows, alpha, maxIter, tol) {
    const k = alpha.length;
    let sAlpha = 0; for (const a of alpha) sAlpha += a;
    let w = alpha.map((a) => a / sAlpha);
    let totalV = 0; for (const r of rows) totalV += r[0];
    let it = 0;
    for (it = 1; it <= maxIter; it++) {
      const acc = new Array(k).fill(0);
      for (const [v, y, ps] of rows) {
        const f = ps.map((p) => (y === 1 ? clipP(p) : 1 - clipP(p)));
        let den = 0; for (let j = 0; j < k; j++) den += w[j] * f[j];
        for (let j = 0; j < k; j++) acc[j] += (v * w[j] * f[j]) / den;
      }
      const nw = acc.map((a, j) => (a + alpha[j]) / (totalV + sAlpha));
      let delta = 0;
      for (let j = 0; j < k; j++) { const d = Math.abs(nw[j] - w[j]); if (d > delta) delta = d; }
      w = nw;
      if (delta < tol) break;
    }
    if (it > maxIter) it = maxIter;
    return [w, it];
  }
  function pageHinkley(deltas, delta, threshold, minObs) {
    let cum = 0, mn = 0, mnIdx = 0, run = 0;
    for (let i = 0; i < deltas.length; i++) {
      run += deltas[i];
      const m = run / (i + 1);
      cum += deltas[i] - m - delta;
      if (cum < mn) { mn = cum; mnIdx = i + 1; }
      if (i + 1 >= minObs && cum - mn > threshold) return mnIdx;
    }
    return null;
  }
  function stackRows(obs, names, tNow, cfg) {
    const st = cfg.stacking, out = [];
    for (const o of obs) {
      if (!names.every((n) => n in o.components)) continue;
      const age = tNow - o.t;
      if (age < 0 || age > st.window_days) continue;
      const decay = Math.pow(0.5, age / st.half_life_days);
      out.push({ v: decay * o.quality, y: o.y, ps: names.map((n) => o.components[n]), t: o.t, qid: o.question_id });
    }
    return out;
  }
  function detectBreaks(rows, names, cfg) {
    const cp = cfg.stacking.changepoint, breaks = {}, bi = names.indexOf("base_rate");
    names.forEach((n, j) => {
      if (j === bi) return;
      const d = rows.map((r) => -bernoulliLoglik(r.ps[j], r.y) + bernoulliLoglik(r.ps[bi], r.y));
      const b = pageHinkley(d, cp.delta, cp.threshold, cp.min_obs);
      if (b !== null) breaks[n] = b;
    });
    return breaks;
  }
  function fitLevel(rows, names, alpha, cfg) {
    const st = cfg.stacking;
    let [w, it] = emWeights(rows.map((r) => [r.v, r.y, r.ps]), alpha, st.em_max_iter, st.em_tol);
    const breaks = detectBreaks(rows, names, cfg), bi = names.indexOf("base_rate");
    for (const n of Object.keys(breaks).sort()) {
      const j = names.indexOf(n), b = breaks[n];
      const [wp] = emWeights(rows.slice(b).map((r) => [r.v, r.y, r.ps]), alpha, st.em_max_iter, st.em_tol);
      const [wpre] = emWeights(rows.slice(0, b).map((r) => [r.v, r.y, r.ps]), alpha, st.em_max_iter, st.em_tol);
      let freed = 0;
      if (wp[j] < w[j]) { freed += w[j] - wp[j]; w[j] = wp[j]; }
      // Les autres sources ne profitent pas de la rupture : plafonnées à leur poids d'avant.
      names.forEach((m, i) => { if (i !== bi && i !== j && !(m in breaks) && w[i] > wpre[i]) { freed += w[i] - wpre[i]; w[i] = wpre[i]; } });
      w[bi] += freed;
    }
    return [w, breaks, it];
  }
  function stackWeights(observations, names, classKey, tNow, cfg, alphaOverride) {
    const am = alphaOverride || cfg.dirichlet.alpha;
    const alpha = names.map((n) => am[n]);
    const rows = stackRows(observations, names, tNow, cfg);
    const [wg, bg, itg] = fitLevel(rows, names, alpha, cfg);
    const kappa = cfg.dirichlet.class_concentration;
    const crow = stackRows(observations.filter((o) => o.class_key === classKey), names, tNow, cfg);
    const [wc, bc, itc] = fitLevel(crow, names, wg.map((x) => kappa * x), cfg);
    let ng = 0; for (const r of rows) ng += r.v;
    let nc = 0; for (const r of crow) nc += r.v;
    const zip = (w) => Object.fromEntries(names.map((n, i) => [n, w[i]]));
    return { names, global: zip(wg), class: zip(wc), n_rows_global: rows.length, n_rows_class: crow.length,
      n_eff_global: ng, n_eff_class: nc, breaks_global: bg, breaks_class: bc, em_iterations: [itg, itc] };
  }

  // ------------------------------------------------------- incertitudes
  function extractionErrorRates(annotations, cfg) {
    const [a, b] = cfg.extraction.prior_error_rate, m = cfg.extraction.pooling_strength, types = cfg.extraction.types;
    let totN = 0, totE = 0;
    const per = {}; types.forEach((t) => (per[t] = [0, 0]));
    for (const ann of annotations || []) if (per[ann.type]) {
      per[ann.type][0] += Math.trunc(ann.n_checked); per[ann.type][1] += Math.trunc(ann.n_errors);
      totN += Math.trunc(ann.n_checked); totE += Math.trunc(ann.n_errors);
    }
    const pooled = (totE + a) / (totN + a + b);
    const out = {};
    for (const t of types) {
      const [n, e] = per[t];
      const pa = e + m * pooled, pb = n - e + m * (1 - pooled);
      out[t] = { rate: pa / (pa + pb), ci95: [betaPpf(0.025, pa, pb), betaPpf(0.975, pa, pb)], n_checked: n, n_errors: e };
    }
    out._pooled = { rate: pooled, n_checked: totN, n_errors: totE };
    return out;
  }
  function evidenceReliability(consistency, rates) {
    let m = consistency * (1 - 2 * rates.relation.rate);
    m *= (1 - rates.entity.rate) * (1 - rates.date.rate) * (1 - rates.causality.rate);
    return m;
  }
  function systematicBiasVariance(observations, domain, nAnc, cfg) {
    const sb = cfg.systematic_bias, floor = sb.floor_logit_var;
    let num = 0, den = 0, n = 0;
    for (const o of observations) {
      if (o.domain !== domain || o.mu_logit === null || o.mu_logit === undefined) continue;
      const p = sigmoid(o.mu_logit), v = p * (1 - p), d = o.y - p;
      num += d * d - v; den += v * v; n += 1;
    }
    const raw = den > 0 ? num / den : floor;
    const m = sb.domain_pseudo_obs;
    const shrunk = (n * raw + m * floor) / (n + m);
    const dv = shrunk > floor ? shrunk : floor;
    const factor = 1 + sb.ancestor_k / (nAnc > 0 ? nAnc : 1);
    return { domain, domain_var: dv, floor, n_obs: n, raw_estimate: raw, ancestor_factor: factor, total: dv * factor };
  }

  // -------------------------------------------------------- composants
  const DIRECTIONS = { yes: 1, no: -1, neutral: 0 };
  function fuseLineage(lineage, asOf) {
    const groups = {}, excluded = [];
    for (const s of lineage) {
      if (s.derived_from_tool_output) { excluded.push({ source_id: s.source_id, reason: "derived_from_tool_output" }); continue; }
      if (toDays(s.timestamp) > asOf) { excluded.push({ source_id: s.source_id, reason: "posterior_to_forecast" }); continue; }
      (groups[s.primary_ancestor_id] = groups[s.primary_ancestor_id] || []).push(s);
    }
    const fused = [];
    for (const anc of Object.keys(groups).sort()) {
      const members = groups[anc].slice().sort((a, b) => (a.source_id < b.source_id ? -1 : a.source_id > b.source_id ? 1 : 0));
      let num = 0, den = 0;
      const types = {};
      for (const s of members) {
        const c = Number(s.self_consistency_score || 0);
        num += c * (DIRECTIONS[s.direction || "neutral"] || 0);
        den += c;
        const t = s.evidence_type || "other";
        types[t] = (types[t] || 0) + 1;
      }
      const score = den > 0 ? num / den : 0;
      const etype = Object.entries(types).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))[0][0];
      fused.push({ primary_ancestor_id: anc, direction: score > 0 ? 1 : score < 0 ? -1 : 0, agreement: Math.abs(score),
        consistency: (den / members.length) * Math.abs(score), evidence_type: etype, n_sources: members.length,
        facts: members.map((s) => s.fact || "").slice(0, 3) });
    }
    return { fused, excluded, n_ancestors: fused.length };
  }
  function evidenceFeatures(fused, rates, types) {
    const x = {}; types.forEach((t) => (x[t] = 0));
    const detail = [];
    for (const ev of fused) {
      const t = ev.evidence_type in x ? ev.evidence_type : "other";
      const m = evidenceReliability(ev.consistency, rates);
      x[t] += ev.direction * m;
      detail.push(Object.assign({}, ev, { evidence_type: t, reliability: m }));
    }
    return [x, detail];
  }
  function fitEvidenceBetas(observations, cfg) {
    const bc = cfg.bayes, types = bc.evidence_types, T = types.length;
    const tau2 = bc.tau * bc.tau, s02 = bc.mu0_sd * bc.mu0_sd, mu0 = bc.mu0;
    const rows = observations.map((o) => {
      const f = o.features || {};
      return [o.quality, o.y, logit(o.base_rate_p), types.map((t) => Number(f[t] || 0))];
    });
    const theta = new Array(T + 1).fill(mu0);
    let hess = null;
    for (let iter = 0; iter < bc.newton_max_iter; iter++) {
      const mu = theta[T];
      const grad = new Array(T + 1).fill(0);
      hess = Array.from({ length: T + 1 }, () => new Array(T + 1).fill(0));
      for (let t = 0; t < T; t++) {
        grad[t] += (theta[t] - mu) / tau2; grad[T] -= (theta[t] - mu) / tau2;
        hess[t][t] += 1 / tau2; hess[t][T] -= 1 / tau2; hess[T][t] -= 1 / tau2; hess[T][T] += 1 / tau2;
      }
      grad[T] += (mu - mu0) / s02;
      hess[T][T] += 1 / s02;
      for (const [v, y, off, x] of rows) {
        let z = off;
        for (let t = 0; t < T; t++) z += theta[t] * x[t];
        const p = sigmoid(z), r = v * (p - y), wgt = v * p * (1 - p);
        for (let a = 0; a < T; a++) {
          if (x[a] === 0) continue;
          grad[a] += r * x[a];
          for (let b = 0; b < T; b++) if (x[b] !== 0) hess[a][b] += wgt * x[a] * x[b];
        }
      }
      const step = solveLinear(hess, grad);
      let mx = 0;
      for (let i = 0; i <= T; i++) { theta[i] -= step[i]; if (Math.abs(step[i]) > mx) mx = Math.abs(step[i]); }
      if (mx < bc.newton_tol) break;
    }
    const cov = invert(hess);
    return { types, beta: Object.fromEntries(types.map((t, i) => [t, theta[i]])), mu: theta[T],
      cov: cov.slice(0, T).map((r) => r.slice(0, T)), n_obs: rows.length };
  }
  function bayesComponent(baseP, x, detail, betas, biasVar) {
    const types = betas.types, xs = types.map((t) => x[t]);
    let mu = logit(baseP);
    types.forEach((t, i) => (mu += betas.beta[t] * xs[i]));
    let vp = 0;
    for (let i = 0; i < types.length; i++) for (let j = 0; j < types.length; j++) vp += xs[i] * betas.cov[i][j] * xs[j];
    let ve = 0;
    const contributions = [];
    for (const ev of detail) {
      const bt = betas.beta[ev.evidence_type], m = ev.reliability;
      ve += ev.direction !== 0 ? bt * bt * (1 - m * m) : 0;
      contributions.push({ primary_ancestor_id: ev.primary_ancestor_id, evidence_type: ev.evidence_type, direction: ev.direction,
        logit_contribution: bt * ev.direction * m, n_sources: ev.n_sources, facts: ev.facts });
    }
    const vt = vp + ve + biasVar;
    const p = probitSigmoid(mu, vt), v = p * (1 - p);
    return { p, mu_logit: mu, var_param: vp, var_extraction: ve, var_bias: biasVar, var_total: vt, variance: v * v * vt, contributions };
  }
  function marketComponent(market, cfg) {
    if (!market || market.p === null || market.p === undefined || market.p === "") return null;
    const [lo, hi] = cfg.market.clip;
    const p = clip(Number(market.p), lo, hi), v = p * (1 - p);
    return { p, variance: v * v * cfg.market.logit_var, source: market.source || "" };
  }

  // ------------------------------------------------------ réconciliation
  function divGrad(x, q, rule, eps) {
    if (rule === "brier") return 2 * (x - q);
    x = Math.min(Math.max(x, eps), 1 - eps); q = Math.min(Math.max(q, eps), 1 - eps);
    return Math.log(x / q) - Math.log((1 - x) / (1 - q));
  }
  function divergence(x, q, rule, eps) {
    if (rule === "brier") return (x - q) * (x - q);
    x = Math.min(Math.max(x, eps), 1 - eps); q = Math.min(Math.max(q, eps), 1 - eps);
    return x * Math.log(x / q) + (1 - x) * Math.log((1 - x) / (1 - q));
  }
  function worldOk(bits, hard) {
    for (const c of hard) {
      if (c.relation === "implies" && bits[c.a] === 1 && bits[c.b] === 0) return false;
      if (c.relation === "exclusive" && bits[c.a] === 1 && bits[c.b] === 1) return false;
      if (c.relation === "partition") { let s = 0; for (const i of c.members) s += bits[i]; if (s !== 1) return false; }
    }
    return true;
  }
  function violation(x, c) {
    if (c.relation === "implies") return Math.max(0, x[c.a] - x[c.b]);
    if (c.relation === "exclusive") return Math.max(0, x[c.a] + x[c.b] - 1);
    let s = 0; for (const i of c.members) s += x[i];
    return Math.abs(s - 1);
  }
  function softGrad(x, soft, mu) {
    const g = new Array(x.length).fill(0);
    for (const c of soft) {
      if (c.relation === "implies") { const v = x[c.a] - x[c.b]; if (v > 0) { g[c.a] += 2 * mu * v; g[c.b] -= 2 * mu * v; } }
      else if (c.relation === "exclusive") { const v = x[c.a] + x[c.b] - 1; if (v > 0) { g[c.a] += 2 * mu * v; g[c.b] += 2 * mu * v; } }
      else { let v = -1; for (const i of c.members) v += x[i]; for (const i of c.members) g[i] += 2 * mu * v; }
    }
    return g;
  }
  function reconcile(q, constraints, cfg) {
    const rc = cfg.reconciliation, rule = rc.scoring_rule, mu = rc.soft_penalty, eps = rc.eps, n = q.length;
    const hard = constraints.filter((c) => c.hard), soft = constraints.filter((c) => !c.hard);
    const before = constraints.map((c) => violation(q, c));
    if (!constraints.length) return { x: q.slice(), iterations: 0, gap: 0, violations_before: [], violations_after: [], method: "none", consistent: true };
    let worlds = [];
    for (let mask = 0; mask < 1 << n; mask++) {
      const bits = []; for (let i = 0; i < n; i++) bits.push((mask >> i) & 1);
      if (worldOk(bits, hard)) worlds.push(bits);
    }
    let consistent = true;
    if (!worlds.length) {
      consistent = false;
      for (let mask = 0; mask < 1 << n; mask++) { const bits = []; for (let i = 0; i < n; i++) bits.push((mask >> i) & 1); worlds.push(bits); }
    }
    const lam = new Map();
    worlds.forEach((_, w) => lam.set(w, 1 / worlds.length));
    const sortedKeys = () => Array.from(lam.keys()).sort((a, b) => a - b);
    let x = new Array(n).fill(0);
    for (const w of sortedKeys()) for (let i = 0; i < n; i++) x[i] += lam.get(w) * worlds[w][i];
    const grad = (xv) => { const g = softGrad(xv, soft, mu); for (let i = 0; i < n; i++) g[i] += divGrad(xv[i], q[i], rule, eps); return g; };
    let it = 0, gap = 0;
    for (it = 1; it <= rc.max_iter; it++) {
      const g = grad(x);
      let bestS = 0, bestVal = null;
      for (let w = 0; w < worlds.length; w++) {
        let val = 0; for (let i = 0; i < n; i++) val += g[i] * worlds[w][i];
        if (bestVal === null || val < bestVal) { bestS = w; bestVal = val; }
      }
      let gx = 0; for (let i = 0; i < n; i++) gx += g[i] * x[i];
      gap = gx - bestVal;
      if (gap < rc.tol) break;
      let away = null, awayVal = null;
      for (const w of sortedKeys()) {
        let val = 0; for (let i = 0; i < n; i++) val += g[i] * worlds[w][i];
        if (awayVal === null || val > awayVal) { away = w; awayVal = val; }
      }
      if (away === bestS) break;
      const d = worlds[bestS].map((v, i) => v - worlds[away][i]);
      const gmax = lam.get(away);
      const dphi = (gam) => { const xx = x.map((v, i) => v + gam * d[i]); const gg = grad(xx); let s = 0; for (let i = 0; i < n; i++) s += gg[i] * d[i]; return s; };
      let gam;
      if (dphi(gmax) <= 0) gam = gmax;
      else {
        let lo = 0, hi = gmax;
        for (let k = 0; k < 100; k++) { const mid = 0.5 * (lo + hi); if (dphi(mid) > 0) hi = mid; else lo = mid; }
        gam = 0.5 * (lo + hi);
      }
      if (gam <= 0) break;
      lam.set(bestS, (lam.get(bestS) || 0) + gam);
      if (gam >= gmax) lam.delete(away); else lam.set(away, lam.get(away) - gam);
      x = new Array(n).fill(0);
      for (const w of sortedKeys()) for (let i = 0; i < n; i++) x[i] += lam.get(w) * worlds[w][i];
    }
    if (it > rc.max_iter) it = rc.max_iter;
    let obj = 0; for (let i = 0; i < n; i++) obj += divergence(x[i], q[i], rule, eps);
    for (const c of soft) { const v = violation(x, c); obj += mu * v * v; }
    return { x, iterations: it, gap, objective: obj, violations_before: before, violations_after: constraints.map((c) => violation(x, c)),
      method: "bregman_" + (rule === "log" ? "kl" : "euclid") + "_pairwise_frank_wolfe", consistent };
  }

  // -------------------------------------------------------- résolubilité
  function resolvabilityScore(stability, interModel, sourceKind, classStats, cfg) {
    const rc = cfg.resolvability;
    const clarity = interModel === null || interModel === undefined ? stability : 0.5 * (stability + interModel);
    const src = sourceKind in rc.source_kind_scores ? rc.source_kind_scores[sourceKind] : rc.source_kind_scores.other;
    const [a, b] = rc.unambiguous_prior;
    const n = classStats ? classStats.n : 0, canc = classStats ? classStats.cancelled : 0;
    const unamb = (n - canc + a) / (n + a + b);
    const prod = Math.max(clarity, 0) * src * unamb;
    return { score: prod > 0 ? Math.pow(prod, 1 / 3) : 0, clarity, source_stability: src, unambiguous_frequency: unamb, class_resolutions: n };
  }

  // ----------------------------------------------------------- prévision
  const DEPENDENCY_ASSUMPTIONS =
    "Sources fusionnées par primary_ancestor_id ; ancêtres distincts supposés conditionnellement " +
    "indépendants ; biais commun non mesuré représenté par une variance additive sur le logit, " +
    "plus grande quand les ancêtres distincts sont peu nombreux ; sources derived_from_tool_output exclues ; " +
    "pas de copule (hors MVP).";

  function constraintsFor(inp) {
    const linked = inp.linked || [];
    const ids = [inp.question.question_id].concat(linked.map((l) => l.question_id));
    const index = {}; ids.forEach((id, i) => (index[id] = i));
    const qv = linked.map((l) => l.p);
    const cons = [], ph = [], ps = [];
    for (const l of linked) {
      const j = index[l.question_id], hard = !!l.hard;
      if (l.relation === "implies") cons.push({ relation: "implies", a: 0, b: j, hard });
      else if (l.relation === "implied_by") cons.push({ relation: "implies", a: j, b: 0, hard });
      else if (l.relation === "exclusive") cons.push({ relation: "exclusive", a: 0, b: j, hard });
      else if (l.relation === "partition_member") (hard ? ph : ps).push(j);
    }
    if (ph.length) cons.push({ relation: "partition", members: [0].concat(ph), hard: true });
    if (ps.length) cons.push({ relation: "partition", members: [0].concat(ps), hard: false });
    for (const c of inp.linked_constraints || []) {
      if (c.a_id in index && c.b_id in index) {
        let rel = c.relation, a = index[c.a_id], b = index[c.b_id];
        if (rel === "implied_by") { rel = "implies"; const t = a; a = b; b = t; }
        if (rel === "implies" || rel === "exclusive") cons.push({ relation: rel, a, b, hard: !!c.hard });
      }
    }
    return [qv, cons, ids];
  }

  function runForecast(inp, history, cfg) {
    const q = inp.question;
    if (q.question_type !== "binary") throw new Error("MVP : seul le type binary est prévu");
    const t = toDays(inp.as_of);
    const bucket = horizonBucket(isoDurationDays(q.horizon), cfg.horizon_buckets_days);
    const ck = q.question_type + "|" + bucket;
    const domain = q.domain || "general";
    const obs = history.observations.filter((o) => o.t <= t);
    const rates = extractionErrorRates(inp.annotations || [], cfg);
    const fusion = fuseLineage(inp.lineage || [], t);
    const br = baseRate(history.reference, q.question_type, bucket, domain, t, cfg);
    const betas = fitEvidenceBetas(obs, cfg);
    const [x, detail] = evidenceFeatures(fusion.fused, rates, cfg.bayes.evidence_types);
    const bias = systematicBiasVariance(obs, domain, fusion.n_ancestors, cfg);
    const bayes = bayesComponent(br.p, x, detail, betas, bias.total);
    const market = marketComponent(inp.market, cfg);
    const comps = [["base_rate", br.p_diffuse, br.variance], ["bayes_hierarchical", bayes.p, bayes.variance]];
    if (market) comps.push(["market_anchor", market.p, market.variance]);
    const names = comps.map((c) => c[0]);
    const sw = stackWeights(obs, names, ck, t, cfg);
    const res = resolvabilityScore(q.resolution_stability_score === undefined ? 0 : q.resolution_stability_score,
      q.resolution_inter_model_agreement, q.source_kind || "other", history.resolution_stats[ck], cfg);
    const r = res.score;
    const eff = {};
    let inf = 0;
    for (const n of names) if (n !== "base_rate") { eff[n] = sw.class[n] * r; inf += eff[n]; }
    eff.base_rate = 1 - inf;
    let pRaw = 0;
    for (const [n, p] of comps) pRaw += eff[n] * p;
    const wDisp = 1 - eff.base_rate;
    const [qLink, cons, ids] = constraintsFor(inp);
    const rec = reconcile([pRaw].concat(qLink), cons, cfg);
    const pRec = rec.x[0];
    let second = 0;
    for (const [n, p, v] of comps) second += eff[n] * (v + p * p);
    let v = second - pRaw * pRaw;
    if (v < 1e-12) v = 1e-12;
    let kappa = (pRaw * (1 - pRaw)) / v - 1;
    if (kappa < 0.5) kappa = 0.5;
    const contribs = bayes.contributions.slice().sort((a, b) => b.logit_contribution - a.logit_contribution ||
      (a.primary_ancestor_id < b.primary_ancestor_id ? -1 : a.primary_ancestor_id > b.primary_ancestor_id ? 1 : 0));
    const bullish = contribs.filter((c) => c.logit_contribution > 0);
    const bearish = contribs.slice().reverse().filter((c) => c.logit_contribution < 0);
    const pick = (o, keys) => Object.fromEntries(keys.map((k) => [k, o[k]]));
    const models_pipeline = {
      base_rate: { reference_class: br.reference_class, p: br.p, p_diffuse: br.p_diffuse, n: br.n, levels: br.levels },
      component_forecasts: comps.map(([n, p]) => ({ component: n, p })),
      stacking_weights: Object.fromEntries(names.map((n) => [n, eff[n]])),
      stacking_weights_class: sw.class,
      stacking_weights_global: sw.global,
      stacking_diagnostics: pick(sw, ["n_rows_global", "n_rows_class", "n_eff_global", "n_eff_class", "breaks_global", "breaks_class", "em_iterations"]),
      dirichlet_hyperparameter_id: cfg.dirichlet.id,
      systematic_bias_variance: bias.total,
      systematic_bias_detail: bias,
      dependency_assumptions: DEPENDENCY_ASSUMPTIONS,
      class_key: ck,
      domain,
      evidence_features: x,
      evidence_betas: { beta: betas.beta, mu: betas.mu, n_obs: betas.n_obs },
      bayes_detail: pick(bayes, ["mu_logit", "var_param", "var_extraction", "var_bias", "var_total"]),
      lineage_fusion: { n_ancestors: fusion.n_ancestors, excluded: fusion.excluded },
      resolvability_detail: res,
      reconciliation: pick(rec, ["method", "iterations", "gap", "violations_before", "violations_after", "consistent"]),
      reconciliation_vector: { ids, raw: [pRaw].concat(qLink), reconciled: rec.x },
    };
    const final_output = {
      p_raw: pRaw, p_reconciled: pRec,
      predictive_distribution: { type: "bernoulli", p: pRec },
      epistemic_uncertainty: { type: "beta", alpha: pRec * kappa, beta: (1 - pRec) * kappa, variance: v },
      w_displayed: wDisp,
      self_negating_risk: !!q.self_negating_risk,
      factors: { bullish: bullish.slice(0, 5), bearish: bearish.slice(0, 5) },
    };
    return { models_pipeline, final_output, extraction_error_rates: rates, resolvability_score: r, class_key: ck };
  }

  // -------------------------------------------------------------- Merkle
  async function merkleRoot(leafHashes) {
    if (!leafHashes.length) return sha256Hex(new Uint8Array(0));
    const leaves = [];
    for (const h of leafHashes) { const b = new Uint8Array(33); b[0] = 0; b.set(hexToBytes(h), 1); leaves.push(await sha256Bytes(b)); }
    async function mth(arr) {
      if (arr.length === 1) return arr[0];
      let k = 1; while (k * 2 < arr.length) k *= 2;
      const l = await mth(arr.slice(0, k)), r = await mth(arr.slice(k));
      const b = new Uint8Array(65); b[0] = 1; b.set(l, 1); b.set(r, 33);
      return sha256Bytes(b);
    }
    return hex(await mth(leaves));
  }
  function ledgerLeaves(entries, uptoSeq) {
    const out = [];
    for (const e of entries) {
      if (uptoSeq !== undefined && uptoSeq !== null && e.seq > uptoSeq) break;
      if (["forecast", "resolution", "reference_batch"].includes(e.record_type)) out.push(e.entry_hash);
      else if (e.record_type === "snapshot") out.push(e.record.snapshot_hash);
    }
    return out;
  }
  const entryHash = (seq, recordType, record) => hashObj({ seq, record_type: recordType, record });

  async function verifyEntries(entries) {
    let prev = GENESIS_HASH;
    const problems = [], anchors = [];
    for (let i = 0; i < entries.length; i++) {
      const e = entries[i];
      if (e.seq !== i) problems.push({ seq: e.seq, problem: "numéro de séquence inattendu" });
      if (e.record.hash_previous !== prev) problems.push({ seq: e.seq, problem: "hash_previous ne correspond pas à l'entrée précédente" });
      if ((await entryHash(e.seq, e.record_type, e.record)) !== e.entry_hash) problems.push({ seq: e.seq, problem: "contenu modifié (entry_hash invalide)" });
      prev = e.entry_hash;
    }
    for (const e of entries) if (e.record_type === "anchor") {
      const rec = e.record, leaves = ledgerLeaves(entries, rec.covered_seq_max);
      const root = await merkleRoot(leaves);
      const ok = root === rec.merkle_root && leaves.length === rec.leaf_count;
      anchors.push({ seq: e.seq, merkle_root: rec.merkle_root, ok, method: rec.anchor_method });
      if (!ok) problems.push({ seq: e.seq, problem: "racine de Merkle ancrée invalide" });
    }
    return { ok: !problems.length, n_entries: entries.length, problems, anchors, head: prev };
  }

  /** Registre en ajout seul, en mémoire ; `persist(entry)` écrit ailleurs (db). */
  class Ledger {
    constructor(entries, persist) { this._entries = (entries || []).map((e) => JSON.parse(JSON.stringify(e))); this.persist = persist || null; }
    get entries() { return JSON.parse(JSON.stringify(this._entries)); }
    get length() { return this._entries.length; }
    headHash() { return this._entries.length ? this._entries[this._entries.length - 1].entry_hash : GENESIS_HASH; }
    forecastsFor(qid) { return this._entries.filter((e) => e.record_type === "forecast" && e.record.question.question_id === qid).map((e) => JSON.parse(JSON.stringify(e.record))); }
    latestForecast(qid) { const f = this.forecastsFor(qid); return f.length ? f[f.length - 1] : null; }
    resolutionFor(qid) { const r = this._entries.filter((e) => e.record_type === "resolution" && e.record.question_id === qid); return r.length ? JSON.parse(JSON.stringify(r[r.length - 1].record)) : null; }
    findForecast(fid) { const e = this._entries.find((x) => x.record_type === "forecast" && x.record.forecast_id === fid); return e ? JSON.parse(JSON.stringify(e)) : null; }
    async _append(recordType, record) {
      record = JSON.parse(JSON.stringify(record));
      record.hash_previous = this.headHash();
      const seq = this._entries.length;
      const entry = { seq, record_type: recordType, record, entry_hash: await entryHash(seq, recordType, record) };
      if (this.persist) await this.persist(entry);
      this._entries.push(entry);
      return JSON.parse(JSON.stringify(entry));
    }
    async appendForecast(record) {
      record = JSON.parse(JSON.stringify(record));
      if (!has(record, "hash_previous")) record.hash_previous = GENESIS_HASH;
      validateForecastRecord(record);
      const fid = record.forecast_id, qid = record.question.question_id;
      if (this.findForecast(fid)) throw new ImmutableRecordError("forecast_id existe déjà : un ForecastRecord ne se modifie pas, il se remplace");
      if (this.resolutionFor(qid)) throw new ImmutableRecordError("question déjà résolue : plus aucune prévision acceptée");
      const latest = this.latestForecast(qid), sup = record.supersedes_forecast_id;
      if (!latest && sup) throw new ValidationError("supersedes_forecast_id pointe vers une question sans prévision");
      if (latest && sup !== latest.forecast_id) throw new ImmutableRecordError("une nouvelle prévision doit remplacer la dernière via supersedes_forecast_id");
      return this._append("forecast", record);
    }
    async appendResolution(record) {
      record = JSON.parse(JSON.stringify(record));
      if (!has(record, "hash_previous")) record.hash_previous = GENESIS_HASH;
      validateResolutionRecord(record);
      if (!this.latestForecast(record.question_id)) throw new ValidationError("aucune prévision pour cette question");
      if (this.resolutionFor(record.question_id)) throw new ImmutableRecordError("question déjà résolue");
      if (![0, 1, "cancelled"].includes(record.outcome)) throw new ValidationError("outcome binaire attendu : 1, 0 ou 'cancelled'");
      return this._append("resolution", record);
    }
    appendSnapshot(h, kind, ts) { return this._append("snapshot", { snapshot_hash: h, kind, timestamp_utc: ts }); }
    appendReferenceBatch(items, source, ts) {
      for (const it of items) for (const f of ["question_type", "horizon_days", "outcome", "resolved_at"]) if (!has(it, f)) throw new ValidationError("élément de référence sans " + f);
      return this._append("reference_batch", { source, items, timestamp_utc: ts });
    }
    appendAnchor(a) { return this._append("anchor", a); }
    async merkleRoot(uptoSeq) { const l = ledgerLeaves(this._entries, uptoSeq); return [await merkleRoot(l), l.length]; }
    verify() { return verifyEntries(this._entries); }
  }

  // ------------------------------------------------------------- scores
  const SCORE_TYPES = ["brier", "log"];
  const brier = (p, y) => { const d = p - y; return d * d; };
  const logLoss = (p, y) => { p = clipP(p); return y === 1 ? -Math.log(p) : -Math.log(1 - p); };
  const score = (p, y, st) => (st === "brier" ? brier(p, y) : logLoss(p, y));
  const blockLength = (n) => Math.max(1, Math.floor(Math.pow(n, 1 / 3) + 0.5));
  function blockBootstrapCi(x, reps, seed, level = 0.95) {
    const n = x.length;
    if (n < 2) return null;
    const L = blockLength(n), rng = new Mulberry32(seed), means = [];
    for (let r = 0; r < reps; r++) {
      let s = 0, k = 0;
      while (k < n) { const start = rng.randint(n); for (let j = 0; j < L; j++) { if (k >= n) break; s += x[(start + j) % n]; k++; } }
      means.push(s / n);
    }
    means.sort((a, b) => a - b);
    return [means[Math.floor(((1 - level) / 2) * reps)], means[Math.min(reps - 1, Math.floor(((1 + level) / 2) * reps))]];
  }
  function dieboldMariano(d) {
    const n = d.length;
    if (n < 3) return null;
    let m = 0; for (const v of d) m += v; m /= n;
    const lag = blockLength(n);
    let g0 = 0; for (const v of d) g0 += (v - m) * (v - m); g0 /= n;
    let lrv = g0;
    for (let k = 1; k <= lag; k++) { let g = 0; for (let t = k; t < n; t++) g += (d[t] - m) * (d[t - k] - m); g /= n; lrv += 2 * (1 - k / (lag + 1)) * g; }
    if (lrv <= 0) return { statistic: 0, p_value: 1, lag };
    const stat = m / Math.sqrt(lrv / n);
    return { statistic: stat, p_value: 2 * (1 - normCdf(Math.abs(stat))), lag };
  }
  function murphyDecomposition(ps, ys, bins) {
    const n = ps.length;
    if (!n) return { n: 0 };
    let obar = 0; for (const y of ys) obar += y; obar /= n;
    const groups = {};
    ps.forEach((p, i) => { const b = Math.min(bins - 1, Math.floor(p * bins)); (groups[b] = groups[b] || []).push(i); });
    let rel = 0, res = 0;
    for (const b of Object.keys(groups).map(Number).sort((a, c) => a - c)) {
      const idx = groups[b];
      let fk = 0, ok = 0; for (const i of idx) { fk += ps[i]; ok += ys[i]; }
      fk /= idx.length; ok /= idx.length;
      rel += idx.length * (fk - ok) * (fk - ok);
      res += idx.length * (ok - obar) * (ok - obar);
    }
    rel /= n; res /= n;
    const unc = obar * (1 - obar);
    let bs = 0; for (let i = 0; i < n; i++) bs += brier(ps[i], ys[i]); bs /= n;
    return { n, brier: bs, reliability: rel, resolution: res, uncertainty: unc, residual_within_bin: bs - (rel - res + unc) };
  }
  function calibrationCurve(ps, ys, bins) {
    const groups = {};
    ps.forEach((p, i) => { const b = Math.min(bins - 1, Math.floor(p * bins)); (groups[b] = groups[b] || []).push(i); });
    return Object.keys(groups).map(Number).sort((a, c) => a - c).map((b) => {
      const idx = groups[b]; let k = 0, mp = 0;
      for (const i of idx) { k += ys[i]; mp += ps[i]; }
      const n = idx.length;
      return { bin: [b / bins, (b + 1) / bins], n, mean_forecast: mp / n, observed: k / n,
        ci95: [betaPpf(0.025, k + 0.5, n - k + 0.5), betaPpf(0.975, k + 0.5, n - k + 0.5)] };
    });
  }
  function resolvedChains(entries) {
    const forecasts = {}, out = [];
    for (const e of entries) {
      if (e.record_type === "forecast") (forecasts[e.record.question.question_id] = forecasts[e.record.question.question_id] || []).push(e.record);
      else if (e.record_type === "resolution") {
        const r = e.record;
        if (r.outcome === "cancelled" || !forecasts[r.question_id]) continue;
        const chain = forecasts[r.question_id];
        if (chain[0].question.question_type !== "binary") continue;
        out.push({ question_id: r.question_id, y: Math.trunc(Number(r.outcome)), chain, class: chain[0].models_pipeline.class_key || "binary|?", resolution: r });
      }
    }
    return out;
  }
  function baseP(rec) {
    for (const c of rec.models_pipeline.component_forecasts) if (c.component === "base_rate") return c.p;
    return rec.models_pipeline.base_rate.p;
  }
  function mean(xs) { if (!xs.length) return null; let s = 0; for (const v of xs) s += v; return s / xs.length; }
  function scoreRecords(entries, cfg) {
    const ev = cfg.evaluation, chains = resolvedChains(entries), classDiffs = {};
    for (const st of SCORE_TYPES) for (const ch of chains) {
      const last = ch.chain[ch.chain.length - 1];
      const key = ch.class + "#" + st;
      (classDiffs[key] = classDiffs[key] || []).push(score(last.final_output.p_reconciled, ch.y, st) - score(baseP(last), ch.y, st));
    }
    const cis = {}; for (const k of Object.keys(classDiffs)) cis[k] = blockBootstrapCi(classDiffs[k], ev.bootstrap_reps, ev.seed);
    const out = [];
    for (const ch of chains) {
      const t0 = ch.chain[0];
      for (const rec of ch.chain) for (const st of SCORE_TYPES) {
        const fo = rec.final_output, sRec = score(fo.p_reconciled, ch.y, st);
        out.push({ forecast_id: rec.forecast_id, question_id: ch.question_id, score_type: st,
          score_raw: score(fo.p_raw, ch.y, st), score_reconciled: sRec,
          score_frozen_t0: score(t0.final_output.p_reconciled, ch.y, st),
          class: { question_type: "binary", horizon_bucket: ch.class.split("|")[1] },
          vs_base_rate_paired_diff: { value: sRec - score(baseP(rec), ch.y, st), ci95: cis[ch.class + "#" + st] || null,
            method: "block_bootstrap_circulaire (différence moyenne de la classe, dernière prévision)" } });
      }
    }
    return out;
  }
  function dashboard(entries, cfg) {
    const ev = cfg.evaluation, chains = resolvedChains(entries), byClass = {};
    for (const ch of chains) (byClass[ch.class] = byClass[ch.class] || []).push(ch);
    byClass.toutes = chains;
    const out = {};
    for (const ck of Object.keys(byClass).sort()) {
      const chs = byClass[ck];
      const ps = chs.map((c) => c.chain[c.chain.length - 1].final_output.p_reconciled), ys = chs.map((c) => c.y);
      const block = { n: chs.length, murphy: murphyDecomposition(ps, ys, ev.calibration_bins), calibration: calibrationCurve(ps, ys, ev.calibration_bins) };
      for (const st of SCORE_TYPES) {
        const diffs = chs.map((c, i) => score(ps[i], ys[i], st) - score(baseP(c.chain[c.chain.length - 1]), ys[i], st));
        const upd = chs.filter((c) => c.chain.length > 1).map((c) => score(c.chain[c.chain.length - 1].final_output.p_reconciled, c.y, st) - score(c.chain[0].final_output.p_reconciled, c.y, st));
        block[st] = { mean_score: mean(chs.map((c, i) => score(ps[i], ys[i], st))),
          vs_base_rate: { mean_diff: mean(diffs), ci95: blockBootstrapCi(diffs, ev.bootstrap_reps, ev.seed), diebold_mariano: dieboldMariano(diffs) },
          update_value: { n: upd.length, mean_diff_vs_t0: mean(upd), ci95: blockBootstrapCi(upd, ev.bootstrap_reps, ev.seed) } };
      }
      out[ck] = block;
    }
    return out;
  }

  // ------------------------------------------------------------- tournoi
  const METHODS = ["base_rate_seul", "moyenne_simple", "bayes_hierarchique", "stack_brut", "stack_officiel"];
  function methodForecasts(rec) {
    const comps = {}; let s = 0;
    for (const c of rec.models_pipeline.component_forecasts) { comps[c.component] = c.p; s += c.p; }
    const out = { base_rate_seul: comps.base_rate, moyenne_simple: s / Object.keys(comps).length, bayes_hierarchique: comps.bayes_hierarchical,
      stack_brut: rec.final_output.p_raw, stack_officiel: rec.final_output.p_reconciled };
    if ("market_anchor" in comps) out.marche_seul = comps.market_anchor;
    return out;
  }
  function bootstrapIndices(n, reps, seed) {
    const L = blockLength(n), rng = new Mulberry32(seed), out = [];
    for (let r = 0; r < reps; r++) {
      const idx = [];
      while (idx.length < n) { const start = rng.randint(n); for (let j = 0; j < L; j++) { if (idx.length >= n) break; idx.push((start + j) % n); } }
      out.push(idx);
    }
    return out;
  }
  function modelConfidenceSet(losses, alpha, reps, seed) {
    const names = Object.keys(losses).sort();
    const n = names.length ? losses[names[0]].length : 0;
    if (n < 5 || names.length < 2) return { mcs: names, eliminated: [], note: "trop peu de questions résolues pour un MCS (n < 5)" };
    const idx = bootstrapIndices(n, reps, seed);
    const alive = names.slice(), eliminated = [];
    while (alive.length > 1) {
      const m = alive.length, d = {}, dbar = {};
      for (const a of alive) {
        const row = [];
        for (let t = 0; t < n; t++) { let avg = 0; for (const b of alive) avg += losses[b][t]; row.push(losses[a][t] - avg / m); }
        d[a] = row;
        let s = 0; for (const v of row) s += v; dbar[a] = s / n;
      }
      const boot = {}; alive.forEach((a) => (boot[a] = []));
      for (const ix of idx) for (const a of alive) { let s = 0; for (const t of ix) s += d[a][t]; boot[a].push(s / n); }
      const tstat = {}, se = {};
      for (const a of alive) {
        let v = 0; for (const b of boot[a]) v += (b - dbar[a]) * (b - dbar[a]); v /= reps;
        se[a] = v > 0 ? Math.sqrt(v) : 1; tstat[a] = dbar[a] / se[a];
      }
      let tmax = -Infinity; for (const a of alive) if (tstat[a] > tmax) tmax = tstat[a];
      let count = 0;
      for (let r = 0; r < reps; r++) {
        let tb = null;
        for (const a of alive) { const v = (boot[a][r] - dbar[a]) / se[a]; if (tb === null || v > tb) tb = v; }
        if (tb >= tmax) count++;
      }
      const pval = count / reps;
      if (pval >= alpha) break;
      const worst = alive.slice().sort((a, b) => tstat[b] - tstat[a] || (a < b ? -1 : 1))[0];
      eliminated.push({ method: worst, p_value: pval, t_stat: tstat[worst] });
      alive.splice(alive.indexOf(worst), 1);
    }
    return { mcs: alive, eliminated, alpha, n };
  }
  function tournament(entries, cfg, st = "log") {
    const ev = cfg.evaluation, chains = resolvedChains(entries), losses = {};
    METHODS.forEach((m) => (losses[m] = []));
    const market = [];
    for (const ch of chains) {
      const mf = methodForecasts(ch.chain[ch.chain.length - 1]);
      for (const m of METHODS) losses[m].push(score(mf[m], ch.y, st));
      if ("marche_seul" in mf) market.push(score(mf.marche_seul, ch.y, st) - score(mf.stack_officiel, ch.y, st));
    }
    const summary = {}; for (const m of METHODS) summary[m] = { mean_loss: mean(losses[m]), n: losses[m].length };
    const mcs = chains.length ? modelConfidenceSet(losses, ev.mcs_alpha, ev.bootstrap_reps, ev.seed) : { mcs: METHODS.slice(), eliminated: [], note: "aucune question résolue" };
    const inMcs = mcs.mcs.includes("stack_officiel");
    return { score_type: st, summary, mcs, official_engine: inMcs ? "stack_officiel" : null,
      official_engine_note: inMcs ? "stack_officiel appartient au MCS" : "stack_officiel exclu du MCS : il ne doit pas rester moteur officiel",
      market_subset: { n: market.length, mean_diff_market_minus_official: mean(market) } };
  }

  // ------------------------------------------------------- mode fantôme
  function normalizeManifold(markets) {
    const out = [];
    for (const m of markets) {
      if (m.outcomeType !== "BINARY") continue;
      const r = m.resolution;
      const resolution = r === "YES" ? 1 : r === "NO" ? 0 : r === "CANCEL" || r === "MKT" ? "cancelled" : null;
      out.push({ platform: "manifold", external_id: String(m.id), title: m.question || "", url: m.url || "", close_time_ms: m.closeTime,
        community_p: m.probability, is_resolved: !!m.isResolved, resolution, resolution_criteria: m.textDescription || m.description || "" });
    }
    return out;
  }
  function normalizeMetaculus(posts) {
    const out = [];
    for (const p of posts) {
      const q = p.question || p;
      if (!(q.type === undefined || q.type === null || q.type === "binary")) continue;
      const latest = (((q.aggregations || {}).recency_weighted || {}).latest) || {};
      const centers = latest.centers || [];
      const r = q.resolution;
      const resolution = ["yes", 1, true].includes(r) ? 1 : ["no", 0, false].includes(r) ? 0 : ["annulled", "ambiguous"].includes(r) ? "cancelled" : null;
      const id = p.id !== undefined ? p.id : q.id;
      out.push({ platform: "metaculus", external_id: String(id), title: p.title || q.title || "", url: `https://www.metaculus.com/questions/${id}/`,
        close_time_iso: q.scheduled_close_time, community_p: centers.length ? centers[0] : null, is_resolved: resolution !== null,
        resolution, resolution_criteria: q.resolution_criteria || "" });
    }
    return out;
  }
  function shadowComparison(entries, cfg) {
    const ev = cfg.evaluation, latest = {}, rows = [];
    for (const e of entries) {
      if (e.record_type === "forecast" && e.record.shadow) latest[e.record.question.question_id] = e.record;
      else if (e.record_type === "resolution") {
        const r = e.record, f = latest[r.question_id];
        if (!f || r.outcome === "cancelled") continue;
        const cp = f.shadow.community_p_at_forecast;
        if (cp === null || cp === undefined) continue;
        const y = Math.trunc(Number(r.outcome));
        rows.push({ question_id: r.question_id, platform: f.shadow.platform, tool_p: f.final_output.p_reconciled, community_p: cp, y,
          diff: logLoss(f.final_output.p_reconciled, y) - logLoss(clipP(cp), y) });
      }
    }
    const diffs = rows.map((r) => r.diff), resolved = new Set(rows.map((r) => r.question_id));
    return { n_resolved: rows.length, n_open: Object.keys(latest).filter((q) => !resolved.has(q)).length,
      mean_log_diff_tool_minus_community: mean(diffs), ci95: blockBootstrapCi(diffs, ev.bootstrap_reps, ev.seed),
      diebold_mariano: dieboldMariano(diffs), rows, scope_note: "Valable uniquement pour les classes de questions des plateformes importées." };
  }

  // ------------------------------------------------- ingestion (sans appel)
  const VERDICTS = ["YES", "NO", "AMBIGUOUS"];
  const TOOL_MARKERS = /oracle\s*calibr|forecast_id/i;
  function render(template, values) {
    let out = template;
    for (const k of Object.keys(values)) out = out.split("{{" + k + "}}").join(String(values[k]));
    return out;
  }
  function parseJsonReply(text) {
    let t = String(text).trim();
    if (t.startsWith("```")) { t = t.replace(/^`+|`+$/g, ""); t = t.includes("\n") ? t.slice(t.indexOf("\n") + 1) : t; }
    try { return JSON.parse(t); } catch (e) {
      const a = t.indexOf("{"), b = t.lastIndexOf("}");
      if (a < 0 || b <= a) throw new Error("réponse LLM sans JSON exploitable");
      return JSON.parse(t.slice(a, b + 1));
    }
  }
  function cleanVerdicts(v, n) {
    const out = (v || []).map((x) => String(x).toUpperCase()).slice(0, n).map((x) => (VERDICTS.includes(x) ? x : "AMBIGUOUS"));
    while (out.length < n) out.push("AMBIGUOUS");
    return out;
  }
  function agreement(a, b) { if (!a.length) return 0; let s = 0; for (let i = 0; i < a.length; i++) if (a[i] === b[i]) s++; return s / a.length; }
  function meanPairwiseAgreement(vs) {
    if (vs.length < 2) return 0;
    let s = 0, n = 0;
    for (let i = 0; i < vs.length; i++) for (let j = i + 1; j < vs.length; j++) { s += agreement(vs[i], vs[j]); n++; }
    return s / n;
  }
  function majority(vs) {
    return vs[0].map((_, i) => {
      const c = {}; for (const v of vs) c[v[i]] = (c[v[i]] || 0) + 1;
      return Object.entries(c).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0][0];
    });
  }
  async function ancestorId(primarySource, date) {
    let s = String(primarySource || "").normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase();
    s = s.replace(/[^a-z0-9]+/g, " ").trim();
    return "ANC-" + (await sha256Hex(s + "|" + String(date || "").slice(0, 10))).slice(0, 12);
  }

  const api = {
    GENESIS_HASH, canonNum, canonicalJson, sha256Hex, hashObj, Mulberry32,
    clip, clipP, logit, sigmoid, probitSigmoid, lgamma, betainc, betaPpf, erfc, normCdf, solveLinear, invert,
    QUESTION_TYPES, AMBIGUITY_POLICIES, RELATIONS, MANDATORY_RESOLUTION_FIELDS, ValidationError, ImmutableRecordError,
    missingResolutionFields, validateForecastRecord, validateResolutionRecord, validateDistribution, horizonBucket, isoDurationDays, toDays,
    buildHistory, baseRate, emWeights, pageHinkley, stackWeights, extractionErrorRates, systematicBiasVariance,
    fuseLineage, evidenceFeatures, fitEvidenceBetas, bayesComponent, marketComponent, reconcile, resolvabilityScore, runForecast,
    merkleRoot, ledgerLeaves, entryHash, verifyEntries, Ledger,
    brier, logLoss, score, blockBootstrapCi, dieboldMariano, murphyDecomposition, calibrationCurve, scoreRecords, dashboard,
    methodForecasts, modelConfidenceSet, tournament, normalizeManifold, normalizeMetaculus, shadowComparison,
    render, parseJsonReply, cleanVerdicts, agreement, meanPairwiseAgreement, majority, ancestorId, TOOL_MARKERS, DEPENDENCY_ASSUMPTIONS,
  };
  root.OracleEngine = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
