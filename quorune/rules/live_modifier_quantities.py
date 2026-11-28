from __future__ import annotations

"""Read closed current player or attached-object values for layer-safe modifiers."""

import math
from collections.abc import Mapping
from ..characteristic_fragments import DEFINING_LIFE_SCOPES, CharacteristicQuantityScope, CharacteristicQuantityReduction
from ..continuous_effects import Layer


def current_player_life_total(host, seat):
    player = host.state.players.get(seat)
    if player is None or type(player.life) is not int:
        raise ValueError('Current life total is unavailable')
    return max(0, player.life)


def _is_transformed_permanent(host, card):
    if card.phased_out or card.face_down or card.permanent_composition is not None:
        return False
    if card.is_token:
        from ..defined_token_faces import defined_token_definition

        definition = defined_token_definition(card)
        return definition is not None and definition.current_face(card.active_face) == definition.back
    record = host.card_db.by_oracle_id(card.oracle_id)
    if record is None or record.layout not in {"transform", "battle"}:
        return False
    if len(record.faces) != 2:
        raise ValueError("A physical double-faced permanent requires exactly two faces")
    front, back = (face.get("name") for face in record.faces)
    if type(front) is not str or not front or type(back) is not str or not back or front == back:
        raise ValueError("A physical double-faced permanent requires distinct recorded faces")
    return card.active_face == back


def controlled_transformed_permanent_count(host, controller):
    player = host.state.players.get(controller)
    if player is None:
        raise ValueError("The current transformed-permanent controller is unavailable")
    cards = tuple(host.state.cards[object_id] for object_id in player.zones["battlefield"])
    if any(card.zone != "battlefield" or card.controller != controller for card in cards):
        raise ValueError("Transformed-permanent count has contradictory battlefield membership")
    return sum(_is_transformed_permanent(host, card) for card in cards)


def current_half_starting_life_count(state, controller, scope):
    starting_life = state.config.starting_life
    if type(starting_life) is not int or starting_life <= 0:
        raise ValueError("The configured starting life total is unavailable")
    if controller not in state.players or any(type(player.in_game) is not bool for player in state.players.values()):
        raise ValueError("A half-starting-life query requires the current active roster")
    opponents_only = scope is CharacteristicQuantityScope.OPPONENTS_BELOW_HALF_STARTING_LIFE
    if not opponents_only and scope is not CharacteristicQuantityScope.PLAYERS_AT_OR_BELOW_HALF_STARTING_LIFE:
        raise ValueError("A half-starting-life comparison requires a typed player scope")
    lives = tuple(player.life for seat, player in state.players.items()
                  if player.in_game and (not opponents_only or seat != controller))
    if any(type(life) is not int for life in lives):
        raise ValueError("A current public life total is malformed")
    if opponents_only:
        return sum(2 * life < starting_life for life in lives)
    return sum(2 * life <= starting_life for life in lives)


def _defining_life_quantity(host, source, scope):
    player_id = source.controller if source.zone in {"battlefield", "stack"} else source.owner
    if scope is CharacteristicQuantityScope.DEFINING_LIFE_TOTAL:
        player = host.state.players.get(player_id)
        if player is None:
            return 0
        if type(player.life) is not int:
            raise ValueError("A defining life total requires a signed integer")
        return player.life
    lives = [player.life for seat, player in host.state.players.items() if seat != player_id and player.in_game]
    if any(type(life) is not int for life in lives):
        raise ValueError("Defining opponent life totals require signed integers")
    return (max(lives, default=0) + 1) // 2


def opponent_chosen_color_permanent_count(host, source, *, enforce_applicability):
    from .chosen_color_target_shapes import current_linked_entry_color

    if source.zone != "battlefield" or source.face_down or source.phased_out:
        return 0
    color = current_linked_entry_color(source)
    if color is None:
        return 0
    count = 0
    for seat, player in host.state.players.items():
        if seat == source.controller or not player.in_game:
            continue
        for object_id in player.zones["battlefield"]:
            permanent = host.state.cards[object_id]
            if permanent.phased_out:
                continue
            data = host._effective_card_data(
                permanent, maximum_layer=Layer.COLOR,
                _enforce_static_component_applicability=enforce_applicability,
            )
            if color in data["colors"]:
                count += 1
    return count


