from __future__ import annotations

"""Closed Detain instructions and their locked continuous-effect owner."""

from typing import Any, Mapping

from ..mana_value_limit import ManaValueLimitSpec
from ..replacement.immutable import thaw_value


DETAIN_OPERATION = "detain_permanents"
DETAIN_CAPABILITY = "continuous.resolution.detain"
DETAIN_RULES = ("506.4a", "508.1c", "509.1b", "602.5", "611.2a", "701.35a", "800.4m")
DETAIN_HANDLER_ID = f"effect.declaration.{DETAIN_OPERATION}.v1"


def detain_target_schema(*, creatures_only: bool, up_to_two: bool) -> dict[str, Any]:
    schema = {
        "zones": ["battlefield"], "categories": ["permanent"],
        "count": 2 if up_to_two else 1, "controller_relation": "opponent",
    }
    if creatures_only:
        schema["types_any"] = ["creature"]
    else:
        schema["types_none"] = ["land"]
    if up_to_two:
        schema["up_to"] = 2
    return schema


def validate_detain_instruction(effect: Mapping[str, Any], *, resolved: bool = False) -> None:
    """Keep target slots and the untargeted opponent set distinct."""
    if not isinstance(effect, Mapping) or effect.get("op") != DETAIN_OPERATION or (
        type(effect.get("schema_version")) is not int or effect["schema_version"] != 1
    ):
        raise ValueError("Detain requires its closed version-one instruction")
    fields = set(effect) - ({"reason", "_runtime_source"} if resolved else set())
    if fields == {"op", "schema_version", "maximum_mana_value"}:
        ManaValueLimitSpec(effect["maximum_mana_value"])
        return
    if fields == {"op", "schema_version", "cards"}:
        refs = effect["cards"]
        if not resolved and type(refs) is str and refs == "$targets":
            return
        if (resolved and isinstance(refs, (list, tuple)) and len(refs) <= 2
                and all(type(ref) is str and ref for ref in refs)
                and len(set(refs)) == len(refs)):
            return
    raise ValueError("Detain requires the exact target slots or a fixed mana-value ceiling")


def detain_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        validate_detain_instruction(effect)
    except (TypeError, ValueError):
        return ()
    if "maximum_mana_value" in effect:
        return (DETAIN_CAPABILITY,) if target_schema is None else ()
    schemas = tuple(detain_target_schema(creatures_only=creatures, up_to_two=optional)
                    for creatures in (False, True) for optional in (False, True))
    return (DETAIN_CAPABILITY, "target.revalidate_resolution") if thaw_value(target_schema) in schemas else ()


def _nonland_opponents_at_most(host, *, actor, maximum_mana_value):
    from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation, select_affected_permanents
    from ..object_predicate import ObjectQuerySpec

    limit = ManaValueLimitSpec(maximum_mana_value)
    spec = AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), excluded_types=("land",)),
        controller_relation=PermanentControllerRelation.OPPONENTS,
    )
    rows = select_affected_permanents(
        host.affected_permanent_object_rows(actor), spec, actor=actor,
        active_seats=host.affected_permanent_active_seats(),
        apnap_order=host.affected_permanent_apnap_order(),
    )
    return tuple(row.ref for row in rows if limit.permits(row.mana_value))


def resolve_detain(host, effect, *, actor, reason):
    from ..continuous_effect_state import create_resolution_declaration_rule_effect, resolution_effect_source
    from ..declaration_rule_effects import ATTACK_BLOCK_PROHIBITION

    validate_detain_instruction(effect, resolved=True)
    refs = (tuple(effect["cards"]) if "cards" in effect else
            _nonland_opponents_at_most(host, actor=actor, maximum_mana_value=effect["maximum_mana_value"]))
    if not refs:
        return []
    cards = tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in refs)
    if any(card.phased_out or card.controller == actor for card in cards):
        raise ValueError("Detain requires current phased-in opposing permanents")
    committed = create_resolution_declaration_rule_effect(
        host, source=resolution_effect_source(host, effect, fallback_card=cards[0]),
        targets=cards, restriction=ATTACK_BLOCK_PROHIBITION,
        expires_on_next_turn_of=actor, forbid_activations=True,
    )
    if committed is None:
        raise ValueError("Detain requires the continuous-effect journal")
    host._log(actor, "permanent.detained", reason,
              {"objects": list(refs), "continuous_effect": committed.effect_id, "expires_on_next_turn_of": actor},
              importance=1, changed_objects=[card.object_id for card in cards])
    return list(refs)
