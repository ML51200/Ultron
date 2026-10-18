"""
ULTRON — LE LABO (banc d'essai).

Le labo rejoue les 90 derniers jours de prix et teste des idées d'amélioration,
SANS JAMAIS toucher au test en cours d'Ultron live (il n'écrit que dans docs/labo/).

Pour chaque idée (une « expérience »), et pour chaque setup :
  1. Apprentissage (2/3 les plus anciens) : le setup remplit-il les règles d'Ultron live ?
     (gain moyen > 0, réussite >= 55 %, >= 30 cas, avec le meilleur objectif choisi ici)
  2. Contrôle (1/3 le plus récent, jamais utilisé pour se régler) : est-ce que ça marche encore ?
     Comme le labo essaie beaucoup de combinaisons, le contrôle est DURCI :
     gain moyen > 0 ET au moins 20 cas (au lieu de 10 en live).
Verdict : « validé » seulement si les deux passent. Le labo ne change rien tout seul :
c'est Mathis qui décide si une idée validée passe dans la prochaine version d'Ultron live.
"""
import json
import os
import time

import engine as E

HEURE = E.HEURE
DOSSIER = os.environ.get("ULTRON_LABO", os.path.join(E.ICI, "docs", "labo"))
CONTROLE_LABO_MIN = 20          # contrôle durci (plus d'essais = plus de risque de chance)

MARCHES_BASE = {"BTC": "BTC-EUR", "ETH": "ETH-EUR"}
MARCHES_PLUS = {"SOL": "SOL-EUR", "XRP": "XRP-EUR"}

NOMS = dict(E.NOMS_SETUPS)
NOMS["rebond"] = "Rebond après chute"

EXPERIENCES = [
    {"id": "reference", "nom": "Référence (Ultron live actuel)",
     "idee": "Les règles et setups exacts d'Ultron live, pour comparer.",
     "tendance": False, "stop_securise": False, "plus_cryptos": False},
    {"id": "tendance", "nom": "Filtre de tendance",
     "idee": "Ne prendre un trade que si le prix est au-dessus de sa moyenne des ~8 derniers jours (tendance de fond haussière).",
     "tendance": True, "stop_securise": False, "plus_cryptos": False},
    {"id": "stop_securise", "nom": "Stop sécurisé",
     "idee": "Dès que le prix a fait la moitié du chemin vers l'objectif, le stop remonte au prix d'entrée : le trade ne peut plus perdre (hors frais).",
     "tendance": False, "stop_securise": True, "plus_cryptos": False},
    {"id": "tendance_stop", "nom": "Tendance + stop sécurisé",
     "idee": "Les deux idées précédentes ensemble.",
     "tendance": True, "stop_securise": True, "plus_cryptos": False},
    {"id": "plus_cryptos", "nom": "Plus de cryptos (SOL, XRP)",
     "idee": "Mêmes règles, mais aussi sur Solana et XRP : plus d'occasions et plus de cas pour juger.",
     "tendance": False, "stop_securise": False, "plus_cryptos": True},
    {"id": "plus_solana", "nom": "Plus Solana seule",
     "idee": "Mêmes règles, avec Solana en plus de BTC et ETH (sans XRP), pour juger Solana à part.",
     "tendance": False, "stop_securise": False, "plus_cryptos": ["SOL"]},
]


# --------------------------------------------------------------------------
# Setups (ceux d'Ultron live + un nouveau, testé seulement ici)
# --------------------------------------------------------------------------
def detecter_labo(b, i, e20, e50):
    trouves = list(E.detecter(b, i, e20, e50))
    if i >= 50:
        x = b[i]
        # NOUVEAU : rebond après chute — le prix est tombé nettement sous sa moyenne courte puis repart
        if x["c"] > x["o"] and min(y["l"] for y in b[i - 3:i + 1]) < e20[i] * 0.97 and x["c"] < e20[i]:
            stop = min(y["l"] for y in b[i - 3:i + 1]) * 0.999
            if 0.003 <= (x["c"] - stop) / x["c"] <= 0.04:
                trouves.append(("rebond", stop))
    return trouves


