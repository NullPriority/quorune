from __future__ import annotations

import json
import re
import time
from datetime import date
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .carddb import CardDatabase, CardRecord
from .util import normalize_card_name, stable_json, unique_preserving_order
from .version import __version__

MOXFIELD_ID_RE = re.compile(r"[A-Za-z0-9_-]{10,64}")
MOXFIELD_HOSTS = frozenset({"moxfield.com", "www.moxfield.com"})
QUANTITY_RE = re.compile(r"^\s*(?P<qty>\d+)\s*[xX]?\s+(?P<rest>.+?)\s*$")
SET_SUFFIX_RE = re.compile(
    r"\s+(?:\([A-Za-z0-9_]+\)|\[[A-Za-z0-9_]+\])(?:\s+[^#]+)?$"
)
TAG_RE = re.compile(r"\s+#[-\w]+")


@dataclass(slots=True)
class DeckEntry:
    name: str
    quantity: int = 1
    board: str = "mainboard"
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "board": self.board,
            "tags": list(self.tags),
        }


@dataclass(slots=True)
class DeckDefinition:
    name: str
    entries: list[DeckEntry]
    commanders: list[str]
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def total_cards(self, *, include_sideboard: bool = False) -> int:
        boards = {"mainboard", "commander", "companion"}
        if include_sideboard:
            boards.add("sideboard")
        return sum(entry.quantity for entry in self.entries if entry.board in boards)

    def expanded(self, boards: Iterable[str] = ("mainboard", "commander")) -> list[str]:
        allowed = set(boards)
        result: list[str] = []
        for entry in self.entries:
            if entry.board in allowed:
                result.extend([entry.name] * entry.quantity)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "entries": [entry.to_dict() for entry in self.entries],
            "commanders": list(self.commanders),
            "source": self.source,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeckDefinition":
        return cls(
            name=str(data.get("name") or "Unnamed Deck"),
            entries=[DeckEntry(**entry) for entry in data.get("entries", [])],
            commanders=list(data.get("commanders") or []),
            source=data.get("source"),
            metadata=dict(data.get("metadata") or {}),
        )


class DeckParseError(ValueError):
    pass


class MoxfieldFetchError(RuntimeError):
    pass


def _strip_line_annotations(value: str) -> tuple[str, list[str]]:
    tags = [match.group(0).strip() for match in TAG_RE.finditer(value)]
    value = TAG_RE.sub("", value).strip()
    value = re.sub(r"\s+\*CMDR\*\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+\*F\*\s*$", "", value, flags=re.IGNORECASE)
    # Moxfield plain-text exports can append set and collector information.
    # Prefer the bracketed card name before removing a suffix only when it is
    # unambiguous; Scryfall resolution later catches mistakes.
    match = re.match(r"^(.*?)(?:\s+\([A-Za-z0-9_]+\)\s+\S+)?$", value)
    if match:
        value = match.group(1).strip()
    return value, tags


