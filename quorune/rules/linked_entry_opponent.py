"""Read and verify the selected seat for one current linked entry pair."""

from collections.abc import Mapping

from ..entry_designations import (
    BoundEntryOpponentChoice,
    COUNTED_ENTRY_OPPONENT_DEPENDENCIES,
    COUNTED_ENTRY_OPPONENT_HANDLER_ID,
    COUNTED_ENTRY_OPPONENT_TEMPLATE,
    ENTRY_DESIGNATION_CAPABILITY,
    ENTRY_OPPONENT_HANDLER_ID, ENTRY_PLAYER_HANDLER_ID,
    LINKED_ENTRY_PLAYER_TEMPLATE, LINKED_PLAYER_DAMAGE_HANDLER_ID, LINKED_PLAYER_DAMAGE_TEMPLATE,
    LINKED_PLAYER_GRAVEYARD_DEFINITION_TEMPLATE, LINKED_PLAYER_CAST_MILL_PROGRAM_TEMPLATE,
    LINKED_PLAYER_CAST_FIELD, linked_player_cast_condition, linked_player_cast_mill_effect,
    LINKED_PLAYER_DAMAGE_CAPABILITIES, linked_player_damage_descriptor,
    LINKED_PLAYER_PROTECTION_HANDLER_ID, LINKED_PLAYER_PROTECTION_TEMPLATE, LINKED_PLAYER_PROTECTION_CAPABILITIES,
    LINKED_ENTRY_OPPONENT_FIELD,
    LINKED_ENTRY_OPPONENT_TEMPLATE,
    LINKED_OPPONENT_HAND_MODIFIER_TEMPLATE,
    LINKED_OPPONENT_DEFINITION_TEMPLATE,
    LINKED_OPPONENT_UPKEEP_FIELD,
    LINKED_OPPONENT_UPKEEP_TEMPLATE,
    is_linked_opponent_upkeep_condition,
)
from ..replacement.face_status_copy import current_copy_history
from ..replacement.immutable import immutable_fingerprint


def entry_opponent_copy_form(card) -> str:
    return immutable_fingerprint(current_copy_history(card).effective.to_dict())


def _contains_owned_value(value, field, expected):
    if isinstance(value, Mapping):
        return value.get(field) == expected or any(_contains_owned_value(child, field, expected) for child in value.values())
    return isinstance(value, (tuple, list)) and any(_contains_owned_value(child, field, expected) for child in value)


def _linked_opponent_characteristic_dependencies(program, template):
    from ..characteristic_fragments import (
        CharacteristicQuantityScope, QueryCharacteristicModifierSpec, QueryPowerToughnessDefinitionSpec,
    )

    frames = {
        LINKED_OPPONENT_HAND_MODIFIER_TEMPLATE: (
            "battlefield", "query_characteristic_modifier", "ability.static.query-characteristic-modifier.v1",
            CharacteristicQuantityScope.LINKED_OPPONENT_HAND, QueryCharacteristicModifierSpec,
            "continuous.characteristics.query_count_modifier",
        ),
        LINKED_OPPONENT_DEFINITION_TEMPLATE: (
            "all", "query_power_toughness_definition", "ability.static.query-power-toughness-definition.v1",
            CharacteristicQuantityScope.LINKED_OPPONENT_DEFINITION, QueryPowerToughnessDefinitionSpec,
            "continuous.characteristics.query_power_toughness_definition",
        ),
        LINKED_PLAYER_GRAVEYARD_DEFINITION_TEMPLATE: (
            "all", "query_power_toughness_definition", "ability.static.query-power-toughness-definition.v1",
            CharacteristicQuantityScope.LINKED_PLAYER_GRAVEYARD, QueryPowerToughnessDefinitionSpec,
            "continuous.characteristics.query_power_toughness_definition",
        ),
    }
    if type(template) is not str or template not in frames:
        values = (program.handlers, program.effects, program.event_condition, program.cost_schema, program.target_schema)
        if any(_contains_owned_value(values, "scope", frame[3].value) for frame in frames.values()):
            raise ValueError("A linked opponent quantity requires its exact source-characteristic frame")
        return ()
    zone, kind, handler_id, scope, spec_type, dependency = frames[template]
    if ((program.event, program.active_zone) != ("continuous", zone)
            or len(program.handlers) != 1 or program.effects or program.cost_schema is not None
            or program.target_schema is not None or program.event_condition is not None):
        raise ValueError("The linked opponent requires its original continuous source characteristic")
    handler = program.handlers[0]
    fragment = handler.get("fragment", {})
    if (handler.get("handler_id") != handler_id or fragment.get("kind") != kind
            or spec_type.from_dict(fragment.get("value")).quantity.scope is not scope):
        raise ValueError("The linked opponent characteristic requires its exact typed quantity")
    return (ENTRY_DESIGNATION_CAPABILITY, dependency)

