import argparse
import base64
import csv
import getpass
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


DEFAULT_SYMBOLS = [
    "/ES",
    "/6E",
    "/GC",
    "/ZW",
    "/ZN",
    "/CL",
]
DEFAULT_OUTPUT_DIR = Path("data/market_data")
DEFAULT_PROVIDER = "csv"
SUPPORTED_FREQUENCIES = {"daily", "5min"}
REVIEWED_BARS_FILE = "reviewed_bars.csv"
SCHWAB_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
SCHWAB_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_REDIRECT_URI = "https://developer.schwab.com/oauth2-redirect.html"
SCHWAB_MAX_DAILY_YEARS = 20
SCHWAB_MAX_INTRADAY_DAYS = 10


FUTURES_PRODUCTS = {
    "/ES": {
        "name": "E-mini S&P 500",
        "exchange": "CME",
    },
    "/6E": {
        "name": "Euro FX",
        "exchange": "CME",
    },
    "/GC": {
        "name": "Gold",
        "exchange": "COMEX",
    },
    "/ZW": {
        "name": "Chicago SRW Wheat",
        "exchange": "CBOT",
    },
    "/ZN": {
        "name": "10-Year T-Note",
        "exchange": "CBOT",
    },
    "/CL": {
        "name": "WTI Crude Oil",
        "exchange": "NYMEX",
    },
}


CANONICAL_COLUMNS = [
    "timestamp",
    "date",
    "symbol",
    "frequency",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "source",
    "retrieved_at",
]
REVIEWED_COLUMNS = [
    "symbol",
    "frequency",
    "comparison_key",
    "selected_source",
    "reviewed_at",
]


def normalize_symbol(symbol):
    symbol = str(symbol).strip().upper()

    if not symbol:
        raise ValueError("Symbol cannot be blank")

    if not symbol.startswith("/"):
        symbol = f"/{symbol}"

    return symbol


def safe_symbol_filename(symbol):
    return normalize_symbol(symbol).replace("/", "").replace(" ", "_")


def normalize_frequency(frequency):
    frequency = str(frequency).strip().lower()

    aliases = {
        "day": "daily",
        "1d": "daily",
        "1day": "daily",
        "5m": "5min",
        "5-min": "5min",
        "5-minute": "5min",
        "5minute": "5min",
    }
    frequency = aliases.get(frequency, frequency)

    if frequency not in SUPPORTED_FREQUENCIES:
        raise ValueError(
            f"Unsupported frequency '{frequency}'. "
            f"Choose one of: {sorted(SUPPORTED_FREQUENCIES)}"
        )

    return frequency


def output_file_for(output_dir, symbol, frequency):
    return (
        Path(output_dir)
        / normalize_frequency(frequency)
        / f"{safe_symbol_filename(symbol)}.csv"
    )


def empty_bars_frame():
    return pd.DataFrame(columns=CANONICAL_COLUMNS)


def reviewed_bars_file_for(output_dir):
    return Path(output_dir) / REVIEWED_BARS_FILE


def empty_reviewed_bars_frame():
    return pd.DataFrame(columns=REVIEWED_COLUMNS)


def load_reviewed_bars(output_dir):
    path = reviewed_bars_file_for(output_dir)

    if not path.exists():
        return empty_reviewed_bars_frame()

    reviewed = pd.read_csv(path)

    for column in REVIEWED_COLUMNS:
        if column not in reviewed.columns:
            reviewed[column] = pd.NA

    reviewed["symbol"] = reviewed["symbol"].map(normalize_symbol)
    reviewed["frequency"] = reviewed["frequency"].map(normalize_frequency)
    reviewed["comparison_key"] = reviewed["comparison_key"].astype(str)

    return reviewed[REVIEWED_COLUMNS].copy()


def comparison_keys_for_bars(bars):
    if bars is None or len(bars) == 0:
        return pd.Series(dtype=object)

    timestamps = pd.to_datetime(
        bars["timestamp"],
        utc=True,
        errors="coerce",
    )
    frequencies = bars["frequency"].astype(str).str.lower()
    daily_keys = timestamps.dt.date.astype(str)
    intraday_keys = timestamps.dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return pd.Series(
        np.where(frequencies == "daily", daily_keys, intraday_keys),
        index=bars.index,
    )


def reviewed_key_set(reviewed_bars):
    if reviewed_bars is None or len(reviewed_bars) == 0:
        return set()

    reviewed = reviewed_bars.copy()
    reviewed["symbol"] = reviewed["symbol"].map(normalize_symbol)
    reviewed["frequency"] = reviewed["frequency"].map(normalize_frequency)
    reviewed["comparison_key"] = reviewed["comparison_key"].astype(str)

    return set(
        zip(
            reviewed["symbol"],
            reviewed["frequency"],
            reviewed["comparison_key"],
        )
    )