def simuler_labo(b, i_entree, entree, stop, objectif, t_entree, stop_securise):
    """Comme E.simuler, avec en option le stop qui remonte au prix d'entrée à mi-chemin."""
    if not stop_securise:
        return E.simuler(b, i_entree, entree, stop, objectif, t_entree)
    mi_chemin = entree + (objectif - entree) / 2
    seuil_nul = entree * (1 + E.FRAIS) / (1 - E.FRAIS)      # prix où le trade ne perd rien, frais compris
    for y in b[i_entree:]:
        if y["t"] < t_entree:
            continue
        if y["l"] <= stop:
            return stop, y["t"] + HEURE, "stop"
        if y["h"] >= objectif:
            return objectif, y["t"] + HEURE, "objectif"
        if y["h"] >= mi_chemin and stop < seuil_nul:
            stop = seuil_nul        # s'applique à partir de la bougie suivante (prudent)
        if y["t"] + HEURE - t_entree >= E.DUREE_MAX_TRADE_H * HEURE:
            return y["c"], y["t"] + HEURE, "durée max"
    return None


# --------------------------------------------------------------------------
# Une expérience
# --------------------------------------------------------------------------
def lancer_experience(exp, series):
    extra = exp["plus_cryptos"]
    extra = MARCHES_PLUS if extra is True else {k: MARCHES_PLUS[k] for k in (extra or [])}
    marches = dict(MARCHES_BASE, **extra)
    series = {s: b for s, b in series.items() if s in marches}
    t_min = min(b[0]["t"] for b in series.values())
    t_max = max(b[-1]["t"] for b in series.values())
    coupure = t_max - (t_max - t_min) * E.CONTROLE_PART

    res = {n: {r: {"appr": [], "ctrl": []} for r in E.OBJECTIFS_R} for n in NOMS}
    for b in series.values():
        cl = [x["c"] for x in b]
        e20, e50, e200 = E.ema(cl, 20), E.ema(cl, 50), E.ema(cl, 200)
        debut = 200 if exp["tendance"] else 50
        for i in range(debut, len(b) - 1):
            if exp["tendance"] and not b[i]["c"] > e200[i]:
                continue
            for nom, stop in detecter_labo(b, i, e20, e50):
                entree = b[i]["c"]
                periode = "appr" if b[i]["t"] + HEURE < coupure else "ctrl"
                for r in E.OBJECTIFS_R:
                    obj = entree + r * (entree - stop)
                    fin = simuler_labo(b, i + 1, entree, stop, obj, b[i]["t"] + HEURE, exp["stop_securise"])
                    if fin:
                        res[nom][r][periode].append(E.rendement_net(entree, fin[0]))

    setups = []
    for nom, par_r in res.items():
        variantes = [dict(r=r, **E.mesurer(p["appr"])) for r, p in par_r.items()]
        ok = [v for v in variantes if E.qualifiee(v)]
        retenue = max(ok or variantes, key=lambda v: (v["esperance_pct"], v["n"]))
        ctrl = E.mesurer(par_r[retenue["r"]]["ctrl"])
        ctrl["ok"] = ctrl["n"] >= CONTROLE_LABO_MIN and ctrl["esperance_pct"] > E.ESPERANCE_MIN_PCT
        if ok and ctrl["ok"]:
            verdict, texte = "validé", "passe l'apprentissage ET le contrôle"
        elif ok:
            verdict = "chance"
            texte = (f"bon sur le passé mais pas confirmé au contrôle "
                     f"({ctrl['n']} cas, gain moyen {E.pct_fr(ctrl['esperance_pct'], 2)} %)")
        else:
            verdict = "refusé"
            texte = (f"{E.pct_fr(retenue['fiabilite'] * 100)} % de réussite sur {retenue['n']} cas, "
                     f"gain moyen {E.pct_fr(retenue['esperance_pct'], 2)} %")
        setups.append({"id": nom, "nom": NOMS[nom], "nouveau": nom not in E.NOMS_SETUPS,
                       "apprentissage": retenue, "controle": ctrl, "verdict": verdict, "texte": texte})

    valides = [s for s in setups if s["verdict"] == "validé"]
    return {"id": exp["id"], "nom": exp["nom"], "idee": exp["idee"], "marches": list(marches),
            "setups": setups, "nb_valides": len(valides),
            # meilleur gain moyen au contrôle parmi tous les setups : un indicateur pour comparer les idées
            "meilleur_controle_pct": max((s["controle"]["esperance_pct"] for s in setups if s["controle"]["n"]), default=0.0)}


