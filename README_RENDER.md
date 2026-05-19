# Mise en ligne sur Render - Crypto Radar Bot

Cette version est prête pour Render : elle lit les identifiants Telegram dans les variables d'environnement :

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Ne mets jamais ton token Telegram dans GitHub.

## Commandes Render

Build command :

```bash
pip install -r requirements.txt
```

Start command :

```bash
python crypto_radar_bot.py
```

## Type de service recommandé

Créer un **Background Worker** sur Render.

## Fichiers à envoyer sur GitHub

- crypto_radar_bot.py
- requirements.txt
- README_FR.md
- README_RENDER.md
- .gitignore

Ne pas envoyer `config.json`.
