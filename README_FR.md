# Crypto Radar Bot — surveillance + alertes crypto

Ce logiciel surveille les cryptos Binance Spot en **USDT** et envoie une alerte Telegram quand il détecte un possible début de pic :

- hausse rapide sur 1, 3 ou 5 minutes ;
- volume beaucoup plus fort que la moyenne ;
- filtre de volume minimum pour éviter les petites cryptos trop illiquides ;
- délai anti-spam par crypto.

⚠️ Important : ce logiciel **ne passe aucun ordre d'achat**. Il sert uniquement à te prévenir. Ce n'est pas un conseil financier.

---

## 1. Installation Windows

### Étape A — Installer Python
Installe Python 3.11 ou plus récent depuis le site officiel de Python.

Pendant l'installation, coche bien :

```text
Add Python to PATH
```

### Étape B — Dézipper le dossier
Dézippe `crypto_radar_bot.zip`, puis ouvre le dossier.

### Étape C — Installer les dépendances
Double-clique sur :

```text
installer_windows.bat
```

Ou lance dans le terminal :

```bash
pip install -r requirements.txt
```

---

## 2. Configurer Telegram

### A — Créer le bot Telegram
1. Ouvre Telegram.
2. Cherche `@BotFather`.
3. Envoie `/newbot`.
4. Donne un nom au bot.
5. Copie le **token** donné par BotFather.

### B — Récupérer ton chat_id
Méthode simple :
1. Écris un message à ton nouveau bot Telegram.
2. Va dans ton navigateur à cette adresse en remplaçant TOKEN :

```text
https://api.telegram.org/botTOKEN/getUpdates
```

3. Cherche `"chat":{"id":...}`.
4. Copie le nombre.

### C — Remplir config.json
Renomme `config.example.json` en `config.json`, puis mets :

```json
"telegram": {
  "bot_token": "TON_TOKEN_ICI",
  "chat_id": "TON_CHAT_ID_ICI"
}
```

Si Telegram n'est pas configuré, les alertes s'affichent seulement dans la fenêtre.

---

## 3. Lancer le logiciel

Double-clique sur :

```text
lancer_windows.bat
```

Ou lance :

```bash
python crypto_radar_bot.py
```

Pour arrêter : `CTRL + C`.

---

## 4. Réglages principaux

Dans `config.json` :

```json
"signals": {
  "pct_1m": 1.5,
  "pct_3m": 2.5,
  "pct_5m": 4.0,
  "volume_ratio": 2.5,
  "min_current_quote_volume_usdt": 50000
}
```

Explication :

- `pct_1m`: déclenche si la crypto monte au moins de ce pourcentage en 1 minute.
- `pct_3m`: déclenche si elle monte au moins de ce pourcentage en 3 minutes.
- `pct_5m`: déclenche si elle monte au moins de ce pourcentage en 5 minutes.
- `volume_ratio`: volume de la bougie actuelle comparé à la moyenne des 20 précédentes.
- `min_current_quote_volume_usdt`: volume minimum en USDT sur la bougie actuelle.

Réglage plus sensible :

```json
"pct_1m": 1.0,
"pct_3m": 2.0,
"pct_5m": 3.0,
"volume_ratio": 2.0
```

Réglage plus strict :

```json
"pct_1m": 2.0,
"pct_3m": 3.5,
"pct_5m": 5.0,
"volume_ratio": 3.5
```

---

## 5. Surveiller seulement certaines cryptos

Dans `config.json`, ajoute par exemple :

```json
"only_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
```

Si la liste est vide, le logiciel surveille automatiquement les paires USDT les plus liquides.

---

## 6. Historique des alertes

Les alertes sont enregistrées dans :

```text
logs/alertes.csv
```

Tu peux l'ouvrir avec Excel.

---

## 7. À savoir

Ce système détecte les mouvements rapides, mais il peut y avoir :
- des faux signaux ;
- des alertes trop tardives sur des mouvements très violents ;
- des mouvements qui retombent juste après l'alerte.

Il faut toujours vérifier le graphique avant d'acheter.
