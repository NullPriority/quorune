from __future__ import annotations

import copy
from typing import Any, Mapping

from .carddb import CardRecord
from .model import CardInstance
from .rules.face_characteristic_values import printed_face_mana_value,printed_face_colors,current_face_mana_value


def _custom_object_characteristics(card: CardInstance) -> Mapping[str, Any]:
    return dict(
        card.annotations.get("object_characteristics")
        or card.annotations.get("token_characteristics")
        or card.annotations.get("copy_overrides")
        or {}
    )


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


def base_card_characteristics(
    card: CardInstance,
    record: CardRecord | None,
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
            "name": "" if card.object_kind == "emblem" else card.printed_name,
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

    face = None
    if card.active_face:
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
    selected=face is not None and (record.layout!='split' or card.zone=='stack' or card.annotations.get('prospective_cast_face') is True)
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
    "custom_copyable_characteristics",
    "copiable_card_characteristics",
    "separate_custom_display_text",
]
