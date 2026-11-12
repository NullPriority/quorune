from __future__ import annotations

"""Closed source-attachment action grammar shared by compiler and runtime catalogs."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..event_counter_reference import EVENT_ENTRY_OBJECT


FIXED_SOURCE_ATTACHMENT_CAPABILITY = "attachment.action.fixed_source"
TARGETED_EQUIPMENT_CAPABILITY = "attachment.action.targeted_equipment"
TARGETED_EQUIPMENT_VERSION = "targeted_equipment_version"
TARGETED_EQUIPMENT_SOURCE = "$target_group.target_0"
TARGETED_EQUIPMENT_RECIPIENT = "$target_group.target_1"
_TARGETED_EQUIPMENT_RECIPIENTS = (TARGETED_EQUIPMENT_RECIPIENT, "$source.zone_object", EVENT_ENTRY_OBJECT)
ENTRY_ATTACHMENT_CAPABILITY = "attachment.action.entry_equipment"
ENTRY_ATTACHMENT_VERSION = "entry_attachment_version"
ENTRY_ATTACHMENT_EVENTS = frozenset({"permanent.enter", "creature.enter"})
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

def entry_equipment_attachment_effect():
    return {
        "op": "attach", "attachment_kind": "equipment", "source": "$source.zone_object",
        "target": EVENT_ENTRY_OBJECT, ENTRY_ATTACHMENT_VERSION: 1,
    }


def targeted_equipment_attachment_effect(recipient: str) -> dict[str, Any]:
    return {
        "op": "attach", "attachment_kind": "equipment", "source": TARGETED_EQUIPMENT_SOURCE,
        "target": recipient, TARGETED_EQUIPMENT_VERSION: 1,
    }


def is_targeted_equipment_attachment(effect: Mapping[str, Any]) -> bool:
    if TARGETED_EQUIPMENT_VERSION in effect:
        return True
    if effect.get("op") != "attach":
        return False
    source, target = effect.get("source"), effect.get("target")
    return (
        isinstance(source, str) and (
            source in {"$target", "$targets"} or source.startswith(("$target.", "$target[", "$target_group."))
        )
    ) or isinstance(target, str) and target.startswith("$target_group.")


def _resolved_attachment_role(value: Any) -> str | None:
    if isinstance(value, (list, tuple)):
        if len(value) > 1:
            raise ValueError("An attachment role has at most one resolved target")
        value = value[0] if value else None
    if value is not None and (type(value) is not str or not value or value.startswith("$")):
        raise ValueError("An attachment role requires a resolved public object reference")
    return value


def validate_targeted_equipment_attachment(
    effect: Mapping[str, Any], *, resolved: bool = False,
) -> tuple[str | None, str | None] | None:
    fields = set(effect) - ({"_runtime_source"} if resolved else set())
    if (fields != {"op", "attachment_kind", "source", "target", TARGETED_EQUIPMENT_VERSION}
        or effect.get("op") != "attach" or effect.get("attachment_kind") != "equipment"
        or type(effect.get(TARGETED_EQUIPMENT_VERSION)) is not int or effect[TARGETED_EQUIPMENT_VERSION] != 1):
        raise ValueError("Targeted Equipment attachment requires its closed versioned instruction")
    source, target = effect["source"], effect["target"]
    if resolved:
        if not isinstance(source, (list, tuple)):
            raise ValueError("Targeted Equipment requires its own resolved target group")
        return _resolved_attachment_role(source), _resolved_attachment_role(target)
    if source != TARGETED_EQUIPMENT_SOURCE or target not in _TARGETED_EQUIPMENT_RECIPIENTS:
        raise ValueError("Targeted Equipment requires the exact attachment and recipient roles")
    return None


def validate_entry_equipment_attachment(effect: Mapping[str, Any], *, resolved: bool = False) -> tuple[str, ...]:
    expected = entry_equipment_attachment_effect()
    fields = set(effect) - ({"_runtime_source"} if resolved else set())
    if (fields != set(expected) or type(effect.get(ENTRY_ATTACHMENT_VERSION)) is not int
            or any(effect.get(key) != expected[key] for key in ("op", "attachment_kind", ENTRY_ATTACHMENT_VERSION))):
        raise ValueError("Entry equipment attachment has a closed schema")
    for field in ("source", "target"):
        value = effect[field]
        if resolved:
            if value is not None and (type(value) is not str or not value or value.startswith("$")):
                raise ValueError("Entry attachment requires resolved battlefield references")
        elif value != expected[field]:
            raise ValueError("Entry attachment requires its original source and entrant")
    return (ENTRY_ATTACHMENT_CAPABILITY, FIXED_SOURCE_ATTACHMENT_CAPABILITY)


def entry_equipment_attachment_runtime_dependencies(program):
    def uses_entry_attachment(value):
        if isinstance(value, Mapping):
            return (ENTRY_ATTACHMENT_VERSION in value
                    or (value.get("op") == "attach" and value.get("target") == EVENT_ENTRY_OBJECT
                        and not is_targeted_equipment_attachment(value))
                    or any(uses_entry_attachment(child) for child in value.values()))
        return isinstance(value, (list, tuple)) and any(uses_entry_attachment(child) for child in value)

    if not uses_entry_attachment(program.effects):
        return ()
    if (program.event not in ENTRY_ATTACHMENT_EVENTS or program.active_zone != "battlefield"
            or program.target_schema is not None or len(program.effects) != 1
            or "cr-701-3-attach" not in program.coverage):
        raise ValueError("Entry attachment requires one closed battlefield entry result")
    effect, = program.effects
    if effect.get("op") != "offer_optional_effect":
        return validate_entry_equipment_attachment(effect)
    children = effect.get("effects")
    if (set(effect) != {"op", "player", "effects"} or effect.get("player") != "$controller"
            or not isinstance(children, (list, tuple)) or len(children) != 1
            or not isinstance(children[0], Mapping)):
        raise ValueError("Optional entry attachment requires one controller choice")
    return (*validate_entry_equipment_attachment(children[0]), "effect.choice.optional_fixed")


def equipment_attachment_is_legal(host, equipment, creature) -> bool:
    from .reconfigure import source_has_reconfigure
    from ..protection import ProtectionVerdict
    from ..target_protection_engine_adapter import protection_verdict_for_ref

    types, subtypes, _ = host._type_parts(str(host._effective_card_data(equipment).get("type_line") or ""))
    recipient = host._effective_card_data(creature)
    recipient_types, _, _ = host._type_parts(str(recipient.get("type_line") or ""))
    return ("creature" in recipient_types and "artifact" in types and "equipment" in subtypes
            and equipment.object_id != creature.object_id
            and ("creature" not in types or source_has_reconfigure(host, equipment))
            and protection_verdict_for_ref(host, recipient, equipment.ref) is ProtectionVerdict.ALLOWED)


def unavailable_optional_equipment_attachment(host, raw_effect, effect) -> bool:
    children = raw_effect.get("effects")
    if (not isinstance(children, (list, tuple)) or len(children) != 1
            or not isinstance(children[0], Mapping)):
        return False
    child, = children
    targeted = is_targeted_equipment_attachment(child)
    if not targeted and ENTRY_ATTACHMENT_VERSION not in child:
        return False
    resolved, = effect["effects"]
    if targeted:
        validate_targeted_equipment_attachment(child)
        equipment_ref, creature_ref = validate_targeted_equipment_attachment(resolved, resolved=True)
    else:
        validate_entry_equipment_attachment(child)
        validate_entry_equipment_attachment(resolved, resolved=True)
        equipment_ref, creature_ref = resolved["source"], resolved["target"]
    cards = {card.ref: card for card in host.state.cards.values() if card.zone == "battlefield" and not card.phased_out}
    equipment, creature = cards.get(equipment_ref), cards.get(creature_ref)
    return equipment is None or creature is None or not equipment_attachment_is_legal(host, equipment, creature)


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
