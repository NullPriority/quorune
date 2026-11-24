"""Commit one bottom-card move before evaluating its public creature result."""

from dataclasses import dataclass
from collections.abc import Mapping

from ..errors import GameRuleError
from ..library_bottom_result_model import (
    LIBRARY_BOTTOM_RESULT_CAPABILITY, LIBRARY_BOTTOM_RESULT_MECHANIC, LIBRARY_BOTTOM_RESULT_OPERATION,
    LibraryBottomResultSpec,
)
from ..object_query import exact_numeric_characteristic
from ..replacement.immutable import FrozenMap, thaw_value
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY
from ..util import stable_json
from .committed_quantity_followups import CommittedQuantityFollowup, fixed_followup_capabilities


@dataclass(frozen=True, slots=True)
class LibraryBottomResultIntent:
    actor: str
    library_player: str
    stack_ref: str
    reason: str
    spec: LibraryBottomResultSpec
    replacement_selections: tuple[str | FrozenMap, ...] = ()


def library_bottom_result_capabilities(spec):
    dependencies = {LIBRARY_BOTTOM_RESULT_CAPABILITY, "zone.change.destination_replacement"}
    if spec.targets_player:
        token = spec.token
        token_deps = fixed_followup_capabilities(token)
        characteristics = token.get("characteristics")
        if (not token_deps or not isinstance(characteristics, Mapping)
                or not characteristics.get("type_line", "").startswith("Token Creature ")
                or "tapped" in token or "ability_fragments" in characteristics):
            raise ValueError("Library-bottom result requires a single ordinary creature token")
        dependencies.update((*token_deps, "target.revalidate_resolution"))
    else:
        dependencies.add(SCALAR_AMOUNT_CAPABILITY)
    return tuple(sorted(dependencies))


def _compiled_dependencies(effect, target_schema):
    spec = LibraryBottomResultSpec.from_effect(effect)
    if stable_json(thaw_value(target_schema)) != stable_json(spec.target_schema()):
        raise ValueError("Library-bottom result changed its original target roles")
    return library_bottom_result_capabilities(spec)


def library_bottom_result_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or LIBRARY_BOTTOM_RESULT_MECHANIC not in mechanic_ids:
        return ()
    try:
        return _compiled_dependencies(effects[0], target_schema)
    except (TypeError, ValueError):
        return ()


def library_bottom_result_runtime_dependencies(program):
    effects = tuple(effect for effect in program.effects if effect.get("op") == LIBRARY_BOTTOM_RESULT_OPERATION)
    if not effects:
        return ()
    if (len(program.effects) != 1 or program.event != "activate" or program.active_zone != "battlefield"
            or program.event_condition is not None):
        raise ValueError("Library-bottom movement requires its one closed battlefield activation")
    return _compiled_dependencies(effects[0], program.target_schema)


def is_closed_library_bottom_result_program(program):
    try:
        required = library_bottom_result_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _bottom_library_card(host, player):
    if player not in host.active_seats:
        return None
    library = host.state.players[player].zones["library"]
    if not library:
        return None
    card = host.state.cards[library[0]]
    if card.owner != player or card.zone != "library":
        raise GameRuleError("Bottom-card movement lost its original library")
    return card


def library_bottom_input_snapshot(host, effect, *, actor):
    """Retain the input incarnation in the server-only replacement frame."""
    if effect.get("op") != LIBRARY_BOTTOM_RESULT_OPERATION:
        return None
    retained = {key: value for key, value in effect.items() if key != "_replacement_selections"}
    LibraryBottomResultSpec.from_effect(retained, actor=actor)
    card = _bottom_library_card(host, effect["player"])
    if card is None:
        raise GameRuleError("Library-bottom replacement requires its current input card")
    return {"library_player": effect["player"], "object_id": card.object_id, "logical_object_id": card.logical_object_id}


def commit_library_bottom_creature_result(host, intent):
    """Keep the selected library position and first committed object private until revealed by the move."""
    from ..scalar_effect_amounts import library_bottom_source_power
    from ..zone_transition_model import PUBLIC_ZONES

    item = next((frame for frame in host.state.stack if frame.ref == intent.stack_ref), None)
    if item is None or item.kind != "activated_ability" or item.controller != intent.actor:
        raise GameRuleError("Library-bottom result lost its resolving activated ability")
    card = _bottom_library_card(host, intent.library_player)
    if card is None:
        return CommittedQuantityFollowup(())
    original = card.logical_object_id
    moved = host.move_card(
        card.object_id, "graveyard", reason=intent.reason, log=True, semantic_events=True,
        replacement_selections=intent.replacement_selections,
    )
    if moved.logical_object_id == original or moved.zone not in PUBLIC_ZONES or moved.face_down:
        return CommittedQuantityFollowup(())
    effective = host._effective_card_data(moved)
    types, _, _ = host._type_parts(effective["type_line"])
    if "creature" not in types:
        return CommittedQuantityFollowup(())
    if intent.spec.targets_player:
        return CommittedQuantityFollowup((intent.spec.token,))
    power = exact_numeric_characteristic(moved, effective, "power")
    if power is None:
        power = 0
    if power > library_bottom_source_power(host, item) or moved.zone == "battlefield":
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((FrozenMap({
        "op": "move_if_in_zone", "card": moved.ref, "from": moved.zone,
        "expected_object_identity": moved.logical_object_id, "destination": "battlefield",
        "controller": intent.actor,
    }),))
