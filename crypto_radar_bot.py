#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Radar Bot - CAC 40 + actions US + alertes Telegram
Version: 4.0 - pré-market + after-hours + penny stocks + gaps

But :
- Surveiller les actions CAC 40 + une watchlist perso + les actions US.
- Détecter les gros gaps, départs de hausse, volume anormal, rebonds depuis le plus bas de séance.
- Alerter en pré-ouverture, marché ouvert et after-hours.
- Envoyer une alerte Telegram.
- NE PAS acheter automatiquement.

Important :
- La source de prix utilisée ici est Yahoo Finance public/non officiel.
- La liste des actions US vient du répertoire Nasdaq Trader.
- Scanner toutes les actions US gratuitement n'est pas garanti en vrai temps réel.

Commande Render :
    python crypto_radar_bot.py
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
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests


APP_NAME = "Stock Radar Bot"
APP_VERSION = "4.0"
CONFIG_PATH = Path("config.json")
LOG_DIR = Path("logs")
CACHE_DIR = Path("cache")
ALERT_LOG = LOG_DIR / "alertes_actions.csv"

YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
DEFAULT_TZ = ZoneInfo("America/New_York")

# Tickers Yahoo Finance pour les valeurs CAC 40.
# A vérifier de temps en temps quand la composition du CAC change.
CAC40_SYMBOLS = [
    "AC.PA", "AI.PA", "AIR.PA", "MT.AS", "CS.PA", "BNP.PA", "EN.PA", "BVI.PA",
    "CAP.PA", "CA.PA", "ACA.PA", "BN.PA", "DSY.PA", "EDEN.PA", "ENGI.PA",
    "EL.PA", "ERF.PA", "RMS.PA", "KER.PA", "OR.PA", "LR.PA", "MC.PA",
    "ML.PA", "ORA.PA", "RI.PA", "PUB.PA", "RNO.PA", "SAF.PA", "SGO.PA",
    "SAN.PA", "SU.PA", "GLE.PA", "STLAP.PA", "STMPA.PA", "TEP.PA", "HO.PA",
    "TTE.PA", "URW.PA", "VIE.PA", "DG.PA"
]

