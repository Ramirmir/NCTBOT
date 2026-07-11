"""Publish Blox Fruits stock changes to a Telegram chat or forum topic.

The program has two modes used by the workflow:
* ``--prepare`` downloads and compares stock, then persists a pending message.
* ``--send`` sends that already-persisted message to Telegram.

Separating the modes lets GitHub Actions cache the new state before sending.  This
prefers avoiding duplicate messages if a workflow is interrupted at an unlucky
moment.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "state.json"
PENDING_MESSAGE_PATH = ROOT / "data" / "pending_message.txt"
MOSCOW_TZ = timezone(timedelta(hours=3))
HTTP_TIMEOUT_SECONDS = 10

DEFAULT_PRIMARY_API_URL = "https://www.gamersberg.com/api/v1/blox-fruits/stock"
DEFAULT_FALLBACK_API_URL = "https://bloxyvalues.com/api/stock"
DEFAULT_THIRD_API_URL = "https://bloxfruitscode.com/wp-json/bfs/v1/stock"


class BotError(RuntimeError):
    """A recoverable bot error that should fail the current Actions run."""


@dataclass(frozen=True)
class Stock:
    normal: tuple[str, ...]
    mirage: tuple[str, ...]

    def canonical(self) -> dict[str, list[str]]:
        """A stable, order-independent representation for change detection."""
        return {
            "normal": sorted(set(self.normal), key=str.casefold),
            "mirage": sorted(set(self.mirage), key=str.casefold),
        }


@dataclass(frozen=True)
class ApiSource:
    """A public JSON endpoint and the parser for its documented response."""

    name: str
    url: str
    parser: Callable[[Any], Stock]
    headers: dict[str, str]


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise BotError(f"Missing required environment variable: {name}")
    return value


def read_json(source: ApiSource) -> Any:
    request = Request(
        source.url,
        headers={
            "Accept": "application/json",
            "User-Agent": "blox-fruits-stock-telegram-bot/1.0",
            **source.headers,
        },
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"application/json", "text/json"}:
                raise BotError(f"{source.name} returned {content_type}, not JSON")
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BotError(f"Could not read {source.name} ({source.url}): {error}") from error


def find_value(data: Any, keys: Iterable[str]) -> Any | None:
    """Find a named field in common API envelopes without scraping HTML."""
    wanted = {key.lower().replace("-", "_").replace(" ", "_") for key in keys}
    if isinstance(data, dict):
        for key, value in data.items():
            normalised = key.lower().replace("-", "_").replace(" ", "_")
            if normalised in wanted:
                return value
        for envelope in ("data", "result", "results", "payload", "stock", "stocks"):
            nested = data.get(envelope)
            if isinstance(nested, dict):
                found = find_value(nested, keys)
                if found is not None:
                    return found
    return None


def fruit_name(value: Any) -> str | None:
    if isinstance(value, str):
        name = value.strip()
        return normalise_fruit_name(name) if name else None
    if isinstance(value, dict):
        for key in ("name", "fruit", "fruit_name", "item", "display_name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return normalise_fruit_name(candidate.strip())
    return None


def normalise_fruit_name(name: str) -> str:
    """Gamersberg emits names such as ``Rocket-Rocket``; show ``Rocket``."""
    left, separator, right = name.partition("-")
    if separator and left.casefold() == right.casefold():
        return left
    return name


def unique_names(names: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            result.append(name)
    if not result:
        raise BotError("API response contained an empty stock list")
    return tuple(result)


def fruit_names(value: Any) -> tuple[str, ...]:
    if isinstance(value, dict):
        # Some APIs expose an object keyed by fruit name.
        value = value.get("items") or value.get("fruits") or value.get("stock") or value
        if isinstance(value, dict):
            values: Iterable[Any] = value.keys()
        else:
            values = value if isinstance(value, list) else []
    elif isinstance(value, list):
        values = value
    else:
        values = []

    return unique_names(name for item in values if (name := fruit_name(item)))


def parse_stock(payload: Any) -> Stock:
    normal_raw = find_value(payload, ("normal", "normal_stock", "normalStock", "dealer"))
    mirage_raw = find_value(payload, ("mirage", "mirage_stock", "mirageStock", "advanced_dealer"))
    if normal_raw is None or mirage_raw is None:
        raise BotError("API response does not contain both Normal and Mirage stock")
    return Stock(normal=fruit_names(normal_raw), mirage=fruit_names(mirage_raw))


def parse_gamersberg_stock(payload: Any) -> Stock:
    """Normalise Gamersberg's ``data: [{normalStock, mirageStock}]`` schema."""
    records = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise BotError("Gamersberg response does not contain a data array")

    normal: list[str] = []
    mirage: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        normal.extend(fruit_names(record.get("normalStock", [])))
        mirage.extend(fruit_names(record.get("mirageStock", [])))
    return Stock(normal=unique_names(normal), mirage=unique_names(mirage))