def normalize_bar_frame(
    bars,
    symbol,
    frequency,
    source,
    retrieved_at=None,
):
    if bars is None or len(bars) == 0:
        return empty_bars_frame()

    working = pd.DataFrame(bars).copy()
    column_map = {
        "datetime": "timestamp",
        "dateTime": "timestamp",
        "time": "timestamp",
        "symbol": "symbol",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "openInterest": "open_interest",
        "open_interest": "open_interest",
    }
    working = working.rename(
        columns={
            column: column_map[column]
            for column in working.columns
            if column in column_map
        }
    )

    if "timestamp" not in working.columns:
        raise ValueError("Market data is missing a timestamp/datetime column")

    timestamp = working["timestamp"]

    if pd.api.types.is_numeric_dtype(timestamp):
        max_value = pd.to_numeric(timestamp, errors="coerce").max()
        unit = "ms" if max_value and max_value > 10_000_000_000 else "s"
        working["timestamp"] = pd.to_datetime(
            timestamp,
            unit=unit,
            utc=True,
            errors="coerce",
        )
    else:
        working["timestamp"] = pd.to_datetime(
            timestamp,
            utc=True,
            errors="coerce",
        )

    working = working[working["timestamp"].notna()].copy()
    working["date"] = working["timestamp"].dt.date.astype(str)
    working["timestamp"] = working["timestamp"].dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    working["symbol"] = normalize_symbol(symbol)
    working["frequency"] = normalize_frequency(frequency)
    working["source"] = source
    working["retrieved_at"] = (
        retrieved_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    for column in ["open", "high", "low", "close", "volume", "open_interest"]:
        if column not in working.columns:
            working[column] = pd.NA

        working[column] = pd.to_numeric(
            working[column],
            errors="coerce",
        )

    return working[CANONICAL_COLUMNS].copy()


def append_market_data(existing, incoming, reviewed_bars=None):
    reviewed_keys = reviewed_key_set(reviewed_bars)

    if existing is None or len(existing) == 0:
        combined = incoming.copy()
    elif incoming is None or len(incoming) == 0:
        combined = existing.copy()
    else:
        if reviewed_keys:
            existing_keys = set(
                zip(
                    existing["symbol"].map(normalize_symbol),
                    existing["frequency"].map(normalize_frequency),
                    comparison_keys_for_bars(existing),
                )
            )
            incoming = incoming.copy()
            incoming_keys = list(
                zip(
                    incoming["symbol"].map(normalize_symbol),
                    incoming["frequency"].map(normalize_frequency),
                    comparison_keys_for_bars(incoming),
                )
            )
            incoming = incoming[
                [
                    key not in reviewed_keys or key not in existing_keys
                    for key in incoming_keys
                ]
            ].copy()

        combined = pd.concat(
            [existing, incoming],
            ignore_index=True,
        )

    if len(combined) == 0:
        return empty_bars_frame()

    combined["timestamp"] = pd.to_datetime(
        combined["timestamp"],
        utc=True,
        errors="coerce",
    )
    combined = combined[combined["timestamp"].notna()].copy()
    combined = combined.sort_values(
        ["symbol", "frequency", "timestamp", "retrieved_at"],
        na_position="last",
    )
    combined = combined.drop_duplicates(
        subset=["symbol", "frequency", "timestamp"],
        keep="last",
    )
    combined["date"] = combined["timestamp"].dt.date.astype(str)
    combined["timestamp"] = combined["timestamp"].dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    return combined[CANONICAL_COLUMNS].reset_index(drop=True)


def save_market_data(output_dir, symbol, frequency, bars):
    output_path = output_file_for(output_dir, symbol, frequency)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing = (
        pd.read_csv(output_path)
        if output_path.exists()
        else empty_bars_frame()
    )
    reviewed_bars = load_reviewed_bars(output_dir)
    combined = append_market_data(existing, bars, reviewed_bars=reviewed_bars)
    combined.to_csv(output_path, index=False)

    return output_path, len(combined)


def write_symbol_manifest(output_dir, symbols):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "symbols.csv"

    with manifest_path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "symbol",
                "name",
                "exchange",
            ],
        )
        writer.writeheader()

        for symbol in symbols:
            normalized = normalize_symbol(symbol)
            product = FUTURES_PRODUCTS.get(normalized, {})
            writer.writerow(
                {
                    "symbol": normalized,
                    "name": product.get("name", ""),
                    "exchange": product.get("exchange", ""),
                }
            )

    return manifest_path