# Liste courte prioritaire : elle est scannée en 1 minute même si elle ne passe pas le pré-filtre.
# OPTU est ajouté explicitement pour éviter de dépendre seulement de la liste Nasdaq Trader.
DEFAULT_WATCHLIST = [
    "OPTU", "REPL", "NVDA", "AMD", "TSLA", "PLTR", "SMCI", "SOUN", "GME", "AMC",
    "AAPL", "MSFT", "META", "GOOGL", "AMZN", "AVGO", "MSTR"
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "telegram": {
        "bot_token": "",
        "chat_id": ""
    },
    "scan": {
        # Toutes les 60 secondes, le bot lance un tour de surveillance.
        # Descendre sous 60s avec toutes les actions US peut provoquer plus d'erreurs Yahoo.
        "interval_seconds": 60,

        # Mode recommandé pour très grande liste :
        # 1) pré-scan rapide par paquets de quotes Yahoo
        # 2) analyse 1 minute seulement sur les candidats suspects + watchlist.
        "large_universe_mode": True,
        "quote_batch_size": 120,
        "pause_between_quote_batches_seconds": 0.20,
        "max_candidate_charts": 260,

        # La watchlist perso passe en analyse graphique même si elle ne passe pas le pré-filtre.
        "always_chart_watchlist": True,
        "max_priority_charts": 80,

        # Mode analyse graphique 1m.
        "chart_interval": "1m",
        "chart_range": "1d",
        "include_prepost": True,
        "pause_between_chart_requests_seconds": 0.05,

        # Anti-spam Telegram.
        "cooldown_minutes_per_symbol": 30,
        "max_last_bar_age_minutes": 90,

        # Recharge la liste US toutes les 6h.
        "refresh_symbol_universe_minutes": 360
    },
    "market": {
        "allow_premarket": True,
        "allow_regular": True,
        "allow_afterhours": True,
        "skip_weekends": True
    },
    "signals": {
        # Important pour les actions comme OPTU : on n'exclut plus les titres sous 1$.
        # Filtre volontairement bas, compensé par les filtres volume/gap.
        "min_price_usd": 0.05,
        "min_price_eur": 0.50,
        "penny_stock_price_usd": 1.00,

        # Détection momentum sur bougies 1m.
        "pct_1m": 1.0,
        "pct_5m": 2.5,
        "pct_15m": 5.0,
        "pct_30m": 8.0,

        # Détection gap / news.
        "gap_prev_close_pct": 7.0,
        "penny_gap_prev_close_pct": 9.0,
        "high_gap_no_volume_pct": 14.0,

        # Détection depuis le plus bas de la séance : utile quand l'action a déjà explosé.
        "pct_from_session_low": 8.0,
        "penny_pct_from_session_low": 12.0,

        # Cassure du plus haut récent.
        "breakout_pct": 1.5,

        # Volume anormal sur bougie 1m.
        "volume_ratio": 2.2,
        "min_bar_volume_shares": 8000,
        "penny_min_bar_volume_shares": 20000,

        # Pré-filtre pour scanner énormément d'actions sans faire 7000 graphiques.
        "prefilter_gap_pct": 3.0,
        "prefilter_penny_gap_pct": 5.0,
        "prefilter_from_day_low_pct": 7.0,
        "prefilter_min_day_volume_shares": 15000,
        "prefilter_day_volume_vs_avg": 0.03,
        "prefilter_min_abs_change_pct_for_volume": 1.5,

        # Alerte directe depuis la quote, même si Yahoo ne donne pas de graphique 1m.
        "quote_gap_alert_pct": 7.0,
        "quote_penny_gap_alert_pct": 9.0,
        "quote_from_day_low_alert_pct": 10.0,
        "quote_volume_alert_ratio": 0.15
    },
    "lists": {
        # Tu peux ajouter tes actions favorites ici.
        "only_symbols": DEFAULT_WATCHLIST,

        # CAC 40 activé par défaut.
        "include_cac40": True,

        # Toutes les actions US activées par défaut.
        "include_us_all": True,

        # ETF / produits dérivés désactivés au début pour éviter trop de bruit.
        "include_us_etfs": False,
        "include_us_warrants_units_rights": False,

        # 0 = pas de limite. Si Yahoo bloque, mets 3000 ou 5000.
        "max_us_symbols": 0,

        # Fichiers facultatifs, un symbole par ligne.
        "symbol_files": ["symbols_perso.txt"],
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
    exchange_timezone_name: str
    bars: List[StockBar]


@dataclass
class QuoteData:
    symbol: str
    price: float
    previous_close: float
    currency: str
    market_state: str
    exchange_timezone_name: str
    change_pct: float
    day_volume: float
    avg_volume: float
    market_time: int
    open_price: float
    day_high: float
    day_low: float
    source: str = "quote"


@dataclass
class Signal:
    symbol: str
    price: float
    previous_close: float
    currency: str
    exchange_timezone_name: str
    stage: str
    pct_1m: float
    pct_5m: float
    pct_15m: float
    pct_30m: float
    gap_prev_close_pct: float
    pct_from_session_low: float
    breakout_pct: float
    volume_ratio: float
    bar_volume_shares: float
    day_volume_shares: float
    score: float
    reason: str
    bar_time_local: str
    source: str


stop_requested = False


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def handle_stop(signum, frame) -> None:
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGINT, handle_stop)
signal.signal(signal.SIGTERM, handle_stop)


class YahooFinanceClient:
    def __init__(self, timeout: int = 20) -> None:
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

    def quotes(self, symbols: List[str]) -> List[QuoteData]:
        clean = [s.upper().strip() for s in symbols if str(s).strip()]
        if not clean:
            return []

        fields = ",".join([
            "symbol",
            "currency",
            "exchangeTimezoneName",
            "marketState",
            "regularMarketPreviousClose",
            "previousClose",
            "regularMarketPrice",
            "regularMarketChangePercent",
            "regularMarketVolume",
            "regularMarketTime",
            "regularMarketOpen",
            "regularMarketDayHigh",
            "regularMarketDayLow",
            "preMarketPrice",
            "preMarketChangePercent",
            "preMarketVolume",
            "preMarketTime",
            "postMarketPrice",
            "postMarketChangePercent",
            "postMarketVolume",
            "postMarketTime",
            "averageDailyVolume10Day",
            "averageDailyVolume3Month",
        ])

        r = self.session.get(
            YAHOO_QUOTE_URL,
            params={"symbols": ",".join(clean), "fields": fields},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        results = ((data.get("quoteResponse") or {}).get("result") or [])

        out: List[QuoteData] = []
        for item in results:
            symbol = str(item.get("symbol") or "").upper().strip()
            if not symbol:
                continue

            market_state = str(item.get("marketState") or "UNKNOWN").upper()
            previous_close = first_float(item, ["regularMarketPreviousClose", "previousClose"])
            if previous_close <= 0:
                continue

            regular_price = first_float(item, ["regularMarketPrice"])
            pre_price = first_float(item, ["preMarketPrice"])
            post_price = first_float(item, ["postMarketPrice"])

            if market_state in {"PRE", "PREPRE"}:
                price = first_nonzero([pre_price, regular_price, post_price])
                change_pct = safe_float(item.get("preMarketChangePercent"), pct_change(price, previous_close))
                day_volume = first_float(item, ["preMarketVolume", "regularMarketVolume"])
                market_time = int(first_float(item, ["preMarketTime", "regularMarketTime"]))
            elif market_state in {"POST", "POSTPOST"}:
                price = first_nonzero([post_price, regular_price, pre_price])
                change_pct = safe_float(item.get("postMarketChangePercent"), pct_change(price, previous_close))
                day_volume = first_float(item, ["postMarketVolume", "regularMarketVolume"])
                market_time = int(first_float(item, ["postMarketTime", "regularMarketTime"]))
            else:
                price = first_nonzero([regular_price, pre_price, post_price])
                change_pct = safe_float(item.get("regularMarketChangePercent"), pct_change(price, previous_close))
                day_volume = first_float(item, ["regularMarketVolume", "preMarketVolume", "postMarketVolume"])
                market_time = int(first_float(item, ["regularMarketTime", "preMarketTime", "postMarketTime"]))

            if price <= 0:
                continue

            open_price = first_float(item, ["regularMarketOpen"])
            day_high = first_float(item, ["regularMarketDayHigh"])
            day_low = first_float(item, ["regularMarketDayLow"])
            # En pré/post marché, Yahoo ne remplit pas toujours high/low. On garde au moins un low exploitable.
            if day_high <= 0:
                day_high = max(price, open_price, previous_close)
            if day_low <= 0:
                day_low = min(x for x in [price, open_price, previous_close] if x > 0)

            avg_volume = first_float(item, ["averageDailyVolume10Day", "averageDailyVolume3Month"])
            out.append(QuoteData(
                symbol=symbol,
                price=price,
                previous_close=previous_close,
                currency=str(item.get("currency") or "USD"),
                market_state=market_state,
                exchange_timezone_name=str(item.get("exchangeTimezoneName") or "America/New_York"),
                change_pct=change_pct,
                day_volume=day_volume,
                avg_volume=avg_volume,
                market_time=market_time,
                open_price=open_price,
                day_high=day_high,
                day_low=day_low,
            ))
        return out

    def chart(self, symbol: str, interval: str, range_: str, include_prepost: bool) -> ChartData:
        symbol = symbol.upper().strip()
        safe_symbol = quote(symbol, safe="")
        url = f"{YAHOO_CHART_BASE_URL}/{safe_symbol}"
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
        exchange_tz = str(meta.get("exchangeTimezoneName") or "America/New_York")

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
            symbol=symbol,
            previous_close=previous_close,
            currency=currency,
            exchange_timezone_name=exchange_tz,
            bars=bars,
        )


class SymbolUniverseLoader:
    def __init__(self, timeout: int = 25) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 StockRadarBot/4.0",
            "Accept": "text/plain,*/*",
        })

    def download_text(self, url: str) -> str:
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.text

    def load_us_symbols(self, config: Dict[str, Any]) -> List[str]:
        lists_cfg = config["lists"]
        include_etfs = bool(lists_cfg.get("include_us_etfs", False))
        include_specials = bool(lists_cfg.get("include_us_warrants_units_rights", False))
        max_us = int(lists_cfg.get("max_us_symbols", 0) or 0)

        CACHE_DIR.mkdir(exist_ok=True)
        cache_file = CACHE_DIR / "us_symbols.txt"
        cache_meta = CACHE_DIR / "us_symbols_meta.json"

        refresh_minutes = int(config["scan"].get("refresh_symbol_universe_minutes", 360))
        if cache_file.exists() and cache_meta.exists():
            try:
                meta = json.loads(cache_meta.read_text(encoding="utf-8"))
                age_min = (time.time() - float(meta.get("created_at", 0))) / 60
                if age_min < refresh_minutes:
                    cached = read_symbol_file(cache_file)
                    return cached[:max_us] if max_us > 0 else cached
            except Exception:
                pass

        try:
            text = self.download_text(NASDAQ_TRADED_URL)
            symbols = parse_nasdaq_traded(text, include_etfs, include_specials)
            symbols = dedupe(symbols)
            if max_us > 0:
                symbols = symbols[:max_us]
            if symbols:
                cache_file.write_text("\n".join(symbols) + "\n", encoding="utf-8")
                cache_meta.write_text(json.dumps({"created_at": time.time(), "count": len(symbols)}, indent=2), encoding="utf-8")
                return symbols
        except Exception as exc:
            print(f"Impossible de télécharger la liste US Nasdaq Trader: {exc}")

        if cache_file.exists():
            print("Utilisation du cache existant pour la liste US.")
            cached = read_symbol_file(cache_file)
            return cached[:max_us] if max_us > 0 else cached

        raise RuntimeError("Impossible de charger la liste des actions US.")


