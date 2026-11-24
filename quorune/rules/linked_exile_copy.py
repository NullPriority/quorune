"""Read the live public exile targets of a linked copy ability."""

from __future__ import annotations

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..granted_activation_model import GrantedActivatedAbilitySpec
from ..linked_ability_exile_model import LINKED_EXILE_COPY_TARGET_PREDICATE, LinkedAbilityExile
from ..linked_ability_exile_state import current_linked_ability_exile
from ..linked_exile_return_model import ExiledObjectIdentity
from ..replacement.immutable import FrozenMap


def linked_exile_copy_candidates(
    host, *, source: ContinuousObjectIdentity, ability_pair: str,
) -> tuple[ExiledObjectIdentity, ...]:
    """Use only the current source incarnation and its original linked producer."""
    if not isinstance(source, ContinuousObjectIdentity):
        raise GameRuleError("A linked exile copy requires its source incarnation")
    current = host.state.cards.get(source.object_id)
    if (current is None or current.logical_object_id != source.logical_object_id
            or current.zone != "battlefield" or current.phased_out):
        return ()

    identities = current_linked_ability_exile(host, source=source, ability_pair=ability_pair)
    eligible = []
    for identity in identities:
        card = host.state.cards[identity.object_id]
        if card.face_down or not card.is_card_object:
            continue
        characteristics = host._effective_card_data(card)
        types, _, _ = host._type_parts(str(characteristics.get("type_line") or ""))
        if "creature" in types:
            eligible.append(identity)
    return tuple(eligible)


def capture_linked_exile_copy_target(
    host, *, source: ContinuousObjectIdentity, ability_pair: str, target_ref: str,
) -> LinkedAbilityExile:
    candidates = linked_exile_copy_candidates(host, source=source, ability_pair=ability_pair)
    target = next((identity for identity in candidates if identity.ref == target_ref), None)
    if target is None:
        raise GameRuleError("The selected card is outside the current linked exile")
    return LinkedAbilityExile(source, ability_pair, target)


def current_linked_exile_copy_target(host, captured: LinkedAbilityExile):
    """Resolve an announced target only while its captured link is still live."""
    if not isinstance(captured, LinkedAbilityExile):
        raise GameRuleError("A linked exile copy requires its captured target")
    candidates = linked_exile_copy_candidates(host, source=captured.source, ability_pair=captured.ability_pair)
    if captured.exiled not in candidates:
        return None
    return host.state.cards[captured.exiled.object_id]


def prepare_linked_exile_copy_values(
    host, captured: LinkedAbilityExile, *, retained_ability: GrantedActivatedAbilitySpec,
) -> FrozenMap | None:
    """Detach the chosen creature's values and keep the specific copied activation."""
    from ..ability_fragments import ability_fragment_to_dict
    from ..card_program_faces import selected_face_id
    from ..defined_token_faces import single_face_copy_values
    from ..entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD, copiable_oracle_id
    from ..token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception

    if not isinstance(retained_ability, GrantedActivatedAbilitySpec):
        raise GameRuleError("A linked exile copy requires its typed retained activation")
    target = current_linked_exile_copy_target(host, captured)
    if target is None:
        return None

    values = single_face_copy_values(host._copyable_characteristics(target))
    values[COPIABLE_ORACLE_FIELD] = copiable_oracle_id(target)
    values[COPIABLE_FACE_FIELD] = selected_face_id(host.card_record(target), target)
    exception = TokenCopyExceptionSpec(
        power=0, toughness=0, add_ability_fragments=(ability_fragment_to_dict(retained_ability),),
    )
    return FrozenMap(apply_copy_exception(values, exception))


def linked_exile_copy_target_schema(ability_pair: str) -> dict:
    """One public creature card exiled by the source's paired entry effect."""
    condition = linked_exile_copy_target_condition({"schema_version": 1, "ability_pair": ability_pair})
    return {
        "zones": ["exile"], "categories": ["card"], "types_any": ["creature"], "count": 1,
        "predicate": LINKED_EXILE_COPY_TARGET_PREDICATE, "resolution_condition": condition,
    }