def stock_sources() -> tuple[ApiSource, ...]:
    """Return three public JSON sources in fallback order.

    The first two addresses remain overridable by existing repository secrets.
    No credentials are required by any source.
    """
    gamersberg_url = os.getenv("STOCK_API_PRIMARY_URL", "").strip() or DEFAULT_PRIMARY_API_URL
    bloxy_url = os.getenv("STOCK_API_FALLBACK_URL", "").strip() or DEFAULT_FALLBACK_API_URL
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/134.0 Safari/537.36",
    }
    return (
        ApiSource(
            name="Gamersberg",
            url=gamersberg_url,
            parser=parse_gamersberg_stock,
            headers={
                **browser_headers,
                "Referer": "https://www.gamersberg.com/blox-fruits/stock",
                "Origin": "https://www.gamersberg.com",
            },
        ),
        ApiSource(
            name="Bloxy Values",
            url=bloxy_url,
            parser=parse_stock,
            headers={
                **browser_headers,
                "Referer": "https://bloxyvalues.com/stock",
                "Origin": "https://bloxyvalues.com",
            },
        ),
        ApiSource(
            name="BloxFruitsCode",
            url=DEFAULT_THIRD_API_URL,
            parser=parse_stock,
            headers={
                **browser_headers,
                "Referer": "https://bloxfruitscode.com/blox-fruits-stock-live-right-now/",
                "Origin": "https://bloxfruitscode.com",
            },
        ),
    )


def get_current_stock() -> Stock:
    errors: list[str] = []
    for source in stock_sources():
        try:
            stock = source.parser(read_json(source))
            logging.info("Stock received from %s", source.name)
            return stock
        except BotError as error:
            logging.warning("Stock source %s failed: %s", source.name, error)
            errors.append(str(error))
    raise BotError("All 3 public stock APIs failed. " + " | ".join(errors))


def read_state() -> Stock | None:
    if not STATE_PATH.exists():
        return None
    try:
        saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return Stock(
            normal=tuple(saved["normal"]),
            mirage=tuple(saved["mirage"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BotError(f"State file is invalid: {error}") from error


def write_state(stock: Stock) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(stock.canonical(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_message(stock: Stock, now: datetime | None = None) -> str:
    local_time = (now or datetime.now(UTC)).astimezone(MOSCOW_TZ).strftime("%H:%M")
    normal = "\n".join(f"- {fruit}" for fruit in stock.normal)
    mirage = "\n".join(f"- {fruit}" for fruit in stock.mirage)
    return (
        "Blox Fruits Stock обновился\n\n"
        "Normal Stock\n\n"
        f"{normal}\n\n"
        "Mirage Stock\n\n"
        f"{mirage}\n\n"
        f"Время обновления: {local_time} (UTC+3)"
    )


def write_github_output(name: str, value: str) -> None:
    output_file = os.getenv("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as file:
            file.write(f"{name}={value}\n")


def prepare() -> bool:
    stock = get_current_stock()
    previous_stock = read_state()
    if previous_stock is not None and stock.canonical() == previous_stock.canonical():
        PENDING_MESSAGE_PATH.unlink(missing_ok=True)
        write_github_output("state_changed", "false")
        logging.info("Stock did not change; no message will be sent.")
        return False

    # State is deliberately written before sending; the workflow persists it
    # through an Actions cache before the separate --send step.
    write_state(stock)
    PENDING_MESSAGE_PATH.write_text(format_message(stock) + "\n", encoding="utf-8")
    write_github_output("state_changed", "true")
    logging.info("Stock changed; message is ready.")
    return True


def send_telegram_message(message: str) -> None:
    token = required_env("TELEGRAM_BOT_TOKEN")
    chat_id = required_env("TELEGRAM_CHAT_ID")
    payload: dict[str, Any] = {"chat_id": chat_id, "text": message}
    topic_id = os.getenv("TOPIC_ID", "").strip()
    if topic_id:
        try:
            payload["message_thread_id"] = int(topic_id)
        except ValueError as error:
            raise BotError("TOPIC_ID must be an integer") from error

    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BotError(f"Telegram sendMessage failed: {error}") from error
    if not response_data.get("ok"):
        raise BotError(f"Telegram rejected the message: {response_data.get('description', 'unknown error')}")


def send() -> None:
    try:
        message = PENDING_MESSAGE_PATH.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise BotError("No pending message to send") from error
    if not message:
        raise BotError("Pending message is empty")
    send_telegram_message(message)
    logging.info("Telegram message sent.")


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--send", action="store_true")
    args = parser.parse_args()
    try:
        prepare() if args.prepare else send()
    except BotError as error:
        logging.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
