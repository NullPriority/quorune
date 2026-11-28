from __future__ import annotations

"""Source-pinned precedence for equal activated catalogs, prices and results."""

import hashlib
from ..rules.persistent_public_copy import reviewed_controlled_artifact_copy_matches
from .current_view_equivalence import _catalog_preserves_cost,_same_current_source
from .battlefield_search_equivalence import fixed_battlefield_search_results_equal
from .activation_target_equivalence import reviewed_activation_with_equivalent_target
from .activation_result_equivalence import fixed_activation_results_equal


def _predefined_token_results_equal(current,old):
    from ..compiler.token_templates import fixed_token_creation_effect_template
    from ..rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
    if len(current.effects)!=1 or list(old.effects)!=[{'op':'create_treasure','controller':'$controller'}]:
        return False
    # Use the existing complete predefined definition, rather than a subset of
    # token characteristics or an independently maintained token definition.
    template=fixed_token_creation_effect_template('Create a Treasure token.')
    if template is None or dict(current.effects[0])!=template.effect:return False
    required=fixed_token_creation_node_capabilities(effects=current.effects,
        target_schema=current.target_schema,mechanic_ids=current.coverage)
    return bool(required) and set(required)<=set(current.capability_dependencies)


def _variable_library_selection_results_equal(current, old):
    """Reconcile one exact private/random-bottom legacy choice with its typed view."""
    from collections.abc import Mapping

    from ..characteristic_fragments import (
        CharacteristicQuantityReduction,
        CharacteristicQuantityScope,
        CharacteristicQuantitySpec,
    )
    from ..object_predicate import ObjectQuerySpec
    from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND, PublicQueryAmountSpec
    from ..semantic_choices.defaults import default_semantic_choice_registry
    from ..variable_library_selection_model import (
        VARIABLE_LIBRARY_SELECTION_OPERATION,
        validate_variable_library_selection,
    )

    if len(current.effects) != 1 or len(old.effects) != 1:
        return False
    effect = current.effects[0]
    legacy = old.effects[0]
    if (not isinstance(effect, Mapping) or effect.get("op") != VARIABLE_LIBRARY_SELECTION_OPERATION
            or not isinstance(legacy, Mapping) or set(legacy) != {"op"}):
        return False
    try:
        validate_variable_library_selection(effect)
        amount = effect.get("look_count")
        if not isinstance(amount, Mapping) or amount.get("kind") != PUBLIC_QUERY_AMOUNT_KIND:
            return False
        spec = PublicQueryAmountSpec.from_dict(amount)
        expected = CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",)),
            reduction=CharacteristicQuantityReduction.OBJECT_COUNT,
        )
        if spec.schema_version != 2 or spec.coefficient != 1 or spec.quantity != expected:
            return False
        if (effect.get("player") != "$controller" or effect.get("public_reveal") is not False
                or effect.get("selected_reveal") is not False
                or effect.get("selection_policy") != "fixed_any"
                or effect.get("minimum") != 1 or effect.get("maximum") != 1
                or tuple(effect.get("predicate_groups", ()))
                or effect.get("remainder_destination") != "library_bottom"
                or effect.get("remainder_order") != "random"):
            return False
        handler = default_semantic_choice_registry().handler_for_operation(str(legacy["op"]))
        # This is the complete legacy private-look / mandatory-one / random-bottom
        # mutation contract. Other old choice operations remain untouched.
        if (handler.schema_version != 1
                or handler.handler_id != "choice.library.choose-one-rest-random-bottom.v1"
                or tuple(handler.mutation_path) != (
                    "RevealLibraryCardsIntent", "ChooseOneRestBottomRandomIntent",
                )
                or tuple(handler.private_data) != ("looked library cards",)
                or "_looked_refs" not in handler.continuation_fields):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return (
        {"artifact_count", "private_library_look", "random_bottom_order"}
        .issubset(old.coverage)
    )


def _private_top_order_results_equal(current, old):
    from ..library_order_model import TOP_ORDER_CAPABILITY, FixedTopLibraryOrderSpec

    if len(current.effects) != 1 or len(old.effects) != 1 or TOP_ORDER_CAPABILITY not in current.capability_dependencies:
        return False
    effect, = current.effects
    legacy, = old.effects
    try:
        spec = FixedTopLibraryOrderSpec.from_effect(effect)
    except (TypeError, ValueError):
        return False
    return (effect == spec.effect() and type(legacy.get('count')) is int
            and legacy == {'op': 'look_reorder_top', 'player': '$controller', 'count': spec.count})


def _draw_source_owner_top_results_equal(current, old):
    from ..rules.owner_library_position_shapes import DRAW_SOURCE_OWNER_TOP_CAPABILITY, draw_source_owner_top_effects
    from .reviewed_overlay import _canonical_effects

    if (DRAW_SOURCE_OWNER_TOP_CAPABILITY not in current.capability_dependencies
            or tuple(current.effects) != draw_source_owner_top_effects() or len(old.effects) != 2):
        return False
    draw, move = _canonical_effects(old)
    return (type(draw.get('count')) is int and draw.get('private') is True
            and draw == {'op': 'draw', 'player': '$controller', 'count': 1, 'private': True}
            and move == {'op': 'move', 'card': '$source', 'destination': 'library'})


def _artifact_sacrifice_return_results_equal(current, old):
    from ..replacement.immutable import thaw_value
    from ..rules.artifact_sacrifice_return import (
        artifact_sacrifice_return_payload,
        is_closed_artifact_sacrifice_return_program,
    )

    if not is_closed_artifact_sacrifice_return_program(current) or len(old.effects) != 1:
        return False
    expected = artifact_sacrifice_return_payload()
    del expected["schema_version"]
    reviewed = thaw_value(old.effects[0])
    reason = reviewed.pop("reason", None)
    return (reason is None or isinstance(reason, str)) and reviewed == expected