def conclusion(resultats):
    ref = next(r for r in resultats if r["id"] == "reference")
    lignes = []
    gagnantes = [r for r in resultats if r["id"] != "reference" and r["nb_valides"] > 0]
    if not gagnantes and not ref["nb_valides"]:
        lignes.append("Aucune idée ne trouve de setup qui passe à la fois l'apprentissage et le contrôle. "
                      "Le marché des 90 derniers jours ne donne pas d'avantage clair : Ultron a raison d'attendre.")
    if ref["nb_valides"]:
        lignes.append("La référence (Ultron live) a au moins un setup validé : Ultron live peut trader avec ses règles actuelles.")
    for r in gagnantes:
        noms = ", ".join(s["nom"] for s in r["setups"] if s["verdict"] == "validé")
        lignes.append(f"« {r['nom']} » valide : {noms}. À surveiller sur plusieurs jours avant de l'adopter.")
    chances = sum(1 for r in resultats for s in r["setups"] if s["verdict"] == "chance")
    if chances:
        lignes.append(f"{chances} combinaison(s) avaient l'air bonnes sur le passé mais ont échoué au contrôle : "
                      "c'est exactement le piège que le labo évite.")
    return lignes


def lancer(maintenant=None, series_forcees=None):
    maintenant = int(maintenant or time.time())
    series, erreurs = {}, []
    toutes = dict(MARCHES_BASE, **MARCHES_PLUS)
    for sym, produit in toutes.items():
        try:
            b = series_forcees[sym] if series_forcees else E.charger_bougies(produit, maintenant)[0]
            series[sym] = b
        except Exception as e:
            erreurs.append(f"{sym} : {e}")
    if not all(s in series for s in MARCHES_BASE):
        raise SystemExit("Prix BTC/ETH indisponibles, labo annulé : " + " | ".join(erreurs))

    resultats = []
    for exp in EXPERIENCES:
        besoin = MARCHES_PLUS if exp["plus_cryptos"] is True else (exp["plus_cryptos"] or [])
        if not all(s in series for s in besoin):
            continue
        resultats.append(lancer_experience(exp, series))

    sortie = {
        "date": E.iso(maintenant), "heure": E.heure_fr(maintenant),
        "historique_jours": E.HISTORIQUE_JOURS,
        "bougies": {s: len(b) for s, b in series.items()},
        "regles": {"fiabilite_min": E.FIABILITE_MIN, "echantillon_min": E.ECHANTILLON_MIN,
                   "controle_part": round(E.CONTROLE_PART, 4), "controle_min_labo": CONTROLE_LABO_MIN},
        "experiences": resultats, "conclusion": conclusion(resultats), "erreurs": erreurs,
    }
    os.makedirs(DOSSIER, exist_ok=True)
    with open(os.path.join(DOSSIER, "resultats.json"), "w") as f:
        json.dump(sortie, f, ensure_ascii=False, indent=1)
    hist_path = os.path.join(DOSSIER, "historique.json")
    try:
        hist = json.load(open(hist_path))
    except FileNotFoundError:
        hist = []
    hist.append({"date": sortie["date"], "valides": {r["id"]: r["nb_valides"] for r in resultats}})
    json.dump(hist[-120:], open(hist_path, "w"), ensure_ascii=False, indent=1)

    for r in resultats:
        print(f"\n== {r['nom']} ({', '.join(r['marches'])})")
        for s in r["setups"]:
            print(f"  [{s['verdict']:7}] {s['nom']}: {s['texte']}")
    print("\n" + "\n".join(sortie["conclusion"]))
    return sortie


if __name__ == "__main__":
    lancer()
