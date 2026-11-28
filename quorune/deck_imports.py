from __future__ import annotations

import csv
import io
import json
import re
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .card_printing import CardPrinting
from .deck import DeckDefinition, DeckEntry, DeckParseError, parse_deck_text
from .util import stable_json, unique_preserving_order
from .version import __version__


class DeckImportError(RuntimeError):
    pass


def public_deck_source(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port is not None:
            return None
    except ValueError:
        return None
    host = (parsed.hostname or "").removeprefix("www.")
    if host == "archidekt.com":
        match = re.fullmatch(r"/decks/([1-9]\d*)(?:/[A-Za-z0-9_-]+)?/?", parsed.path)
        return ("archidekt", match[1]) if match else None
    if host == "tappedout.net":
        match = re.fullmatch(r"/mtg-decks/([a-zA-Z0-9_-]+)/?", parsed.path)
        return ("tappedout", match[1]) if match else None
    return None


_BOARD_NAMES = {
    "commander": "commander", "commanders": "commander", "commandzone": "commander",
    "main": "mainboard", "mainboard": "mainboard", "deck": "mainboard",
    "side": "sideboard", "sideboard": "sideboard",
    "maybe": "maybeboard", "maybeboard": "maybeboard", "considering": "maybeboard",
    "companion": "companion",
}


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _make_deck(name: str, entries: list[DeckEntry], source: str, provider: str) -> DeckDefinition:
    if not entries:
        raise DeckParseError("The public deck export contains no card entries")
    return DeckDefinition(
        name=name,
        entries=entries,
        commanders=unique_preserving_order(entry.name for entry in entries if entry.board == "commander"),
        source=source,
        metadata={"import_provider": provider},
    )


def _finish(value: str) -> str | None:
    return {
        "foil": "foil", "etched": "etched", "nonfoil": "nonfoil",
        "normal": "nonfoil", "true": "foil", "yes": "foil", "1": "foil",
    }.get(value.strip().casefold())


def parse_archidekt(payload: object, *, source: str) -> DeckDefinition:
    if not isinstance(payload, dict) or payload.get("private") or not isinstance(payload.get("cards"), list):
        raise DeckParseError("Expected an accessible Archidekt deck with card entries")
    category_data = {
        row["name"].casefold(): row for row in (payload.get("categories") or ())
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    if payload.get("customCards"):
        raise DeckParseError("This Archidekt deck includes custom cards")
    entries = []
    for row in payload["cards"]:
        if not isinstance(row, dict):
            raise DeckParseError("An Archidekt card entry is malformed")
        card = row.get("card")
        if not isinstance(card, dict):
            raise DeckParseError("An Archidekt entry is missing its card")
        raw_definition = card.get("oracleCard")
        definition = raw_definition if isinstance(raw_definition, dict) else {}
        title = str(definition.get("name") or card.get("displayName") or "").strip()
        quantity = row.get("quantity")
        if not title or type(quantity) is not int or quantity < 1:
            raise DeckParseError("An Archidekt card entry is missing its name or quantity")
        categories = [name.strip() for name in (row.get("categories") or ()) if isinstance(name, str) and name.strip()]
        boards = {_BOARD_NAMES.get(_name_key(name)) for name in categories}
        included = any(
            category_data.get(name.casefold(), {}).get("includedInDeck", True)
            for name in categories
        )
        if "commander" in boards:
            board = "commander"
        elif row.get("companion") or "companion" in boards:
            board = "companion"
        elif "sideboard" in boards:
            board = "sideboard"
        elif "maybeboard" in boards or (categories and not included):
            board = "maybeboard"
        else:
            board = "mainboard"
        raw_edition = card.get("edition")
        edition = raw_edition if isinstance(raw_edition, dict) else {}
        entries.append(DeckEntry(
            title, quantity, board,
            tags=unique_preserving_order(name for name in categories if _name_key(name) not in _BOARD_NAMES),
            printing=CardPrinting.from_dict({
                "scryfall_id": card.get("uid"),
                "set_code": edition.get("editioncode"),
                "collector_number": card.get("collectorNumber"),
                "language": definition.get("lang"),
                "finish": _finish(str(row.get("modifier") or "")),
            }),
        ))
    return _make_deck(str(payload.get("name") or "Archidekt deck"), entries, source, "archidekt")


def parse_tappedout_csv(raw: str, *, source: str, name: str) -> DeckDefinition:
    reader = csv.DictReader(io.StringIO(raw))
    headers = {_name_key(column) for column in reader.fieldnames or ()}
    if not headers.intersection({"name", "card", "cardname"}) or not headers.intersection({"qty", "quantity", "amount"}):
        raise DeckParseError("Expected a TappedOut CSV with card names and quantities")
    entries = []
    for row in reader:
        fields = {_name_key(key): value.strip() for key, value in row.items() if isinstance(key, str) and isinstance(value, str)}
        title = fields.get("name") or fields.get("card") or fields.get("cardname")
        if not title:
            raise DeckParseError("A TappedOut row is missing its card name")
        quantity_text = fields.get("qty") or fields.get("quantity") or fields.get("amount") or ""
        if not quantity_text.isdecimal() or int(quantity_text) < 1:
            raise DeckParseError("A TappedOut row has an invalid quantity")
        board_key = _name_key(fields.get("board") or fields.get("section") or "mainboard")
        board = _BOARD_NAMES.get(board_key)
        if fields.get("commander", "").casefold() in {"1", "true", "yes", "commander"}:
            board = "commander"
        if board is None:
            raise DeckParseError("A TappedOut row has an unsupported board")
        edition = fields.get("setcode") or fields.get("set") or fields.get("printing")
        entries.append(DeckEntry(
            title, int(quantity_text), board,
            tags=unique_preserving_order(tag.strip() for tag in re.split(r"[,;|]", fields.get("tags", "")) if tag.strip()),
            printing=CardPrinting.from_dict({
                "scryfall_id": fields.get("scryfallid"),
                "set_code": edition if edition and re.fullmatch(r"[A-Za-z0-9]+", edition) else None,
                "collector_number": fields.get("collectornumber") or fields.get("collector"),
                "language": fields.get("language") or fields.get("lang"),
                "finish": _finish(fields.get("finish") or fields.get("foil") or ""),
            }),
        ))
    return _make_deck(name, entries, source, "tappedout")


class _ProviderRedirects(HTTPRedirectHandler):
    def __init__(self, host: str):
        self.hosts = {host, f"www.{host}"}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in self.hosts or parsed.username or parsed.password or parsed.port is not None:
            raise DeckImportError("The deck provider redirected outside its public HTTPS site")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(host: str, endpoint: str, timeout: float) -> str:
    opener = build_opener(_ProviderRedirects(host))
    request = Request(endpoint, headers={
        "Accept": "application/json,text/csv,text/plain;q=0.9,*/*;q=0.1",
        "User-Agent": f"Quorune/{__version__} public deck import",
    })
    with opener.open(request, timeout=timeout) as response:
        limit = 8 * 1024 * 1024
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise DeckImportError("The public deck export is too large")
        return raw.decode("utf-8-sig")


def fetch_public_deck(
    value: str, *, cache_dir: Path | None, force_refresh: bool = False, timeout: float = 20,
) -> DeckDefinition:
    identity = public_deck_source(value)
    if identity is None:
        raise ValueError("Enter a public Archidekt or TappedOut deck link")
    provider, deck_id = identity
    host = f"{provider}.{'com' if provider == 'archidekt' else 'net'}"
    source = f"https://{host}/{'decks' if provider == 'archidekt' else 'mtg-decks'}/{deck_id}/"
    cache = cache_dir / f"{provider}-{deck_id}.json" if cache_dir is not None else None
    if cache is not None and cache.is_file() and not force_refresh:
        return DeckDefinition.from_dict(json.loads(cache.read_text(encoding="utf-8")))
    try:
        if provider == "archidekt":
            endpoint = f"https://{host}/api/decks/{deck_id}/"
            deck = parse_archidekt(json.loads(_download(host, endpoint, timeout)), source=source)
        else:
            name = deck_id.replace("-", " ")
            try:
                deck = parse_tappedout_csv(_download(host, f"{source}?fmt=csv", timeout), source=source, name=name)
            except DeckParseError:
                exported = _download(host, f"{source}?fmt=txt", timeout)
                if exported.lstrip().startswith("<"):
                    raise DeckParseError("The deck provider returned a page instead of a text export")
                deck = parse_deck_text(exported, name=name, source=source)
                deck.metadata["import_provider"] = provider
    except (HTTPError, URLError, TimeoutError, UnicodeError, ValueError) as exc:
        label = "Archidekt" if provider == "archidekt" else "TappedOut"
        raise DeckImportError(f"Could not import this public {label} deck. Export a text list and paste it instead.") from exc
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache.parent, delete=False) as handle:
            handle.write(stable_json(deck.to_dict()))
            pending = Path(handle.name)
        try:
            pending.replace(cache)
        finally:
            pending.unlink(missing_ok=True)
    return deck
