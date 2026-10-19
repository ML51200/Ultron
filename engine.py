"""
ULTRON — bot de paper trading crypto (argent fictif).

Tourne une fois par heure (GitHub Actions). A chaque passage :
  1. récupère les bougies d'1 heure de BTC-EUR, ETH-EUR et SOL-EUR (Coinbase, données publiques)
  2. gère les positions ouvertes (stop, objectif, durée max)
  3. mesure sur 90 jours d'historique la qualité de chaque setup (frais compris)
  4. n'ouvre un trade QUE si le setup est autorisé (version 2) :
       - sur les 2/3 les plus anciens de l'historique : gain moyen > 0,
         réussite >= 55 % et au moins 30 cas
       - ET sur le 1/3 le plus récent (période de contrôle, anti-chance) :
         gain moyen > 0 avec au moins 10 cas
     et que la perte maximale du trade est <= 2 €
  5. enregistre tout dans docs/data/ (lu par le tableau de bord)

Aucune clé, aucun compte d'exchange : rien de réel n'est acheté.
"""
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

# --------------------------------------------------------------------------
# Règles de Mathis
# --------------------------------------------------------------------------
VERSION = 2
CAPITAL_DEPART = 100.0      # euros fictifs
RISQUE_MAX_EUR = 2.0        # perte max par trade
FIABILITE_MIN = 0.55        # 55 % de réussite minimum (période d'apprentissage)
ECHANTILLON_MIN = 30        # il faut au moins 30 cas passés pour juger
ESPERANCE_MIN_PCT = 0.0     # gain moyen par trade, frais compris, strictement supérieur à 0
CONTROLE_PART = 1 / 3       # le tiers le plus récent de l'historique sert de contrôle
CONTROLE_ECHANTILLON_MIN = 10   # cas minimum sur la période de contrôle
HISTORIQUE_JOURS = 90       # profondeur de l'historique (bougies d'1 h)
DUREE_TEST_JOURS = 30
FRAIS = 0.0015              # 0,10 % de frais + 0,05 % de glissement, à l'achat ET à la vente
DUREE_MAX_TRADE_H = 24      # un trade est fermé au bout de 24 h quoi qu'il arrive
OBJECTIFS_R = [0.5, 0.75, 1.0, 1.5, 2.0]   # objectif = x fois la distance du stop
MARCHES = {"BTC": "BTC-EUR", "ETH": "ETH-EUR", "SOL": "SOL-EUR"}   # Solana ajoutée le 19/10/2026 (validée par le labo)

HEURE = 3600
PAUSE_API_S = 0.35          # petite pause entre deux requêtes de prix
BOUGIES_MIN = int(HISTORIQUE_JOURS * 24 * 0.8)   # en dessous, l'historique est jugé incomplet


def regles():
    """Toutes les règles, écrites dans state.json : le tableau de bord les lit de là."""
    return {
        "version": VERSION,
        "fiabilite_min": FIABILITE_MIN, "echantillon_min": ECHANTILLON_MIN,
        "esperance_min_pct": ESPERANCE_MIN_PCT,
        "controle_part": round(CONTROLE_PART, 4), "controle_echantillon_min": CONTROLE_ECHANTILLON_MIN,
        "risque_max_eur": RISQUE_MAX_EUR, "duree_max_trade_h": DUREE_MAX_TRADE_H,
        "duree_test_jours": DUREE_TEST_JOURS, "historique_jours": HISTORIQUE_JOURS,
        "frais_par_cote": FRAIS, "objectifs_r": OBJECTIFS_R,
    }
ICI = os.path.dirname(os.path.abspath(__file__))
DOSSIER = os.environ.get("ULTRON_DATA", os.path.join(ICI, "docs", "data"))

NOMS_SETUPS = {
    "balayage": "Balayage + reprise",
    "repli": "Repli dans la tendance",
    "cassure": "Cassure du plus haut 24 h",
}


