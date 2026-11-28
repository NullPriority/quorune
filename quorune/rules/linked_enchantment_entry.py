"""The targeted entry carrier for the shared linked graveyard Aura lifecycle."""

from __future__ import annotations

from collections.abc import Mapping

from ..ability_fragments import ability_fragment_to_dict, canonical_ability_fragments
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)
from ..continuous_effect_model import ContinuousEffectDuration, ContinuousOperation, Layer
from ..enchant_spec import LinkedGraveyardCreatureEnchantSpec
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .linked_graveyard_aura import (
    ENTRY_DEPENDENCIES, LINK_ANNOTATION, LINKED_RETURN_EVENT_CODE, linked_graveyard_aura_spell_target,
)

LINKED_ENCHANTMENT_ENTRY_VERSION = "linked_enchantment_entry_version"
LINKED_ENCHANTMENT_ENTRY_MECHANIC = "linked-graveyard-enchantment-entry"
LINKED_ENCHANTMENT_ENTRY_TEMPLATE = "linked-graveyard-enchantment-entry-v1"
LINKED_ENCHANTMENT_ENTRY_DEPENDENCIES = (
    *ENTRY_DEPENDENCIES, "target.revalidate_resolution", "trigger.condition.fixed_public_state",
    "continuous.characteristics.fixed_type_replacement",
)

_ENCHANTMENT_TYPES = (
    "Aura", "Background", "Cartouche", "Case", "Class", "Curse", "Plan",
    "Role", "Room", "Rune", "Saga", "Shard", "Shrine",
)
_FORM_SPEC = LinkedGraveyardCreatureEnchantSpec(LINK_ANNOTATION)
_FORM_TYPES = (
    ContinuousOperation("remove_types", _ENCHANTMENT_TYPES, field="subtypes"),
    ContinuousOperation("add_types", ("Aura",), field="subtypes"),
)
_FORM_ABILITY = (ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(_FORM_SPEC)),)


def linked_enchantment_entry_condition():
    condition = FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
        predicate=ObjectQuerySpec(zones=("battlefield",)),
        schema_version=2,
    )
    return {"field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
            "op": "truthy", "value": True, "condition": condition.to_dict()}


def linked_enchantment_entry_effect():
    return {
        "op": "reanimate_attached_creature_aura", LINKED_ENCHANTMENT_ENTRY_VERSION: 1,
        "aura": "$source", "aura_incarnation": "$context.card_object_identity", "card": "$target.0",
    }


def linked_enchantment_entry_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if LINKED_ENCHANTMENT_ENTRY_MECHANIC not in program.coverage and not any(
        LINKED_ENCHANTMENT_ENTRY_VERSION in effect for effect in nested_effects(program.effects)
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
        or program.cost_schema is not None or program.destination is not None
        or program.trigger_limit is not None or program.handlers
        or not program.ability_id.startswith("trigger:")
        or FrozenMap(program.target_schema or {}) != FrozenMap(linked_graveyard_aura_spell_target())
        or FrozenMap(program.event_condition or {}) != FrozenMap(linked_enchantment_entry_condition())
        or tuple(map(FrozenMap, program.effects)) != (FrozenMap(linked_enchantment_entry_effect()),)
    ):
        raise ValueError("Linked enchantment return requires its exact targeted intervening entry ability")
    return LINKED_ENCHANTMENT_ENTRY_DEPENDENCIES


def _form_sources(host, aura):
    from ..continuous_effect_state import active_resolution_effects

    layers = active_resolution_effects(host.state, aura)
    types = {value.source_id for value in layers if value.layer is Layer.TYPE and value.operations == _FORM_TYPES}
    abilities = {value.source_id for value in layers if value.layer is Layer.ABILITY and value.operations == _FORM_ABILITY}
    return types & abilities


def linked_enchantment_current_spec(host, card):
    if card.zone != "battlefield" or not _form_sources(host, card):
        return None
    effective = canonical_ability_fragments(host._effective_card_data(card).get("ability_fragments", ()))
    return _FORM_SPEC if _FORM_SPEC in effective else None


def _install_aura_form(host, aura, stack_ref):
    from ..continuous_effect_state import (
        ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
    )

    if stack_ref in _form_sources(host, aura):
        return
    effects = create_resolution_continuous_effect_components(
        host, source=ResolutionEffectSource(stack_ref), targets=(aura,),
        components=(
            ResolutionContinuousComponent(Layer.TYPE, "4", _FORM_TYPES),
            ResolutionContinuousComponent(Layer.ABILITY, "6", _FORM_ABILITY),
        ),
        duration=ContinuousEffectDuration.ZONE_OBJECT,
    )
    if len(effects) != 2:
        raise GameRuleError("Linked enchantment entry requires the layered effect journal")


def apply_linked_enchantment_entry(host, effect, *, actor, reason):
    from ..aura.runtime import return_linked_graveyard_creature
    from ..continuous_effect_state import ResolutionEffectSource
    from ..selection.target_query import target_identity_matches_snapshot

    exact_fields = set(linked_enchantment_entry_effect())
    if (
        not exact_fields <= set(effect) <= exact_fields | {"_runtime_source", "_replacement_selections"}
        or type(effect.get(LINKED_ENCHANTMENT_ENTRY_VERSION)) is not int
        or effect[LINKED_ENCHANTMENT_ENTRY_VERSION] != 1
    ):
        raise GameRuleError("Linked enchantment entry has a closed resolving descriptor")
    source = ResolutionEffectSource.from_effect(effect)
    aura = host.state.cards.get(source.object_id)
    if (
        aura is None or aura.zone != "battlefield" or aura.phased_out
        or aura.ref != effect["aura"] or aura.logical_object_id != effect["aura_incarnation"]
        or aura.logical_object_id != source.logical_object_id
    ):
        return None
    item = next((candidate for candidate in host.state.stack if candidate.ref == source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None and item.semantic_key else None
    if (
        item is None or item.controller != actor or not item.context.get("currently_resolving")
        or item.source_object_id != aura.object_id or tuple(item.targets) != (effect["card"],)
        or program is None or not linked_enchantment_entry_program_dependencies(program)
    ):
        raise GameRuleError("Linked enchantment entry lost its original resolving ability or target")
    target_ref = effect["card"]
    target = next((card for card in host.state.cards.values() if card.ref == target_ref), None)
    snapshot = item.context.get("target_snapshots", {}).get(target_ref)
    if (
        target is None or target.zone != "graveyard" or not target.is_card_object
        or not isinstance(snapshot, Mapping) or not target_identity_matches_snapshot(host, target_ref, snapshot)
    ):
        return None
    types, _, _ = host._type_parts(str(host._effective_card_data(target).get("type_line") or ""))
    if "creature" not in types:
        return None
    _install_aura_form(host, aura, item.ref)
    return return_linked_graveyard_creature(
        host, aura, target, _FORM_SPEC, actor=actor, reason=reason,
        effect={**effect, "event_code": LINKED_RETURN_EVENT_CODE},
    )
