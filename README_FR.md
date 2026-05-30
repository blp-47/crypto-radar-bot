# Stock Radar Bot - CAC 40 + actions US

Cette version remplace le radar crypto par un radar actions.

Elle surveille :

- ta watchlist personnelle,
- le CAC 40,
- les actions US listées dans le répertoire Nasdaq Trader.

Le bot détecte :

- les gaps par rapport à la clôture de la veille,
- les hausses rapides en 1 / 5 / 15 / 30 minutes,
- les volumes anormaux,
- les actions qui commencent à bouger fortement.

## Fichiers importants

- `crypto_radar_bot.py` : programme principal. Le nom reste identique pour Render.
- `config.json` : réglages actifs.
- `config.example.json` : modèle de réglages.
- `symbols_perso.txt` : ajoute ici tes actions favorites.

## Réglage des listes

Dans `config.json` :

```json
"include_cac40": true,
"include_us_all": true
```

Pour limiter l'univers US :

```json
"max_us_symbols": 3000
```

Pour tout prendre :

```json
"max_us_symbols": 0
```

## Réglages d'alerte importants

Pour les actions, le réglage le plus important est souvent :

```json
"gap_prev_close_pct": 8.0
```

Pour être alerté plus tôt :

```json
"prefilter_gap_pct": 3.0,
"gap_prev_close_pct": 5.0
```

Mais ça donnera plus de fausses alertes.

## Sécurité

Ce bot n'achète rien et ne donne pas de conseil financier. Il sert uniquement à envoyer des alertes.