def linked_exile_copy_target_condition(value: Mapping) -> dict:
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "ability_pair"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["ability_pair"]) is not str or not value["ability_pair"].strip()
            or value["ability_pair"] != value["ability_pair"].strip()):
        raise ValueError("A linked exile copy target requires its exact paired entry effect")
    return dict(value)


def linked_exile_copy_target_matches(card, *, source_incarnation, ability_pair) -> bool:
    """Evaluate one public target without scanning unrelated exile objects."""
    from ..linked_ability_exile_state import card_has_current_exile_link

    return bool(
        card is not None and card.zone == "exile" and card.is_card_object and not card.face_down
        and card_has_current_exile_link(card, source_incarnation=source_incarnation, ability_pair=ability_pair)
    )


LINKED_EXILE_COPY_VERSION = "linked_exile_copy_version"
LINKED_EXILE_COPY_CAPABILITY = "continuous.resolution.linked_exile_self_copy"
LINKED_EXILE_COPY_PAIR = "entry-exiled-creature-copy"
LINKED_EXILE_COPY_OPERATION = "copy_until_end_of_turn"
LINKED_EXILE_COPY_TEMPLATE = "linked-exile-self-copy-v1"
LINKED_EXILE_COPY_DEPENDENCIES = (
    LINKED_EXILE_COPY_CAPABILITY, "zone.exile.linked_ability_pair", "target.revalidate_resolution",
)


def linked_exile_copy_effect() -> dict:
    return {
        "op": LINKED_EXILE_COPY_OPERATION, LINKED_EXILE_COPY_VERSION: 1,
        "source": "$source.zone_object", "target": "$target.0", "ability_pair": LINKED_EXILE_COPY_PAIR,
    }


def is_linked_exile_copy_effect(effect) -> bool:
    return isinstance(effect, Mapping) and LINKED_EXILE_COPY_VERSION in effect


def validate_linked_exile_copy_effect(effect, *, resolved=False):
    expected = linked_exile_copy_effect()
    if (not is_linked_exile_copy_effect(effect) or type(effect[LINKED_EXILE_COPY_VERSION]) is not int
            or effect[LINKED_EXILE_COPY_VERSION] != 1):
        raise ValueError("A linked exile copy requires its supported version")
    if not resolved:
        if dict(effect) != expected:
            raise ValueError("A linked exile copy has a closed descriptor")
        return
    if (set(effect) - {"_runtime_source", "reason"} != set(expected)
            or any(effect[key] != expected[key] for key in ("op", "ability_pair"))
            or type(effect["target"]) is not str or not effect["target"]
            or (effect["source"] is not None and (type(effect["source"]) is not str or not effect["source"]))):
        raise ValueError("A resolved linked exile copy requires its original source and target")


def linked_exile_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..replacement.immutable import thaw_value

    if len(effects) != 1 or "cr-115-targets" not in mechanic_ids:
        return ()
    try:
        validate_linked_exile_copy_effect(effects[0])
        if thaw_value(target_schema) != linked_exile_copy_target_schema(LINKED_EXILE_COPY_PAIR):
            return ()
    except (ValueError, TypeError):
        return ()
    return LINKED_EXILE_COPY_DEPENDENCIES


def linked_exile_copy_runtime_dependencies(program):
    if not any(is_linked_exile_copy_effect(effect) for effect in program.effects):
        return ()
    required = linked_exile_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=("cr-115-targets",),
    )
    if (not required or program.event != "activate" or program.active_zone != "battlefield"
            or not _closed_linked_exile_copy_cost(program.cost_schema)):
        raise ValueError("Linked exile copy requires one ordinary two-mana battlefield activation")
    return required


def _closed_linked_exile_copy_cost(cost) -> bool:
    from ..replacement.immutable import thaw_value

    expected = {
        "text": "{2}", "mana": {"GENERIC": 2, **dict.fromkeys("WUBRGC", 0)},
        "complex_symbols": [], "tap_source": False, "untap_source": False,
        "discard_source": False, "sacrifice_source": False, "exile_source": False,
        "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
        "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
    }
    return isinstance(cost, Mapping) and thaw_value(cost) == expected


def is_closed_linked_exile_copy_program(program) -> bool:
    try:
        required = linked_exile_copy_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
