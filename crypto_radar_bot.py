#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crypto Radar Bot - Surveillance + alertes Telegram
Version: 1.0 - AFR / Benoît

But :
- Surveiller les paires Binance Spot en USDT
- Détecter le début d'un pic : hausse rapide + volume anormal
- Envoyer une alerte Telegram
- NE PAS acheter automatiquement

Installation :
    pip install -r requirements.txt
    copier config.example.json en config.json
    remplir TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID
    python crypto_radar_bot.py
"""

from __future__ import annotations

import csv
import json
import math
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


APP_NAME = "Crypto Radar Bot"
BINANCE_BASE_URL = "https://api.binance.com"
CONFIG_PATH = Path("config.json")
LOG_DIR = Path("logs")
ALERT_LOG = LOG_DIR / "alertes.csv"


DEFAULT_CONFIG: Dict[str, Any] = {
    "telegram": {
        "bot_token": "",
        "chat_id": ""
    },
    "scan": {
        "interval_seconds": 60,
        "kline_interval": "1m",
        "kline_limit": 60,
        "max_symbols": 150,
        "min_quote_volume_24h_usdt": 1000000,
        "min_last_price_usdt": 0.00000001,
        "cooldown_minutes_per_symbol": 30
    },
    "signals": {
        "pct_1m": 1.5,
        "pct_3m": 2.5,
        "pct_5m": 4.0,
        "volume_ratio": 2.5,
        "min_current_quote_volume_usdt": 50000
    },
    "lists": {
        "only_symbols": [],
        "exclude_symbols": [
            "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
            "EURUSDT", "TRYUSDT", "BRLUSDT", "AEURUSDT"
        ]
    },
    "risk": {
        "message": "Alerte informative uniquement. Ce logiciel ne donne pas un conseil financier et ne passe aucun ordre."
    }
}


@dataclass
class KlinePoint:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time_ms: int
    quote_volume: float


@dataclass
class Signal:
    symbol: str
    price: float
    pct_1m: float
    pct_3m: float
    pct_5m: float
    volume_ratio: float
    current_quote_volume: float
    score: float
    reason: str


stop_requested = False


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def handle_stop(signum, frame) -> None:
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGINT, handle_stop)
signal.signal(signal.SIGTERM, handle_stop)


class BinanceClient:
    def __init__(self, base_url: str = BINANCE_BASE_URL, timeout: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"{APP_NAME}/1.0"
        })

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self.base_url + path
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def exchange_symbols_usdt(self) -> List[str]:
        data = self.get("/api/v3/exchangeInfo")
        symbols = []
        for item in data.get("symbols", []):
            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == "USDT"
                and item.get("isSpotTradingAllowed", True)
            ):
                symbols.append(item["symbol"])
        return sorted(symbols)

    def ticker_24h(self) -> List[Dict[str, Any]]:
        return self.get("/api/v3/ticker/24hr")

    def klines(self, symbol: str, interval: str = "1m", limit: int = 60) -> List[KlinePoint]:
        raw = self.get("/api/v3/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        })
        points: List[KlinePoint] = []
        for row in raw:
            points.append(KlinePoint(
                open_time_ms=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                close_time_ms=int(row[6]),
                quote_volume=float(row[7]),
            ))
        return points


def load_config() -> Dict[str, Any]:
    """Charge config.json puis remplace Telegram par les variables d'environnement si elles existent.

    Sur Render, on ne met jamais le token Telegram dans GitHub.
    On utilise plutôt :
      - TELEGRAM_BOT_TOKEN
      - TELEGRAM_CHAT_ID
    """
    config = DEFAULT_CONFIG.copy()

    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            user_config = json.load(f)
        # Fusion douce avec les valeurs par défaut pour éviter les clés manquantes.
        config = deep_merge(config, user_config)
    else:
        # En local, on garde le comportement pratique : création d'un exemple de config.
        # Sur Render, les variables d'environnement suffisent, donc pas besoin de config.json.
        if not os.environ.get("TELEGRAM_BOT_TOKEN") and not os.environ.get("TELEGRAM_CHAT_ID"):
            CONFIG_PATH.write_text(
                json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            print("Fichier config.json créé. Remplis Telegram puis relance le logiciel.")

    token_env = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_env = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token_env:
        config["telegram"]["bot_token"] = token_env
    if chat_env:
        config["telegram"]["chat_id"] = chat_env

    return config


def deep_merge(default: Dict[str, Any], custom: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in custom.items():
        if isinstance(value, dict) and isinstance(default.get(key), dict):
            default[key] = deep_merge(default[key], value)
        else:
            default[key] = value
    return default


def pct_change(new: float, old: float) -> float:
    if old <= 0:
        return 0.0
    return (new - old) / old * 100.0


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def pick_symbols(client: BinanceClient, config: Dict[str, Any]) -> List[str]:
    scan_cfg = config["scan"]
    lists_cfg = config["lists"]

    only = [s.upper().strip() for s in lists_cfg.get("only_symbols", []) if s.strip()]
    exclude = set(s.upper().strip() for s in lists_cfg.get("exclude_symbols", []) if s.strip())

    if only:
        return [s for s in only if s not in exclude]

    exchange_symbols = set(client.exchange_symbols_usdt())
    tickers = client.ticker_24h()

    min_quote_vol = float(scan_cfg.get("min_quote_volume_24h_usdt", 0))
    min_last_price = float(scan_cfg.get("min_last_price_usdt", 0))
    max_symbols = int(scan_cfg.get("max_symbols", 150))

    candidates: List[Tuple[str, float]] = []
    for t in tickers:
        symbol = str(t.get("symbol", "")).upper()
        if symbol not in exchange_symbols or symbol in exclude:
            continue
        quote_volume = safe_float(t.get("quoteVolume"))
        last_price = safe_float(t.get("lastPrice"))
        if quote_volume >= min_quote_vol and last_price >= min_last_price:
            candidates.append((symbol, quote_volume))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in candidates[:max_symbols]]


def analyze_symbol(symbol: str, klines: List[KlinePoint], config: Dict[str, Any]) -> Optional[Signal]:
    sig_cfg = config["signals"]

    if len(klines) < 25:
        return None

    # On utilise la dernière bougie reçue. Si elle est en cours, c'est justement utile pour détecter tôt.
    current = klines[-1]
    price = current.close

    close_1m_ago = klines[-2].close
    close_3m_ago = klines[-4].close
    close_5m_ago = klines[-6].close

    p1 = pct_change(price, close_1m_ago)
    p3 = pct_change(price, close_3m_ago)
    p5 = pct_change(price, close_5m_ago)

    # Volume moyen des 20 bougies précédentes, sans la bougie actuelle.
    previous = klines[-21:-1]
    avg_quote_volume = sum(k.quote_volume for k in previous) / max(len(previous), 1)
    if avg_quote_volume <= 0:
        return None

    vol_ratio = current.quote_volume / avg_quote_volume
    min_current_qv = float(sig_cfg.get("min_current_quote_volume_usdt", 0))

    trigger_1m = p1 >= float(sig_cfg["pct_1m"])
    trigger_3m = p3 >= float(sig_cfg["pct_3m"])
    trigger_5m = p5 >= float(sig_cfg["pct_5m"])
    trigger_vol = vol_ratio >= float(sig_cfg["volume_ratio"])
    trigger_qv = current.quote_volume >= min_current_qv

    if not (trigger_vol and trigger_qv and (trigger_1m or trigger_3m or trigger_5m)):
        return None

    # Score simple pour classer l'urgence.
    score = max(p1 / max(float(sig_cfg["pct_1m"]), 0.01),
                p3 / max(float(sig_cfg["pct_3m"]), 0.01),
                p5 / max(float(sig_cfg["pct_5m"]), 0.01)) + (vol_ratio / max(float(sig_cfg["volume_ratio"]), 0.01))

    reasons = []
    if trigger_1m:
        reasons.append(f"+{p1:.2f}% en 1 min")
    if trigger_3m:
        reasons.append(f"+{p3:.2f}% en 3 min")
    if trigger_5m:
        reasons.append(f"+{p5:.2f}% en 5 min")
    reasons.append(f"volume x{vol_ratio:.1f}")

    return Signal(
        symbol=symbol,
        price=price,
        pct_1m=p1,
        pct_3m=p3,
        pct_5m=p5,
        volume_ratio=vol_ratio,
        current_quote_volume=current.quote_volume,
        score=score,
        reason=", ".join(reasons)
    )


def telegram_send(config: Dict[str, Any], text: str) -> bool:
    token = str(config["telegram"].get("bot_token", "")).strip()
    chat_id = str(config["telegram"].get("chat_id", "")).strip()

    if not token or not chat_id:
        print("[Telegram non configuré] " + text.replace("\n", " | "))
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as exc:
        print(f"Erreur Telegram: {exc}")
        return False


def format_alert(signal: Signal, risk_message: str) -> str:
    symbol_url = f"https://www.binance.com/fr/trade/{signal.symbol.replace('USDT', '_USDT')}?type=spot"
    return (
        f"🚨 <b>ALERTE CRYPTO - Début de pic possible</b>\n\n"
        f"Symbole : <b>{signal.symbol}</b>\n"
        f"Prix : <b>{signal.price:g} USDT</b>\n\n"
        f"Variation :\n"
        f"• 1 min : <b>{signal.pct_1m:+.2f}%</b>\n"
        f"• 3 min : <b>{signal.pct_3m:+.2f}%</b>\n"
        f"• 5 min : <b>{signal.pct_5m:+.2f}%</b>\n"
        f"• Volume : <b>x{signal.volume_ratio:.1f}</b>\n"
        f"• Volume bougie : <b>{signal.current_quote_volume:,.0f} USDT</b>\n\n"
        f"Raison : {signal.reason}\n"
        f"Score : {signal.score:.2f}\n"
        f"Lien : {symbol_url}\n\n"
        f"⚠️ {risk_message}"
    )


def ensure_alert_log() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    if not ALERT_LOG.exists():
        with ALERT_LOG.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "date",
                "symbol",
                "price",
                "pct_1m",
                "pct_3m",
                "pct_5m",
                "volume_ratio",
                "current_quote_volume_usdt",
                "score",
                "reason"
            ])


def log_alert(signal: Signal) -> None:
    ensure_alert_log()
    with ALERT_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            now_iso(),
            signal.symbol,
            f"{signal.price:.12g}",
            f"{signal.pct_1m:.4f}",
            f"{signal.pct_3m:.4f}",
            f"{signal.pct_5m:.4f}",
            f"{signal.volume_ratio:.4f}",
            f"{signal.current_quote_volume:.2f}",
            f"{signal.score:.4f}",
            signal.reason
        ])


def run() -> None:
    config = load_config()
    client = BinanceClient()

    scan_cfg = config["scan"]
    interval_seconds = int(scan_cfg.get("interval_seconds", 60))
    kline_interval = str(scan_cfg.get("kline_interval", "1m"))
    kline_limit = int(scan_cfg.get("kline_limit", 60))
    cooldown_seconds = int(scan_cfg.get("cooldown_minutes_per_symbol", 30)) * 60

    print("=" * 70)
    print(APP_NAME)
    print("Surveillance Binance Spot USDT - alertes informatives uniquement")
    print("Arrêt : CTRL+C")
    print("=" * 70)

    last_alert_at: Dict[str, float] = {}
    loop_count = 0

    telegram_send(config, f"✅ {APP_NAME} démarré\nSurveillance toutes les {interval_seconds} secondes.")

    while not stop_requested:
        started = time.time()
        loop_count += 1

        try:
            symbols = pick_symbols(client, config)
            print(f"\n[{now_iso()}] Scan {loop_count} - {len(symbols)} symboles surveillés")

            signals: List[Signal] = []

            for i, symbol in enumerate(symbols, start=1):
                if stop_requested:
                    break

                try:
                    points = client.klines(symbol, interval=kline_interval, limit=kline_limit)
                    signal_found = analyze_symbol(symbol, points, config)
                    if signal_found:
                        signals.append(signal_found)
                except requests.HTTPError as exc:
                    print(f"{symbol}: erreur HTTP {exc}")
                except Exception as exc:
                    print(f"{symbol}: erreur {exc}")

                # Petite pause pour rester propre avec l'API.
                if i % 20 == 0:
                    time.sleep(0.2)

            signals.sort(key=lambda s: s.score, reverse=True)

            for signal_found in signals:
                now_ts = time.time()
                last_ts = last_alert_at.get(signal_found.symbol, 0)
                if now_ts - last_ts < cooldown_seconds:
                    continue

                message = format_alert(signal_found, config["risk"]["message"])
                print("\n" + message.replace("<b>", "").replace("</b>", ""))
                telegram_send(config, message)
                log_alert(signal_found)
                last_alert_at[signal_found.symbol] = now_ts

            elapsed = time.time() - started
            sleep_for = max(5, interval_seconds - elapsed)
            print(f"Scan terminé en {elapsed:.1f}s. Prochain scan dans {sleep_for:.0f}s.")

            for _ in range(int(sleep_for)):
                if stop_requested:
                    break
                time.sleep(1)

        except Exception as exc:
            print(f"Erreur générale: {exc}")
            telegram_send(config, f"⚠️ {APP_NAME}: erreur générale\n{exc}")
            time.sleep(15)

    telegram_send(config, f"🛑 {APP_NAME} arrêté.")
    print("Arrêt demandé. Fin du logiciel.")


if __name__ == "__main__":
    run()