def parse_deck_text(
    text: str,
    *,
    name: str = "Imported Deck",
    commander: str | None = None,
    source: str | None = None,
) -> DeckDefinition:
    entries: list[DeckEntry] = []
    commanders: list[str] = []
    board = "mainboard"
    section_aliases = {
        "commander": "commander",
        "commanders": "commander",
        "mainboard": "mainboard",
        "deck": "mainboard",
        "maindeck": "mainboard",
        "sideboard": "sideboard",
        "maybeboard": "maybeboard",
        "considering": "maybeboard",
        "companion": "companion",
    }

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith(";"):
            continue
        lower = line.rstrip(":").strip().casefold()
        if lower in section_aliases and (line.endswith(":") or not QUANTITY_RE.match(line)):
            board = section_aliases[lower]
            continue
        quantity_match = QUANTITY_RE.match(line)
        if quantity_match:
            quantity = int(quantity_match.group("qty"))
            rest = quantity_match.group("rest")
        else:
            # Permissive mode: a lone card name defaults to one.
            quantity = 1
            rest = line
        is_commander_marker = bool(re.search(r"\*CMDR\*", rest, re.IGNORECASE))
        card_name, tags = _strip_line_annotations(rest)
        if not card_name:
            continue
        entry_board = "commander" if is_commander_marker else board
        entry = DeckEntry(name=card_name, quantity=quantity, board=entry_board, tags=tags)
        entries.append(entry)
        if entry_board == "commander":
            commanders.extend([card_name] * quantity)

    if commander:
        normalized_commander = normalize_card_name(commander)
        commanders = [commander]
        found = False
        for entry in entries:
            if normalize_card_name(entry.name) == normalized_commander:
                found = True
                entry.board = "commander"
                if entry.quantity > 1:
                    entry.quantity -= 1
                    entries.append(DeckEntry(commander, 1, "commander", entry.tags.copy()))
                break
        if not found:
            entries.append(DeckEntry(commander, 1, "commander"))
    commanders = unique_preserving_order(commanders)
    if not entries:
        raise DeckParseError("No card lines were found")
    return DeckDefinition(name=name, entries=entries, commanders=commanders, source=source)


def _extract_moxfield_board(
    board_value: Any,
    board_name: str,
) -> list[DeckEntry]:
    entries: list[DeckEntry] = []

    def visit(value: Any, inferred_name: str | None = None) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return

        card_obj = value.get("card") if isinstance(value.get("card"), dict) else None
        card_name = None
        if card_obj:
            card_name = card_obj.get("name") or card_obj.get("faceName")
        card_name = card_name or value.get("cardName") or value.get("name") or inferred_name
        quantity = value.get("quantity") or value.get("count") or value.get("qty")
        if card_name and quantity is not None:
            try:
                quantity_int = int(quantity)
            except (TypeError, ValueError):
                quantity_int = 1
            tags_raw = value.get("tags") or value.get("categories") or []
            if isinstance(tags_raw, dict):
                tags = [str(tag) for tag, enabled in tags_raw.items() if enabled]
            elif isinstance(tags_raw, list):
                tags = [str(tag) for tag in tags_raw]
            else:
                tags = []
            entries.append(
                DeckEntry(
                    name=str(card_name),
                    quantity=quantity_int,
                    board=board_name,
                    tags=tags,
                )
            )
            return

        # The common Moxfield shape is a mapping keyed by card name.
        for key, child in value.items():
            if key in {
                "card",
                "quantity",
                "count",
                "qty",
                "tags",
                "categories",
                "boards",
            }:
                continue
            if isinstance(child, dict):
                visit(child, inferred_name=str(key))
            elif isinstance(child, list):
                visit(child)

    visit(board_value)
    # Deduplicate objects reached through wrapper keys.
    merged: dict[tuple[str, str], DeckEntry] = {}
    for entry in entries:
        key = (normalize_card_name(entry.name), entry.board)
        existing = merged.get(key)
        if existing is None:
            merged[key] = entry
        else:
            existing.quantity = max(existing.quantity, entry.quantity)
            existing.tags = unique_preserving_order(existing.tags + entry.tags)
    return list(merged.values())


