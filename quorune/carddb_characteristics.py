from __future__ import annotations

import copy
from typing import Any, Mapping

from .carddb import CardRecord
from .cast_face_offers import CURRENT_CAST_FACE_OFFERS_VERSION
from .model import CardInstance
from .shared_type_doors import printed_shared_type_faces
from .omen import omen_characteristic_face
from .rules.face_characteristic_values import (
    current_face_mana_value,
    printed_face_colors,
    printed_face_mana_value,
)
from .defined_token_faces import (
    DEFINED_TOKEN_FACES_FIELD, DEFINED_TOKEN_IDENTITY_FIELD, defined_token_characteristic_values,
    defined_token_current_characteristics, defined_token_copiable_characteristics,
)


def _custom_object_characteristics(card: CardInstance) -> Mapping[str, Any]:
    values = dict(defined_token_characteristic_values(card))
    if DEFINED_TOKEN_FACES_FIELD in values:
        return defined_token_current_characteristics(card, values)
    return values


def separate_custom_display_text(
    card: CardInstance,
    characteristics: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep current custom-object display prose out of execution inputs."""

    result = dict(characteristics)
    custom = (
        card.annotations.get("object_characteristics")
        or card.annotations.get("token_characteristics")
        or card.annotations.get("copy_overrides")
        or {}
    )
    from .entry_copy import COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD
    for field in (COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD):
        if field in custom:
            result[field] = custom[field]
    if "display_text" in custom:
        result["display_oracle_text"] = str(
            result.get("oracle_text") or ""
        )
        result["oracle_text"] = str(
            result.get("executable_oracle_text") or ""
        )
    return result


def custom_copyable_characteristics(card: CardInstance) -> dict[str, Any]:
    """Return isolated custom-object copiable values with typed text roles."""

    result = copy.deepcopy(
        dict(card.annotations.get("token_characteristics") or {})
    )
    if DEFINED_TOKEN_FACES_FIELD in result:
        result = defined_token_current_characteristics(card)
    result.setdefault("name", card.printed_name)
    result.setdefault("mana_cost", "")
    result.setdefault("mana_value", 0)
    result.setdefault("type_line", "Token")
    if "display_text" in result:
        result.setdefault("executable_oracle_text", "")
    else:
        result.setdefault("oracle_text", "")
    result.setdefault("keywords", [])
    result.setdefault("colors", [])
    result.setdefault("produced_mana", [])
    return result


def copiable_card_characteristics(card, record, base):
    """Capture copiable values while keeping a derived original value separate."""
    from .card_program_faces import selected_face_id
    result = dict(base)
    result.update(copy.deepcopy(dict(card.annotations.get('copy_overrides') or {})))
    if DEFINED_TOKEN_FACES_FIELD in result or DEFINED_TOKEN_IDENTITY_FIELD in card.annotations:
        result = defined_token_copiable_characteristics(card, result)
    if record is not None and record.faces:
        from .entry_copy import COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD
        result.setdefault(COPIABLE_ORACLE_FIELD, record.oracle_id)
        result.setdefault(COPIABLE_FACE_FIELD, selected_face_id(record, card))
    if (record is not None and record.layout == 'transform' and len(record.faces) == 2
            and selected_face_id(record, card) == record.faces[1].get('name')):
        # Mana value is derived, rather than itself copied. The back face's
        # own copiable mana cost remains empty under CR 202.3b and 707.2.
        result['mana_value'] = printed_face_mana_value({'mana_cost': result.get('mana_cost') or ''})
    return result


def library_reveal_printed_identity(record: CardRecord) -> tuple[str, str]:
    """Read the public identity of the physical card before it changes zones."""
    if record.faces and record.layout in {"modal_dfc", "transform", "adventure", "prepare"}:
        front = record.faces[0]
        return str(front["name"]), str(front["type_line"])
    return record.name, record.type_line


def _shared_type_split_characteristics(card: CardInstance, record: CardRecord) -> dict[str, Any] | None:
    if (card.shared_type_doors is None or not card.shared_type_doors.printed
            or card.annotations.get("copy_overrides") is not None):
        return None
    faces = printed_shared_type_faces(record)
    doors = card.shared_type_doors.rebind(tuple(face["name"] for face in faces))
    unlocked = tuple(face for face in faces if face["name"] in doors.unlocked)
    text = "\n".join(str(face.get("oracle_text") or "") for face in unlocked)
    colors = {color for face in unlocked for color in printed_face_colors(face)}
    return {
        "name": " // ".join(face["name"] for face in unlocked),
        "mana_cost": " // ".join(str(face.get("mana_cost") or "") for face in unlocked),
        "mana_value": sum(printed_face_mana_value(face) for face in unlocked),
        "type_line": faces[0]["type_line"],
        "oracle_text": text, "executable_oracle_text": text,
        "power": None, "toughness": None, "loyalty": None, "defense": None,
        "colors": [color for color in "WUBRG" if color in colors],
        "keywords": list(record.keywords) if unlocked else [],
        "produced_mana": [], "ability_fragments": [],
    }


def shared_type_copy_display_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Keep copied door text as display data beside its typed ability catalog."""
    result = dict(values)
    result["display_text"] = result.pop("oracle_text", "")
    result["executable_oracle_text"] = ""
    return result


def public_object_rules_names(
    card: CardInstance, effective: Mapping[str, Any], record: CardRecord | None = None,
) -> tuple[str, ...]:
    """Read public rules names from the current object and its typed face data."""

    if card.zone not in {"battlefield", "graveyard"} or card.face_down or card.phased_out:
        return ()
    if card.zone == "graveyard" and record is not None and record.faces:
        if record.layout == "split":
            return tuple(face["name"] for face in record.faces)
        if record.layout in {"modal_dfc", "transform", "flip", "meld", "adventure", "omen", "prepare", "battle"}:
            return (record.faces[0]["name"],)

    name = effective.get("name")
    if type(name) is not str or not name:
        return ()
    if card.zone == "battlefield":
        from .rules.room_copy_values import current_room_rules_names

        room_names = current_room_rules_names(card, name)
        if room_names is not None:
            return room_names
    return (name,)


def base_card_characteristics(
    card: CardInstance,
    record: CardRecord | None,
    *,
    cast_face_version: int | None = CURRENT_CAST_FACE_OFFERS_VERSION,
    public_zone_front_faces: bool = False,
    split_permanent_face_values_version: int | None = 1,
) -> dict[str, Any]:
    """Adapt printed or custom object data to the shared evaluator schema."""

    values = _custom_object_characteristics(card)
    if record is None or "display_text" in values:
        legacy_text = str(values.get("oracle_text", ""))
        display_text = str(
            values.get(
                "display_text",
                legacy_text or (record.oracle_text if record else ""),
            )
        )
        return {
            "name": "" if card.object_kind == "emblem" else values.get('name',card.printed_name) if DEFINED_TOKEN_FACES_FIELD in values or DEFINED_TOKEN_IDENTITY_FIELD in card.annotations else card.printed_name,
            "mana_cost": str(
                values.get("mana_cost", record.mana_cost if record else "")
            ),
            "mana_value": values.get(
                "mana_value", record.mana_value if record else 0
            ),
            "type_line": str(
                values.get(
                    "type_line", record.type_line if record else "Token"
                )
            ),
            "oracle_text": display_text,
            "executable_oracle_text": legacy_text,
            "power": values.get("power"),
            "toughness": values.get("toughness"),
            "loyalty": values.get("loyalty"),
            "defense": values.get("defense"),
            "keywords": list(
                values.get("keywords", record.keywords if record else [])
            ),
            "colors": list(
                values.get("colors", record.colors if record else [])
            ),
            "produced_mana": list(
                values.get(
                    "produced_mana",
                    record.produced_mana if record else [],
                )
            ),
            "ability_fragments": list(
                values.get("ability_fragments", [])
            ),
        }

    shared_type_values = _shared_type_split_characteristics(card, record)
    if shared_type_values is not None:
        return shared_type_values
    face = omen_characteristic_face(card, record, cast_face_version=cast_face_version)
    if (face is None and public_zone_front_faces and record.layout in {'modal_dfc', 'transform'}
            and len(record.faces) == 2 and card.zone in {'graveyard', 'exile', 'command'}
            and not card.face_down and card.annotations.get('prospective_cast_face') is not True):
        face = record.faces[0]
    elif face is None and card.active_face:
        face = next(
            (
                value
                for value in record.faces
                if str(value.get("name") or "") == card.active_face
            ),
            None,
        )
    if record.layout=='prepare' and not values.get('is_prepare_spell'):
        face=record.faces[0]
    selected = face is not None and (
        record.layout != "split" or card.zone == "stack"
        or card.annotations.get("prospective_cast_face") is True
        or (
            split_permanent_face_values_version is not None and card.zone == "battlefield"
            and printed_shared_type_faces(record)
        )
    )
    return {
        "name": str(face.get("name")) if face else record.name,
        "mana_cost": str(face.get("mana_cost") or "") if face else record.mana_cost,
        "mana_value": current_face_mana_value(record, face) if selected else record.mana_value,
        "type_line": str(face.get("type_line") or "") if face else record.type_line,
        "oracle_text": str(face.get("oracle_text") or "") if face else record.oracle_text,
        "executable_oracle_text": (
            str(face.get("oracle_text") or "")
            if face
            else record.oracle_text
        ),
        "power": face.get("power") if face else record.power,
        "toughness": face.get("toughness") if face else record.toughness,
        "loyalty": face.get("loyalty") if face else record.loyalty,
        "defense": face.get("defense") if face else record.defense,
        "keywords": list(record.keywords),
        "colors": printed_face_colors(face) if selected else list(record.colors),
        "produced_mana": list(record.produced_mana),
        "ability_fragments": [],
    }


__all__ = [
    "base_card_characteristics",
    "public_object_rules_names",
    "custom_copyable_characteristics",
    "copiable_card_characteristics",
    "separate_custom_display_text",
]