def parse_nasdaq_traded(text: str, include_etfs: bool, include_specials: bool) -> List[str]:
    # Colonnes : Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|...
    out: List[str] = []
    for line in text.splitlines():
        if not line or line.startswith("Nasdaq Traded|") or line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        nasdaq_traded, symbol, name, _exchange, _market, etf, _lot, test_issue = parts[:8]
        if nasdaq_traded.upper() != "Y":
            continue
        if test_issue.upper() != "N":
            continue
        if etf.upper() == "Y" and not include_etfs:
            continue
        if not should_keep_us_security(name, include_specials):
            continue
        out.append(normalize_us_yahoo_symbol(symbol))
    return out


def should_keep_us_security(name: str, include_specials: bool) -> bool:
    n = name.lower()
    if include_specials:
        return True

    reject_words = [
        "warrant", " unit", " units", " right", " rights", "preferred", "preference",
        "depositary share", "note", "notes", "bond", "debenture", "etf", "etn",
        "fund", "trust", "acquisition corp. - unit", "spac unit", "income strategy"
    ]
    if any(w in n for w in reject_words):
        return False

    keep_words = [
        "common stock", "common shares", "ordinary share", "ordinary shares",
        "american depositary", "ads", "class a common", "class b common",
        "class c common", "class a ordinary", "class b ordinary", "class c ordinary"
    ]
    return any(w in n for w in keep_words)


def normalize_us_yahoo_symbol(symbol: str) -> str:
    # Yahoo utilise BRK-B au lieu de BRK.B pour les actions US à classes.
    return symbol.strip().upper().replace(".", "-")


def read_symbol_file(path: Path) -> List[str]:
    out: List[str] = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip().upper()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return dedupe(out)


def value_at(values: List[Any], index: int) -> Any:
    try:
        return values[index]
    except Exception:
        return None


def load_config() -> Dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)

    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            user_config = json.load(f)
        config = deep_merge(config, user_config)
    else:
        try:
            CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
            print("config.json absent : un fichier par défaut vient d'être créé.")
        except Exception:
            pass

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


def first_float(item: Dict[str, Any], keys: List[str]) -> float:
    for key in keys:
        value = safe_float(item.get(key), 0.0)
        if value > 0:
            return value
    return 0.0


def first_nonzero(values: Iterable[float]) -> float:
    for value in values:
        if value and value > 0:
            return value
    return 0.0


