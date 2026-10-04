"""Construction de l'historique d'apprentissage à partir des entrées du registre.

L'historique est toujours reconstruit « tel qu'il était » à une position du
registre : une prévision n'apprend que des résolutions inscrites avant elle.
C'est ce qui rend les taux de base et les poids hors échantillon.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..schema import horizon_bucket, iso_duration_days

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def to_days(ts: str) -> float:
    """Horodatage ISO-8601 -> jours depuis 1970 (UTC si pas de fuseau)."""
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - EPOCH).total_seconds() / 86400.0


def class_key(question_type: str, horizon_days: float, buckets: list[float]) -> str:
    return f"{question_type}|{horizon_bucket(horizon_days, buckets)}"


def build_history(entries: list[dict], cfg: dict, before_seq: int | None = None) -> dict:
    """Résume le registre (jusqu'à before_seq exclu) en données d'apprentissage.

    Retourne :
      observations : une par question binaire résolue (non annulée), avec la
                     dernière prévision émise avant la résolution ;
      reference    : issues résolues utilisables pour les taux de base
                     (questions du registre + lots de référence importés) ;
      resolution_stats : par classe, nombre de résolutions et d'annulations ;
      latest       : dernière prévision par question (pour la réconciliation).
    """
    buckets = cfg["horizon_buckets_days"]
    forecasts: dict[str, list[dict]] = {}
    resolutions: dict[str, dict] = {}
    reference: list[dict] = []
    for e in entries:
        if before_seq is not None and e["seq"] >= before_seq:
            break
        rec, rt = e["record"], e["record_type"]
        if rt == "forecast":
            forecasts.setdefault(rec["question"]["question_id"], []).append({"seq": e["seq"], "rec": rec})
        elif rt == "resolution":
            resolutions[rec["question_id"]] = {"seq": e["seq"], "rec": rec}
        elif rt == "reference_batch":
            for it in rec["items"]:
                reference.append({
                    "question_type": it["question_type"],
                    "bucket": horizon_bucket(float(it["horizon_days"]), buckets),
                    "domain": it.get("domain") or "general",
                    "y": int(it["outcome"]),
                    "t": to_days(it["resolved_at"]),
                })

    observations, stats, latest = [], {}, {}
    for qid in sorted(forecasts):
        flist = forecasts[qid]
        latest[qid] = flist[-1]["rec"]
        res = resolutions.get(qid)
        if res is None:
            continue
        q = flist[0]["rec"]["question"]
        hd = iso_duration_days(q["horizon"])
        ck = class_key(q["question_type"], hd, buckets)
        st = stats.setdefault(ck, {"n": 0, "cancelled": 0})
        st["n"] += 1
        outcome = res["rec"]["outcome"]
        if outcome == "cancelled":
            st["cancelled"] += 1
            continue
        if q["question_type"] != "binary":
            continue
        t_res = to_days(res["rec"]["timestamp_utc"])
        prior = [f for f in flist if f["seq"] < res["seq"]]
        if not prior:
            continue
        fr = prior[-1]["rec"]
        mp = fr["models_pipeline"]
        comps = {c["component"]: c["p"] for c in mp["component_forecasts"]}
        y = int(outcome)
        domain = mp.get("domain") or "general"
        observations.append({
            "question_id": qid,
            "forecast_id": fr["forecast_id"],
            "t": t_res,
            "y": y,
            "class_key": ck,
            "domain": domain,
            "quality": float(res["rec"]["resolvability_score_post"]),
            "components": comps,
            "base_rate_p": mp["base_rate"]["p"],
            "mu_logit": mp.get("bayes_detail", {}).get("mu_logit"),
            "features": mp.get("evidence_features") or {},
        })
        reference.append({"question_type": q["question_type"], "bucket": ck.split("|")[1], "domain": domain,
                          "y": y, "t": t_res})
    observations.sort(key=lambda o: (o["t"], o["question_id"]))
    reference.sort(key=lambda r: (r["t"], r["question_type"], r["bucket"], r["domain"], r["y"]))
    return {"observations": observations, "reference": reference, "resolution_stats": stats, "latest": latest}
