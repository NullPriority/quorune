from __future__ import annotations

"""Closed source-attachment action grammar shared by compiler and runtime catalogs."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype


FIXED_SOURCE_ATTACHMENT_CAPABILITY = "attachment.action.fixed_source"
FIXED_RESTRICTED_EQUIP_CAPABILITY = "attachment.equip.fixed_restricted"
FIXED_NONMANA_EQUIP_CAPABILITY = "attachment.equip.fixed_nonmana"
LIVING_WEAPON_CAPABILITY = "trigger.keyword.living_weapon"
FOR_MIRRODIN_CAPABILITY = "trigger.keyword.for_mirrodin"
JOB_SELECT_CAPABILITY = "trigger.keyword.job_select"
CREATE_AND_ATTACH = "create_token_then_attach_source"
ATTACH_CREATED_TOKEN = "attach_source_to_created_token"
CREATED_TOKEN_ATTACHMENT_DEPENDENCIES = (
    "token.creation.fixed_definition",
    FIXED_SOURCE_ATTACHMENT_CAPABILITY,
)


def validate_created_token_attachment(effect: Mapping[str, Any], *, resolved: bool = False) -> tuple[str, ...]:
    """Validate creation of one printed definition before the attachment instruction."""
    from ..created_token_counter_model import validate_single_created_token

    fields = {"op", "schema_version", "controller", "source", "token", "token_mechanics"}
    if (
        set(effect) != fields or effect.get("op") != CREATE_AND_ATTACH
        or type(effect.get("schema_version")) is not int or effect["schema_version"] != 1
    ):
        raise ValueError("Attached-token creation has a closed schema")
    controller, source = effect["controller"], effect["source"]
    if resolved:
        if type(controller) is not str or not controller or (
            source is not None and (type(source) is not str or not source)
        ):
            raise ValueError("Attached-token creation controller or source is malformed")
    elif controller != "$controller" or source != "$source.zone_object":
        raise ValueError("Attached-token creation must bind its controller and source")
    token = effect["token"]
    if not isinstance(token, Mapping):
        raise ValueError("Attached-token creation requires a fixed creature definition")
    characteristics = token.get("characteristics")
    if not isinstance(characteristics, Mapping) or not {"power", "toughness"} <= set(characteristics):
        raise ValueError("Attached-token creation requires a fixed creature definition")
    return tuple(dict.fromkeys((*CREATED_TOKEN_ATTACHMENT_DEPENDENCIES, *validate_single_created_token(effect))))


def created_token_attachment_payload_dependencies(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    operation = value.get("op")
    if operation == ATTACH_CREATED_TOKEN:
        raise ValueError("Attachment recipients must come from committed token results")
    if operation == CREATE_AND_ATTACH:
        return validate_created_token_attachment(value)
    return ()

_FIXED_MANA = r"(?:\{(?:\d+|[WUBRGC])\})+"
_EQUIP = re.compile(
    rf"^Equip(?: (?P<restriction>creature token|commander|legendary creature|"
    rf"[A-Z][A-Za-z'-]*))? (?P<cost>{_FIXED_MANA})"
    r"(?P<once>\. Activate only once each turn\.)?\.?$",
)
_NONMANA_EQUIP = re.compile(
    r"^Equip[—-](?P<cost>Discard a card|Sacrifice a creature|"
    r"Sacrifice an artifact|Sacrifice another nonland permanent|"
    r"Pay [1-9][0-9]* life|Pay (?:\{E\})+|"
    r"Remove a charge counter from this Equipment)"
    r"(?P<once>\. Activate only once each turn)?\.?$", re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedEquipAbilitySpec:
    cost_text: str
    target_schema: Mapping[str, Any]
    restricted: bool
    once_per_turn: bool
    nonmana: bool = False

    @property
    def capability_id(self) -> str:
        if self.nonmana:
            return FIXED_NONMANA_EQUIP_CAPABILITY
        return (
            FIXED_RESTRICTED_EQUIP_CAPABILITY
            if self.restricted or self.once_per_turn
            else "attachment.equip.fixed_mana"
        )


def fixed_equip_ability_spec(text: str) -> FixedEquipAbilitySpec | None:
    """Parse ordinary restricted mana or closed fixed nonmana Equip."""

    normalized = " ".join(text.strip().split())
    flavor = re.fullmatch(r"[A-Za-z][A-Za-z0-9 '’!?-]+\s+[—-]\s+(Equip(?: |[—-]).+)", normalized)
    if flavor is not None:
        normalized = flavor.group(1)
    nonmana = _NONMANA_EQUIP.fullmatch(normalized)
    match = nonmana or _EQUIP.fullmatch(normalized)
    if match is None:
        return None
    restriction = None if nonmana else match.group("restriction")
    schema: dict[str, Any] = {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "controller": "you",
        "creature": True,
        "count": 1,
    }
    if restriction is not None:
        normalized = restriction.casefold()
        if normalized == "creature token":
            schema["token"] = True
        elif normalized == "commander":
            schema["commander"] = True
        elif normalized == "legendary creature":
            schema["supertypes_any"] = ["legendary"]
        else:
            subtype = canonical_creature_subtype(restriction)
            if subtype is None:
                return None
            schema["subtypes_any"] = [subtype]
    return FixedEquipAbilitySpec(
        cost_text=match.group("cost"),
        target_schema=schema,
        restricted=restriction is not None,
        once_per_turn=match.group("once") is not None,
        nonmana=nonmana is not None,
    )


__all__ = [
    "FOR_MIRRODIN_CAPABILITY",
    "FIXED_RESTRICTED_EQUIP_CAPABILITY",
    "FIXED_SOURCE_ATTACHMENT_CAPABILITY",
    "FixedEquipAbilitySpec",
    "LIVING_WEAPON_CAPABILITY",
    "JOB_SELECT_CAPABILITY",
    "fixed_equip_ability_spec",
]