def _post_mill_channel_matches_reviewed(record, current, old):
    """Match the exact reviewed mill and required return, including its Channel price."""
    from ..compiler.activated_costs import activated_ability_cost
    from ..compiler.public_quantity_activation_costs import compile_ability_local_mana_reduction
    from ..replacement.immutable import thaw_value
    from ..rules.graveyard_return_choices import mandatory_creature_or_planeswalker_return_effect
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    if (
        current.active_zone != "hand" or current.target_schema is not None
        or old.cost_schema is not None or current.cost_schema is None
        or current.provenance.get("template_id") != "fixed-effect-clause-sequence-v1"
        or any(len(view.handlers) != 1 or view.handlers[0].get("handler_id") != "activation.catalog.pinned.v1"
               for view in (current, old))
    ):
        return False
    mill = {"op": "mill", "player": "$controller", "count": 3}
    legacy_choice = {
        "op": "choose_objects", "player": "$controller",
        "prompt": "Choose a creature or planeswalker card in your graveyard to return.",
        "selector": {"zones": ["graveyard"], "categories": ["card"],
                     "types_any": ["creature", "planeswalker"], "owner_relation": "you", "min": 1, "max": 1},
        "then": [{"op": "move_selected", "destination": "hand", "reason": record.name}],
    }
    if tuple(thaw_value(current.effects)) != (mill, mandatory_creature_or_planeswalker_return_effect()):
        return False
    if tuple(thaw_value(old.effects)) != ({**mill, "reason": record.name}, legacy_choice):
        return False
    try:
        legacy, = activated_abilities_from_descriptors(old.handlers)
        typed, = activated_abilities_from_descriptors(current.handlers)
    except (TypeError, ValueError):
        return False
    if not legacy.discard_source or legacy.zones != ("hand",) or legacy.generic_reduction_per_legendary_creature != 1:
        return False
    lines = record.oracle_text.splitlines(keepends=True)
    index = typed.line_index
    if not 0 <= index < len(lines):
        return False
    start = sum(map(len, lines[:index]))
    span = {"start": start, "end": start + len(lines[index].rstrip("\r\n")), "line": index + 1}
    return (
        current.provenance.get("source_span") == span
        and typed.ability_local_mana_reduction is not None
        and typed == compile_ability_local_mana_reduction(legacy, source_name=record.name)
        and thaw_value(current.cost_schema) == activated_ability_cost(typed)
    )


def _results_equal(current,old):
    from ..rules.artifact_graveyard_cast_activation import reviewed_artifact_graveyard_cast_result_matches
    from ..rules.extra_turn_shapes import is_closed_fixed_end_turn_program
    from .reviewed_overlay import _canonical_effects

    if list(old.effects) == [{"op": "end_turn"}] and is_closed_fixed_end_turn_program(current):
        return True
    return (_canonical_effects(current)==_canonical_effects(old)
        or reviewed_controlled_artifact_copy_matches(current, old)
        or _private_top_order_results_equal(current,old)
        or _draw_source_owner_top_results_equal(current,old)
        or fixed_battlefield_search_results_equal(current,old)
        or _variable_library_selection_results_equal(current,old)
        or fixed_activation_results_equal(current,old)
        or _artifact_sacrifice_return_results_equal(current,old)
        or reviewed_artifact_graveyard_cast_result_matches(current,old)
        or _predefined_token_results_equal(current,old))


def shadowed_reviewed_activation_view_keys(record,generated_programs,reviewed_programs,*,capability_registry=None,capability_profile='traditional'):
    if capability_registry is None or record.layout!='normal' or record.faces:return set()
    from .binding import bind_semantic_program_runtime
    from .void_counter_play_overlay import reviewed_void_counter_play_matches
    generated=tuple(generated_programs);reviewed=tuple(reviewed_programs);shadowed=set()
    oracle_hash=hashlib.sha256(record.oracle_text.encode('utf-8')).hexdigest()
    for old in reviewed:
        if old.event!='activate' or not old.ability_id.startswith('ability:'):continue
        matches=[]
        for current in generated:
            if reviewed_void_counter_play_matches(record, current, old):
                if bind_semantic_program_runtime(current, capability_registry=capability_registry,
                                                 profile=capability_profile)["strict"]:
                    matches.append(current)
                continue
            comparison=reviewed_activation_with_equivalent_target(current,old)
            if comparison is None:
                from ..rules.stack_spell_copy_shapes import reviewed_stack_copy_with_current_policy
                comparison=reviewed_stack_copy_with_current_policy(current,old)
            if comparison is None:continue
            if (current.key!=old.key or current.ability_id!=old.ability_id
                    or not _same_current_source(record,current,comparison)
                    or not str(current.provenance.get('authored_by','')).startswith('oracle-ir-v')
                    or current.provenance.get('source_oracle_hash')!=oracle_hash
                    or current.semantic_schema_version!=old.semantic_schema_version
                    or current.event_condition!=old.event_condition or current.trigger_limit!=old.trigger_limit
                    or current.requires_arbiter or current.capability_closure.get('blockers')!=[]
                    or not (
                        _catalog_preserves_cost(current,comparison) and _results_equal(current,comparison)
                        or _post_mill_channel_matches_reviewed(record,current,comparison)
                    )):continue
            if bind_semantic_program_runtime(current,capability_registry=capability_registry,profile=capability_profile)['strict']:
                matches.append(current)
        if len(matches)==1:shadowed.add(old.key)
    return shadowed
