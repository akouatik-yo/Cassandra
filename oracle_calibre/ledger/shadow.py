"""Mode fantôme : prévoir des questions ouvertes de plateformes publiques
(Manifold, Metaculus) et se mesurer à la prévision communautaire.

Les réponses brutes des API sont normalisées ici ; le téléchargement se fait
hors du moteur (CLI `oracle shadow-fetch`, ou fichier JSON importé dans
l'interface). La prévision communautaire n'entre PAS dans le stack (sinon la
comparaison serait circulaire) : elle est seulement stockée dans le
ForecastRecord, au champ `shadow`, pour la comparaison appariée.

Périmètre : ces questions sont sélectionnées par les plateformes ; un
résultat favorable ne vaut que pour leurs classes de questions.
"""
from __future__ import annotations

from ..engine.mathx import clip_p
from .scoring import block_bootstrap_ci, diebold_mariano, log_loss


def normalize_manifold(markets: list[dict]) -> list[dict]:
    out = []
    for m in markets:
        if m.get("outcomeType") != "BINARY":
            continue
        res = m.get("resolution")
        resolution = 1 if res == "YES" else 0 if res == "NO" else "cancelled" if res in ("CANCEL", "MKT") else None
        out.append({
            "platform": "manifold", "external_id": str(m["id"]), "title": m.get("question", ""),
            "url": m.get("url", ""), "close_time_ms": m.get("closeTime"),
            "community_p": m.get("probability"), "is_resolved": bool(m.get("isResolved")),
            "resolution": resolution, "resolution_criteria": m.get("textDescription") or m.get("description") or "",
        })
    return out


def normalize_metaculus(posts: list[dict]) -> list[dict]:
    """Adaptateur tolérant au format /api/posts/ (non vérifié contre l'API réelle
    faute d'accès réseau lors du développement : à contrôler au premier import)."""
    out = []
    for p in posts:
        q = p.get("question") or p
        if q.get("type") not in (None, "binary"):
            continue
        agg = (((q.get("aggregations") or {}).get("recency_weighted") or {}).get("latest") or {})
        centers = agg.get("centers") or []
        res = q.get("resolution")
        resolution = 1 if res in ("yes", 1, True) else 0 if res in ("no", 0, False) else \
            "cancelled" if res in ("annulled", "ambiguous") else None
        out.append({
            "platform": "metaculus", "external_id": str(p.get("id", q.get("id"))), "title": p.get("title", q.get("title", "")),
            "url": f"https://www.metaculus.com/questions/{p.get('id', q.get('id'))}/",
            "close_time_iso": q.get("scheduled_close_time"),
            "community_p": centers[0] if centers else None,
            "is_resolved": resolution is not None, "resolution": resolution,
            "resolution_criteria": q.get("resolution_criteria", ""),
        })
    return out


def shadow_comparison(entries: list[dict], cfg: dict) -> dict:
    """Différence appariée de log-score : outil - communauté (négatif = l'outil fait mieux)."""
    ev = cfg["evaluation"]
    latest: dict[str, dict] = {}
    rows = []
    for e in entries:
        if e["record_type"] == "forecast" and e["record"].get("shadow"):
            latest[e["record"]["question"]["question_id"]] = e["record"]
        elif e["record_type"] == "resolution":
            r = e["record"]
            f = latest.get(r["question_id"])
            if f is None or r["outcome"] == "cancelled":
                continue
            cp = f["shadow"].get("community_p_at_forecast")
            if cp is None:
                continue
            y = int(r["outcome"])
            rows.append({"question_id": r["question_id"], "platform": f["shadow"]["platform"],
                         "tool_p": f["final_output"]["p_reconciled"], "community_p": cp, "y": y,
                         "diff": log_loss(f["final_output"]["p_reconciled"], y) - log_loss(clip_p(cp), y)})
    diffs = [r["diff"] for r in rows]
    s = 0.0
    for d in diffs:
        s += d
    n_open = 0
    resolved_ids = {r["question_id"] for r in rows}
    for qid, f in latest.items():
        if qid not in resolved_ids:
            n_open += 1
    return {"n_resolved": len(rows), "n_open": n_open,
            "mean_log_diff_tool_minus_community": s / len(diffs) if diffs else None,
            "ci95": block_bootstrap_ci(diffs, ev["bootstrap_reps"], ev["seed"]),
            "diebold_mariano": diebold_mariano(diffs), "rows": rows,
            "scope_note": "Valable uniquement pour les classes de questions des plateformes importées."}

