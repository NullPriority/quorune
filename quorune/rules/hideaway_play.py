"""Exact permission to play the original Hideaway-linked exile objects."""

from ..continuous_effect_model import ContinuousObjectIdentity
from ..hideaway_model import (
    HIDEAWAY_CAPABILITY, HIDEAWAY_PAIR, HIDEAWAY_PLAY_CAPABILITY, HIDEAWAY_PLAY_MECHANIC,
    HIDEAWAY_PLAY_OPERATION, HIDEAWAY_PLAY_TEMPLATE, HIDEAWAY_REQUIRED_POWER, validate_hideaway_play_effect,
)
from ..linked_ability_exile_state import current_linked_ability_exile
from ..public_characteristic_aggregate_model import CONTROLLED_CREATURES_POWER_QUERY, PublicCharacteristicAggregateSpec
from ..public_characteristic_aggregates import current_public_characteristic_aggregate
from ..zone_object_state import face_down_exile_viewers
from .effect_shape_walkers import nested_effects


HIDEAWAY_PLAY_CAPABILITIES = (
    HIDEAWAY_PLAY_CAPABILITY, HIDEAWAY_CAPABILITY, "zone.exile.linked_ability_pair",
    "quantity_expression.public_characteristic_aggregate", "zone.exile.fixed_top_resolving_play",
)


def hideaway_play_activation_capabilities(*, effects, target_schema, active_zone, cost_schema):
    if (len(effects) != 1 or target_schema is not None or active_zone != "battlefield"
            or cost_schema is None or "".join(str(cost_schema.get("text", "")).split()) != "{G},{T}"):
        return ()
    try:
        validate_hideaway_play_effect(effects[0], player="$controller")
    except (TypeError, ValueError):
        return ()
    return HIDEAWAY_PLAY_CAPABILITIES


def hideaway_play_program_dependencies(program):
    effects = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                    if effect.get("op") == HIDEAWAY_PLAY_OPERATION)
    if not effects and HIDEAWAY_PLAY_MECHANIC not in program.coverage:
        return ()
    required = hideaway_play_activation_capabilities(
        effects=effects, target_schema=program.target_schema, active_zone=program.active_zone,
        cost_schema=program.cost_schema,
    )
    if (not required or program.event != "activate" or program.event_condition is not None
            or tuple(program.effects) != effects
            or program.provenance.get("template_id") != HIDEAWAY_PLAY_TEMPLATE
            or tuple(handler.get("handler_id") for handler in program.handlers) != ("activation.catalog.pinned.v1",)):
        raise ValueError("Hideaway play requires the original untargeted battlefield activation")
    return required


def is_closed_hideaway_play_program(program):
    try:
        required = hideaway_play_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def validate_hideaway_card_pair(abilities):
    from .hideaway_library import hideaway_selection_program_dependencies

    if not any(hideaway_play_program_dependencies(program) for program in abilities):
        return
    if sum(bool(hideaway_selection_program_dependencies(program)) for program in abilities) != 1:
        raise ValueError("The linked play requires the original single Hideaway keyword")


def _resolving_hideaway_source(host, item, actor):
    if (item is None or item.kind != "activated_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        return None
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_hideaway_play_program(program)):
        return None
    return ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))


def authorized_hideaway_exile(host, *, item, actor, card):
    """Authorize only a currently available card in this original printed pair."""
    from ..linked_ability_exile_state import card_has_current_exile_link

    if card.zone != "exile" or not card.is_card_object:
        return False
    source = _resolving_hideaway_source(host, item, actor)
    return bool(
        source is not None and (not card.face_down or actor in face_down_exile_viewers(host.state, card))
        and card_has_current_exile_link(card, source_incarnation=source.logical_object_id, ability_pair=HIDEAWAY_PAIR)
    )


def hideaway_play_options(host, *, item, actor, qualified):
    source = _resolving_hideaway_source(host, item, actor)
    if source is None or actor not in host.active_seats:
        return ()
    aggregate = PublicCharacteristicAggregateSpec("power", "sum", "source_controller", CONTROLLED_CREATURES_POWER_QUERY)
    if not qualified and current_public_characteristic_aggregate(host, actor, aggregate) < HIDEAWAY_REQUIRED_POWER:
        return ()

    from ..discover_casting import _fixed_top_cast_option_pairs
    from ..discover_model import ExileCastProgress
    from .land_play_permissions import resolving_exile_land_play_options
    from .library_selection import LibrarySelectionObjectIdentity

    options = []
    for link in current_linked_ability_exile(host, source=source, ability_pair=HIDEAWAY_PAIR):
        card = host.state.cards[link.object_id]
        if not authorized_hideaway_exile(host, item=item, actor=actor, card=card):
            continue
        identity = LibrarySelectionObjectIdentity(link.object_id, link.logical_object_id, link.ref)
        progress = ExileCastProgress(None, (identity,), identity.ref, card.owner, "free")
        options.extend(public for public, _ in _fixed_top_cast_option_pairs(
            host, actor=actor, card=card, progress=progress, authorized_face_down=True,
        ))
        options.extend(resolving_exile_land_play_options(host, actor, card, hideaway=True))
    return tuple(options)
