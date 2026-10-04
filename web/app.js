/* Oracle Calibré — interface. Fonctionne avec ServerBackend (Python) ou LocalBackend (claude.ai). */
(function () {
  "use strict";
  const E = window.OracleEngine, B = window.OracleBackends, BOOT = window.ORACLE_BOOT;
  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const esc = (s) => String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const nf1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  const nf3 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3, minimumFractionDigits: 3 });
  const pct = (p) => (p === null || p === undefined || Number.isNaN(p) ? "—" : nf1.format(100 * p) + " %");
  const num = (x) => (x === null || x === undefined || Number.isNaN(x) ? "—" : nf3.format(x));
  const short = (h) => (h ? String(h).slice(0, 12) + "…" : "—");
  const fmtDate = (s) => { try { return new Date(s).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" }); } catch (e) { return s; } };
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* stockage indisponible */ } } };

  const LABELS = {
    base_rate: "Taux de base élargi (diffus)", bayes_hierarchical: "Bayésien hiérarchique", market_anchor: "Ancrage marché",
    cancel: "Annuler la question", freeze_old: "Garder l'ancienne définition", adopt_new: "Adopter la nouvelle définition",
    implies: "implique", implied_by: "est impliquée par", exclusive: "exclusive de", partition_member: "même partition que",
    yes: "oui", no: "non", neutral: "neutre",
    base_rate_seul: "Taux de base seul", moyenne_simple: "Moyenne simple", bayes_hierarchique: "Bayésien seul", stack_brut: "Stack (brut)",
    stack_officiel: "Stack réconcilié (officiel)", marche_seul: "Marché seul",
  };
  const SOURCE_KINDS = ["official_api", "official_statistics", "official_statement", "market_platform", "reputable_media", "other"];
  const DOMAINS = ["politics", "geopolitics", "economics", "finance", "science", "technology", "health", "climate", "sports", "society", "other"];

  const S = { ns: store.get("oc-ns") === "demo" ? "demo" : "real", backend: null, info: null, tab: "prevoir",
    flow: { step: "saisie", sources: [], links: [], market: { p: "", source: "" }, supersedes: "", text: "" }, busy: false };

  // ------------------------------------------------------------ démarrage
  async function makeBackend(ns) {
    if (BOOT.mode === "server") return new B.ServerBackend(ns).init();
    let sample = null, db = null;
    if (window.claude && window.claude.use) {
      [sample, db] = await Promise.all([window.claude.use("sample").catch(() => null), window.claude.use("db").catch(() => null)]);
    }
    const st = db ? new B.DbStore(db, ns === "demo" ? "demo" : "reel") : (S.memStores = S.memStores || {})[ns] || (S.memStores[ns] = new B.MemoryStore());
    try {
      return await new B.LocalBackend({ cfg: BOOT.cfg, prompts: BOOT.prompts, build: BOOT.build, sample, store: st, ns }).init();
    } catch (e) {
      console.error(e);
      const mem = new B.MemoryStore();
      return new B.LocalBackend({ cfg: BOOT.cfg, prompts: BOOT.prompts, build: BOOT.build, sample, store: mem, ns }).init();
    }
  }

  async function boot() {
    renderShell();
    setStatus("Connexion au registre…");
    S.backend = await makeBackend(S.ns);
    S.info = await S.backend.info();
    renderChips();
    const h = (location.hash || "").replace("#", "");
    if (TABS.some((t) => t[0] === h)) S.tab = h;
    await show(S.tab);
    setStatus("");
  }

  const TABS = [["prevoir", "Prévoir"], ["registre", "Registre"], ["resolution", "Résolution"], ["calibration", "Calibration"],
    ["tournoi", "Tournoi"], ["fantome", "Mode fantôme"], ["apropos", "Méthode"]];

  function renderShell() {
    $("#app").innerHTML = `
      <header class="top">
        <div class="brand"><span class="mark" aria-hidden="true"></span><div><div class="name">Oracle Calibré</div>
          <div class="tag">Prévisions probabilistes enregistrées avant résolution</div></div></div>
        <div class="ns" role="group" aria-label="Registre">
          <button id="ns-real" class="seg ${S.ns === "real" ? "on" : ""}">Registre réel</button>
          <button id="ns-demo" class="seg ${S.ns === "demo" ? "on" : ""}">Bac à sable</button>
        </div>
      </header>
      <div class="chips" id="chips"></div>
      <nav class="tabs" id="tabs">${TABS.map(([id, l]) => `<a href="#${id}" data-tab="${id}">${l}</a>`).join("")}</nav>
      <div id="status" class="status" role="status"></div>
      <main id="main"></main>`;
    $("#ns-real").onclick = () => switchNs("real");
    $("#ns-demo").onclick = () => switchNs("demo");
    $$("#tabs a").forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); show(a.dataset.tab); }));
  }
  async function switchNs(ns) {
    if (ns === S.ns || S.busy) return;
    S.ns = ns; store.set("oc-ns", ns);
    $("#ns-real").classList.toggle("on", ns === "real"); $("#ns-demo").classList.toggle("on", ns === "demo");
    setStatus("Changement de registre…");
    S.backend = await makeBackend(ns); S.info = await S.backend.info(); renderChips();
    S.flow = { step: "saisie", sources: [], links: [], market: { p: "", source: "" }, supersedes: "", text: "" };
    await show(S.tab); setStatus("");
  }
  function renderChips() {
    const i = S.info || {};
    $("#chips").innerHTML = [
      S.ns === "demo" ? `<span class="chip warn">Bac à sable : rien ici ne compte dans l'évaluation réelle</span>` : `<span class="chip">Registre réel</span>`,
      i.llm ? `<span class="chip ok">Claude disponible pour la formalisation</span>` : `<span class="chip warn">Claude indisponible : saisie manuelle de la règle</span>`,
      i.persistent ? `<span class="chip ok">Registre persistant</span>` : `<span class="chip bad">Registre en mémoire : il disparaîtra au rechargement</span>`,
      `<span class="chip">Moteur ${i.runtime === "python" ? "Python (référence)" : "JavaScript conforme au moteur Python"}</span>`,
      `<span class="chip">Config. ${esc(i.statistical_config_version || "")}</span>`].join("");
  }
  function setStatus(msg, kind) { const el = $("#status"); el.textContent = msg || ""; el.className = "status" + (kind ? " " + kind : ""); }
  async function guard(fn, busyMsg) {
    if (S.busy) return;
    S.busy = true; setStatus(busyMsg || "Calcul en cours…");
    $$("button.act").forEach((b) => (b.disabled = true));
    try { await fn(); setStatus(""); } catch (e) { (e instanceof E.ValidationError ? console.warn : console.error)(e); setStatus(e.message || String(e), "bad"); }
    finally { S.busy = false; $$("button.act").forEach((b) => (b.disabled = false)); }
  }
  async function show(tab) {
    S.tab = tab;
    $$("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
    const main = $("#main");
    main.innerHTML = `<p class="muted">Chargement…</p>`;
    try { await PAGES[tab](main); } catch (e) { console.error(e); main.innerHTML = `<p class="err">${esc(e.message || e)}</p>`; }
  }

  // ------------------------------------------------------------- graphiques
  function gauge(fo, baseP) {
    const X = (p) => 24 + 552 * p;
    const a = fo.epistemic_uncertainty.alpha, b = fo.epistemic_uncertainty.beta;
    const lo = E.betaPpf(0.05, a, b), hi = E.betaPpf(0.95, a, b), p = fo.p_reconciled;
    let ticks = "";
    for (let i = 0; i <= 10; i++) {
      const x = X(i / 10);
      ticks += `<line x1="${x}" y1="52" x2="${x}" y2="${i % 5 === 0 ? 62 : 58}" class="tick"/>` +
        (i % 2 === 0 ? `<text x="${x}" y="78" class="tl">${i * 10}${i === 10 ? " %" : ""}</text>` : "");
    }
    const raw = Math.abs(fo.p_raw - p) > 5e-4 ? `<line x1="${X(fo.p_raw)}" y1="22" x2="${X(fo.p_raw)}" y2="52" class="ghost"/><text x="${X(fo.p_raw)}" y="16" class="tl">brut</text>` : "";
    return `<svg class="gauge" viewBox="0 0 600 88" role="img" aria-label="Probabilité ${pct(p)}, intervalle épistémique à 90 % de ${pct(lo)} à ${pct(hi)}">
      <rect x="24" y="30" width="552" height="22" class="track"/>
      <rect x="${X(lo)}" y="30" width="${Math.max(1, X(hi) - X(lo))}" height="22" class="band"/>
      ${ticks}${raw}
      <path d="M${X(baseP) - 6} 64 L${X(baseP) + 6} 64 L${X(baseP)} 56 Z" class="basemark"/>
      <line x1="${X(p)}" y1="24" x2="${X(p)}" y2="58" class="needle"/><circle cx="${X(p)}" cy="41" r="5" class="needle-dot"/>
</svg>
      <div class="legend"><span><i class="lg-band"></i>Intervalle épistémique 90 % (${pct(lo)} – ${pct(hi)})</span>
      <span><i class="lg-base"></i>Taux de base de la classe (${pct(baseP)})</span>${raw ? `<span><i class="lg-ghost"></i>Avant réconciliation (${pct(fo.p_raw)})</span>` : ""}</div>`;
  }
  function calibrationChart(points) {
    const P = (v) => 36 + 248 * v, Q = (v) => 284 - 248 * v;
    let grid = "";
    for (let i = 0; i <= 5; i++) {
      const v = i / 5;
      grid += `<line x1="${P(v)}" y1="${Q(0)}" x2="${P(v)}" y2="${Q(1)}" class="grid"/><line x1="${P(0)}" y1="${Q(v)}" x2="${P(1)}" y2="${Q(v)}" class="grid"/>` +
        `<text x="${P(v)}" y="302" class="tl">${v * 100}</text><text x="28" y="${Q(v) + 4}" class="tl" text-anchor="end">${v * 100}</text>`;
    }
    const pts = points.map((c) => `<line x1="${P(c.mean_forecast)}" y1="${Q(c.ci95[0])}" x2="${P(c.mean_forecast)}" y2="${Q(c.ci95[1])}" class="ci"/>` +
      `<circle cx="${P(c.mean_forecast)}" cy="${Q(c.observed)}" r="${Math.min(9, 3 + Math.sqrt(c.n))}" class="pt"><title>${c.n} prévisions, moyenne ${pct(c.mean_forecast)}, observé ${pct(c.observed)}</title></circle>`).join("");
    return `<svg class="calib" viewBox="0 0 320 318" role="img" aria-label="Courbe de calibration">
      ${grid}<line x1="${P(0)}" y1="${Q(0)}" x2="${P(1)}" y2="${Q(1)}" class="diag"/>${pts}
      <text x="${P(0.5)}" y="316" class="tl">prévu (%)</text></svg>`;
  }

  // ----------------------------------------------------------- page Prévoir
  async function latestOpenQuestions() {
    const entries = await S.backend.entries();
    const latest = {}, resolved = {};
    for (const e of entries) {
      if (e.record_type === "forecast") latest[e.record.question.question_id] = e.record;
      if (e.record_type === "resolution") resolved[e.record.question_id] = e.record;
    }
    return { entries, latest, resolved };
  }

  const PAGES = {};
  PAGES.prevoir = async (main) => {
    const f = S.flow;
    if (f.step === "validation") return renderValidation(main);
    if (f.step === "resultat") return renderResult(main);
    const { latest, resolved } = await latestOpenQuestions();
    const open = Object.values(latest).filter((r) => !resolved[r.question.question_id]);
    main.innerHTML = `
      <section class="lead"><h1>Quelle question sur le futur voulez-vous mesurer ?</h1>
        <p>La question devient un événement résoluble, avec sa règle de résolution. Vous validez la règle, puis la probabilité est figée dans le registre avant que l'issue soit connue.</p></section>
      <div class="field"><label for="q-text">Question</label>
        <textarea id="q-text" rows="3" placeholder="Ex. : Le taux de chômage en France sera-t-il inférieur à 7 % au deuxième trimestre 2027 selon l'INSEE ?">${esc(f.text)}</textarea></div>
      <details class="opt" ${f.sources.length ? "open" : ""}><summary>Sources datées <span class="muted">(${f.sources.length})</span></summary>
        <p class="hint">Collez des extraits de documents publiés <b>avant aujourd'hui</b>. Claude n'en tire que des triplets (fait, date, source primaire). Plusieurs reprises d'une même dépêche ne comptent qu'une fois.</p>
        <div id="srcs"></div><button class="ghost-btn" id="add-src">Ajouter une source</button></details>
      <details class="opt" ${f.market.p !== "" ? "open" : ""}><summary>Prix de marché <span class="muted">(facultatif)</span></summary>
        <div class="row"><div class="field"><label for="m-p">Probabilité du marché (%)</label><input id="m-p" type="number" min="1" max="99" step="0.1" value="${esc(f.market.p)}"></div>
        <div class="field grow"><label for="m-src">Marché et date</label><input id="m-src" value="${esc(f.market.source)}" placeholder="Ex. : Manifold, 4 oct. 2026"></div></div></details>
      <details class="opt" ${f.links.length || f.supersedes ? "open" : ""}><summary>Questions liées et mises à jour</summary>
        <div class="field"><label for="sup">Mettre à jour une prévision existante</label>
          <select id="sup"><option value="">Non, nouvelle question</option>${open.map((r) => `<option value="${esc(r.forecast_id)}" ${f.supersedes === r.forecast_id ? "selected" : ""}>${esc(r.question.text.slice(0, 90))}</option>`).join("")}</select></div>
        <div class="row"><div class="field grow"><label for="lk-q">Lier à</label><select id="lk-q">${open.map((r) => `<option value="${esc(r.question.question_id)}">${esc(r.question.text.slice(0, 80))} (${pct(r.final_output.p_reconciled)})</option>`).join("")}</select></div>
          <div class="field"><label for="lk-rel">Cette question…</label><select id="lk-rel">${E.RELATIONS.map((r) => `<option value="${r}">${LABELS[r]}</option>`).join("")}</select></div>
          <div class="field"><label for="lk-hard">Contrainte</label><select id="lk-hard"><option value="1">dure (logique)</option><option value="0">molle</option></select></div>
          <div class="field end"><button class="ghost-btn" id="lk-add" ${open.length ? "" : "disabled"}>Lier</button></div></div>
        <ul class="links">${f.links.map((l, i) => `<li>${esc(LABELS[l.relation])} « ${esc((latest[l.question_id] || { question: { text: l.question_id } }).question.text.slice(0, 70))} » (${l.hard ? "dure" : "molle"}) <button class="x" data-i="${i}" aria-label="Retirer">×</button></li>`).join("")}</ul></details>
      <div class="actions">
        <button class="primary act" id="go-llm" ${S.info.llm ? "" : "disabled"}>Formaliser avec Claude</button>
        <button class="secondary act" id="go-manual">Écrire la règle moi-même</button>
        <span class="muted small" id="prog"></span></div>
      ${S.info.llm ? `<p class="hint">Claude rédige la règle, puis la réécrit ${BOOT.cfg.ingestion.n_reformulations} fois indépendamment pour mesurer sa stabilité. Comptez une à trois minutes.</p>` : ""}`;
    renderSources();
    $("#add-src").onclick = () => { syncInputs(); f.sources.push({ title: "", url: "", published_at: "", text: "", derived_from_tool_output: false }); renderSources(); };
    $("#lk-add").onclick = () => { syncInputs(); f.links.push({ question_id: $("#lk-q").value, relation: $("#lk-rel").value, hard: $("#lk-hard").value === "1" }); show("prevoir"); };
    $$(".links .x").forEach((b) => (b.onclick = () => { syncInputs(); f.links.splice(Number(b.dataset.i), 1); show("prevoir"); }));
    $("#go-llm").onclick = () => guard(async () => {
      syncInputs();
      if (!f.text.trim()) throw new Error("Écrivez d'abord la question.");
      const d = await S.backend.formalize(f.text.trim(), cleanSources(), (k, n) => setStatus(`Formalisation : ${k}/${n} appels à Claude terminés…`));
      f.draftId = d.draft_id; f.draft = d.draft; f.original = JSON.parse(JSON.stringify(d.draft)); f.manual = false; f.step = "validation";
      await show("prevoir");
    }, "Claude formalise la question…");
    $("#go-manual").onclick = () => {
      syncInputs();
      f.manual = true; f.draftId = null;
      f.draft = { text: f.text.trim(), resolution_rule: "", resolution_source: "", source_kind: "official_statistics", resolution_deadline: "",
        horizon: "", ambiguity_policy: "cancel", domain: "other", test_scenarios: [], self_negating_risk: false, self_negating_reason: "" };
      f.original = JSON.parse(JSON.stringify(f.draft)); f.step = "validation"; show("prevoir");
    };
  };
  function renderSources() {
    const f = S.flow;
    $("#srcs").innerHTML = f.sources.map((s, i) => `<fieldset class="src"><legend>Source ${i + 1}</legend>
      <div class="row"><div class="field grow"><label for="s-t-${i}">Titre</label><input id="s-t-${i}" value="${esc(s.title)}"></div>
      <div class="field"><label for="s-d-${i}">Publié le</label><input id="s-d-${i}" type="date" value="${esc(s.published_at)}"></div></div>
      <div class="field"><label for="s-u-${i}">Adresse</label><input id="s-u-${i}" value="${esc(s.url)}" placeholder="https://"></div>
      <div class="field"><label for="s-x-${i}">Extrait</label><textarea id="s-x-${i}" rows="4" maxlength="40000">${esc(s.text)}</textarea></div>
      <label class="check"><input type="checkbox" id="s-r-${i}" ${s.derived_from_tool_output ? "checked" : ""}> Ce texte reprend une prévision publiée par cet outil (exclu du calcul)</label>
      <button class="x" data-i="${i}" aria-label="Retirer la source">Retirer</button></fieldset>`).join("");
    $$("#srcs .x").forEach((b) => (b.onclick = () => { syncInputs(); f.sources.splice(Number(b.dataset.i), 1); renderSources(); }));
  }
  function syncInputs() {
    const f = S.flow;
    if ($("#q-text")) f.text = $("#q-text").value;
    f.sources = f.sources.map((s, i) => $(`#s-t-${i}`) ? { title: $(`#s-t-${i}`).value, url: $(`#s-u-${i}`).value, published_at: $(`#s-d-${i}`).value,
      text: $(`#s-x-${i}`).value, derived_from_tool_output: $(`#s-r-${i}`).checked } : s);
    if ($("#m-p")) f.market = { p: $("#m-p").value, source: $("#m-src").value };
    if ($("#sup")) f.supersedes = $("#sup").value;
  }
  const cleanSources = () => S.flow.sources.filter((s) => s.text.trim());

  function renderValidation(main) {
    const f = S.flow, d = f.draft, manual = f.manual;
    const scen = d.test_scenarios || [];
    const cols = [["Règle retenue", d.rule_verdicts || []]].concat((d.reformulations || []).map((r, i) => [`Reformulation ${i + 1}`, r.verdicts]))
      .concat(d.second_model_reformulation ? [["Second modèle", d.second_model_reformulation.verdicts]] : []);
    const vcls = (v) => (v === "YES" ? "v-yes" : v === "NO" ? "v-no" : "v-amb");
    const vtxt = (v) => (v === "YES" ? "OUI" : v === "NO" ? "NON" : "?");
    main.innerHTML = `
      <section class="lead"><h1>Validez la règle de résolution</h1>
        <p>Une fois la prévision enregistrée, cette règle est gelée : elle seule décidera de l'issue. Corrigez-la si besoin.</p></section>
      ${manual ? "" : `<div class="panel stab"><div class="kv"><span>Stabilité de la règle</span><b>${pct(d.resolution_stability_score)}</b></div>
        <div class="kv"><span>Accord avec un second modèle</span><b>${d.resolution_inter_model_agreement === null ? "non mesuré" : pct(d.resolution_inter_model_agreement)}</b></div>
        <p class="hint">Mesure : accord des verdicts de ${cols.length} règles écrites indépendamment, sur ${scen.length} scénarios de test. Un accord élevé montre que la règle est stable. Il ne prouve pas qu'elle est juste : c'est votre validation qui en répond.</p>
        <div class="scroll"><table class="verdicts"><thead><tr><th>Scénario</th>${cols.map((c) => `<th>${esc(c[0])}</th>`).join("")}</tr></thead>
        <tbody>${scen.map((s, i) => `<tr><td>${esc(s)}</td>${cols.map((c) => `<td class="${vcls(c[1][i])}">${vtxt(c[1][i])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>`}
      <div class="field"><label for="v-text">Question formalisée</label><textarea id="v-text" rows="2">${esc(d.text)}</textarea></div>
      <div class="field"><label for="v-rule">Règle de résolution (conditions du OUI et du NON)</label><textarea id="v-rule" rows="5">${esc(d.resolution_rule)}</textarea></div>
      <div class="row"><div class="field grow"><label for="v-src">Source de résolution</label><input id="v-src" value="${esc(d.resolution_source)}"></div>
        <div class="field"><label for="v-kind">Nature de la source</label><select id="v-kind">${SOURCE_KINDS.map((k) => `<option ${d.source_kind === k ? "selected" : ""}>${k}</option>`).join("")}</select></div></div>
      <div class="row"><div class="field"><label for="v-dl">Échéance</label><input id="v-dl" type="date" value="${esc((d.resolution_deadline || "").slice(0, 10))}"></div>
        <div class="field"><label for="v-hz">Horizon (ISO 8601)</label><input id="v-hz" value="${esc(d.horizon)}" placeholder="P90D"></div>
        <div class="field"><label for="v-pol">Si la définition doit changer</label><select id="v-pol">${E.AMBIGUITY_POLICIES.map((k) => `<option value="${k}" ${d.ambiguity_policy === k ? "selected" : ""}>${LABELS[k]}</option>`).join("")}</select></div>
        <div class="field"><label for="v-dom">Domaine</label><select id="v-dom">${DOMAINS.map((k) => `<option ${d.domain === k ? "selected" : ""}>${k}</option>`).join("")}</select></div></div>
      ${manual ? `<label class="check"><input type="checkbox" id="v-neg" ${d.self_negating_risk ? "checked" : ""}> Publier cette prévision pourrait influencer l'événement</label>` : ""}
      ${d.definition ? `<details class="opt"><summary>Définitions</summary><p>${esc(d.definition)}</p></details>` : ""}
      ${(d.subquestions || []).length ? `<details class="opt" open><summary>Décomposition proposée (${d.subquestions.length} sous-questions)</summary><ul class="subq">${d.subquestions.map((s) => `<li>La question ${esc(LABELS[s.relation])} : « ${esc(s.text)} » <span class="chip ${s.hard ? "" : "warn"}">${s.hard ? "contrainte dure" : "contrainte molle"}</span></li>`).join("")}</ul>
        <p class="hint">Chaque sous-question peut être prévue séparément puis liée à celle-ci (onglet Prévoir, « Questions liées »).</p></details>` : ""}
      ${!manual && d.self_negating_risk ? `<div class="alert">Risque d'autoréalisation ou d'autoréfutation : ${esc(d.self_negating_reason)}</div>` : ""}
      <div class="actions"><button class="primary act" id="v-ok">Valider la règle et figer la prévision</button>
        <button class="secondary act" id="v-skip">Figer sans valider</button><button class="ghost-btn" id="v-back">Revenir</button></div>
      <p class="hint">« Figer sans valider » enregistre la prévision avec le statut « règle non validée », affiché partout.</p>`;
    $("#v-back").onclick = () => { S.flow.step = "saisie"; show("prevoir"); };
    $("#v-ok").onclick = () => commitFlow(true);
    $("#v-skip").onclick = () => commitFlow(false);
  }
  function readValidation() {
    return { text: $("#v-text").value.trim(), resolution_rule: $("#v-rule").value.trim(), resolution_source: $("#v-src").value.trim(),
      source_kind: $("#v-kind").value, resolution_deadline: $("#v-dl").value, horizon: $("#v-hz").value.trim().toUpperCase(),
      ambiguity_policy: $("#v-pol").value, domain: $("#v-dom").value, self_negating_risk: $("#v-neg") ? $("#v-neg").checked : undefined };
  }
  function commitFlow(validated) {
    guard(async () => {
      const f = S.flow, vals = readValidation();
      const missing = [["resolution_rule", "la règle"], ["resolution_source", "la source"], ["resolution_deadline", "l'échéance"], ["horizon", "l'horizon"]]
        .filter(([k]) => !vals[k]).map(([, l]) => l);
      if (missing.length) throw new E.ValidationError("Enregistrement refusé : il manque " + missing.join(", ") + ".");
      E.isoDurationDays(vals.horizon);
      if (f.manual) {
        const d = await S.backend.manual(Object.assign({}, vals, { resolution_human_validated: validated, self_negating_risk: !!vals.self_negating_risk }));
        f.draftId = d.draft_id;
      } else {
        const edits = {};
        for (const k of ["text", "resolution_rule", "resolution_source", "resolution_deadline", "horizon", "ambiguity_policy"])
          if (vals[k] !== (k === "resolution_deadline" ? (f.original[k] || "").slice(0, 10) : f.original[k])) edits[k] = vals[k];
        await S.backend.validate(f.draftId, edits, validated, (k, n) => setStatus(`Nouvelle mesure de stabilité : ${k}/${n}…`));
      }
      const mp = parseFloat(String(f.market.p).replace(",", "."));
      const opts = { market: Number.isFinite(mp) ? { p: mp / 100, source: f.market.source } : null, links: f.links, supersedes: f.supersedes || null };
      const rec = await S.backend.commit(f.draftId, opts, (k, n) => setStatus(`Extraction des sources : ${k}/${n}…`));
      S.flow = { step: "resultat", record: rec, sources: [], links: [], market: { p: "", source: "" }, supersedes: "", text: "" };
      await show("prevoir");
    }, "Calcul et enregistrement…");
  }
  function renderResult(main) {
    const rec = S.flow.record;
    main.innerHTML = `<div class="saved">Prévision figée au registre le ${esc(fmtDate(rec.timestamp_utc))}</div>${recordView(rec)}
      <div class="actions"><button class="primary" id="again">Nouvelle question</button></div>`;
    $("#again").onclick = () => { S.flow.step = "saisie"; show("prevoir"); };
    bindRecord(main);
  }

  function recordView(rec) {
    const q = rec.question, mp = rec.models_pipeline, fo = rec.final_output;
    const baseComp = mp.component_forecasts.find((c) => c.component === "base_rate");
    const anc = {};
    for (const s of rec.information_lineage || []) (anc[s.primary_ancestor_id] = anc[s.primary_ancestor_id] || []).push(s);
    const factor = (c) => `<li><b>${esc((c.facts || [])[0] || c.primary_ancestor_id)}</b><span class="muted"> ${esc(c.evidence_type)}, ${c.n_sources} source(s), ${c.logit_contribution > 0 ? "+" : ""}${num(c.logit_contribution)} en logit</span></li>`;
    return `<article class="record" data-fid="${esc(rec.forecast_id)}">
      <h2 class="qtitle">${esc(q.text)}</h2>
      <div class="status-row">${q.resolution_human_validated ? `<span class="chip ok">Règle validée par l'utilisateur</span>` : `<span class="chip warn">Règle non validée par un humain</span>`}
        ${rec.shadow ? `<span class="chip">Mode fantôme · ${esc(rec.shadow.platform)}</span>` : ""}${rec.supersedes_forecast_id ? `<span class="chip">Mise à jour d'une prévision antérieure</span>` : ""}
        <span class="chip">Classe ${esc(mp.class_key)} · ${esc(mp.domain)}</span></div>
      ${fo.self_negating_risk ? `<div class="alert">La publication de cette prévision peut influencer l'événement. Ce risque est signalé, il n'est pas modélisé.${fo.self_negating_reason ? " " + esc(fo.self_negating_reason) : ""}</div>` : ""}
      <div class="headline"><div class="pbig">${pct(fo.p_reconciled)}</div>
        <div class="pmeta"><div>probabilité que la réponse soit <b>OUI</b></div>
        <div class="muted">w affiché : <b>${pct(fo.w_displayed)}</b> du poids va aux modèles informés ; le reste revient au taux de base de la classe.</div></div></div>
      ${gauge(fo, baseComp ? baseComp.p : mp.base_rate.p)}
      <div class="grid2">
        <section><h3>Composants et poids du stack</h3><table class="tbl"><thead><tr><th>Composant</th><th>Prévision</th><th>Poids appliqué</th><th>Poids appris (classe)</th></tr></thead>
          <tbody>${mp.component_forecasts.map((c) => `<tr><td>${esc(LABELS[c.component] || c.component)}</td><td class="n">${pct(c.p)}</td><td class="n">${pct(mp.stacking_weights[c.component])}</td><td class="n">${pct((mp.stacking_weights_class || {})[c.component])}</td></tr>`).join("")}
          <tr class="sum"><td>Prévision brute</td><td class="n">${pct(fo.p_raw)}</td><td colspan="2" class="muted">${fo.p_raw !== fo.p_reconciled ? "réconciliée à " + pct(fo.p_reconciled) : "aucune contrainte logique active"}</td></tr></tbody></table>
          <p class="hint">Résolubilité ${pct(q.resolvability_score)} : elle réduit d'autant les poids informatifs (un seul rétrécissement). Historique utilisé : ${num(mp.stacking_diagnostics.n_eff_class)} résolutions effectives dans la classe, ${num(mp.stacking_diagnostics.n_eff_global)} au total.</p></section>
        <section><h3>Incertitude</h3><dl class="dl">
          <dt>Bêta épistémique</dt><dd>α = ${num(fo.epistemic_uncertainty.alpha)}, β = ${num(fo.epistemic_uncertainty.beta)}</dd>
          <dt>Variance « biais systématique »</dt><dd>${num(mp.systematic_bias_variance)} (logit, plancher ${num(mp.systematic_bias_detail.floor)})</dd>
          <dt>Ancêtres primaires distincts</dt><dd>${mp.lineage_fusion.n_ancestors}</dd>
          <dt>Taux de base</dt><dd>${pct(mp.base_rate.p)} sur ${mp.base_rate.n.domain} cas du domaine, ${mp.base_rate.n.global} au total</dd></dl></section></div>
      <div class="grid2"><section><h3>Facteurs haussiers</h3><ul class="factors up">${fo.factors.bullish.map(factor).join("") || "<li class='muted'>Aucun</li>"}</ul></section>
        <section><h3>Facteurs baissiers</h3><ul class="factors down">${fo.factors.bearish.map(factor).join("") || "<li class='muted'>Aucun</li>"}</ul></section></div>
      <section><h3>Sources et lignage</h3>${Object.keys(anc).length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Ancêtre primaire</th><th>Fait</th><th>Sens</th><th>Cohérence</th><th>Sources</th></tr></thead><tbody>
        ${Object.entries(anc).map(([a, ss]) => `<tr class="${ss.every((s) => s.derived_from_tool_output) ? "excluded" : ""}"><td class="mono">${esc(a)}<br><span class="muted">${esc(ss[0].primary_source || "")}</span></td><td>${esc(ss[0].fact)}${ss.some((s) => s.derived_from_tool_output) ? ` <span class="chip bad">issu de l'outil, exclu</span>` : ""}</td><td>${esc(LABELS[ss[0].direction] || ss[0].direction)}</td><td class="n">${pct(ss[0].self_consistency_score)}</td><td>${ss.map((s) => esc(s.document_title || s.source_id)).join("<br>")}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">Aucune source : la prévision repose sur le taux de base${mp.component_forecasts.some((c) => c.component === "market_anchor") ? " et le marché" : ""}.</p>`}</section>
      <section><h3>Règle de résolution gelée</h3><p class="rule">${esc(q.resolution_rule)}</p>
        <dl class="dl"><dt>Source</dt><dd>${esc(q.resolution_source)}</dd><dt>Échéance</dt><dd>${esc(q.resolution_deadline)}</dd><dt>Politique d'ambiguïté</dt><dd>${esc(LABELS[q.ambiguity_policy])}</dd>
        <dt>Stabilité / inter-modèles</dt><dd>${pct(q.resolution_stability_score)} / ${q.resolution_inter_model_agreement === null ? "non mesuré" : pct(q.resolution_inter_model_agreement)}</dd></dl></section>
      <section><h3>Hypothèses</h3><ul>${(rec.assumptions || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ul></section>
      <details class="opt"><summary>Reproductibilité et enregistrement complet</summary><dl class="dl mono small">
        <dt>forecast_id</dt><dd>${esc(rec.forecast_id)}</dd><dt>hash_previous</dt><dd>${esc(rec.hash_previous)}</dd>
        <dt>Snapshots LLM</dt><dd>${esc(rec.reproducibility.llm_snapshots_hash)}</dd><dt>Prompts</dt><dd>${esc(rec.reproducibility.prompt_hash)}</dd>
        <dt>Modèle</dt><dd>${esc(rec.reproducibility.model_id)}</dd><dt>Code</dt><dd>${esc(rec.reproducibility.code_commit)} (${esc(rec.reproducibility.engine_runtime)})</dd>
        <dt>Config. statistique</dt><dd>${esc(rec.reproducibility.statistical_config_version)}</dd></dl>
        <div class="actions"><button class="secondary replay">Rejouer depuis les snapshots</button><span class="small replay-out"></span></div>
        <pre class="json">${esc(JSON.stringify(rec, null, 1))}</pre></details></article>`;
  }
  function bindRecord(scope) {
    $$(".record", scope).forEach((el) => {
      const b = $(".replay", el);
      if (b) b.onclick = () => guard(async () => {
        const r = await S.backend.replay(el.dataset.fid);
        $(".replay-out", el).innerHTML = r.max_abs_diff < 1e-9 ? `<span class="chip ok">Rejeu identique (écart ${r.max_abs_diff.toExponential(1)})</span>` :
          `<span class="chip bad">Écart au rejeu : ${r.max_abs_diff.toExponential(2)}</span>`;
      }, "Rejeu sans appel à Claude…");
    });
  }

  // ------------------------------------------------------------- registre
  PAGES.registre = async (main) => {
    const { entries, resolved } = await latestOpenQuestions();
    const rows = entries.filter((e) => e.record_type !== "snapshot").slice().reverse();
    main.innerHTML = `<section class="lead"><h1>Registre</h1><p>${entries.length} entrées chaînées par hash. Une prévision ne se modifie jamais : une mise à jour ajoute un nouvel enregistrement.</p></section>
      <div class="actions"><button class="secondary act" id="verify">Vérifier la chaîne et les ancrages</button>
        <button class="secondary act" id="anchor" ${entries.length ? "" : "disabled"}>Calculer une racine de Merkle</button>
        <button class="ghost-btn" id="export" ${entries.length ? "" : "disabled"}>Exporter le registre (JSON)</button></div>
      <div id="vres"></div><div id="exp"></div>
      ${rows.length ? `<ol class="ledger">${rows.map((e) => ledgerRow(e, resolved)).join("")}</ol>` : `<div class="empty"><p><b>Le registre est vide.</b></p><p>Votre première prévision apparaîtra ici, avec son hash et celui de l'entrée précédente.</p><button class="primary" id="to-prev">Poser une question</button></div>`}`;
    if ($("#to-prev")) $("#to-prev").onclick = () => show("prevoir");
    $("#verify").onclick = () => guard(async () => {
      const v = await S.backend.verify();
      $("#vres").innerHTML = `<div class="panel ${v.ok ? "okp" : "badp"}"><b>${v.ok ? "Chaîne intègre" : "Chaîne altérée"}</b> : ${v.n_entries} entrées, tête ${short(v.head)}.
        ${v.anchors.length ? `${v.anchors.filter((a) => a.ok).length}/${v.anchors.length} racines de Merkle ancrées concordent.` : "Aucune racine ancrée."}
        ${v.problems.length ? `<ul>${v.problems.map((p) => `<li>Entrée ${p.seq} : ${esc(p.problem)}</li>`).join("")}</ul>` : ""}</div>`;
    }, "Vérification…");
    $("#anchor").onclick = () => guard(async () => {
      const a = await S.backend.anchor();
      $("#vres").innerHTML = `<div class="panel"><b>Racine de Merkle</b> couvrant ${a.leaf_count} feuilles (entrées 0 à ${a.covered_seq_max}) :
        <code class="sel">${esc(a.merkle_root)}</code><p class="hint">Pour un ancrage externe, horodatez cette racine (RFC 3161, commande <code>python -m oracle_calibre.cli anchor --method rfc3161</code>) ou publiez-la dans un dépôt public. Sans ancrage externe, l'opérateur pourrait antidater une entrée.</p></div>`;
      await show("registre");
    }, "Calcul de la racine…");
    $("#export").onclick = async () => {
      const txt = JSON.stringify(await S.backend.entries());
      $("#exp").innerHTML = `<div class="panel"><p>Copiez ce JSON puis vérifiez-le hors ligne avec <code>python -m oracle_calibre.cli verify-file registre.json</code>.</p>
        <button class="secondary" id="copy">Copier</button><textarea id="exp-txt" rows="6" readonly>${esc(txt)}</textarea></div>`;
      $("#copy").onclick = async () => { try { await navigator.clipboard.writeText(txt); setStatus("Registre copié."); } catch (e) { $("#exp-txt").select(); setStatus("Copie refusée : le texte est sélectionné, utilisez Ctrl+C."); } };
    };
    $$(".ledger .open").forEach((b) => (b.onclick = async () => {
      const e = entries.find((x) => String(x.seq) === b.dataset.seq);
      const box = $(`#d-${e.seq}`);
      if (box.innerHTML) { box.innerHTML = ""; return; }
      box.innerHTML = recordView(e.record); bindRecord(box);
    }));
  };
  function ledgerRow(e, resolved) {
    const r = e.record;
    if (e.record_type === "forecast") {
      const res = resolved[r.question.question_id];
      const out = res ? (res.outcome === "cancelled" ? `<span class="chip">annulée</span>` : `<span class="chip ${res.outcome === 1 ? "ok" : "bad"}">résolue ${res.outcome === 1 ? "OUI" : "NON"}</span>`) : `<span class="chip">ouverte</span>`;
      return `<li class="le"><div class="le-h"><span class="seq">#${e.seq}</span><span class="p">${pct(r.final_output.p_reconciled)}</span>
        <span class="t">${esc(r.question.text)}</span>${out}${r.question.resolution_human_validated ? "" : `<span class="chip warn">règle non validée</span>`}
        <button class="ghost-btn open" data-seq="${e.seq}">Détail</button></div>
        <div class="le-m mono small">${esc(fmtDate(r.timestamp_utc))} · hash ${short(e.entry_hash)} · précédent ${short(r.hash_previous)}</div><div id="d-${e.seq}"></div></li>`;
    }
    let lbl = e.record_type;
    if (e.record_type === "resolution") lbl = `Résolution : ${r.outcome === "cancelled" ? "annulée" : r.outcome === 1 ? "OUI" : "NON"}`;
    else if (e.record_type === "anchor") lbl = `Racine de Merkle ${short(r.merkle_root)} (${r.leaf_count} feuilles)`;
    else if (e.record_type === "reference_batch") lbl = `Lot de référence : ${(r.items || []).length} issues (${esc(r.source)})`;
    return `<li class="le minor"><div class="le-h"><span class="seq">#${e.seq}</span><span class="t">${lbl}</span></div>
      <div class="le-m mono small">${esc(fmtDate(r.timestamp_utc))} · hash ${short(e.entry_hash)}</div></li>`;
  }

  // ------------------------------------------------------------ résolution
  PAGES.resolution = async (main) => {
    const { latest, resolved } = await latestOpenQuestions();
    const open = Object.values(latest).filter((r) => !resolved[r.question.question_id])
      .sort((a, b) => String(a.question.resolution_deadline).localeCompare(String(b.question.resolution_deadline)));
    const today = new Date().toISOString().slice(0, 10);
    main.innerHTML = `<section class="lead"><h1>Résolution</h1><p>Appliquez la règle gelée. La politique d'ambiguïté préenregistrée s'applique si la définition a dû changer.</p></section>
      ${open.length ? open.map((r) => `<div class="panel res" data-q="${esc(r.question.question_id)}">
        <div class="res-h"><b>${esc(r.question.text)}</b>${String(r.question.resolution_deadline) <= today ? `<span class="chip warn">échéance passée</span>` : `<span class="chip">échéance ${esc(r.question.resolution_deadline)}</span>`}</div>
        <p class="rule small">${esc(r.question.resolution_rule)}</p>
        <div class="row"><div class="field"><label>Issue</label><select class="r-out"><option value="1">OUI</option><option value="0">NON</option><option value="cancelled">Annulée</option></select></div>
          <div class="field grow"><label>Source effective</label><input class="r-src" value="${esc(r.question.resolution_source)}"></div>
          <div class="field"><label>Clarté de la résolution</label><input class="r-q" type="range" min="0" max="1" step="0.05" value="0.9"><span class="small r-qv">90 %</span></div>
          <div class="field"><label>Politique appliquée</label><select class="r-pol">${E.AMBIGUITY_POLICIES.map((k) => `<option value="${k}" ${k === r.question.ambiguity_policy ? "selected" : ""}>${LABELS[k]}</option>`).join("")}</select></div>
          <div class="field end"><button class="primary act r-go">Enregistrer</button></div></div></div>`).join("") :
        `<div class="empty"><p><b>Aucune question ouverte.</b></p><p>Les questions prévues et non résolues apparaissent ici, triées par échéance.</p></div>`}
      <p class="hint">La clarté de la résolution (resolvability_score_post) pondère cette résolution dans l'apprentissage des poids : une résolution contestée compte peu.</p>`;
    $$(".res").forEach((el) => {
      $(".r-q", el).oninput = () => ($(".r-qv", el).textContent = Math.round(100 * $(".r-q", el).value) + " %");
      $(".r-go", el).onclick = () => guard(async () => {
        const o = $(".r-out", el).value;
        await S.backend.resolve({ question_id: el.dataset.q, outcome: o === "cancelled" ? "cancelled" : Number(o), effective_source: $(".r-src", el).value,
          resolvability_score_post: Number($(".r-q", el).value), ambiguity_policy_applied: $(".r-pol", el).value });
        await show("resolution"); setStatus("Résolution enregistrée.");
      }, "Enregistrement…");
    });
  };

  // ------------------------------------------------------------ calibration
  function demoOffer() {
    return S.ns === "demo" ? `<div class="actions"><button class="secondary act" id="demo">Générer 40 questions synthétiques résolues</button><span class="small muted" id="demo-p"></span></div>
      <p class="hint">Données fictives, tirées au hasard, pour voir les tableaux fonctionner. Elles restent dans le bac à sable.</p>` : "";
  }
  function bindDemo(page) {
    if (!$("#demo")) return;
    $("#demo").onclick = () => guard(async () => {
      await S.backend.demo(40, (k, n) => setStatus(`Historique synthétique : ${k}/${n}…`));
      await show(page);
    }, "Génération de l'historique synthétique…");
  }
  PAGES.calibration = async (main) => {
    const d = await S.backend.dashboard();
    const classes = Object.keys(d).filter((k) => k !== "toutes");
    if (!d.toutes || !d.toutes.n) {
      main.innerHTML = `<section class="lead"><h1>Calibration</h1></section><div class="empty"><p><b>Aucune question résolue pour l'instant.</b></p>
        <p>Parmi les événements annoncés à 70 %, environ 70 % devraient se réaliser. Ce tableau le vérifiera classe par classe dès les premières résolutions.</p></div>${demoOffer()}`;
      return bindDemo("calibration");
    }
    const block = (name, b) => {
      const lg = b.log, br = b.brier, vs = lg.vs_base_rate;
      const verdict = vs.ci95 ? (vs.ci95[1] < 0 ? "ok" : vs.ci95[0] > 0 ? "bad" : "") : "";
      return `<section class="panel cls"><h3>${name === "toutes" ? "Toutes classes" : "Classe " + esc(name)} <span class="muted">· ${b.n} questions</span></h3>
        <div class="grid2"><div>${calibrationChart(b.calibration)}</div><div>
        <dl class="dl"><dt>Brier moyen</dt><dd>${num(br.mean_score)}</dd><dt>Log-score moyen (perte)</dt><dd>${num(lg.mean_score)}</dd>
          <dt>Murphy</dt><dd>fiabilité ${num(b.murphy.reliability)} · résolution ${num(b.murphy.resolution)} · incertitude ${num(b.murphy.uncertainty)}</dd>
          <dt>Écart apparié au taux de base (log)</dt><dd class="${verdict}">${num(vs.mean_diff)} [${vs.ci95 ? num(vs.ci95[0]) + " ; " + num(vs.ci95[1]) : "n insuffisant"}]
            ${vs.diebold_mariano ? `<br><span class="muted">Diebold-Mariano : stat. ${num(vs.diebold_mariano.statistic)}, p = ${num(vs.diebold_mariano.p_value)}</span>` : ""}</dd>
          <dt>Valeur des mises à jour</dt><dd>${lg.update_value.n ? `${num(lg.update_value.mean_diff_vs_t0)} vs prévision gelée à t0 (${lg.update_value.n} questions)` : "aucune mise à jour résolue"}</dd></dl>
        <p class="hint">Négatif = meilleur que le taux de base. Intervalle à 95 % par bootstrap par blocs temporels, sur la différence appariée question par question.</p></div></div></section>`;
    };
    main.innerHTML = `<section class="lead"><h1>Calibration</h1><p>Points : fréquence observée par tranche de probabilité, avec intervalle de Jeffreys à 95 %. La diagonale est la calibration parfaite.</p></section>
      ${block("toutes", d.toutes)}${classes.length > 1 ? classes.map((c) => block(c, d[c])).join("") : ""}`;
  };

  PAGES.tournoi = async (main) => {
    const t = await S.backend.tournament();
    if (!t.summary.stack_officiel.n) {
      main.innerHTML = `<section class="lead"><h1>Tournoi des méthodes</h1></section><div class="empty"><p><b>Aucune question résolue.</b></p>
        <p>Chaque méthode est recalculée à partir des composants stockés. Elles sont comparées sur les mêmes questions, prévues aux mêmes instants.</p></div>${demoOffer()}`;
      return bindDemo("tournoi");
    }
    const best = Math.min(...Object.values(t.summary).map((s) => s.mean_loss));
    main.innerHTML = `<section class="lead"><h1>Tournoi des méthodes</h1><p>Perte logarithmique moyenne (plus bas = meilleur) et Model Confidence Set au seuil ${t.mcs.alpha || BOOT.cfg.evaluation.mcs_alpha}.</p></section>
      <div class="panel ${t.official_engine ? "okp" : "badp"}">${esc(t.official_engine_note)}${t.mcs.note ? ` <span class="muted">(${esc(t.mcs.note)})</span>` : ""}</div>
      <table class="tbl"><thead><tr><th>Méthode</th><th>Perte log moyenne</th><th>n</th><th>Dans le MCS</th></tr></thead><tbody>
      ${Object.entries(t.summary).map(([m, s]) => `<tr class="${s.mean_loss === best ? "best" : ""}"><td>${esc(LABELS[m] || m)}</td><td class="n">${num(s.mean_loss)}</td><td class="n">${s.n}</td>
        <td>${t.mcs.mcs.includes(m) ? `<span class="chip ok">oui</span>` : (() => { const el = t.mcs.eliminated.find((x) => x.method === m); return `<span class="chip">éliminée${el ? `, p = ${num(el.p_value)}` : ""}</span>`; })()}</td></tr>`).join("")}
      </tbody></table>
      ${t.market_subset.n ? `<p class="hint">Sous-ensemble avec marché (${t.market_subset.n} questions) : marché seul − officiel = ${num(t.market_subset.mean_diff_market_minus_official)}.</p>` : ""}
      <p class="hint">Le MCS corrige les comparaisons multiples : une méthode n'est éliminée que si elle est significativement moins bonne que les autres.</p>`;
  };

  // -------------------------------------------------------------- fantôme
  PAGES.fantome = async (main) => {
    const r = await S.backend.shadowReport();
    main.innerHTML = `<section class="lead"><h1>Mode fantôme</h1><p>Prévoir les questions ouvertes de Manifold ou Metaculus, puis comparer à la prévision communautaire sur les mêmes questions.</p></section>
      <div class="panel"><div class="row"><div class="field"><label for="sh-pf">Plateforme</label><select id="sh-pf"><option value="manifold">Manifold</option><option value="metaculus">Metaculus</option></select></div></div>
        <div class="field"><label for="sh-json">Réponse JSON de l'API</label><textarea id="sh-json" rows="5" placeholder='Manifold : https://api.manifold.markets/v0/search-markets?filter=open&amp;contractType=BINARY'></textarea></div>
        <p class="hint">La page ne peut pas interroger ces sites elle-même. Ouvrez l'adresse de l'API dans un onglet, puis collez la réponse ici. Les questions déjà résolues dans le JSON servent à clore les prévisions fantômes ouvertes.</p>
        <div class="actions"><button class="primary act" id="sh-go">Importer</button></div></div>
      <section class="panel"><h3>Comparaison à la communauté</h3>${r.n_resolved ? `<dl class="dl"><dt>Questions résolues</dt><dd>${r.n_resolved} (ouvertes : ${r.n_open})</dd>
        <dt>Log-score outil − communauté</dt><dd>${num(r.mean_log_diff_tool_minus_community)} [${r.ci95 ? num(r.ci95[0]) + " ; " + num(r.ci95[1]) : "n insuffisant"}]</dd></dl>` :
        `<p class="muted">${r.n_open} prévisions fantômes ouvertes, aucune résolue pour l'instant.</p>`}
        <p class="hint">${esc(r.scope_note)} Le prix communautaire n'entre jamais dans le calcul de l'outil.</p></section>
      <details class="opt"><summary>Importer des issues de référence (taux de base)</summary>
        <p class="hint">Liste JSON de questions résolues : <code>[{"question_type":"binary","horizon_days":90,"domain":"economics","outcome":1,"resolved_at":"2026-03-01"}]</code>. Elles nourrissent uniquement les taux de base.</p>
        <textarea id="ref-json" rows="4"></textarea><div class="actions"><button class="secondary act" id="ref-go">Inscrire au registre</button></div></details>`;
    $("#sh-go").onclick = () => guard(async () => {
      let data = JSON.parse($("#sh-json").value);
      if (data && !Array.isArray(data)) data = data.results || data.markets || [data];
      const out = await S.backend.importShadow($("#sh-pf").value, data);
      setStatus(`${out.created} prévisions fantômes créées, ${out.resolved} résolues, sur ${out.seen} questions lues.`);
      await show("fantome");
    }, "Import…");
    $("#ref-go").onclick = () => guard(async () => {
      const items = JSON.parse($("#ref-json").value);
      await S.backend.importReference(items, "import manuel");
      setStatus(`${items.length} issues de référence inscrites.`);
    }, "Inscription…");
  };

  PAGES.apropos = async (main) => {
    const c = BOOT.cfg;
    main.innerHTML = `<section class="lead"><h1>Méthode</h1><p>Le LLM formalise et extrait. Il ne produit jamais de probabilité. Tout le calcul est déterministe et rejouable.</p></section>
      <div class="grid2"><section class="panel"><h3>Ce que fait chaque étape</h3><ol class="steps">
        <li><b>Formalisation</b> : règle, source, échéance, politique d'ambiguïté ; ${c.ingestion.n_reformulations} reformulations indépendantes testées sur 5 scénarios.</li>
        <li><b>Taux de base</b> : par classe (type × horizon × domaine), estimé uniquement sur les résolutions déjà inscrites.</li>
        <li><b>Sources</b> : triplets fait/date/source primaire, extraits ${c.ingestion.n_extractions} fois ; les copies d'une même information comptent une fois.</li>
        <li><b>Stacking</b> : poids appris sur le log-score des prévisions passées, prior de Dirichlet « ${esc(c.dirichlet.id)} », demi-vie ${c.stacking.half_life_days} jours, détecteur de rupture.</li>
        <li><b>Réconciliation</b> : projection de Bregman (KL) sur les contraintes logiques dures ; p brut et p réconcilié sont stockés.</li>
        <li><b>Registre</b> : ajout seul, chaîné par SHA-256, racine de Merkle à ancrer à l'extérieur.</li></ol></section>
      <section class="panel"><h3>Limites connues</h3><ul>
        <li>Sans historique résolu, w reste proche de 0 : l'outil annonce le taux de base, presque 50 %. C'est voulu.</li>
        <li>Les chocs structurels inédits ne sont couverts que par l'élargissement de l'incertitude.</li>
        <li>La validation prospective est lente ; le mode fantôme ne vaut que pour les classes de questions des plateformes.</li>
        <li>Sans validation humaine, une règle stable peut rester fausse.</li>
        <li>Dans cette version hébergée, l'ancrage externe se fait hors de la page (racine à publier) et Claude ne cherche pas de sources lui-même.</li></ul></section></div>
      <section class="panel"><h3>Version</h3><dl class="dl mono small"><dt>Code</dt><dd>${esc(BOOT.build.code_commit)} (${esc(BOOT.build.code_version)})</dd>
        <dt>Empreinte du moteur</dt><dd>${esc(BOOT.build.dependency_lock_hash)}</dd><dt>Configuration</dt><dd>${esc(c.statistical_config_version)} · ${esc(BOOT.build.config_hash)}</dd></dl>
        <details><summary>Configuration préenregistrée complète</summary><pre class="json">${esc(JSON.stringify(c, null, 1))}</pre></details></section>`;
  };

  window.addEventListener("hashchange", () => { const h = location.hash.replace("#", ""); if (TABS.some((t) => t[0] === h) && h !== S.tab) show(h); });
  boot().catch((e) => { console.error(e); $("#app").innerHTML = `<p class="err">Démarrage impossible : ${esc(e.message || e)}</p>`; });
})();
