# Stock Radar Bot sur Render

Commande de démarrage :

```bash
python crypto_radar_bot.py
```

Build command :

```bash
pip install -r requirements.txt
```

Variables d'environnement à mettre dans Render :

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Le fichier `config.json` fourni ne contient aucun secret. Tu peux donc le mettre sur GitHub.
Si tu ne veux pas de `config.json`, le bot utilisera les réglages par défaut du fichier Python.

## Réglage conseillé pour Render Starter à 7 $

Le mode `large_universe_mode` est activé. Il scanne rapidement toutes les actions avec des quotes Yahoo, puis analyse en 1 minute seulement les candidats suspects. C'est beaucoup plus léger que d'ouvrir un graphique 1 minute sur 7000 actions.

Si Yahoo bloque ou ralentit :

- Mets `quote_batch_size` à 80 au lieu de 120.
- Mets `pause_between_quote_batches_seconds` à 0.5 au lieu de 0.25.
- Mets `max_us_symbols` à 3000 ou 5000.

## Important

Yahoo Finance n'est pas une API bourse professionnelle. Pour du vrai temps réel sur toutes les actions US, il faudra plutôt une API dédiée comme Polygon, Finnhub, Alpaca, IEX, Twelve Data, etc.


## Alerte pré-marché US

Cette version demande explicitement à Yahoo Finance les champs `preMarketPrice`, `preMarketChangePercent` et `preMarketVolume`.
Cela permet de détecter un gros gap avant l'ouverture officielle US, par exemple entre la clôture de la veille et le prix de pré-marché.

Dans `config.json`, garde :

```json
"market": {
  "allow_premarket": true,
  "allow_regular": true,
  "allow_afterhours": false,
  "skip_weekends": true
}
```

Pour une détection plus tôt mais avec plus de fausses alertes, baisse `prefilter_gap_pct` à `3.0` et `gap_prev_close_pct` à `5.0`.
