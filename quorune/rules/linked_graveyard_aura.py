"""Authority for the source-spanned linked graveyard Aura lifecycle."""

from collections.abc import Mapping

from ..ability_fragments import ability_fragment_to_dict
from ..enchant_spec import LinkedGraveyardCreatureEnchantSpec
from ..replacement.immutable import FrozenMap


LINKED_GRAVEYARD_AURA_CAPABILITY = "attachment.aura.linked_graveyard_creature_return"
LINKED_GRAVEYARD_AURA_MECHANIC = "linked-graveyard-aura-creature-return"
LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE = "linked-graveyard-aura-spell-v1"
LINKED_GRAVEYARD_AURA_ENTRY_TEMPLATE = "linked-graveyard-aura-entry-v1"
LINK_ANNOTATION = "linked_graveyard_aura_creature"
LINKED_RETURN_EVENT_CODE = "aura.linked_graveyard_return"

SPELL_DEPENDENCIES = (
    LINKED_GRAVEYARD_AURA_CAPABILITY, "attachment.aura.typed_restriction", "target.revalidate_resolution",
)
ENTRY_DEPENDENCIES = (
    LINKED_GRAVEYARD_AURA_CAPABILITY, "attachment.aura.typed_restriction", "zone.reanimate.fixed_target",
    "zone.change.destination_replacement", "trigger.event.normalized_zone_change", "trigger.placement.apnap",
)


def linked_graveyard_aura_spell_target():
    return {"zones": ["graveyard"], "categories": ["card"], "creature": True, "count": 1}


def linked_graveyard_aura_spell_handler(*, link=LINK_ANNOTATION):
    from ..semantic_runtime.ability_fragments import LINKED_GRAVEYARD_ENCHANT_HANDLER_ID

    return {
        "handler_id": LINKED_GRAVEYARD_ENCHANT_HANDLER_ID, "schema_version": 1, "event": "resolve",
        "fragment": ability_fragment_to_dict(LinkedGraveyardCreatureEnchantSpec(link)),
    }


def linked_graveyard_aura_prepare_effect():
    return {"op": "prepare_graveyard_creature_aura", "aura": "$card", "card": "$target.0"}


def linked_graveyard_aura_entry_effect(*, link=LINK_ANNOTATION, event_code=LINKED_RETURN_EVENT_CODE):
    return {
        "op": "reanimate_attached_creature_aura", "aura": "$source",
        "aura_incarnation": "$context.card_object_identity", "link_annotation": link,
        "event_code": event_code,
    }


def _native_linked_handler(value):
    fragment = value.get("fragment")
    schema = fragment.get("value") if isinstance(fragment, Mapping) else None
    return isinstance(schema, Mapping) and schema.get("link_annotation") == LINK_ANNOTATION


def linked_graveyard_aura_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if (LINKED_GRAVEYARD_AURA_MECHANIC not in program.coverage
            and not any(_native_linked_handler(value) for value in program.handlers)
            and not any(effect.get("link_annotation") == LINK_ANNOTATION
                        for effect in nested_effects(program.effects))):
        return ()
    if program.cost_schema is not None or program.event_condition is not None or program.trigger_limit is not None:
        raise ValueError("Linked graveyard Aura requires its original spell or self-entry event")
    effects = tuple(map(FrozenMap, program.effects))
    if ((program.event, program.active_zone, program.destination) == ("resolve", "stack", "battlefield")
            and program.ability_id == "spell:front" and isinstance(program.target_schema, Mapping)
            and FrozenMap(program.target_schema) == FrozenMap(linked_graveyard_aura_spell_target())
            and tuple(map(FrozenMap, program.handlers)) == (FrozenMap(linked_graveyard_aura_spell_handler()),)
            and effects == (FrozenMap(linked_graveyard_aura_prepare_effect()),)):
        return SPELL_DEPENDENCIES
    if ((program.event, program.active_zone) == ("permanent.enter.self", "battlefield")
            and program.ability_id.startswith("trigger:") and program.target_schema is None
            and program.destination is None and not program.handlers
            and effects == (FrozenMap(linked_graveyard_aura_entry_effect()),)):
        return ENTRY_DEPENDENCIES
    raise ValueError("Linked graveyard Aura requires its complete incarnation-bound spell or entry descriptor")
