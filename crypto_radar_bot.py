#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Radar Bot - Surveillance actions US + alertes Telegram
Version: 2.0 - adaptée depuis le Crypto Radar Bot

But :
- Surveiller une liste d'actions US via Yahoo Finance
- Détecter un début de mouvement : gap avant ouverture, hausse rapide, volume anormal
- Envoyer une alerte Telegram
- NE PAS acheter automatiquement

Installation :
    pip install -r requirements.txt
    copier config.example.json en config.json si utilisation locale
    remplir TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID ou variables d'environnement Render
    python crypto_radar_bot.py

Note importante :
- Cette version utilise l'endpoint public/non officiel de Yahoo Finance.
- Pour un usage professionnel très fiable ou pour scanner tout le marché, il faut une API bourse dédiée.
"""

from __future__ import annotations

import copy
import csv
import json
import math
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests


APP_NAME = "Stock Radar Bot"
YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
CONFIG_PATH = Path("config.json")
LOG_DIR = Path("logs")
ALERT_LOG = LOG_DIR / "alertes_actions.csv"
US_EASTERN = ZoneInfo("America/New_York")


DEFAULT_WATCHLIST = [
    "REPL", "NVDA", "AMD", "TSLA", "PLTR", "SMCI", "SOUN", "GME", "AMC",
    "AAPL", "MSFT", "META", "GOOGL", "AMZN", "AVGO", "MSTR"
]


DEFAULT_CONFIG: Dict[str, Any] = {
    "telegram": {
        "bot_token": "",
        "chat_id": ""
    },
    "scan": {
        "interval_seconds": 60,
        "chart_interval": "1m",
        "chart_range": "1d",
        "include_prepost": True,
        "max_symbols": 50,
        "cooldown_minutes_per_symbol": 45,
        "max_last_bar_age_minutes": 30
    },
    "market": {
        "allow_premarket": True,
        "allow_regular": True,
        "allow_afterhours": False,
        "skip_weekends": True
    },
    "signals": {
        "min_price_usd": 1.0,
        "pct_1m": 0.8,
        "pct_5m": 2.0,
        "pct_15m": 4.0,
        "pct_30m": 7.0,
        "gap_prev_close_pct": 8.0,
        "volume_ratio": 3.0,
        "min_bar_volume_shares": 10000
    },
    "lists": {
        "only_symbols": DEFAULT_WATCHLIST,
        "exclude_symbols": []
    },
    "risk": {
        "message": "Alerte informative uniquement. Ce logiciel ne donne pas un conseil financier et ne passe aucun ordre."
    }
}


@dataclass
class StockBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class ChartData:
    symbol: str
    previous_close: float
    currency: str
    bars: List[StockBar]


@dataclass
class Signal:
    symbol: str
    price: float
    previous_close: float
    stage: str
    pct_1m: float
    pct_5m: float
    pct_15m: float
    pct_30m: float
    gap_prev_close_pct: float
    volume_ratio: float
    bar_volume_shares: float
    score: float
    reason: str
    bar_time_local: str


stop_requested = False


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def handle_stop(signum, frame) -> None:
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGINT, handle_stop)
signal.signal(signal.SIGTERM, handle_stop)


class YahooFinanceClient:
    def __init__(self, base_url: str = YAHOO_CHART_BASE_URL, timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        })

    def chart(self, symbol: str, interval: str, range_: str, include_prepost: bool) -> ChartData:
        safe_symbol = quote(symbol.upper().strip(), safe="")
        url = f"{self.base_url}/{safe_symbol}"
        params = {
            "interval": interval,
            "range": range_,
            "includePrePost": "true" if include_prepost else "false",
            "events": "div,splits",
        }
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()

        chart = data.get("chart", {})
        error = chart.get("error")
        if error:
            raise RuntimeError(error.get("description") or str(error))

        result = (chart.get("result") or [None])[0]
        if not result:
            raise RuntimeError("Aucune donnée Yahoo Finance reçue")

        meta = result.get("meta", {})
        previous_close = safe_float(
            meta.get("previousClose"),
            default=safe_float(meta.get("chartPreviousClose"), 0.0)
        )
        currency = str(meta.get("currency", "USD"))

        timestamps = result.get("timestamp") or []
        quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]

        opens = quote_data.get("open") or []
        highs = quote_data.get("high") or []
        lows = quote_data.get("low") or []
        closes = quote_data.get("close") or []
        volumes = quote_data.get("volume") or []

        bars: List[StockBar] = []
        for i, ts in enumerate(timestamps):
            close = value_at(closes, i)
            if close is None:
                continue
            bars.append(StockBar(
                timestamp=int(ts),
                open=safe_float(value_at(opens, i), close),
                high=safe_float(value_at(highs, i), close),
                low=safe_float(value_at(lows, i), close),
                close=safe_float(close, 0.0),
                volume=safe_float(value_at(volumes, i), 0.0),
            ))

        if not bars:
            raise RuntimeError("Aucune bougie exploitable")

        return ChartData(
            symbol=symbol.upper().strip(),
            previous_close=previous_close,
            currency=currency,
            bars=bars,
        )


def value_at(values: List[Any], index: int) -> Any:
    try:
        return values[index]
    except Exception:
        return None


def load_config() -> Dict[str, Any]:
    """Charge config.json puis remplace Telegram par les variables d'environnement si elles existent.

    Sur Render, ne mets jamais le token Telegram dans GitHub.
    Utilise plutôt :
      - TELEGRAM_BOT_TOKEN
      - TELEGRAM_CHAT_ID
    """
    config = copy.deepcopy(DEFAULT_CONFIG)

    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            user_config = json.load(f)
        config = deep_merge(config, user_config)
    else:
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
        if value is None:
            return default
        x = float(value)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def pick_symbols(config: Dict[str, Any]) -> List[str]:
    scan_cfg = config["scan"]
    lists_cfg = config["lists"]

    only = [s.upper().strip() for s in lists_cfg.get("only_symbols", []) if str(s).strip()]
    exclude = set(s.upper().strip() for s in lists_cfg.get("exclude_symbols", []) if str(s).strip())
    max_symbols = int(scan_cfg.get("max_symbols", 50))

    if not only:
        only = DEFAULT_WATCHLIST[:]

    cleaned: List[str] = []
    for symbol in only:
        if symbol in exclude:
            continue
        if symbol not in cleaned:
            cleaned.append(symbol)

    return cleaned[:max_symbols]


def market_stage_for_timestamp(ts: int) -> str:
    dt_et = datetime.fromtimestamp(ts, timezone.utc).astimezone(US_EASTERN)
    t = dt_et.time()

    if dtime(4, 0) <= t < dtime(9, 30):
        return "pré-marché"
    if dtime(9, 30) <= t < dtime(16, 0):
        return "marché ouvert"
    if dtime(16, 0) <= t < dtime(20, 0):
        return "after-hours"
    return "fermé"


def stage_allowed(stage: str, config: Dict[str, Any]) -> bool:
    market_cfg = config["market"]
    if stage == "pré-marché":
        return bool(market_cfg.get("allow_premarket", True))
    if stage == "marché ouvert":
        return bool(market_cfg.get("allow_regular", True))
    if stage == "after-hours":
        return bool(market_cfg.get("allow_afterhours", False))
    return False


def is_weekend_now_et() -> bool:
    return datetime.now(timezone.utc).astimezone(US_EASTERN).weekday() >= 5


def bar_age_minutes(bar: StockBar) -> float:
    now_ts = datetime.now(timezone.utc).timestamp()
    return max(0.0, (now_ts - bar.timestamp) / 60.0)


def close_n_bars_ago(bars: List[StockBar], n: int) -> float:
    if len(bars) <= n:
        return bars[0].close
    return bars[-1 - n].close


def analyze_symbol(chart: ChartData, config: Dict[str, Any]) -> Optional[Signal]:
    sig_cfg = config["signals"]
    scan_cfg = config["scan"]

    bars = chart.bars
    if len(bars) < 10:
        return None

    current = bars[-1]
    price = current.close

    min_price = float(sig_cfg.get("min_price_usd", 1.0))
    if price < min_price:
        return None

    stage = market_stage_for_timestamp(current.timestamp)
    if config["market"].get("skip_weekends", True) and is_weekend_now_et():
        return None
    if not stage_allowed(stage, config):
        return None

    max_age = float(scan_cfg.get("max_last_bar_age_minutes", 30))
    if bar_age_minutes(current) > max_age:
        return None

    p1 = pct_change(price, close_n_bars_ago(bars, 1))
    p5 = pct_change(price, close_n_bars_ago(bars, 5))
    p15 = pct_change(price, close_n_bars_ago(bars, 15))
    p30 = pct_change(price, close_n_bars_ago(bars, 30))
    gap = pct_change(price, chart.previous_close)

    previous = [b.volume for b in bars[-21:-1] if b.volume and b.volume > 0]
    avg_volume = sum(previous) / len(previous) if previous else 0.0
    min_bar_vol = float(sig_cfg.get("min_bar_volume_shares", 10000))

    if avg_volume > 0:
        vol_ratio = current.volume / avg_volume
    else:
        vol_ratio = 999.0 if current.volume >= min_bar_vol else 0.0

    trigger_1m = p1 >= float(sig_cfg["pct_1m"])
    trigger_5m = p5 >= float(sig_cfg["pct_5m"])
    trigger_15m = p15 >= float(sig_cfg["pct_15m"])
    trigger_30m = p30 >= float(sig_cfg["pct_30m"])
    trigger_volume = vol_ratio >= float(sig_cfg["volume_ratio"])
    trigger_min_vol = current.volume >= min_bar_vol
    trigger_gap = gap >= float(sig_cfg["gap_prev_close_pct"])

    # Deux types d'alertes :
    # 1) gap brutal depuis la clôture de la veille, utile pour REPL et les biotechs à news
    # 2) mouvement qui démarre vraiment en intraday, avec volume anormal
    gap_signal = trigger_gap and trigger_min_vol
    momentum_signal = trigger_volume and trigger_min_vol and (trigger_1m or trigger_5m or trigger_15m or trigger_30m)

    if not (gap_signal or momentum_signal):
        return None

    reasons: List[str] = []
    if trigger_gap:
        reasons.append(f"gap +{gap:.2f}% vs clôture veille")
    if trigger_1m:
        reasons.append(f"+{p1:.2f}% en 1 min")
    if trigger_5m:
        reasons.append(f"+{p5:.2f}% en 5 min")
    if trigger_15m:
        reasons.append(f"+{p15:.2f}% en 15 min")
    if trigger_30m:
        reasons.append(f"+{p30:.2f}% en 30 min")
    if trigger_volume:
        reasons.append(f"volume x{vol_ratio:.1f}")
    reasons.append(f"volume bougie {current.volume:,.0f} titres")

    score = 0.0
    score += max(0.0, gap / max(float(sig_cfg["gap_prev_close_pct"]), 0.01))
    score += max(0.0, p1 / max(float(sig_cfg["pct_1m"]), 0.01))
    score += max(0.0, p5 / max(float(sig_cfg["pct_5m"]), 0.01))
    score += max(0.0, p15 / max(float(sig_cfg["pct_15m"]), 0.01))
    score += max(0.0, p30 / max(float(sig_cfg["pct_30m"]), 0.01))
    score += max(0.0, vol_ratio / max(float(sig_cfg["volume_ratio"]), 0.01))

    bar_dt_local = datetime.fromtimestamp(current.timestamp, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    return Signal(
        symbol=chart.symbol,
        price=price,
        previous_close=chart.previous_close,
        stage=stage,
        pct_1m=p1,
        pct_5m=p5,
        pct_15m=p15,
        pct_30m=p30,
        gap_prev_close_pct=gap,
        volume_ratio=vol_ratio,
        bar_volume_shares=current.volume,
        score=score,
        reason=", ".join(reasons),
        bar_time_local=bar_dt_local,
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
    yahoo_url = f"https://finance.yahoo.com/quote/{signal.symbol}"
    tradingview_url = f"https://www.tradingview.com/symbols/NASDAQ-{signal.symbol}/"

    return (
        f"🚨 <b>ALERTE ACTION - Mouvement possible</b>\n\n"
        f"Action : <b>{signal.symbol}</b>\n"
        f"Session : <b>{signal.stage}</b>\n"
        f"Prix : <b>{signal.price:.4g} $</b>\n"
        f"Clôture veille : <b>{signal.previous_close:.4g} $</b>\n"
        f"Heure bougie : <b>{signal.bar_time_local}</b>\n\n"
        f"Variation :\n"
        f"• vs clôture veille : <b>{signal.gap_prev_close_pct:+.2f}%</b>\n"
        f"• 1 min : <b>{signal.pct_1m:+.2f}%</b>\n"
        f"• 5 min : <b>{signal.pct_5m:+.2f}%</b>\n"
        f"• 15 min : <b>{signal.pct_15m:+.2f}%</b>\n"
        f"• 30 min : <b>{signal.pct_30m:+.2f}%</b>\n"
        f"• Volume : <b>x{signal.volume_ratio:.1f}</b>\n"
        f"• Volume bougie : <b>{signal.bar_volume_shares:,.0f} titres</b>\n\n"
        f"Raison : {signal.reason}\n"
        f"Score : {signal.score:.2f}\n"
        f"Yahoo : {yahoo_url}\n"
        f"TradingView : {tradingview_url}\n\n"
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
                "stage",
                "price",
                "previous_close",
                "gap_prev_close_pct",
                "pct_1m",
                "pct_5m",
                "pct_15m",
                "pct_30m",
                "volume_ratio",
                "bar_volume_shares",
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
            signal.stage,
            f"{signal.price:.12g}",
            f"{signal.previous_close:.12g}",
            f"{signal.gap_prev_close_pct:.4f}",
            f"{signal.pct_1m:.4f}",
            f"{signal.pct_5m:.4f}",
            f"{signal.pct_15m:.4f}",
            f"{signal.pct_30m:.4f}",
            f"{signal.volume_ratio:.4f}",
            f"{signal.bar_volume_shares:.0f}",
            f"{signal.score:.4f}",
            signal.reason
        ])


def run() -> None:
    config = load_config()
    client = YahooFinanceClient()

    scan_cfg = config["scan"]
    interval_seconds = int(scan_cfg.get("interval_seconds", 60))
    chart_interval = str(scan_cfg.get("chart_interval", "1m"))
    chart_range = str(scan_cfg.get("chart_range", "1d"))
    include_prepost = bool(scan_cfg.get("include_prepost", True))
    cooldown_seconds = int(scan_cfg.get("cooldown_minutes_per_symbol", 45)) * 60

    print("=" * 70)
    print(APP_NAME)
    print("Surveillance actions US - alertes informatives uniquement")
    print("Source données : Yahoo Finance public/non officiel")
    print("Arrêt : CTRL+C")
    print("=" * 70)

    last_alert_at: Dict[str, float] = {}
    loop_count = 0

    telegram_send(config, f"✅ {APP_NAME} démarré\nSurveillance toutes les {interval_seconds} secondes.")

    while not stop_requested:
        started = time.time()
        loop_count += 1

        try:
            symbols = pick_symbols(config)
            print(f"\n[{now_iso()}] Scan {loop_count} - {len(symbols)} actions surveillées")

            signals: List[Signal] = []

            for symbol in symbols:
                if stop_requested:
                    break

                try:
                    chart = client.chart(
                        symbol=symbol,
                        interval=chart_interval,
                        range_=chart_range,
                        include_prepost=include_prepost,
                    )
                    signal_found = analyze_symbol(chart, config)
                    if signal_found:
                        signals.append(signal_found)
                except requests.HTTPError as exc:
                    print(f"{symbol}: erreur HTTP {exc}")
                except Exception as exc:
                    print(f"{symbol}: erreur {exc}")

                # Pause légère pour éviter de taper trop fort sur Yahoo.
                time.sleep(0.15)

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