# --------------------------------------------------------------------------
# Données
# --------------------------------------------------------------------------
def telecharger(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ultron-paper-bot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def iso_api(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bougies_coinbase(produit, debut, fin):
    """Coinbase renvoie 300 bougies max par requête : on avance par tranches de 300 h."""
    tout, t = [], debut
    while t < fin:
        t2 = min(t + 300 * HEURE, fin)
        data = telecharger(f"https://api.exchange.coinbase.com/products/{produit}/candles"
                           f"?granularity=3600&start={iso_api(t)}&end={iso_api(t2)}")
        # format Coinbase : [time, low, high, open, close, volume], plus récent d'abord
        tout += [{"t": int(c[0]), "o": float(c[3]), "h": float(c[2]), "l": float(c[1]), "c": float(c[4])} for c in data]
        t = t2
        time.sleep(PAUSE_API_S)
    return tout


def bougies_binance(produit, debut, fin):
    """Secours : Binance (data-api.binance.vision), 1000 bougies max par requête."""
    sym = produit.replace("-", "")
    tout, t = [], debut
    while t < fin:
        data = telecharger(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}&interval=1h"
                           f"&startTime={t * 1000}&limit=1000")
        if not data:
            break
        tout += [{"t": int(c[0]) // 1000, "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4])} for c in data]
        t = int(data[-1][0]) // 1000 + HEURE
        time.sleep(PAUSE_API_S)
    return tout


def charger_bougies(produit, maintenant):
    """Renvoie ~90 jours de bougies CLÔTURÉES, dédupliquées, de la plus ancienne à la plus récente."""
    fin = maintenant - maintenant % HEURE                 # début de l'heure en cours
    debut = fin - HISTORIQUE_JOURS * 24 * HEURE
    erreurs = []
    for source in (bougies_coinbase, bougies_binance):
        try:
            b = source(produit, debut, fin)
            b = sorted({x["t"]: x for x in b}.values(), key=lambda x: x["t"])
            b = [x for x in b if debut <= x["t"] and x["t"] + HEURE <= maintenant]   # on retire la bougie en cours
            if len(b) >= BOUGIES_MIN:
                return b, source.__name__.replace("bougies_", "")
            erreurs.append(f"{source.__name__}: seulement {len(b)} bougies")
        except Exception as e:  # réseau, format...
            erreurs.append(f"{source.__name__}: {e}")
    raise RuntimeError(" | ".join(erreurs))


def donnees_valides(b, maintenant):
    """Contrôles de bon sens avant de faire confiance aux prix."""
    for x in b:
        if not (x["l"] <= min(x["o"], x["c"]) and x["h"] >= max(x["o"], x["c"]) and x["l"] > 0):
            return False, "bougie incohérente"
    age = maintenant - (b[-1]["t"] + HEURE)
    if age > 2 * HEURE:
        return False, f"données en retard de {age // 60} min"
    return True, "ok"


# --------------------------------------------------------------------------
# Indicateurs et setups
# --------------------------------------------------------------------------
def ema(valeurs, n):
    k = 2 / (n + 1)
    out, e = [], None
    for v in valeurs:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def detecter(b, i, e20, e50):
    """Renvoie la liste des setups présents sur la bougie i : [(nom, stop)]."""
    if i < 50:
        return []
    x = b[i]
    vert = x["c"] > x["o"]
    trouves = []

    plus_bas_24 = min(y["l"] for y in b[i - 24:i])
    if x["l"] < plus_bas_24 and x["c"] > plus_bas_24 and vert:
        trouves.append(("balayage", x["l"] * 0.999))

    if e20[i] > e50[i] and x["c"] > e50[i] and x["l"] <= e20[i] < x["c"] and vert:
        trouves.append(("repli", min(y["l"] for y in b[i - 4:i + 1]) * 0.999))

    plus_haut_24 = max(y["h"] for y in b[i - 24:i])
    if x["c"] > plus_haut_24 and vert:
        trouves.append(("cassure", min(y["l"] for y in b[i - 2:i + 1]) * 0.999))

    # on refuse les stops trop serrés (les frais mangent tout) ou trop larges
    return [(n, s) for n, s in trouves if 0.003 <= (x["c"] - s) / x["c"] <= 0.04]


def simuler(b, i_entree, prix, stop, objectif, t_entree):
    """Joue un trade bougie par bougie après l'entrée.
    Prudence : si stop et objectif sont touchés dans la même bougie, on compte le stop.
    Renvoie (prix_sortie, t_sortie, raison) ou None si pas encore terminé."""
    for y in b[i_entree:]:
        if y["t"] < t_entree:
            continue
        if y["l"] <= stop:
            return stop, y["t"] + HEURE, "stop"
        if y["h"] >= objectif:
            return objectif, y["t"] + HEURE, "objectif"
        if y["t"] + HEURE - t_entree >= DUREE_MAX_TRADE_H * HEURE:
            return y["c"], y["t"] + HEURE, "durée max"
    return None


def rendement_net(entree, sortie):
    return (sortie * (1 - FRAIS)) / (entree * (1 + FRAIS)) - 1


def pct_fr(x, d=0):
    return f"{x:.{d}f}".replace(".", ",").replace("-", "−")


def mesurer(perfs):
    n = len(perfs)
    return {"n": n,
            "fiabilite": (sum(1 for p in perfs if p > 0) / n) if n else 0.0,
            "esperance_pct": (100 * sum(perfs) / n) if n else 0.0}


def qualifiee(v):
    return v["n"] >= ECHANTILLON_MIN and v["fiabilite"] >= FIABILITE_MIN and v["esperance_pct"] > ESPERANCE_MIN_PCT


def evaluer_setups(series):
    """Backtest sur l'historique, coupé en deux pour éviter de croire à un coup de chance :
    - les 2/3 les plus anciens (apprentissage) servent à choisir l'objectif ;
      le setup doit y remplir les 3 conditions (gain moyen > 0, réussite >= 55 %, >= 30 cas) ;
    - le 1/3 le plus récent (contrôle) doit confirmer : gain moyen > 0 avec >= 10 cas."""
    t_min = min(b[0]["t"] for b in series.values())
    t_max = max(b[-1]["t"] for b in series.values())
    coupure = t_max - (t_max - t_min) * CONTROLE_PART       # un trade appartient à la période de son entrée

    res = {n: {r: {"appr": [], "ctrl": []} for r in OBJECTIFS_R} for n in NOMS_SETUPS}
    for b in series.values():
        cl = [x["c"] for x in b]
        e20, e50 = ema(cl, 20), ema(cl, 50)
        for i in range(50, len(b) - 1):
            for nom, stop in detecter(b, i, e20, e50):
                entree = b[i]["c"]
                periode = "appr" if b[i]["t"] + HEURE < coupure else "ctrl"
                for r in OBJECTIFS_R:
                    obj = entree + r * (entree - stop)
                    fin = simuler(b, i + 1, entree, stop, obj, b[i]["t"] + HEURE)
                    if fin:
                        res[nom][r][periode].append(rendement_net(entree, fin[0]))

    stats = {}
    for nom, par_r in res.items():
        variantes = [dict(r=r, **mesurer(p["appr"])) for r, p in par_r.items()]
        ok = [v for v in variantes if qualifiee(v)]
        # parmi les objectifs testés, on garde celui qui a le meilleur gain moyen (d'abord parmi ceux qui passent)
        retenue = max(ok or variantes, key=lambda v: (v["esperance_pct"], v["n"]))
        ctrl = mesurer(par_r[retenue["r"]]["ctrl"])
        ctrl["ok"] = ctrl["n"] >= CONTROLE_ECHANTILLON_MIN and ctrl["esperance_pct"] > ESPERANCE_MIN_PCT
        autorise = bool(ok) and ctrl["ok"]

        if not ok:
            raison = (f"refusé : {pct_fr(retenue['fiabilite'] * 100)} % de réussite sur {retenue['n']} cas, "
                      f"gain moyen {pct_fr(retenue['esperance_pct'], 2)} % par trade ; il faut ≥ {pct_fr(FIABILITE_MIN * 100)} % "
                      f"sur ≥ {ECHANTILLON_MIN} cas et un gain moyen positif")
        elif not ctrl["ok"]:
            detail = (f"seulement {ctrl['n']} cas, il en faut {CONTROLE_ECHANTILLON_MIN}" if ctrl["n"] < CONTROLE_ECHANTILLON_MIN
                      else f"gain moyen {pct_fr(ctrl['esperance_pct'], 2)} % sur {ctrl['n']} cas")
            raison = (f"bloqué : marchait avant ({pct_fr(retenue['fiabilite'] * 100)} % de réussite sur {retenue['n']} cas, "
                      f"gain moyen {pct_fr(retenue['esperance_pct'], 2)} %) mais pas sur la période de contrôle ({detail})")
        else:
            raison = (f"autorisé : {pct_fr(retenue['fiabilite'] * 100)} % de réussite sur {retenue['n']} cas, "
                      f"gain moyen {pct_fr(retenue['esperance_pct'], 2)} %, confirmé sur la période de contrôle "
                      f"({pct_fr(ctrl['esperance_pct'], 2)} % sur {ctrl['n']} cas)")
        stats[nom] = {"nom": NOMS_SETUPS[nom], "variantes": variantes, "retenue": retenue,
                      "controle": ctrl, "autorise": autorise, "raison": raison}
    return stats


# --------------------------------------------------------------------------
# Etat
# --------------------------------------------------------------------------
def lire(nom, defaut):
    try:
        with open(os.path.join(DOSSIER, nom), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return defaut


def ecrire(nom, data):
    os.makedirs(DOSSIER, exist_ok=True)
    tmp = os.path.join(DOSSIER, nom + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DOSSIER, nom))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def heure_fr(t):
    # Paris : UTC+2 en été, UTC+1 en hiver (changement le dernier dimanche d'octobre)
    d = datetime.fromtimestamp(t, timezone.utc)
    fin_ete = max(datetime(d.year, 10, j, 1, tzinfo=timezone.utc) for j in range(25, 32)
                  if datetime(d.year, 10, j).weekday() == 6)
    debut_ete = max(datetime(d.year, 3, j, 1, tzinfo=timezone.utc) for j in range(25, 32)
                    if datetime(d.year, 3, j).weekday() == 6)
    decal = 2 if debut_ete <= d < fin_ete else 1
    return (d + timedelta(hours=decal)).strftime("%d/%m %Hh%M")


def eur(x):
    return f"{x:+.2f} €".replace(".", ",")


# --------------------------------------------------------------------------
# Passage horaire
# --------------------------------------------------------------------------
def passage(maintenant=None, series_forcees=None):
    maintenant = int(maintenant or time.time())
    etat = lire("state.json", None)
    if etat is None:
        etat = {
            "version": VERSION,
            "capital_depart": CAPITAL_DEPART, "cash": CAPITAL_DEPART,
            "debut": maintenant, "fin": maintenant + DUREE_TEST_JOURS * 86400,
            "positions": {}, "statut": "en cours", "passages": 0,
        }
    etat["regles"] = regles()          # toujours à jour : le tableau de bord lit les règles ici
    trades = lire("trades.json", [])
    journal = lire("journal.json", [])
    courbe = lire("equity.json", [])
    evenements = []
    if etat["passages"] == 0:
        evenements.append(
            f"🆕 Nouveau test (version {VERSION}) : {pct_fr(CAPITAL_DEPART)} € fictifs pendant {DUREE_TEST_JOURS} jours. "
            f"Un setup n'est autorisé que s'il a, frais compris, un gain moyen positif, au moins "
            f"{pct_fr(FIABILITE_MIN * 100)} % de réussite sur au moins {ECHANTILLON_MIN} cas, et s'il le confirme "
            f"sur la période de contrôle (le dernier tiers des {HISTORIQUE_JOURS} jours).")
    if etat["passages"] > 0 and not series_forcees and "SOL" not in (etat.get("prix") or {}):
        evenements.append("🆕 Solana (SOL) rejoint BTC et ETH : mêmes règles, les setups sont maintenant mesurés "
                          "sur les trois cryptos. Le capital et la durée du test ne changent pas.")

    if etat["statut"] == "terminé":
        print("Test terminé, rien à faire.")
        return

    # 1. prix
    series, sources, probleme = {}, {}, None
    for sym, produit in MARCHES.items():
        try:
            if series_forcees:
                b, src = series_forcees[sym], "simulation"
            else:
                b, src = charger_bougies(produit, maintenant)
            ok, msg = donnees_valides(b, maintenant)
            if not ok:
                probleme = f"{sym} : {msg}"
            series[sym], sources[sym] = b, src
        except Exception as e:
            probleme = f"{sym} : prix indisponibles ({e})"

    prix = {s: b[-1]["c"] for s, b in series.items()}

    if probleme:
        evenements.append(f"⚠️ Je ne trade pas ce passage : {probleme}. Les positions ouvertes restent protégées par leur stop au prochain passage.")
    else:
        # 2. gestion des positions ouvertes
        for sym, pos in list(etat["positions"].items()):
            fin = simuler(series[sym], 0, pos["entree"], pos["stop"], pos["objectif"], pos["t_entree"])
            if fin:
                p_sortie, t_sortie, raison = fin
                valeur = pos["quantite"] * p_sortie * (1 - FRAIS)
                pnl = valeur - pos["mise"]
                etat["cash"] += valeur
                tr = next(t for t in trades if t["id"] == pos["id"])
                tr.update({"statut": "fermé", "sortie": p_sortie, "t_sortie": iso(t_sortie),
                           "raison": raison, "pnl_eur": round(pnl, 4)})
                del etat["positions"][sym]
                evenements.append(f"{'✅' if pnl > 0 else '❌'} {sym} fermé ({raison}) : {eur(pnl)}.")

        # 3. fin du test : on solde tout
        if maintenant >= etat["fin"]:
            for sym, pos in list(etat["positions"].items()):
                valeur = pos["quantite"] * prix[sym] * (1 - FRAIS)
                pnl = valeur - pos["mise"]
                etat["cash"] += valeur
                tr = next(t for t in trades if t["id"] == pos["id"])
                tr.update({"statut": "fermé", "sortie": prix[sym], "t_sortie": iso(maintenant),
                           "raison": "fin du test", "pnl_eur": round(pnl, 4)})
                del etat["positions"][sym]
                evenements.append(f"🏁 {sym} soldé pour la fin du test : {eur(pnl)}.")
            etat["statut"] = "terminé"

        # 4. évaluation des setups + nouvelles entrées
        stats = evaluer_setups(series)
        avant = etat.get("setups") or {}
        for nom, st in stats.items():
            # au premier passage, puis à chaque changement de verdict, le journal explique pourquoi
            if etat["passages"] == 0 or (avant.get(nom) or {}).get("autorise") != st["autorise"]:
                evenements.append(f"📋 « {st['nom']} » : {st['raison']}.")
        etat["setups"] = stats
        if etat["statut"] != "terminé":
            for sym, b in series.items():
                if sym in etat["positions"]:
                    continue
                cl = [x["c"] for x in b]
                e20, e50 = ema(cl, 20), ema(cl, 50)
                i = len(b) - 1
                for nom, stop in detecter(b, i, e20, e50):
                    st = stats[nom]
                    v = st["retenue"]
                    if not st["autorise"]:
                        evenements.append(f"👀 {sym} : setup « {st['nom']} » repéré mais {st['raison']}.")
                        continue
                    entree = b[i]["c"]
                    dist = (entree - stop) / entree
                    mise = min(RISQUE_MAX_EUR / (dist + 2 * FRAIS), etat["cash"])
                    if mise < 5:
                        evenements.append(f"{sym} : setup valide mais plus assez de cash libre.")
                        break
                    quantite = mise / (entree * (1 + FRAIS))
                    objectif = entree + v["r"] * (entree - stop)
                    perte_max = mise - quantite * stop * (1 - FRAIS)
                    tid = f"{sym}-{b[i]['t']}"
                    etat["cash"] -= mise
                    etat["positions"][sym] = {"id": tid, "entree": entree, "stop": stop, "objectif": objectif,
                                              "quantite": quantite, "mise": mise, "t_entree": b[i]["t"] + HEURE}
                    trades.append({"id": tid, "symbole": sym, "setup": st["nom"], "statut": "ouvert",
                                   "t_entree": iso(b[i]["t"] + HEURE), "entree": entree, "stop": stop,
                                   "objectif": objectif, "mise_eur": round(mise, 2),
                                   "perte_max_eur": round(perte_max, 2),
                                   "fiabilite": round(v["fiabilite"], 3), "echantillon": v["n"],
                                   "esperance_pct": round(v["esperance_pct"], 3),
                                   "controle_esperance_pct": round(st["controle"]["esperance_pct"], 3),
                                   "objectif_r": v["r"]})
                    prix_fr = f"{entree:,.2f}".replace(",", " ").replace(".", ",")
                    evenements.append(
                        f"🚀 Achat {sym} à {prix_fr} € (« {st['nom']} », {pct_fr(v['fiabilite'] * 100)} % de réussite "
                        f"sur {v['n']} cas, gain moyen {pct_fr(v['esperance_pct'], 2)} %, confirmé sur la période de contrôle). "
                        f"Mise {pct_fr(mise, 2)} €, perte max {pct_fr(perte_max, 2)} €.")
                    break

    # 5. bilan
    valeur_pos = sum(p["quantite"] * prix.get(s, p["entree"]) for s, p in etat["positions"].items())
    capital = etat["cash"] + valeur_pos
    etat.update({"capital": round(capital, 4), "prix": prix, "sources": sources,
                 "dernier_passage": iso(maintenant), "passages": etat["passages"] + 1,
                 "donnees_ok": probleme is None})
    courbe.append({"t": iso(maintenant), "capital": round(capital, 4)})
    if not evenements:
        evenements.append("Rien d'assez fiable cette heure-ci. J'attends.")
    journal.append({"t": iso(maintenant), "heure": heure_fr(maintenant), "capital": round(capital, 2),
                    "messages": evenements})

    ecrire("state.json", etat)
    ecrire("trades.json", trades)
    ecrire("journal.json", journal[-500:])
    ecrire("equity.json", courbe)
    for m in evenements:
        print(m)
    print(f"Capital : {capital:.2f} €")


if __name__ == "__main__":
    passage()