def parse_moxfield_json(data: dict[str, Any], *, source: str | None = None) -> DeckDefinition:
    deck_name = str(data.get("name") or data.get("deckName") or "Moxfield Deck")
    entries: list[DeckEntry] = []
    board_candidates: list[tuple[str, Any]] = []

    boards = data.get("boards")
    if isinstance(boards, dict):
        for board_name, board_value in boards.items():
            board_candidates.append((str(board_name).casefold(), board_value))

    for key in (
        "mainboard",
        "commanders",
        "commander",
        "sideboard",
        "companions",
        "companion",
        "maybeboard",
    ):
        if key in data:
            board_candidates.append((key, data[key]))

    aliases = {
        "commanders": "commander",
        "commander": "commander",
        "mainboard": "mainboard",
        "sideboard": "sideboard",
        "companions": "companion",
        "companion": "companion",
        "maybeboard": "maybeboard",
    }
    for raw_name, value in board_candidates:
        board_name = aliases.get(raw_name, raw_name)
        entries.extend(_extract_moxfield_board(value, board_name))

    # Some API versions wrap cards under a top-level deck object.
    if not entries:
        for wrapper in ("deck", "data"):
            nested = data.get(wrapper)
            if isinstance(nested, dict):
                try:
                    return parse_moxfield_json(nested, source=source)
                except DeckParseError:
                    pass

    if not entries:
        raise DeckParseError("Could not identify card boards in Moxfield JSON")
    commanders = unique_preserving_order(
        entry.name for entry in entries if entry.board == "commander"
    )
    return DeckDefinition(
        name=deck_name,
        entries=entries,
        commanders=commanders,
        source=source,
        metadata={
            key: data.get(key)
            for key in ("id", "publicId", "format", "createdAtUtc", "lastUpdatedAtUtc")
            if data.get(key) is not None
        },
    )


def extract_moxfield_id(value: str) -> str:
    candidate = value.strip()
    if MOXFIELD_ID_RE.fullmatch(candidate):
        return candidate

    parsed = urllib.parse.urlparse(candidate)
    if not parsed.scheme and candidate.casefold().startswith(("moxfield.com/", "www.moxfield.com/")):
        parsed = urllib.parse.urlparse("https://" + candidate)
    if parsed.scheme.casefold() not in {"http", "https"} or (parsed.hostname or "").casefold() not in MOXFIELD_HOSTS:
        raise ValueError(f"Expected a Moxfield deck URL or public id, got {value!r}")
    path_parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].casefold() != "decks":
        raise ValueError(f"Moxfield URL does not identify a deck: {value!r}")
    deck_id = path_parts[1]
    if not MOXFIELD_ID_RE.fullmatch(deck_id):
        raise ValueError(f"Invalid Moxfield public deck id in {value!r}")
    return deck_id


def is_moxfield_source(value: str) -> bool:
    try:
        extract_moxfield_id(value)
    except ValueError:
        return False
    return True