def dedupe(items: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for item in items:
        s = str(item).upper().strip()
        if not s or s in seen:
            continue
        cleaned.append(s)
        seen.add(s)
    return cleaned


def chunks(items: List[str], size: int) -> Iterable[List[str]]:
    size = max(1, size)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def build_symbol_universe(config: Dict[str, Any], loader: SymbolUniverseLoader) -> List[str]:
    lists_cfg = config["lists"]

    symbols: List[str] = []
    symbols.extend([s.upper().strip() for s in lists_cfg.get("only_symbols", []) if str(s).strip()])

    if bool(lists_cfg.get("include_cac40", False)):
        symbols.extend(CAC40_SYMBOLS)

    for file_name in lists_cfg.get("symbol_files", []) or []:
        try:
            symbols.extend(read_symbol_file(Path(str(file_name))))
        except Exception as exc:
            print(f"Impossible de lire {file_name}: {exc}")

    if bool(lists_cfg.get("include_us_all", False)):
        symbols.extend(loader.load_us_symbols(config))

    exclude = set(s.upper().strip() for s in lists_cfg.get("exclude_symbols", []) if str(s).strip())
    return [s for s in dedupe(symbols) if s not in exclude]


def priority_symbols(config: Dict[str, Any], universe: List[str]) -> List[str]:
    lists_cfg = config["lists"]
    symbols: List[str] = []
    symbols.extend([s.upper().strip() for s in lists_cfg.get("only_symbols", []) if str(s).strip()])
    for file_name in lists_cfg.get("symbol_files", []) or []:
        try:
            symbols.extend(read_symbol_file(Path(str(file_name))))
        except Exception:
            pass
    universe_set = set(universe)
    out = [s for s in dedupe(symbols) if s in universe_set or "." not in s]
    max_priority = int(config["scan"].get("max_priority_charts", 80))
    return out[:max_priority]


def timezone_from_name(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return DEFAULT_TZ


def market_stage_for_timestamp(ts: int, exchange_tz_name: str) -> str:
    tz = timezone_from_name(exchange_tz_name)
    dt_local = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz)
    t = dt_local.time()

    if exchange_tz_name == "America/New_York":
        if dtime(4, 0) <= t < dtime(9, 30):
            return "pré-marché"
        if dtime(9, 30) <= t < dtime(16, 0):
            return "marché ouvert"
        if dtime(16, 0) <= t < dtime(20, 0):
            return "after-hours"
        return "fermé"

    if exchange_tz_name in {"Europe/Paris", "Europe/Amsterdam"}:
        if dtime(9, 0) <= t < dtime(17, 30):
            return "marché ouvert"
        return "fermé"

    if dtime(9, 0) <= t < dtime(17, 30):
        return "marché ouvert"
    return "fermé"


def stage_from_market_state(market_state: str, exchange_tz_name: str, market_time: int) -> str:
    s = market_state.upper()
    if s in {"PRE", "PREPRE"}:
        return "pré-marché"
    if s in {"REGULAR", "REGULAR_MARKET"}:
        return "marché ouvert"
    if s in {"POST", "POSTPOST"}:
        return "after-hours"
    if market_time > 0:
        return market_stage_for_timestamp(market_time, exchange_tz_name)
    return "fermé"


def stage_allowed(stage: str, config: Dict[str, Any]) -> bool:
    market_cfg = config["market"]
    if stage == "pré-marché":
        return bool(market_cfg.get("allow_premarket", True))
    if stage == "marché ouvert":
        return bool(market_cfg.get("allow_regular", True))
    if stage == "after-hours":
        return bool(market_cfg.get("allow_afterhours", True))
    return False


def is_weekend_now(exchange_tz_name: str) -> bool:
    tz = timezone_from_name(exchange_tz_name)
    return datetime.now(timezone.utc).astimezone(tz).weekday() >= 5


def bar_age_minutes(bar: StockBar) -> float:
    now_ts = datetime.now(timezone.utc).timestamp()
    return max(0.0, (now_ts - bar.timestamp) / 60.0)


def close_n_bars_ago(bars: List[StockBar], n: int) -> float:
    if len(bars) <= n:
        return bars[0].close
    return bars[-1 - n].close


def min_price_for_currency(config: Dict[str, Any], currency: str) -> float:
    sig_cfg = config["signals"]
    return float(sig_cfg.get("min_price_eur", 0.50) if currency.upper() == "EUR" else sig_cfg.get("min_price_usd", 0.05))


def is_penny_stock(price: float, currency: str, config: Dict[str, Any]) -> bool:
    if currency.upper() != "USD":
        return False
    return price < float(config["signals"].get("penny_stock_price_usd", 1.0))


def bar_min_volume(price: float, currency: str, config: Dict[str, Any]) -> float:
    sig_cfg = config["signals"]
    if is_penny_stock(price, currency, config):
        return float(sig_cfg.get("penny_min_bar_volume_shares", 20000))
    return float(sig_cfg.get("min_bar_volume_shares", 8000))


def session_bars_for_current_day(bars: List[StockBar], exchange_tz_name: str) -> List[StockBar]:
    if not bars:
        return []
    tz = timezone_from_name(exchange_tz_name)
    current_date = datetime.fromtimestamp(bars[-1].timestamp, timezone.utc).astimezone(tz).date()
    same_day = [
        b for b in bars
        if datetime.fromtimestamp(b.timestamp, timezone.utc).astimezone(tz).date() == current_date
    ]
    return same_day or bars


def quote_candidate_score(quote_data: QuoteData, config: Dict[str, Any]) -> Optional[Tuple[float, str]]:
    sig_cfg = config["signals"]
    price = quote_data.price
    if price < min_price_for_currency(config, quote_data.currency):
        return None

    stage = stage_from_market_state(quote_data.market_state, quote_data.exchange_timezone_name, quote_data.market_time)
    if config["market"].get("skip_weekends", True) and is_weekend_now(quote_data.exchange_timezone_name):
        return None
    if not stage_allowed(stage, config):
        return None

    gap = pct_change(price, quote_data.previous_close)
    from_day_low = pct_change(price, quote_data.day_low) if quote_data.day_low > 0 else 0.0
    day_volume = quote_data.day_volume
    avg_volume = quote_data.avg_volume
    day_vol_ratio = day_volume / avg_volume if avg_volume > 0 else 0.0

    penny = is_penny_stock(price, quote_data.currency, config)
    prefilter_gap = float(sig_cfg.get("prefilter_penny_gap_pct", 5.0) if penny else sig_cfg.get("prefilter_gap_pct", 3.0))
    from_day_low_threshold = float(sig_cfg.get("prefilter_from_day_low_pct", 7.0))
    min_day_vol = float(sig_cfg.get("prefilter_min_day_volume_shares", 15000))
    min_day_ratio = float(sig_cfg.get("prefilter_day_volume_vs_avg", 0.03))
    min_abs_change_for_volume = float(sig_cfg.get("prefilter_min_abs_change_pct_for_volume", 1.5))

    trigger_gap = gap >= prefilter_gap
    trigger_day_low = from_day_low >= from_day_low_threshold
    trigger_day_volume = (
        day_volume >= min_day_vol
        and (day_vol_ratio >= min_day_ratio or avg_volume <= 0)
        and abs(gap) >= min_abs_change_for_volume
    )

    if not (trigger_gap or trigger_day_low or trigger_day_volume):
        return None

    reasons = []
    if trigger_gap:
        reasons.append(f"candidat gap {gap:+.2f}%")
    if trigger_day_low:
        reasons.append(f"candidat +{from_day_low:.2f}% depuis plus bas")
    if trigger_day_volume:
        reasons.append(f"candidat volume jour x{day_vol_ratio:.2f}")

    score = 0.0
    score += max(0.0, gap / max(prefilter_gap, 0.01))
    score += max(0.0, from_day_low / max(from_day_low_threshold, 0.01))
    score += max(0.0, day_vol_ratio / max(min_day_ratio, 0.01))
    return score, ", ".join(reasons)


def quote_alert_signal(quote_data: QuoteData, config: Dict[str, Any]) -> Optional[Signal]:
    sig_cfg = config["signals"]
    price = quote_data.price
    if price < min_price_for_currency(config, quote_data.currency):
        return None

    stage = stage_from_market_state(quote_data.market_state, quote_data.exchange_timezone_name, quote_data.market_time)
    if config["market"].get("skip_weekends", True) and is_weekend_now(quote_data.exchange_timezone_name):
        return None
    if not stage_allowed(stage, config):
        return None

    gap = pct_change(price, quote_data.previous_close)
    from_day_low = pct_change(price, quote_data.day_low) if quote_data.day_low > 0 else 0.0
    day_volume = quote_data.day_volume
    avg_volume = quote_data.avg_volume
    day_vol_ratio = day_volume / avg_volume if avg_volume > 0 else 0.0

    penny = is_penny_stock(price, quote_data.currency, config)
    gap_alert = float(sig_cfg.get("quote_penny_gap_alert_pct", 9.0) if penny else sig_cfg.get("quote_gap_alert_pct", 7.0))
    day_low_alert = float(sig_cfg.get("quote_from_day_low_alert_pct", 10.0))
    vol_alert_ratio = float(sig_cfg.get("quote_volume_alert_ratio", 0.15))
    min_day_vol = float(sig_cfg.get("prefilter_min_day_volume_shares", 15000))

    trigger_gap = gap >= gap_alert
    trigger_day_low = from_day_low >= day_low_alert and gap >= float(sig_cfg.get("prefilter_min_abs_change_pct_for_volume", 1.5))
    trigger_volume = (
        day_volume >= min_day_vol
        and (day_vol_ratio >= vol_alert_ratio or avg_volume <= 0)
        and gap >= float(sig_cfg.get("prefilter_min_abs_change_pct_for_volume", 1.5))
    )

    if not (trigger_gap or trigger_day_low or trigger_volume):
        return None

    local_tz = timezone_from_name(quote_data.exchange_timezone_name)
    if quote_data.market_time > 0:
        local_time = datetime.fromtimestamp(quote_data.market_time, timezone.utc).astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    else:
        local_time = datetime.now(timezone.utc).astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    reasons = []
    if trigger_gap:
        reasons.append(f"gap direct {gap:+.2f}% vs clôture veille")
    if trigger_day_low:
        reasons.append(f"+{from_day_low:.2f}% depuis plus bas du jour")
    if trigger_volume:
        reasons.append(f"volume jour x{day_vol_ratio:.2f}")

    score = 0.0
    score += max(0.0, gap / max(gap_alert, 0.01))
    score += max(0.0, from_day_low / max(day_low_alert, 0.01))
    score += max(0.0, day_vol_ratio / max(vol_alert_ratio, 0.01))

    source = "quote_premarket_afterhours" if stage in {"pré-marché", "after-hours"} else "quote_live"

    return Signal(
        symbol=quote_data.symbol,
        price=price,
        previous_close=quote_data.previous_close,
        currency=quote_data.currency,
        exchange_timezone_name=quote_data.exchange_timezone_name,
        stage=stage,
        pct_1m=0.0,
        pct_5m=0.0,
        pct_15m=0.0,
        pct_30m=0.0,
        gap_prev_close_pct=gap,
        pct_from_session_low=from_day_low,
        breakout_pct=0.0,
        volume_ratio=day_vol_ratio,
        bar_volume_shares=0.0,
        day_volume_shares=day_volume,
        score=score,
        reason=", ".join(reasons),
        bar_time_local=local_time,
        source=source,
    )


def analyze_symbol_chart(chart: ChartData, config: Dict[str, Any]) -> Optional[Signal]:
    sig_cfg = config["signals"]
    scan_cfg = config["scan"]

    bars = chart.bars
    if len(bars) < 5:
        return None

    current = bars[-1]
    price = current.close

    if price < min_price_for_currency(config, chart.currency):
        return None

    stage = market_stage_for_timestamp(current.timestamp, chart.exchange_timezone_name)
    if config["market"].get("skip_weekends", True) and is_weekend_now(chart.exchange_timezone_name):
        return None
    if not stage_allowed(stage, config):
        return None

    max_age = float(scan_cfg.get("max_last_bar_age_minutes", 90))
    if bar_age_minutes(current) > max_age:
        return None

    p1 = pct_change(price, close_n_bars_ago(bars, 1))
    p5 = pct_change(price, close_n_bars_ago(bars, min(5, len(bars) - 1)))
    p15 = pct_change(price, close_n_bars_ago(bars, min(15, len(bars) - 1)))
    p30 = pct_change(price, close_n_bars_ago(bars, min(30, len(bars) - 1)))
    gap = pct_change(price, chart.previous_close)

    day_bars = session_bars_for_current_day(bars, chart.exchange_timezone_name)
    session_low = min((b.low for b in day_bars if b.low > 0), default=price)
    from_session_low = pct_change(price, session_low) if session_low > 0 else 0.0

    recent_window = bars[-61:-1] if len(bars) > 61 else bars[:-1]
    recent_high = max((b.high for b in recent_window if b.high > 0), default=price)
    breakout = pct_change(price, recent_high) if recent_high > 0 else 0.0

    previous_volumes = [b.volume for b in bars[-21:-1] if b.volume and b.volume > 0]
    avg_volume = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 0.0
    min_bar_vol = bar_min_volume(price, chart.currency, config)

    if avg_volume > 0:
        vol_ratio = current.volume / avg_volume
    else:
        vol_ratio = 999.0 if current.volume >= min_bar_vol else 0.0

    penny = is_penny_stock(price, chart.currency, config)
    gap_threshold = float(sig_cfg.get("penny_gap_prev_close_pct", 9.0) if penny else sig_cfg.get("gap_prev_close_pct", 7.0))
    from_low_threshold = float(sig_cfg.get("penny_pct_from_session_low", 12.0) if penny else sig_cfg.get("pct_from_session_low", 8.0))
    high_gap_no_volume = float(sig_cfg.get("high_gap_no_volume_pct", 14.0))

    trigger_1m = p1 >= float(sig_cfg["pct_1m"])
    trigger_5m = p5 >= float(sig_cfg["pct_5m"])
    trigger_15m = p15 >= float(sig_cfg["pct_15m"])
    trigger_30m = p30 >= float(sig_cfg["pct_30m"])
    trigger_volume = vol_ratio >= float(sig_cfg["volume_ratio"])
    trigger_min_vol = current.volume >= min_bar_vol
    trigger_gap = gap >= gap_threshold
    trigger_high_gap = gap >= high_gap_no_volume
    trigger_from_low = from_session_low >= from_low_threshold
    trigger_breakout = breakout >= float(sig_cfg.get("breakout_pct", 1.5))

    gap_signal = trigger_gap and (trigger_min_vol or trigger_high_gap or stage in {"pré-marché", "after-hours"})
    momentum_signal = trigger_min_vol and (
        (trigger_volume and (trigger_1m or trigger_5m or trigger_15m or trigger_30m))
        or trigger_from_low
        or trigger_breakout
    )
    low_volume_explosion_signal = trigger_high_gap and (trigger_from_low or trigger_5m or trigger_15m or trigger_30m)

    if not (gap_signal or momentum_signal or low_volume_explosion_signal):
        return None

    reasons: List[str] = []
    if trigger_gap:
        reasons.append(f"gap {gap:+.2f}% vs clôture veille")
    if trigger_from_low:
        reasons.append(f"+{from_session_low:.2f}% depuis plus bas séance")
    if trigger_breakout:
        reasons.append(f"cassure plus haut récent +{breakout:.2f}%")
    if trigger_1m:
        reasons.append(f"{p1:+.2f}% en 1 min")
    if trigger_5m:
        reasons.append(f"{p5:+.2f}% en 5 min")
    if trigger_15m:
        reasons.append(f"{p15:+.2f}% en 15 min")
    if trigger_30m:
        reasons.append(f"{p30:+.2f}% en 30 min")
    if trigger_volume:
        reasons.append(f"volume bougie x{vol_ratio:.1f}")
    if trigger_min_vol:
        reasons.append(f"volume bougie {current.volume:,.0f} titres")

    score = 0.0
    score += max(0.0, gap / max(gap_threshold, 0.01))
    score += max(0.0, from_session_low / max(from_low_threshold, 0.01))
    score += max(0.0, breakout / max(float(sig_cfg.get("breakout_pct", 1.5)), 0.01))
    score += max(0.0, p1 / max(float(sig_cfg["pct_1m"]), 0.01))
    score += max(0.0, p5 / max(float(sig_cfg["pct_5m"]), 0.01))
    score += max(0.0, p15 / max(float(sig_cfg["pct_15m"]), 0.01))
    score += max(0.0, p30 / max(float(sig_cfg["pct_30m"]), 0.01))
    score += max(0.0, vol_ratio / max(float(sig_cfg["volume_ratio"]), 0.01))

    local_tz = timezone_from_name(chart.exchange_timezone_name)
    bar_dt_local = datetime.fromtimestamp(current.timestamp, timezone.utc).astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    return Signal(
        symbol=chart.symbol,
        price=price,
        previous_close=chart.previous_close,
        currency=chart.currency,
        exchange_timezone_name=chart.exchange_timezone_name,
        stage=stage,
        pct_1m=p1,
        pct_5m=p5,
        pct_15m=p15,
        pct_30m=p30,
        gap_prev_close_pct=gap,
        pct_from_session_low=from_session_low,
        breakout_pct=breakout,
        volume_ratio=vol_ratio,
        bar_volume_shares=current.volume,
        day_volume_shares=0.0,
        score=score,
        reason=", ".join(reasons),
        bar_time_local=bar_dt_local,
        source="chart_1m_prepost",
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


def money_suffix(currency: str) -> str:
    c = currency.upper()
    if c == "EUR":
        return "€"
    if c == "GBP":
        return "£"
    if c == "USD":
        return "$"
    return c


def format_alert(signal: Signal, risk_message: str) -> str:
    yahoo_symbol = quote(signal.symbol, safe="")
    yahoo_url = f"https://finance.yahoo.com/quote/{yahoo_symbol}"
    suffix = money_suffix(signal.currency)

    title = "🚨 <b>ALERTE ACTION - Mouvement possible</b>"
    if signal.stage == "pré-marché":
        title = "🌅 <b>ALERTE PRÉ-MARCHÉ - Mouvement possible</b>"
    elif signal.stage == "after-hours":
        title = "🌙 <b>ALERTE AFTER-HOURS - Mouvement possible</b>"

    lines = [
        title,
        "",
        f"Action : <b>{signal.symbol}</b>",
        f"Session : <b>{signal.stage}</b>",
        f"Source : <b>{signal.source}</b>",
        f"Prix : <b>{signal.price:.4g} {suffix}</b>",
        f"Clôture veille : <b>{signal.previous_close:.4g} {suffix}</b>",
        f"Heure donnée : <b>{signal.bar_time_local}</b>",
        "",
        "Variation :",
        f"• vs clôture veille : <b>{signal.gap_prev_close_pct:+.2f}%</b>",
    ]

    if signal.source.startswith("chart_"):
        lines.extend([
            f"• 1 min : <b>{signal.pct_1m:+.2f}%</b>",
            f"• 5 min : <b>{signal.pct_5m:+.2f}%</b>",
            f"• 15 min : <b>{signal.pct_15m:+.2f}%</b>",
            f"• 30 min : <b>{signal.pct_30m:+.2f}%</b>",
            f"• depuis plus bas séance : <b>+{signal.pct_from_session_low:.2f}%</b>",
            f"• cassure plus haut récent : <b>{signal.breakout_pct:+.2f}%</b>",
            f"• Volume bougie : <b>x{signal.volume_ratio:.1f}</b>",
            f"• Volume bougie : <b>{signal.bar_volume_shares:,.0f} titres</b>",
        ])
    else:
        lines.extend([
            f"• depuis plus bas du jour : <b>+{signal.pct_from_session_low:.2f}%</b>",
            f"• Volume jour : <b>x{signal.volume_ratio:.2f}</b> vs moyenne",
            f"• Volume jour : <b>{signal.day_volume_shares:,.0f} titres</b>",
        ])

    lines.extend([
        "",
        f"Raison : {signal.reason}",
        f"Score : {signal.score:.2f}",
        f"Yahoo : {yahoo_url}",
        "",
        f"⚠️ {risk_message}"
    ])
    return "\n".join(lines)


def ensure_alert_log() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    if not ALERT_LOG.exists():
        with ALERT_LOG.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "date", "source", "symbol", "currency", "exchange_timezone", "stage", "price",
                "previous_close", "gap_prev_close_pct", "pct_1m", "pct_5m", "pct_15m",
                "pct_30m", "pct_from_session_low", "breakout_pct", "volume_ratio",
                "bar_volume_shares", "day_volume_shares", "score", "reason"
            ])


