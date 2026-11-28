from __future__ import annotations

"""Library/hand result capability projection through existing typed validators."""

from collections.abc import Mapping
from types import MappingProxyType
from .fixed_hand_putback import fixed_putback_node_capabilities, fixed_putback_payload_dependencies
from .exile_until_same_name import (
    EXILE_UNTIL_SAME_NAME_CAPABILITY, EXILE_UNTIL_SAME_NAME_MECHANIC,
    EXILE_UNTIL_SAME_NAME_OPERATION, exile_until_same_name_node_capabilities, validate_exile_until_same_name,
)
from .hand_library_redraw import hand_library_redraw_node_capabilities, hand_library_redraw_payload_dependencies
from .library_order_shapes import fixed_library_order_capabilities
from .variable_library_selection_shapes import variable_library_selection_node_capabilities
from .reveal_until_library_selection_shapes import reveal_until_library_selection_node_capabilities
from .library_search_capability_shapes import (
    fixed_library_search_node_capabilities, fixed_type_to_hand_search_node_capabilities,
    partner_with_search_node_capabilities,
)
from .library_selection_capability_shapes import fixed_library_selection_node_capabilities
from .hand_inspection_capability_shapes import fixed_hand_inspection_node_capabilities
from .announced_permanent_reveal import (
    CAPABILITY as ANNOUNCED_PERMANENT_REVEAL_CAPABILITY, MECHANIC as ANNOUNCED_PERMANENT_REVEAL_MECHANIC,
    announced_permanent_reveal_node_capabilities, announced_permanent_reveal_payload_dependencies,
)
from .mana_contribution_draw import (
    MANA_CONTRIBUTION_DRAW_CAPABILITIES,
    MANA_CONTRIBUTION_DRAW_CAPABILITY,
    MANA_CONTRIBUTION_DRAW_MECHANIC,
    MANA_CONTRIBUTION_DRAW_OPERATION,
    mana_contribution_draw_node_capabilities,
    validate_mana_contribution_draw,
)
from .secret_number_choices import (
    CAPABILITY as SECRET_NUMBER_CAPABILITY,
    MECHANIC as SECRET_NUMBER_MECHANIC,
    secret_number_node_capabilities,
    secret_number_payload_dependencies,
)
from ..library_search_model import (
    PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY,
    PRIVATE_SEARCH_RANDOM_DISCARD_MECHANIC,
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,
)
from ..public_library_piles_model import (
    DISTINCT_PUBLIC_SEARCH_SPLIT_CAPABILITY,
    DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION,
    PILE_CAPABILITY,
    PILE_MECHANIC,
    PILE_OPERATION,
    is_public_search_split,
    public_search_split_target_schema,
    validate_public_search_split,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from ..private_opponent_library_cast_model import DAMAGE_LIBRARY_CAST_CAPABILITY, DAMAGE_LIBRARY_CAST_MECHANIC
from .private_opponent_library_cast_shapes import (
    private_opponent_library_cast_node_capabilities, private_opponent_library_cast_payload_dependencies,
)
from .revealed_creature_statistics import REVEALED_CREATURE_CAPABILITY, REVEALED_CREATURE_MECHANIC

# Projection of existing canonical capability identities, not another registry.
LIBRARY_RESULT_MECHANICS = MappingProxyType({
    'library.search.paid_creature_mana_value_bound': 'paid-creature-mana-value-library-search',
    'library.order.fixed_hand_putback':'fixed-draw-hand-putback',
    'library.search.named_library_graveyard': 'named-library-graveyard-search',
    'library.shuffle.target_permanent_then_reveal': 'target-permanent-shuffle-reveal',
    'library.search.qualified_card': 'qualified-library-card-search',
    'library.inspect.fixed_target_player': 'fixed-target-library-inspection',
    'zone.shuffle.whole_graveyard': 'fixed-owner-zone-move',
    'library.search.ordered_destination_allocation': 'ordered-search-destination-allocation',
    PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY: PRIVATE_SEARCH_RANDOM_DISCARD_MECHANIC,
    'library.order.fixed_controller': 'fixed-controller-top-order',
    'library.order.fixed_hand_to_top': 'fixed-hand-to-library-top',
    'zone.hand.library_redraw': 'hand-library-redraw',
    MANA_CONTRIBUTION_DRAW_CAPABILITY: MANA_CONTRIBUTION_DRAW_MECHANIC,
    SECRET_NUMBER_CAPABILITY: SECRET_NUMBER_MECHANIC,
    ANNOUNCED_PERMANENT_REVEAL_CAPABILITY: ANNOUNCED_PERMANENT_REVEAL_MECHANIC,
    'library.order.affected_player_hand_to_top': 'fixed-affected-player-hand-to-top',
    'library.order.graveyard_targets': 'ordered-graveyard-library-targets',
    EXILE_UNTIL_SAME_NAME_CAPABILITY: EXILE_UNTIL_SAME_NAME_MECHANIC,
    'library.select.public_piles': 'public-library-piles',
    'zone.exile.private_opponent_library_cast': 'private-opponent-library-exile-cast',
    DAMAGE_LIBRARY_CAST_CAPABILITY: DAMAGE_LIBRARY_CAST_MECHANIC,
    REVEALED_CREATURE_CAPABILITY: REVEALED_CREATURE_MECHANIC,
})


PUBLIC_SEARCH_SPLIT_CAPABILITIES = (
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, PILE_CAPABILITY, "target.revalidate_resolution",
    "zone.change.destination_replacement",
)


def _public_search_split_dependencies(effects, target_schema):
    if (len(effects) != 1 or not isinstance(effects[0], Mapping) or not is_public_search_split(effects[0])
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or thaw_value(target_schema) != public_search_split_target_schema()):
        return ()
    validate_public_search_split(effects[0])
    if effects[0]["schema_version"] == DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION:
        return (*PUBLIC_SEARCH_SPLIT_CAPABILITIES, DISTINCT_PUBLIC_SEARCH_SPLIT_CAPABILITY)
    return PUBLIC_SEARCH_SPLIT_CAPABILITIES


def public_search_split_runtime_dependencies(program):
    if not any(is_public_search_split(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    deps = _public_search_split_dependencies(program.effects, program.target_schema)
    if (not deps or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Public search split requires its exact resolving spell and one opponent target")
    return deps


def public_library_piles_node_capabilities(*, effects, target_schema, mechanic_ids):
    if {PILE_MECHANIC, FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, "cr-115-targets"} <= set(mechanic_ids):
        try:
            if required := _public_search_split_dependencies(effects, target_schema):
                return required
        except (TypeError, ValueError, KeyError):
            return ()
    if target_schema is not None or len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    if PILE_MECHANIC not in mechanic_ids or effects[0].get("op") != PILE_OPERATION:
        return ()
    try:
        return library_result_payload_dependencies(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()


def is_closed_public_library_piles_program(program):
    if program.event != "resolve" or program.active_zone != "stack":
        return False
    required = public_library_piles_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)


def library_result_node_capabilities(*, effects, target_schema, mechanic_ids):
    required = set()
    for resolver in (
        fixed_putback_node_capabilities,
        hand_library_redraw_node_capabilities,
        mana_contribution_draw_node_capabilities,
        secret_number_node_capabilities,
        announced_permanent_reveal_node_capabilities,
        fixed_library_order_capabilities,
        variable_library_selection_node_capabilities,
        reveal_until_library_selection_node_capabilities,
        exile_until_same_name_node_capabilities,
        public_library_piles_node_capabilities,
        fixed_library_search_node_capabilities,
        fixed_type_to_hand_search_node_capabilities,
        partner_with_search_node_capabilities,
        fixed_library_selection_node_capabilities,
        private_opponent_library_cast_node_capabilities,
        fixed_hand_inspection_node_capabilities,
        target_permanent_shuffle_reveal_node_capabilities,
    ):
        required.update(resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids))
    return tuple(sorted(required))


def library_result_payload_dependencies(value):
    from .private_search_play import (
        PRIVATE_SEARCH_PLAY_DEPENDENCIES, uses_private_search_play, validate_private_search_play,
    )

    if uses_private_search_play(value):
        validate_private_search_play(value)
        return PRIVATE_SEARCH_PLAY_DEPENDENCIES
    from .paid_creature_library_search import (
        CAPABILITY as PAID_CREATURE_SEARCH_CAPABILITY, is_paid_creature_search, validate_paid_creature_search,
    )

    if is_paid_creature_search(value):
        validate_paid_creature_search(value)
        return (PAID_CREATURE_SEARCH_CAPABILITY,)

    from .dragon_graveyard_search_copy import dragon_graveyard_copy_search_payload_dependencies

    if is_public_search_split(value):
        validate_public_search_split(value)
        return PUBLIC_SEARCH_SPLIT_CAPABILITIES
    from .paid_artifact_library_search import (
        PAID_ARTIFACT_SEARCH_CAPABILITY, is_paid_artifact_search, validate_paid_artifact_search_effect,
    )

    if dragon_search := dragon_graveyard_copy_search_payload_dependencies(value):
        return dragon_search
    if is_paid_artifact_search(value):
        validate_paid_artifact_search_effect(value)
        return (PAID_ARTIFACT_SEARCH_CAPABILITY,)
    if value.get('op') == EXILE_UNTIL_SAME_NAME_OPERATION:
        validate_exile_until_same_name(value)
        return (EXILE_UNTIL_SAME_NAME_CAPABILITY,)
    from ..public_library_piles_model import PILE_CAPABILITY, PILE_OPERATION, PILE_SILVER_CAPABILITY, PublicLibraryPileSpec

    if value.get("op") == PILE_OPERATION:
        spec = PublicLibraryPileSpec.from_effect(value)
        return (PILE_CAPABILITY, PILE_SILVER_CAPABILITY) if spec.silver_split else (PILE_CAPABILITY,)
    from ..library_search_model import is_bargain_search_effect

    if is_bargain_search_effect(value):
        from .bargain_search import BARGAIN_SEARCH_CAPABILITIES, bargain_search_limit

        bargain_search_limit(value)
        return BARGAIN_SEARCH_CAPABILITIES
    if value.get('op')=='fixed_hand_putback':
        return fixed_putback_payload_dependencies(value)
    if value.get('op')=='hand_library_redraw':
        return hand_library_redraw_payload_dependencies(value)
    if secret_numbers := secret_number_payload_dependencies(value):
        return secret_numbers
    if reveal := announced_permanent_reveal_payload_dependencies(value):
        return reveal
    if value.get('op') == MANA_CONTRIBUTION_DRAW_OPERATION:
        validate_mana_contribution_draw(value)
        return MANA_CONTRIBUTION_DRAW_CAPABILITIES
    if value.get('op') == 'fixed_library_selection':
        return private_opponent_library_cast_payload_dependencies(value)
    if value.get("op") == "target_permanent_shuffle_reveal":
        from ..target_controller_information import TARGET_OWNER_INFORMATION_CAPABILITY
        from ..target_permanent_shuffle_reveal_model import WARP_CAPABILITY, TargetPermanentShuffleRevealSpec

        TargetPermanentShuffleRevealSpec.from_effect(value)
        return (WARP_CAPABILITY, TARGET_OWNER_INFORMATION_CAPABILITY)
    return ()


def is_closed_mana_contribution_draw_program(program):
    """Admit only the complete validated draw and its direct capabilities."""
    required = mana_contribution_draw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)


def target_permanent_shuffle_reveal_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}


def target_permanent_shuffle_reveal_node_capabilities(*, effects, target_schema, mechanic_ids):
    from collections.abc import Mapping
    from ..replacement.immutable import thaw_value
    from ..target_controller_information import TARGET_OWNER_INFORMATION_MECHANIC
    from ..target_permanent_shuffle_reveal_model import WARP_MECHANIC, WARP_OPERATION

    if len(effects) != 2 or effects[0] != {"op": "shuffle_into_library", "card": "$target.0"}:
        return ()
    reveal = effects[1]
    if not isinstance(reveal, Mapping) or reveal.get("op") != WARP_OPERATION:
        return ()
    mechanics = {WARP_MECHANIC, TARGET_OWNER_INFORMATION_MECHANIC, "cr-115-targets"}
    if not mechanics <= set(mechanic_ids) or not isinstance(target_schema, Mapping):
        return ()
    if type(target_schema.get("count")) is not int or thaw_value(target_schema) != target_permanent_shuffle_reveal_schema():
        return ()
    try:
        return (*library_result_payload_dependencies(reveal), "zone.single_owner_move.fixed_destination",
                "target.revalidate_resolution")
    except (TypeError, ValueError, KeyError):
        return ()


def is_closed_target_permanent_shuffle_reveal_program(program):
    ability_id = getattr(program, "ability_id", None)
    if (
        (program.event, program.active_zone) != ("resolve", "stack")
        or not isinstance(ability_id, str)
        or not ability_id.startswith("spell:")
        or program.cost_schema is not None
        or program.event_condition is not None
        or program.handlers
    ):
        return False
    required = target_permanent_shuffle_reveal_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)