def fetch_moxfield_deck(
    url_or_id: str,
    *,
    cache_dir: str | Path | None = None,
    timeout: float = 30,
    force_refresh: bool = False,
) -> DeckDefinition:
    """
    Fetch a public Moxfield deck through its unofficial public endpoints.

    Moxfield does not publish a supported public deck API, so this function is
    deliberately defensive and caches successful responses. If Cloudflare or a
    future API change blocks access, export the deck as Plain Text and load the
    resulting file instead.
    """
    deck_id = extract_moxfield_id(url_or_id)
    source_url = f"https://www.moxfield.com/decks/{deck_id}"
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_path = Path(cache_dir) / f"moxfield-{deck_id}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists() and not force_refresh:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("kind") == "json":
                return parse_moxfield_json(cached["payload"], source=source_url)
            return parse_deck_text(
                str(cached["payload"]),
                name=str(cached.get("name") or f"Moxfield {deck_id}"),
                source=source_url,
            )

    endpoint_candidates = [
        # Current public-deck consumers prefer v3, with v2 and the older host
        # retained as compatibility fallbacks. These routes are unofficial.
        (f"https://api2.moxfield.com/v3/decks/all/{deck_id}", "json"),
        (f"https://api2.moxfield.com/v2/decks/all/{deck_id}", "json"),
        (f"https://api.moxfield.com/v2/decks/all/{deck_id}", "json"),
        (f"https://api2.moxfield.com/v2/decks/all/{deck_id}/export", "text"),
    ]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 Chrome/126 Safari/537.36 quorune/{__version__}"
        ),
        "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
        "Referer": source_url,
        "Origin": "https://www.moxfield.com",
    }
    errors: list[str] = []
    for endpoint, expected in endpoint_candidates:
        request = urllib.request.Request(endpoint, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8-sig")
                content_type = response.headers.get("Content-Type", "")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            errors.append(f"{endpoint}: {exc}")
            continue
        try:
            if expected == "json" or "json" in content_type:
                payload = json.loads(raw)
                deck = parse_moxfield_json(payload, source=source_url)
                cache_payload = {"kind": "json", "payload": payload, "fetched_at": time.time()}
            else:
                # Some export endpoints return a JSON string containing text.
                try:
                    decoded = json.loads(raw)
                    if isinstance(decoded, str):
                        raw = decoded
                    elif isinstance(decoded, dict):
                        deck = parse_moxfield_json(decoded, source=source_url)
                        cache_payload = {
                            "kind": "json",
                            "payload": decoded,
                            "fetched_at": time.time(),
                        }
                        if cache_path:
                            cache_path.write_text(stable_json(cache_payload), encoding="utf-8")
                        deck.metadata["moxfield_endpoint"] = endpoint
                        return deck
                except json.JSONDecodeError:
                    pass
                deck = parse_deck_text(raw, name=f"Moxfield {deck_id}", source=source_url)
                cache_payload = {
                    "kind": "text",
                    "payload": raw,
                    "name": deck.name,
                    "fetched_at": time.time(),
                }
            if cache_path:
                cache_path.write_text(stable_json(cache_payload), encoding="utf-8")
            deck.metadata["moxfield_endpoint"] = endpoint
            return deck
        except (DeckParseError, json.JSONDecodeError) as exc:
            errors.append(f"{endpoint}: response could not be parsed ({exc})")

    raise MoxfieldFetchError(
        "Unable to fetch this public Moxfield deck. Moxfield's endpoints are "
        "unofficial and may be blocked. Use More → Export → Plain Text, save "
        "that text locally, and pass the file path instead. Attempts: "
        + " | ".join(errors)
    )


class DeckLoader:
    def __init__(
        self,
        card_db: CardDatabase,
        *,
        cache_dir: str | Path | None = None,
    ):
        self.card_db = card_db
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def load(
        self,
        source: str | Path,
        *,
        commander: str | None = None,
        deck_name: str | None = None,
        force_refresh: bool = False,
    ) -> DeckDefinition:
        source_str = str(source)
        if is_moxfield_source(source_str):
            deck = fetch_moxfield_deck(
                source_str,
                cache_dir=self.cache_dir,
                force_refresh=force_refresh,
            )
        else:
            path = Path(source)
            if not path.exists():
                raise FileNotFoundError(path)
            raw = path.read_text(encoding="utf-8-sig")
            if path.suffix.casefold() == ".json":
                data = json.loads(raw)
                if isinstance(data, dict) and "entries" in data:
                    deck = DeckDefinition.from_dict(data)
                elif isinstance(data, dict):
                    deck = parse_moxfield_json(data, source=str(path))
                else:
                    raise DeckParseError("JSON deck files must contain an object")
            else:
                deck = parse_deck_text(
                    raw,
                    name=deck_name or path.stem,
                    commander=commander,
                    source=str(path),
                )
        if deck_name:
            deck.name = deck_name
        if commander and not deck.commanders:
            deck.commanders = [commander]
        self.resolve_names(deck)
        return deck

    def resolve_names(self, deck: DeckDefinition) -> None:
        """Canonicalize all card names against the local Oracle database."""
        unresolved: list[str] = []
        for entry in deck.entries:
            try:
                card = self.card_db.lookup(entry.name)
            except KeyError:
                unresolved.append(entry.name)
                continue
            entry.name = card.name
        canonical_commanders: list[str] = []
        for name in deck.commanders:
            try:
                canonical_commanders.append(self.card_db.lookup(name).name)
            except KeyError:
                unresolved.append(name)
        deck.commanders = unique_preserving_order(canonical_commanders)
        if unresolved:
            raise DeckParseError(
                "The following names were not found in the local Scryfall data: "
                + ", ".join(unique_preserving_order(unresolved))
            )

    def validate_commander_deck(
        self,
        deck: DeckDefinition,
        *,
        expected_size: int = 100,
        require_commander: bool = True,
        check_color_identity: bool = True,
        check_legality: bool = True,
    ) -> list[str]:
        issues: list[str] = []
        declared_format = str(deck.metadata.get("format") or "").strip().casefold()
        if declared_format and declared_format != "commander":
            issues.append(
                f"Deck source declares format {declared_format!r}; expected 'commander'"
            )
        total = deck.total_cards()
        if total != expected_size:
            issues.append(f"Deck contains {total} cards; expected {expected_size}")
        if require_commander and not deck.commanders:
            issues.append("No commander was identified")
        if len(deck.commanders) > 2:
            issues.append(f"Deck has {len(deck.commanders)} commanders")

        counts: dict[str, int] = {}
        records: dict[str, CardRecord] = {}
        for entry in deck.entries:
            if entry.board not in {"mainboard", "commander"}:
                continue
            card = self.card_db.lookup(entry.name)
            records[card.name] = card
            counts[card.name] = counts.get(card.name, 0) + entry.quantity
        from .compiler.deck_copy_limits import compiled_deck_copy_limit

        for name, count in sorted(counts.items()):
            if count <= 1 or "Basic" in records[name].type_line:
                continue
            limit = compiled_deck_copy_limit(records[name])
            if limit is None:
                issues.append(f"Singleton warning: {count} copies of {name}")
            elif limit.maximum is not None and count > limit.maximum:
                issues.append(f"Deck has {count} copies of {name}. Its printed limit is {limit.maximum}.")

        if check_legality:
            issues.extend(
                issue["message"]
                for issue in self.commander_legality_issues(deck)
            )

        if check_color_identity and deck.commanders:
            identity: set[str] = set()
            for commander_name in deck.commanders:
                identity.update(self.card_db.lookup(commander_name).color_identity)
            for name in counts:
                card_identity = set(records[name].color_identity)
                if not card_identity.issubset(identity):
                    issues.append(
                        f"Color identity warning: {name} has {sorted(card_identity)}, "
                        f"outside commander identity {sorted(identity)}"
                    )
        return issues

    def commander_legality_issues(
        self,
        deck: DeckDefinition,
        *,
        as_of: date | None = None,
    ) -> list[dict[str, Any]]:
        """Return structured Commander-legality issues for an exact snapshot.

        A card that is present in the Oracle snapshot but has a future release
        date may be explicitly acknowledged as preview content. Banned,
        unknown, and already-released illegal cards remain non-overridable.
        """

        today = as_of or date.today()
        legality_entries = {
            (entry.name, entry.board)
            for entry in deck.entries
            if entry.board
            in {"mainboard", "commander", "companion", "sideboard"}
        }
        issues: list[dict[str, Any]] = []
        for name, board in sorted(legality_entries):
            card = self.card_db.lookup(name)
            legality = str(
                card.legalities.get("commander") or "unknown"
            ).casefold()
            if legality == "legal":
                continue
            try:
                release_date = date.fromisoformat(card.released_at)
            except ValueError:
                release_date = None
            preview = (
                legality == "not_legal"
                and release_date is not None
                and release_date > today
            )
            issues.append(
                {
                    "code": (
                        "preview_not_yet_legal"
                        if preview
                        else "commander_illegal"
                    ),
                    "message": (
                        f"Commander legality: {name} is {legality} "
                        f"on the {board}"
                    ),
                    "card": name,
                    "board": board,
                    "legality": legality,
                    "released_at": card.released_at or None,
                    "confirmable": preview,
                }
            )
        return issues
