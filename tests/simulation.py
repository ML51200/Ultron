"""
Tests d'Ultron sur des prix inventés (aucun accès réseau).

  python tests/simulation.py

1. Un mois complet de passages horaires sur une marche aléatoire :
   rien ne plante, aucun trade ne perd plus de 2 €, le test se termine proprement.
2. Un setup « chanceux » (marché qui monte pendant l'apprentissage puis baisse) :
   il remplit les conditions sur les 2/3 anciens mais doit être BLOQUÉ par le contrôle.
3. Un marché qui monte tout le temps : le contrôle confirme, des trades s'ouvrent
   et aucun ne perd plus de 2 €.
"""
import contextlib
import io
import json
import math
import os
import random
import sys
import tempfile
import time

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ICI))
import engine  # noqa: E402

H = engine.HEURE
JOUR = 24 * H


def marche(n, depart, derive, vol, graine, t0):
    """Bougies d'1 h : derive = tendance par heure (liste ou nombre), vol = volatilité par heure."""
    rnd = random.Random(graine)
    b, prix = [], depart
    for i in range(n):
        d = derive[i] if isinstance(derive, list) else derive
        o = prix
        c = o * math.exp(d + rnd.gauss(0, vol))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol * 0.5)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, vol * 0.5)))
        b.append({"t": t0 + i * H, "o": o, "h": h, "l": l, "c": c})
        prix = c
    return b


def fenetre(b, maintenant):
    """Ce qu'Ultron verrait à cet instant : 90 jours de bougies clôturées."""
    debut = maintenant - maintenant % H - engine.HISTORIQUE_JOURS * JOUR
    return [x for x in b if debut <= x["t"] and x["t"] + H <= maintenant]


def nouveau_dossier():
    d = tempfile.mkdtemp(prefix="ultron-test-")
    engine.DOSSIER = d
    return d


def lire(d, nom):
    with open(os.path.join(d, nom), encoding="utf-8") as f:
        return json.load(f)


def faire_passages(series, t_debut, heures):
    bilan = {"autorises": 0, "bloques_controle": 0, "refuses": 0}
    for h in range(heures):
        maintenant = t_debut + h * H + 7 * 60
        vues = {s: fenetre(b, maintenant) for s, b in series.items()}
        with contextlib.redirect_stdout(io.StringIO()):
            engine.passage(maintenant, vues)
        etat = lire(engine.DOSSIER, "state.json")
        if h == 0:
            bilan["premier_message"] = lire(engine.DOSSIER, "journal.json")[0]["messages"][0]
        for st in (etat.get("setups") or {}).values():
            if st["autorise"]:
                bilan["autorises"] += 1
            elif st["raison"].startswith("bloqué"):
                bilan["bloques_controle"] += 1
            else:
                bilan["refuses"] += 1
    return bilan


def verifier_trades(d):
    trades = lire(d, "trades.json")
    pire = min([t["pnl_eur"] for t in trades if t.get("pnl_eur") is not None] or [0.0])
    assert all(t["perte_max_eur"] <= engine.RISQUE_MAX_EUR + 1e-9 for t in trades), "perte max prévue > 2 €"
    assert pire >= -engine.RISQUE_MAX_EUR - 1e-6, f"un trade a perdu {pire:.4f} €"
    return trades, pire


