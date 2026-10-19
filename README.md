# Ultron

Bot de **paper trading** crypto (argent fictif) : 100 € fictifs, BTC, ETH et SOL (Solana, ajoutée le 19/10/2026), pendant 30 jours (version 2).

## Les règles (version 2)
- Ultron mesure 3 setups sur **90 jours** de bougies d'1 heure, frais compris.
- Un setup n'est autorisé que si, sur les 2/3 les plus anciens de l'historique : **gain moyen par trade > 0**, **réussite ≥ 55 %**, **au moins 30 cas**.
- **Contrôle anti-chance** : sur le 1/3 le plus récent, le gain moyen doit aussi être positif (au moins 10 cas). Sinon le setup est bloqué.
- Chaque trade a un stop automatique : **2 € de perte maximum**.
- Un trade dure 24 h maximum.
- Toutes les règles sont écrites dans `docs/data/state.json` → `regles` ; le tableau de bord les lit de là.
- Les données de la version 1 (test d'une semaine) sont archivées dans `docs/data/archive-v1/`.
- Tests sur prix inventés : `python tests/simulation.py`.

## Comment ça marche
- `engine.py` tourne **toutes les heures** sur les serveurs de GitHub (`.github/workflows/ultron.yml`).
- Il lit les prix publics de Coinbase (BTC-EUR, ETH-EUR, SOL-EUR), mesure la fiabilité de 3 setups, gère les positions et écrit tout dans `docs/data/`.
- Le tableau de bord (`docs/index.html`) est publié avec GitHub Pages.

Aucune clé d'exchange, aucun ordre réel. Ce n'est pas un conseil en investissement.