def validate_chosen_color_definition_program(program, required):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..characteristic_fragments import (
        CHOSEN_COLOR_DEFINITION_CAPABILITY, CHOSEN_COLOR_DEFINITION_TEMPLATE,
        QueryPowerToughnessDefinitionSpec,
    )

    uses_quantity = CHOSEN_COLOR_DEFINITION_CAPABILITY in required
    is_definition = program.provenance.get("template_id") == CHOSEN_COLOR_DEFINITION_TEMPLATE
    if not uses_quantity and not is_definition:
        return
    if (
        not uses_quantity or not is_definition or (program.event, program.active_zone) != ("continuous", "all")
        or len(program.handlers) != 1 or program.effects or program.cost_schema is not None
        or program.target_schema is not None or program.event_condition is not None
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("The chosen-color count requires its continuous characteristic definition")
    handler = program.handlers[0]
    fragment = handler.get("fragment", {})
    if (
        handler.get("handler_id") != "ability.static.query-power-toughness-definition.v1"
        or not isinstance(fragment, Mapping) or fragment.get("kind") != "query_power_toughness_definition"
        or QueryPowerToughnessDefinitionSpec.from_dict(fragment.get("value")).quantity.scope
            is not CharacteristicQuantityScope.OPPONENT_CHOSEN_COLOR_PERMANENTS
    ):
        raise ValueError("The chosen-color count requires its exact statistic descriptor")


def is_controller_turn_life_loss_modifier(descriptor):
    if descriptor.get("handler_id") != "ability.static.query-characteristic-modifier.v1":
        return False
    fragment = descriptor.get("fragment", {})
    value = fragment.get("value", {}) if isinstance(fragment, Mapping) else {}
    quantity = value.get("quantity", {}) if isinstance(value, Mapping) else {}
    return (isinstance(quantity, Mapping)
            and quantity.get("scope") == CharacteristicQuantityScope.CONTROLLER_TURN_LIFE_LOSS.value)


def controller_turn_life_loss_modifier_runtime_dependencies(program):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..characteristic_fragments import CONTROLLER_TURN_LIFE_LOSS_CAPABILITIES, CONTROLLER_TURN_LIFE_LOSS_MODIFIER_TEMPLATE

    expected = tuple(handler for handler in program.handlers if is_controller_turn_life_loss_modifier(handler))
    marked = CONTROLLER_TURN_LIFE_LOSS_MODIFIER_TEMPLATE in str(program.provenance.get("template_id") or "").split("+")
    if not expected and not marked:
        return ()
    if (
        (program.event, program.active_zone) != ("continuous", "battlefield")
        or len(expected) != 1 or len(program.handlers) != 1
        or program.effects or program.target_schema is not None or program.cost_schema is not None
        or program.event_condition is not None or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Controller turn life loss needs its complete current battlefield modifier")
    return ("continuous.characteristics.query_count_modifier", *CONTROLLER_TURN_LIFE_LOSS_CAPABILITIES)


def controller_turn_life_loss(state, controller):
    from ..turn_history import current_turn_history_events

    history = state.turn_history
    if history is None or type(history.schema_version) is not int or history.schema_version != 1 or history.turn_sequence != state.turn_sequence:
        raise ValueError("Controller turn life loss requires the current turn history")
    losses = tuple(event.amount for event in current_turn_history_events(
        history, turn_sequence=state.turn_sequence, kind="player_lost_life",
    ) if event.target == controller)
    if any(type(amount) is not int or amount < 0 for amount in losses):
        raise ValueError("Controller turn life loss requires exact nonnegative occurrences")
    return sum(losses)


def current_modifier_quantity(host,source,quantity,*,enforce_applicability=True):
    if quantity.scope is CharacteristicQuantityScope.SOURCE_LINKED_EXILE:
        from ..conditional_exile_model import current_source_linked_exile_count

        return current_source_linked_exile_count(source, host.state.cards)
    if quantity.scope in DEFINING_LIFE_SCOPES:
        return _defining_life_quantity(host, source, quantity.scope)
    if quantity.scope is CharacteristicQuantityScope.LINKED_PLAYER_GRAVEYARD:
        from .linked_entry_opponent import current_linked_entry_player_graveyard_count

        return current_linked_entry_player_graveyard_count(host, source)
    if quantity.scope is CharacteristicQuantityScope.OPPONENT_CHOSEN_COLOR_PERMANENTS:
        return opponent_chosen_color_permanent_count(host, source, enforce_applicability=enforce_applicability)
    controller=host.state.players.get(source.controller)
    if controller is None:
        raise ValueError('Current quantity source controller is unavailable')
    if quantity.scope is CharacteristicQuantityScope.CONTROLLER_TRANSFORMED_PERMANENTS:
        return controlled_transformed_permanent_count(host, source.controller)
    if quantity.scope in {CharacteristicQuantityScope.OPPONENTS_BELOW_HALF_STARTING_LIFE,
                          CharacteristicQuantityScope.PLAYERS_AT_OR_BELOW_HALF_STARTING_LIFE}:
        return current_half_starting_life_count(host.state, source.controller, quantity.scope)
    if quantity.scope is CharacteristicQuantityScope.CONTROLLER_LIFE_TOTAL:
        return current_player_life_total(host,source.controller)
    if quantity.scope is CharacteristicQuantityScope.CONTROLLER_TURN_LIFE_LOSS:
        return controller_turn_life_loss(host.state, source.controller)
    if quantity.scope is CharacteristicQuantityScope.OPPONENT_POISON_COUNTERS:
        values=[]
        for seat,player in host.state.players.items():
            if type(player.in_game) is not bool:
                raise ValueError('Current opponent roster is unavailable')
            if seat!=source.controller and player.in_game:
                if type(player.poison) is not int or player.poison<0:
                    raise ValueError('Current opponent poison count is unavailable')
                values.append(player.poison)
        return sum(values)
    if quantity.scope is not CharacteristicQuantityScope.ATTACHED_OBJECT:
        raise ValueError('Current modifier quantity requires a represented reference')
    if source.attached_to is None:
        return 0
    from ..attachments import attached_object_identity
    identity=attached_object_identity(host.state.cards,source)
    if identity is None:
        raise ValueError('Current attached-object identity is unavailable')
    recipient=host.state.cards[identity.object_id]
    if quantity.reduction is CharacteristicQuantityReduction.TOTAL_COUNTERS:
        from ..counter_snapshot import permanent_counter_total

        return permanent_counter_total(recipient.counters)
    data=host._effective_card_data(recipient,maximum_layer=Layer.COLOR,
        _enforce_static_component_applicability=enforce_applicability)
    if quantity.reduction is CharacteristicQuantityReduction.DISTINCT_COLORS:
        colors=data.get('colors')
        if not isinstance(colors,(list,tuple)) or any(type(c) is not str or c not in {'W','U','B','R','G'} for c in colors):
            raise ValueError('Current attached-object colors are unavailable')
        return len(set(colors))
    value=data.get('mana_value')
    if type(value) not in {int,float} or not math.isfinite(value) or value<0 or int(value)!=value:
        raise ValueError('Current attached-object mana value is unavailable')
    return int(value)