class CsvProvider:
    def __init__(self, input_dir):
        if not input_dir:
            raise ValueError("--input-dir is required when provider=csv")

        self.input_dir = Path(input_dir)

    def fetch_bars(self, symbol, frequency, start=None, end=None):
        candidates = [
            self.input_dir
            / normalize_frequency(frequency)
            / f"{safe_symbol_filename(symbol)}.csv",
            self.input_dir
            / f"{safe_symbol_filename(symbol)}_{normalize_frequency(frequency)}.csv",
            self.input_dir / f"{safe_symbol_filename(symbol)}.csv",
        ]
        input_path = next(
            (candidate for candidate in candidates if candidate.exists()),
            None,
        )

        if input_path is None:
            return empty_bars_frame()

        bars = normalize_bar_frame(
            pd.read_csv(input_path),
            symbol=symbol,
            frequency=frequency,
            source="csv",
        )

        return filter_date_range(bars, start=start, end=end)


class SchwabProvider:
    def __init__(
        self,
        access_token=None,
        client_id=None,
        client_secret=None,
        redirect_uri=SCHWAB_REDIRECT_URI,
        max_history=False,
        base_url=SCHWAB_BASE_URL,
    ):
        self.access_token = access_token or os.getenv("SCHWAB_ACCESS_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.max_history = max_history

        if not self.access_token:
            self.access_token = prompt_for_schwab_access_token(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )

    def fetch_bars(self, symbol, frequency, start=None, end=None):
        params = schwab_price_history_params(
            symbol=symbol,
            frequency=frequency,
            start=start,
            end=end,
            max_history=self.max_history,
        )
        url = f"{self.base_url}/pricehistory?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Schwab pricehistory request failed for {symbol} "
                f"{frequency}: HTTP {error.code} {body}"
            ) from error
        except URLError as error:
            raise RuntimeError(
                f"Schwab pricehistory request failed for {symbol} "
                f"{frequency}: {error.reason}"
            ) from error

        candles = payload.get("candles", [])
        return normalize_bar_frame(
            candles,
            symbol=symbol,
            frequency=frequency,
            source="schwab",
        )


def schwab_price_history_params(
    symbol,
    frequency,
    start=None,
    end=None,
    max_history=False,
):
    frequency = normalize_frequency(frequency)

    if frequency == "daily":
        params = {
            "symbol": normalize_symbol(symbol),
            "periodType": "year",
            "period": SCHWAB_MAX_DAILY_YEARS if max_history else 1,
            "frequencyType": "daily",
            "frequency": 1,
        }
    else:
        params = {
            "symbol": normalize_symbol(symbol),
            "periodType": "day",
            "period": SCHWAB_MAX_INTRADAY_DAYS if max_history else 1,
            "frequencyType": "minute",
            "frequency": 5,
            "needExtendedHoursData": "true",
        }

    start_ms = date_to_epoch_ms(start)
    end_ms = date_to_epoch_ms(end)

    if start_ms is not None:
        params["startDate"] = start_ms

    if end_ms is not None:
        params["endDate"] = end_ms

    return params


def schwab_authorization_url(client_id, redirect_uri=SCHWAB_REDIRECT_URI):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": "readonly",
        "redirect_uri": redirect_uri,
    }

    return f"{SCHWAB_AUTHORIZE_URL}?{urlencode(params)}"


def extract_authorization_code(value):
    value = str(value).strip()

    if not value:
        raise ValueError("Authorization code cannot be blank")

    parsed = urlparse(value)

    if parsed.query:
        query_values = parse_qs(parsed.query)

        if "code" in query_values and query_values["code"]:
            return query_values["code"][0]

    return value