def linked_entry_opponent_runtime_dependencies(program) -> tuple[str, ...]:
    template = program.provenance.get("template_id")
    if required := _linked_opponent_characteristic_dependencies(program, template):
        return required
    entry_templates = {
        ENTRY_OPPONENT_HANDLER_ID: LINKED_ENTRY_OPPONENT_TEMPLATE,
        ENTRY_PLAYER_HANDLER_ID: LINKED_ENTRY_PLAYER_TEMPLATE,
        COUNTED_ENTRY_OPPONENT_HANDLER_ID: COUNTED_ENTRY_OPPONENT_TEMPLATE,
    }
    owned = [item["handler_id"] for item in program.handlers if item.get("handler_id") in entry_templates]
    if owned or template in entry_templates.values():
        if (len(owned) != 1 or entry_templates[owned[0]] != template or len(program.handlers) != 1
                or (program.event, program.active_zone) != ("zone.change", "all")
                or program.target_schema is not None or program.cost_schema is not None or program.effects):
            raise ValueError("An entry opponent choice requires its exact replacement program")
        if template == COUNTED_ENTRY_OPPONENT_TEMPLATE:
            return COUNTED_ENTRY_OPPONENT_DEPENDENCIES
        return (ENTRY_DESIGNATION_CAPABILITY,)
    if template == LINKED_PLAYER_DAMAGE_TEMPLATE or any(
        handler.get("handler_id") == LINKED_PLAYER_DAMAGE_HANDLER_ID for handler in program.handlers
    ):
        from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE

        if (
            template != LINKED_PLAYER_DAMAGE_TEMPLATE or (program.event, program.active_zone) != ("damage", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.event_condition is not None
            or program.effects or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or len(program.handlers) != 1 or dict(program.handlers[0]) != linked_player_damage_descriptor()
        ):
            raise ValueError("Linked player damage requires its exact current battlefield component")
        return LINKED_PLAYER_DAMAGE_CAPABILITIES
    if template == LINKED_PLAYER_PROTECTION_TEMPLATE or any(
        handler.get("handler_id") == LINKED_PLAYER_PROTECTION_HANDLER_ID for handler in program.handlers
    ):
        return _linked_player_protection_dependencies(program)
    if (template == LINKED_PLAYER_CAST_MILL_PROGRAM_TEMPLATE
            or _contains_owned_value(program.event_condition, "field", LINKED_PLAYER_CAST_FIELD)):
        from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
        from ..compiler.mill_templates import EVENT_MILL_RECIPIENT_CAPABILITY, MILL_CAPABILITY_ID

        if (
            template != LINKED_PLAYER_CAST_MILL_PROGRAM_TEMPLATE or (program.event, program.active_zone) != ("spell.cast", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.event_condition != linked_player_cast_condition() or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or len(program.effects) != 1 or dict(program.effects[0]) != linked_player_cast_mill_effect()
        ):
            raise ValueError("Linked player mill requires the original current chosen caster and one card")
        return (ENTRY_DESIGNATION_CAPABILITY, 'trigger.event.normalized_spell_cast',
                MILL_CAPABILITY_ID, EVENT_MILL_RECIPIENT_CAPABILITY)
    if (_contains_owned_value(program.event_condition, "field", LINKED_OPPONENT_UPKEEP_FIELD)
            or template == LINKED_OPPONENT_UPKEEP_TEMPLATE):
        from .upkeep_hand_differences import upkeep_hand_difference_program_dependencies

        if (template != LINKED_OPPONENT_UPKEEP_TEMPLATE
                or not is_linked_opponent_upkeep_condition(program.event_condition)
                or not upkeep_hand_difference_program_dependencies(program)):
            raise ValueError("Linked upkeep requires its exact selected-seat hand-damage program")
        return (ENTRY_DESIGNATION_CAPABILITY,)
    return ()


def _linked_player_protection_dependencies(program) -> tuple[str, ...]:
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..semantic_runtime.chosen_characteristics import linked_player_protection_descriptor

    if (
        program.provenance.get("template_id") != LINKED_PLAYER_PROTECTION_TEMPLATE
        or (program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.event_condition is not None
        or program.effects or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or len(program.handlers) != 1 or dict(program.handlers[0]) != linked_player_protection_descriptor()
    ):
        raise ValueError("Linked player protection requires its exact current battlefield component")
    return LINKED_PLAYER_PROTECTION_CAPABILITIES


def current_linked_entry_opponent(host, card) -> str | None:
    return _current_linked_entry_seat(host, card, any_player=False, template=LINKED_ENTRY_OPPONENT_TEMPLATE)


def current_linked_entry_player(host, card) -> str | None:
    return _current_linked_entry_seat(host, card, any_player=True, template=LINKED_ENTRY_PLAYER_TEMPLATE)


def bound_linked_entry_player(semantics, program_is_trusted, card) -> str | None:
    """The retained selection also identifies players who have since left the game."""
    return _bound_linked_entry_seat(semantics, program_is_trusted, card,
                                    any_player=True, template=LINKED_ENTRY_PLAYER_TEMPLATE)


def current_linked_entry_player_graveyard_count(host, card) -> int:
    seat = current_linked_entry_player(host, card)
    if seat is None:
        return 0
    return sum(host.state.cards[object_id].is_card_object
               for object_id in host.state.players[seat].zones["graveyard"])


def _current_linked_entry_seat(host, card, *, any_player, template) -> str | None:
    seat = _bound_linked_entry_seat(
        host.semantics, host.semantic_program_is_current_trusted, card, any_player=any_player, template=template,
    )
    player = host.state.players.get(seat)
    return seat if player is not None and player.in_game else None


def _bound_linked_entry_seat(semantics, program_is_trusted, card, *, any_player, template) -> str | None:
    raw = card.annotations.get(LINKED_ENTRY_OPPONENT_FIELD)
    if raw is None:
        return None
    binding = BoundEntryOpponentChoice.from_dict(raw)
    if binding.choice.any_player != any_player or not binding.matches(card, copy_form=entry_opponent_copy_form(card)):
        return None
    provider = semantics.get(binding.choice.entry_program_key)
    if (provider is None or provider.provenance.get("template_id") != template
            or not program_is_trusted(provider)):
        return None
    return binding.choice.seat