def log_alert(signal: Signal) -> None:
    ensure_alert_log()
    with ALERT_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            now_iso(), signal.source, signal.symbol, signal.currency, signal.exchange_timezone_name, signal.stage,
            f"{signal.price:.12g}", f"{signal.previous_close:.12g}",
            f"{signal.gap_prev_close_pct:.4f}", f"{signal.pct_1m:.4f}",
            f"{signal.pct_5m:.4f}", f"{signal.pct_15m:.4f}", f"{signal.pct_30m:.4f}",
            f"{signal.pct_from_session_low:.4f}", f"{signal.breakout_pct:.4f}",
            f"{signal.volume_ratio:.4f}", f"{signal.bar_volume_shares:.0f}",
            f"{signal.day_volume_shares:.0f}", f"{signal.score:.4f}", signal.reason
        ])


def should_send_signal(signal_found: Signal, last_alert_at: Dict[str, float], cooldown_seconds: int) -> bool:
    now_ts = time.time()
    last_ts = last_alert_at.get(signal_found.symbol, 0)
    if now_ts - last_ts < cooldown_seconds:
        return False
    last_alert_at[signal_found.symbol] = now_ts
    return True


def stronger_signal(current: Optional[Signal], candidate: Signal) -> Signal:
    if current is None:
        return candidate
    return candidate if candidate.score > current.score else current


