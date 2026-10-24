from __future__ import annotations

import gzip
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

MANA_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")
SPACE_RE = re.compile(r"\s+")


def normalize_card_name(value: str) -> str:
    """Normalize names for resilient exact matching without changing card semantics."""
    value = unicodedata.normalize("NFKC", value)
    value = (
        value.replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .replace("–", "-")
        .replace("—", "-")
    )
    value = SPACE_RE.sub(" ", value.strip()).casefold()
    return value


def open_text_auto(path: str | Path):
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    with open_text_auto(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"Expected a JSON object at {path}:{line_number}")
            yield obj


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def parse_mana_symbols(cost: str | None) -> list[str]:
    if not cost:
        return []
    return [symbol.upper() for symbol in MANA_SYMBOL_RE.findall(cost)]


def mana_cost_to_vector(cost: str | None) -> tuple[dict[str, int], list[str]]:
    """
    Parse an ordinary printed mana cost into a conservative payment vector.

    Returns (fixed requirements, complex symbols). Hybrid, Phyrexian, X, snow,
    half-mana, and other special symbols are intentionally returned as complex
    so the caller can supply a declared cost instead of receiving a false legal
    judgment.
    """
    fixed: dict[str, int] = {"GENERIC": 0, "W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
    complex_symbols: list[str] = []
    for symbol in parse_mana_symbols(cost):
        if symbol.isdigit():
            fixed["GENERIC"] += int(symbol)
        elif symbol in {"W", "U", "B", "R", "G", "C"}:
            fixed[symbol] += 1
        else:
            complex_symbols.append(symbol)
    return fixed, complex_symbols


def normalize_mana_bundle(bundle: Mapping[str, int] | None) -> dict[str, int]:
    result = {key: 0 for key in ("W", "U", "B", "R", "G", "C")}
    if not bundle:
        return result
    for raw_key, raw_value in bundle.items():
        key = str(raw_key).upper()
        if key not in result:
            raise ValueError(f"Unsupported mana type {raw_key!r}; use W/U/B/R/G/C")
        value = int(raw_value)
        if value < 0:
            raise ValueError("Mana quantities cannot be negative")
        result[key] += value
    return result


def spendable_total(pool: Mapping[str, int]) -> int:
    return sum(int(pool.get(color, 0)) for color in ("W", "U", "B", "R", "G", "C"))


def pay_mana_from_pool(
    pool: Mapping[str, int],
    requirements: Mapping[str, int],
    *,
    payment: Mapping[str, int] | None = None,
    spending_policy: Any | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """
    Validate and spend an ordinary mana requirement.

    When ``payment`` is supplied, it is the exact W/U/B/R/G/C bundle the
    player chose to spend.  This matters whenever preserving a color for a
    later priority window is strategically relevant.  Without a declaration,
    colored and colorless requirements are paid first and generic is then paid
    deterministically from C, W, U, B, R, G.

    Restricted mana, snow, hybrid, Phyrexian, convoke, improvise, delve, and
    other nonordinary payments remain explicit player/model reasoning.
    """
    new_pool = normalize_mana_bundle(pool)
    req = {"GENERIC": int(requirements.get("GENERIC", 0))}
    for color in ("W", "U", "B", "R", "G", "C"):
        req[color] = int(requirements.get(color, 0))
        if req[color] < 0:
            raise ValueError("Mana requirements cannot be negative")
    if req["GENERIC"] < 0:
        raise ValueError("Mana requirements cannot be negative")

    if spending_policy is not None:
        return spending_policy.pay(new_pool,req,payment)

    if payment is not None:
        spent = normalize_mana_bundle(payment)
        for color in ("W", "U", "B", "R", "G", "C"):
            if spent[color] > new_pool[color]:
                raise ValueError(
                    f"Declared payment spends {spent[color]} {color}, but the pool has {new_pool[color]}"
                )
            if spent[color] < req[color]:
                raise ValueError(
                    f"Declared payment supplies only {spent[color]} {color}; {req[color]} is required"
                )
        required_total = req["GENERIC"] + sum(req[color] for color in ("W", "U", "B", "R", "G", "C"))
        spent_total = sum(spent.values())
        if spent_total != required_total:
            raise ValueError(
                f"Declared payment spends {spent_total} mana; exactly {required_total} is required"
            )
        surplus_after_fixed = sum(
            spent[color] - req[color] for color in ("W", "U", "B", "R", "G", "C")
        )
        if surplus_after_fixed != req["GENERIC"]:
            raise ValueError("Declared payment does not satisfy the generic component")
        for color, amount in spent.items():
            new_pool[color] -= amount
        return new_pool, spent

    spent = {key: 0 for key in ("W", "U", "B", "R", "G", "C")}
    for color in ("W", "U", "B", "R", "G", "C"):
        amount = req[color]
        if new_pool[color] < amount:
            raise ValueError(
                f"Insufficient {color} mana: need {amount}, have {new_pool[color]}"
            )
        new_pool[color] -= amount
        spent[color] += amount

    generic = req["GENERIC"]
    for color in ("C", "W", "U", "B", "R", "G"):
        if generic <= 0:
            break
        amount = min(new_pool[color], generic)
        new_pool[color] -= amount
        spent[color] += amount
        generic -= amount
    if generic:
        raise ValueError(
            f"Insufficient generic mana: short {generic}; pool had {spendable_total(pool)} total"
        )
    return new_pool, spent


def compact_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {}, 0, False)}


def truncate(text: str | None, limit: int = 240) -> str:
    if not text:
        return ""
    text = SPACE_RE.sub(" ", text.strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
