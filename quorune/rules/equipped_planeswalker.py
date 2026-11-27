"""Closed declaration and triggered-ability rules for an attached planeswalker."""

from ..ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE, GrantedTriggeredAbilitySpec, StaticComponentSpec,
    canonical_ability_fragments,
)
from ..attachments import attached_object_identity
from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY
from .optional_counter_capability_shapes import damage_source_counter_effect


EQUIPPED_PLANESWALKER_HANDLER = "continuous.attached.planeswalker-combat-loyalty.v1"
EQUIPPED_PLANESWALKER_CAPABILITY = "continuous.attached.planeswalker_combat_loyalty"
EQUIPPED_DAMAGE_COVERAGE = "equipped-planeswalker-combat-loyalty"
EQUIPPED_PLANESWALKER_TEMPLATE = "equipped-planeswalker-combat-loyalty-v1"
EQUIPPED_PLANESWALKER_DEPENDENCIES = (
    EQUIPPED_PLANESWALKER_CAPABILITY, "combat.declaration.typed_components",
)
EQUIPPED_DAMAGE_DEPENDENCIES = (
    EQUIPPED_PLANESWALKER_CAPABILITY, "trigger.event.normalized_damage",
    "counter.producer.fixed_effect", SCALAR_AMOUNT_CAPABILITY,
)


def equipped_damage_condition():
    return {"all": [
        {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.source"},
        {"field": "combat", "op": "truthy", "value": True},
        {"field": "source_controller_is_active_player", "op": "eq", "value": True},
    ]}


def equipped_damage_effect():
    return damage_source_counter_effect("loyalty")


def equipped_planeswalker_fragment(descriptor):
    if (set(descriptor) != {"handler_id", "schema_version", "event", "fragment"}
            or descriptor["handler_id"] != EQUIPPED_PLANESWALKER_HANDLER
            or type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != 1
            or descriptor["event"] != "characteristics.evaluate"):
        raise ValueError("Attached planeswalker descriptor is outside its closed scope")
    fragment, = canonical_ability_fragments((descriptor["fragment"],))
    if (not isinstance(fragment, GrantedTriggeredAbilitySpec)
            or fragment.event != "damage.dealt" or fragment.schema_version != 1):
        raise ValueError("Attached planeswalker requires its typed combat damage trigger")
    return fragment


def equipped_damage_runtime_dependencies(program):
    if EQUIPPED_DAMAGE_COVERAGE not in program.coverage:
        return ()
    if ((program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or program.provenance.get("granted_only") is not True
            or not program.ability_id.startswith("trigger:")
            or not program.ability_id.endswith(":granted")
            or program.handlers or program.cost_schema is not None or program.target_schema is not None
            or FrozenMap(program.event_condition or {}) != FrozenMap(equipped_damage_condition())
            or tuple(FrozenMap(effect) for effect in program.effects) != (FrozenMap(equipped_damage_effect()),)):
        raise ValueError("Attached planeswalker requires exact current equipped combat damage and loyalty")
    return EQUIPPED_DAMAGE_DEPENDENCIES


def validate_equipped_planeswalker_child(program, lookup):
    descriptors = tuple(handler for handler in program.handlers
                        if handler.get("handler_id") == EQUIPPED_PLANESWALKER_HANDLER)
    if not descriptors:
        return
    if (len(descriptors) != 1 or len(program.handlers) != 1
            or (program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
            or program.provenance.get("template_id") != EQUIPPED_PLANESWALKER_TEMPLATE
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or program.effects or program.event_condition is not None
            or program.cost_schema is not None or program.target_schema is not None):
        raise ValueError("Attached planeswalker requires its effective static ability")
    fragment = equipped_planeswalker_fragment(descriptors[0])
    child = lookup(fragment.semantic_key)
    if (child is None or child.ability_id != fragment.ability_id
            or child.key.partition(":")[0] != program.key.partition(":")[0]
            or not equipped_damage_runtime_dependencies(child)):
        raise ValueError("Attached planeswalker has no matching granted combat trigger")


def current_equipped_creature(state, source, resolve):
    identity = attached_object_identity(state.cards, source)
    if identity is None:
        return None
    recipient = resolve(state.cards[identity.object_id])
    return recipient if recipient is not None and "creature" in recipient.types else None


def equipped_planeswalker_cannot_be_attacked(host, reference):
    source = next((card for card in host.state.cards.values() if card.ref == reference), None)
    if source is None or not source.attached_to:
        return False
    for fragment in host._effective_ability_fragments(source):
        if not isinstance(fragment, StaticComponentSpec):
            continue
        program = host.semantics.get(fragment.semantic_key)
        if (program is not None and program.provenance.get("template_id") == EQUIPPED_PLANESWALKER_TEMPLATE
                and host.semantic_program_is_current_trusted(program)):
            return current_equipped_creature(host.state, source, host._public_object_query_result) is not None
    return False