def test_mois_aleatoire():
    print("1) Un mois de passages horaires sur une marche aléatoire…")
    d = nouveau_dossier()
    t0 = 1_700_000_000 - 1_700_000_000 % H
    n = (engine.HISTORIQUE_JOURS + engine.DUREE_TEST_JOURS + 2) * 24
    series = {"BTC": marche(n, 60000, 0.0, 0.006, 1, t0), "ETH": marche(n, 2500, 0.0, 0.008, 2, t0), "SOL": marche(n, 150, 0.0, 0.01, 7, t0)}
    t_debut = t0 + engine.HISTORIQUE_JOURS * JOUR + H
    debut = time.time()
    bilan = faire_passages(series, t_debut, engine.DUREE_TEST_JOURS * 24 + 2)
    etat = lire(d, "state.json")
    trades, pire = verifier_trades(d)
    assert etat["statut"] == "terminé", "le test aurait dû se terminer au bout de 30 jours"
    assert not etat["positions"], "des positions restent ouvertes après la fin du test"
    assert abs(etat["capital"] - etat["cash"]) < 1e-3   # capital est arrondi à 4 décimales
    assert etat["regles"]["fiabilite_min"] == 0.55 and etat["regles"]["echantillon_min"] == 30
    assert etat["regles"]["duree_test_jours"] == 30 and etat["regles"]["historique_jours"] == 90
    assert bilan["premier_message"].startswith("🆕 Nouveau test (version 2)")   # (le journal ne garde que 500 entrées)
    print(f"   ok : {etat['passages']} passages en {time.time() - debut:.0f} s, {len(trades)} trades, "
          f"pire trade {pire:+.2f} €, capital final {etat['capital']:.2f} €")
    print(f"   verdicts (setup × passage) : {bilan['autorises']} autorisés, "
          f"{bilan['bloques_controle']} bloqués par le contrôle, {bilan['refuses']} refusés d'emblée")
    return bilan


def test_setup_chanceux():
    print("2) Setup « chanceux » : marche avant, plus pendant la période de contrôle…")
    t0 = 1_700_000_000 - 1_700_000_000 % H
    n = engine.HISTORIQUE_JOURS * 24
    coupe = int(n * 2 / 3)
    derive = [0.0012] * coupe + [-0.0012] * (n - coupe)     # hausse, puis baisse
    series = {"BTC": marche(n, 60000, derive, 0.004, 3, t0), "ETH": marche(n, 2500, derive, 0.005, 4, t0), "SOL": marche(n, 150, derive, 0.006, 8, t0)}
    stats = engine.evaluer_setups(series)
    for st in stats.values():
        v, c = st["retenue"], st["controle"]
        print(f"   {st['nom']:<28} apprentissage {v['fiabilite']*100:3.0f} % sur {v['n']:>3} cas, "
              f"gain moyen {v['esperance_pct']:+.2f} % | contrôle {c['esperance_pct']:+.2f} % sur {c['n']} cas "
              f"→ {'AUTORISÉ' if st['autorise'] else 'BLOQUÉ'}")
    chanceux = [st for st in stats.values() if engine.qualifiee(st["retenue"])]
    assert chanceux, "le scénario aurait dû produire au moins un setup qui passe sur l'apprentissage"
    assert not any(st["autorise"] for st in chanceux), "un setup chanceux a été autorisé"
    assert all("marchait avant" in st["raison"] for st in chanceux)
    print(f"   ok : {len(chanceux)} setup(s) chanceux, tous bloqués. Exemple de message :")
    print(f"   « {chanceux[0]['raison']} »")


def test_marche_haussier():
    print("3) Marché qui monte tout le temps : le contrôle confirme et des trades s'ouvrent…")
    d = nouveau_dossier()
    t0 = 1_700_000_000 - 1_700_000_000 % H
    n = (engine.HISTORIQUE_JOURS + 12) * 24
    series = {"BTC": marche(n, 60000, 0.0012, 0.004, 5, t0), "ETH": marche(n, 2500, 0.0012, 0.005, 6, t0), "SOL": marche(n, 150, 0.0012, 0.006, 9, t0)}
    t_debut = t0 + engine.HISTORIQUE_JOURS * JOUR + H
    bilan = faire_passages(series, t_debut, 10 * 24)
    trades, pire = verifier_trades(d)
    assert bilan["autorises"] > 0, "aucun setup autorisé alors que le marché monte tout le temps"
    assert trades, "aucun trade ouvert"
    print(f"   ok : {len(trades)} trades en 10 jours, pire trade {pire:+.2f} €, "
          f"capital {lire(d, 'state.json')['capital']:.2f} €")


if __name__ == "__main__":
    test_setup_chanceux()
    test_marche_haussier()
    test_mois_aleatoire()
    print("Tous les tests sont passés.")