def scan_large_universe(config: Dict[str, Any], client: YahooFinanceClient, universe: List[str]) -> List[Signal]:
    scan_cfg = config["scan"]
    quote_batch_size = int(scan_cfg.get("quote_batch_size", 120))
    pause_quote = float(scan_cfg.get("pause_between_quote_batches_seconds", 0.20))
    max_candidate_charts = int(scan_cfg.get("max_candidate_charts", 260))
    chart_interval = str(scan_cfg.get("chart_interval", "1m"))
    chart_range = str(scan_cfg.get("chart_range", "1d"))
    include_prepost = bool(scan_cfg.get("include_prepost", True))
    pause_chart = float(scan_cfg.get("pause_between_chart_requests_seconds", 0.05))

    candidate_scores: Dict[str, Tuple[float, str]] = {}
    final_signals: Dict[str, Signal] = {}
    total_quotes = 0

    for batch in chunks(universe, quote_batch_size):
        if stop_requested:
            break
        try:
            quotes = client.quotes(batch)
            total_quotes += len(quotes)
            for q in quotes:
                qs = quote_alert_signal(q, config)
                if qs:
                    final_signals[q.symbol] = stronger_signal(final_signals.get(q.symbol), qs)

                candidate = quote_candidate_score(q, config)
                if candidate:
                    existing = candidate_scores.get(q.symbol)
                    if existing is None or candidate[0] > existing[0]:
                        candidate_scores[q.symbol] = candidate
        except Exception as exc:
            print(f"Erreur batch quotes: {exc}")
        if pause_quote > 0:
            time.sleep(pause_quote)

    sorted_candidates = sorted(candidate_scores.items(), key=lambda item: item[1][0], reverse=True)
    candidate_symbols = [symbol for symbol, _score_reason in sorted_candidates[:max_candidate_charts]]

    if bool(scan_cfg.get("always_chart_watchlist", True)):
        for symbol in reversed(priority_symbols(config, universe)):
            if symbol not in candidate_symbols:
                candidate_symbols.insert(0, symbol)

    candidate_symbols = dedupe(candidate_symbols)[:max_candidate_charts + int(scan_cfg.get("max_priority_charts", 80))]

    print(
        f"Pré-scan quotes : {total_quotes} quotes reçues, "
        f"{len(candidate_scores)} candidats, {len(final_signals)} alertes directes."
    )

    for symbol in candidate_symbols:
        if stop_requested:
            break
        try:
            chart = client.chart(symbol, chart_interval, chart_range, include_prepost)
            chart_signal = analyze_symbol_chart(chart, config)
            if chart_signal:
                final_signals[symbol] = stronger_signal(final_signals.get(symbol), chart_signal)
        except Exception as exc:
            print(f"{symbol}: analyse 1m impossible ({exc})")
        if pause_chart > 0:
            time.sleep(pause_chart)

    out = list(final_signals.values())
    out.sort(key=lambda s: s.score, reverse=True)
    return out