def exchange_schwab_authorization_code(
    client_id,
    client_secret,
    authorization_code,
    redirect_uri=SCHWAB_REDIRECT_URI,
    token_url=SCHWAB_TOKEN_URL,
):
    credentials = f"{client_id}:{client_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("ascii")
    payload = urlencode(
        {
            "grant_type": "authorization_code",
            "code": extract_authorization_code(authorization_code),
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = Request(
        token_url,
        data=payload,
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            token_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Schwab token request failed: HTTP {error.code} {body}"
        ) from error
    except URLError as error:
        raise RuntimeError(
            f"Schwab token request failed: {error.reason}"
        ) from error

    if "access_token" not in token_payload:
        raise RuntimeError(
            "Schwab token response did not include an access_token"
        )

    return token_payload


def prompt_for_schwab_access_token(
    client_id=None,
    client_secret=None,
    redirect_uri=SCHWAB_REDIRECT_URI,
):
    client_id = (
        client_id
        or os.getenv("SCHWAB_CLIENT_ID")
        or input("Schwab client_id: ").strip()
    )
    client_secret = (
        client_secret
        or os.getenv("SCHWAB_CLIENT_SECRET")
        or getpass.getpass("Schwab client_secret: ").strip()
    )

    if not client_id:
        raise ValueError("Schwab client_id is required")

    if not client_secret:
        raise ValueError("Schwab client_secret is required")

    authorization_url = schwab_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
    )
    print("\nOpen this Schwab authorization URL in your browser:")
    print(authorization_url)
    print(
        "\nAfter approving access, paste the full redirect URL "
        "or just the code value."
    )
    authorization_code = input("Schwab authorization code or URL: ").strip()
    token_payload = exchange_schwab_authorization_code(
        client_id=client_id,
        client_secret=client_secret,
        authorization_code=authorization_code,
        redirect_uri=redirect_uri,
    )

    if token_payload.get("refresh_token"):
        print(
            "Received access token and refresh token. "
            "This script uses the access token for this run only."
        )
    else:
        print("Received access token for this run.")

    return token_payload["access_token"]


def date_to_epoch_ms(value):
    if not value:
        return None

    timestamp = pd.to_datetime(value, utc=True, errors="raise")

    return int(timestamp.timestamp() * 1000)


def filter_date_range(bars, start=None, end=None):
    if len(bars) == 0:
        return bars

    working = bars.copy()
    timestamps = pd.to_datetime(
        working["timestamp"],
        utc=True,
        errors="coerce",
    )

    if start:
        working = working[timestamps >= pd.to_datetime(start, utc=True)]
        timestamps = pd.to_datetime(
            working["timestamp"],
            utc=True,
            errors="coerce",
        )

    if end:
        working = working[timestamps <= pd.to_datetime(end, utc=True)]

    return working.reset_index(drop=True)


def create_provider(args):
    if args.provider == "csv":
        return CsvProvider(args.input_dir)

    if args.provider == "schwab":
        return SchwabProvider(
            access_token=args.access_token,
            client_id=args.client_id,
            client_secret=args.client_secret,
            redirect_uri=args.redirect_uri,
            max_history=args.all,
        )

    raise ValueError(f"Unsupported provider: {args.provider}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Download or ingest futures market data and store normalized "
            "daily/5-minute bars locally."
        )
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="Futures roots or contract symbols, e.g. /ES /6E /GC",
    )
    parser.add_argument(
        "--frequencies",
        nargs="+",
        default=["daily", "5min"],
        help="One or more frequencies: daily, 5min",
    )
    parser.add_argument(
        "--provider",
        choices=["csv", "schwab"],
        default=DEFAULT_PROVIDER,
        help=(
            "Data provider. Use csv for vendor/exported files; use schwab "
            "only if your Schwab API account supports the requested bars."
        ),
    )
    parser.add_argument(
        "--input-dir",
        help="Directory containing source CSV bars when provider=csv",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where normalized market data files are written",
    )
    parser.add_argument(
        "--start",
        help="Optional inclusive start date/time, e.g. 2026-01-01",
    )
    parser.add_argument(
        "--end",
        help="Optional inclusive end date/time, e.g. 2026-06-05",
    )
    parser.add_argument(
        "--access-token",
        help="Schwab bearer token. Defaults to SCHWAB_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--client-id",
        help="Schwab OAuth client_id. Defaults to SCHWAB_CLIENT_ID or prompt.",
    )
    parser.add_argument(
        "--client-secret",
        help=(
            "Schwab OAuth client_secret. Defaults to SCHWAB_CLIENT_SECRET "
            "or hidden prompt."
        ),
    )
    parser.add_argument(
        "--redirect-uri",
        default=SCHWAB_REDIRECT_URI,
        help="Schwab OAuth redirect URI configured for your app.",
    )
    parser.add_argument(
        "-all",
        "--all",
        action="store_true",
        help=(
            "Request as much Schwab price history as this script can ask for. "
            "Without this flag, Schwab mode uses the default shorter window."
        ),
    )

    return parser.parse_args()


def main():
    args = parse_args()
    provider = create_provider(args)
    output_dir = Path(args.output_dir)
    symbols = [normalize_symbol(symbol) for symbol in args.symbols]
    frequencies = [
        normalize_frequency(frequency)
        for frequency in args.frequencies
    ]

    manifest_path = write_symbol_manifest(output_dir, symbols)
    print(f"Wrote symbol manifest: {manifest_path}")

    for symbol in symbols:
        for frequency in frequencies:
            bars = provider.fetch_bars(
                symbol=symbol,
                frequency=frequency,
                start=args.start,
                end=args.end,
            )

            output_path, row_count = save_market_data(
                output_dir=output_dir,
                symbol=symbol,
                frequency=frequency,
                bars=bars,
            )
            print(
                f"{symbol} {frequency}: added {len(bars)} bars; "
                f"{row_count} total rows -> {output_path}"
            )


if __name__ == "__main__":
    main()
