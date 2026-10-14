# Ultron

Bot de **paper trading** crypto (argent fictif) : 100 € fictifs, BTC et ETH, pendant une semaine.

## Les règles
- Un trade n'est ouvert que si le même setup a réussi **au moins 80 % du temps** sur l'historique récent (au moins 10 cas, frais compris).
- Chaque trade a un stop automatique : **2 € de perte maximum**.
- Un trade dure 24 h maximum.

## Comment ça marche
- `engine.py` tourne **toutes les heures** sur les serveurs de GitHub (`.github/workflows/ultron.yml`).
- Il lit les prix publics de Coinbase (BTC-EUR, ETH-EUR), mesure la fiabilité de 3 setups, gère les positions et écrit tout dans `docs/data/`.
- Le tableau de bord (`docs/index.html`) est publié avec GitHub Pages.

Aucune clé d'exchange, aucun ordre réel. Ce n'est pas un conseil en investissement.