def scan_small_universe(config: Dict[str, Any], client: YahooFinanceClient, universe: List[str]) -> List[Signal]:
    scan_cfg = config["scan"]
    chart_interval = str(scan_cfg.get("chart_interval", "1m"))
    chart_range = str(scan_cfg.get("chart_range", "1d"))
    include_prepost = bool(scan_cfg.get("include_prepost", True))
    pause_chart = float(scan_cfg.get("pause_between_chart_requests_seconds", 0.05))

    signals: Dict[str, Signal] = {}
    for symbol in universe:
        if stop_requested:
            break
        try:
            # On regarde d'abord la quote pour pré-market / after-hours.
            quotes = client.quotes([symbol])
            if quotes:
                qs = quote_alert_signal(quotes[0], config)
                if qs:
                    signals[symbol] = stronger_signal(signals.get(symbol), qs)

            chart = client.chart(symbol, chart_interval, chart_range, include_prepost)
            sig = analyze_symbol_chart(chart, config)
            if sig:
                signals[symbol] = stronger_signal(signals.get(symbol), sig)
        except Exception as exc:
            print(f"{symbol}: erreur {exc}")
        if pause_chart > 0:
            time.sleep(pause_chart)

    out = list(signals.values())
    out.sort(key=lambda s: s.score, reverse=True)
    return out


def run() -> None:
    config = load_config()
    client = YahooFinanceClient()
    loader = SymbolUniverseLoader()

    scan_cfg = config["scan"]
    interval_seconds = int(scan_cfg.get("interval_seconds", 60))
    cooldown_seconds = int(scan_cfg.get("cooldown_minutes_per_symbol", 30)) * 60
    refresh_universe_seconds = int(scan_cfg.get("refresh_symbol_universe_minutes", 360)) * 60
    large_mode = bool(scan_cfg.get("large_universe_mode", True))

    print("=" * 70)
    print(f"{APP_NAME} v{APP_VERSION}")
    print("Surveillance actions CAC 40 + actions US - alertes informatives uniquement")
    print("Sessions : pré-marché + marché ouvert + after-hours")
    print("Données prix : Yahoo Finance public/non officiel")
    print("Liste actions US : Nasdaq Trader Symbol Directory")
    print("Arrêt : CTRL+C")
    print("=" * 70)

    last_alert_at: Dict[str, float] = {}
    loop_count = 0
    universe: List[str] = []
    universe_loaded_at = 0.0

    telegram_send(
        config,
        f"✅ {APP_NAME} v{APP_VERSION} démarré\n"
        f"Surveillance toutes les {interval_seconds} secondes.\n"
        "Sessions actives : pré-marché + marché ouvert + after-hours."
    )

    while not stop_requested:
        started = time.time()
        loop_count += 1

        try:
            if not universe or time.time() - universe_loaded_at > refresh_universe_seconds:
                universe = build_symbol_universe(config, loader)
                universe_loaded_at = time.time()
                print(f"Univers chargé : {len(universe)} actions.")

            print(f"\n[{now_iso()}] Scan {loop_count} - {len(universe)} actions")

            if large_mode and len(universe) > 300:
                signals = scan_large_universe(config, client, universe)
            else:
                signals = scan_small_universe(config, client, universe)

            sent = 0
            for signal_found in signals:
                if not should_send_signal(signal_found, last_alert_at, cooldown_seconds):
                    continue
                message = format_alert(signal_found, config["risk"]["message"])
                print("\n" + message.replace("<b>", "").replace("</b>", ""))
                telegram_send(config, message)
                log_alert(signal_found)
                sent += 1

            elapsed = time.time() - started
            sleep_for = max(5, interval_seconds - elapsed)
            print(f"Scan terminé en {elapsed:.1f}s. Alertes envoyées : {sent}. Prochain scan dans {sleep_for:.0f}s.")

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
