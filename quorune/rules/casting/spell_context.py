from __future__ import annotations

"""Pure declared-choice and paid-cost serialization for one real cast spell."""

import copy
from typing import Any
from ...announced_damage import ANNOUNCED_DAMAGE_CONTEXT
from ..action_proposals import thaw_json
from ...evoke import EVOKE_PAYMENT_FIELD
from ...kicker import KICKER_MECHANIC_ID
from ...morph import MORPH_CAST_METHOD
from ...mutate_model import MUTATE_PAYMENT_FIELD
from ...object_query import exact_numeric_characteristic
from ...public_quantity_reductions import public_mana_value
from ...scalar_effect_amount_model import (
    PAID_SACRIFICE_MANA_VALUE_CONTEXT, PAID_SACRIFICE_STAT_CONTEXT, PaidCreatureStatSnapshot,
    PaidSacrificeManaValueSnapshot,
)
from .paid_entry_copy_costs import paid_entry_copy_cast_context
from .multikicker_costs import multikicker_cast_context
from .replicate_costs import replicate_cast_context
from .conspire_costs import conspire_cast_context
from .casualty_costs import casualty_cast_context
from .bargain_costs import declared_bargain_context
from .characteristic_methods import characteristic_stack_context
from .model import CastProposalError
from .spell_text import CAST_TEXT_FRAMES


def fixed_cast_cost_object_context(host, paid) -> dict[str, Any]:
    combat = host.state.combat
    attack_context = copy.deepcopy(combat.attack_target_context.get(paid.object_id)) if combat is not None else None
    return {
        'card_ref': paid.ref,
        'logical_object_id': paid.logical_object_id,
        **({'attack_target_context': attack_context} if attack_context is not None else {}),
    }


def _fixed_cast_lifecycle_cost_context(
    costs,
) -> dict[str, Any]:
    return (
        {
            "fixed_cast_lifecycle_cost_objects": copy.deepcopy(
                costs.paid_contexts
            )
        }
        if costs.paid_contexts
        else {}
    )


def capture_paid_creature_stats(host, creature, previous):
    if previous is not None:
        raise ValueError('The paid stat result requires one sacrificed creature')
    effective = host._effective_card_data(creature)
    return PaidCreatureStatSnapshot(
        creature.logical_object_id,
        exact_numeric_characteristic(creature, effective, 'power'),
        exact_numeric_characteristic(creature, effective, 'toughness'),
    )


def capture_paid_sacrifice_mana_value(logical_id, effective, previous):
    try:
        if previous is not None:
            raise ValueError('The paid mana value requires one sacrificed permanent')
        return PaidSacrificeManaValueSnapshot(logical_id, public_mana_value(effective))
    except ValueError as exc:
        raise CastProposalError(str(exc), reason='paid_sacrifice_mana_value_invalid') from exc


def declared_spell_context(proposal,details,selected_option,costs,*,provenance,lifecycle_context,
        method_spec,used_uncounterable,intrinsic_uncounterable,used_improvise,next_payments=()):
    return {
        **provenance,
        **({ANNOUNCED_DAMAGE_CONTEXT: copy.deepcopy(details[ANNOUNCED_DAMAGE_CONTEXT])}
           if ANNOUNCED_DAMAGE_CONTEXT in details else {}),
        **paid_entry_copy_cast_context(selected_option),
        **multikicker_cast_context(selected_option),
        **replicate_cast_context(selected_option),
        **conspire_cast_context(selected_option),
        **casualty_cast_context(selected_option),
        **declared_bargain_context(selected_option),
        **({CAST_TEXT_FRAMES: copy.deepcopy(selected_option[CAST_TEXT_FRAMES])}
            if CAST_TEXT_FRAMES in selected_option else {}),
        "target_groups": thaw_json(proposal.target_groups),
        "target_snapshots": thaw_json(proposal.target_snapshots),
        "targets_revalidated": False,
        "targets_chosen_at_creation": True,
        "aura_spell": bool(details.get("aura_spell", False)),
        **(
            {
                "aura_enchant_spec": copy.deepcopy(
                    details["aura_enchant_spec"]
                )
            }
            if details.get("aura_enchant_spec") is not None
            else {}
        ),
        "cant_be_countered": (
            used_uncounterable or intrinsic_uncounterable
        ),
        "granted_improvise": used_improvise or "improvise" in next_payments,
        **({"next_spell_payment_grants": list(next_payments)} if next_payments else {}),
        "cost_option": proposal.cost_option_id,
        'kicked': selected_option.get('kind') == KICKER_MECHANIC_ID,
        **({MUTATE_PAYMENT_FIELD:copy.deepcopy(selected_option[MUTATE_PAYMENT_FIELD])}
            if MUTATE_PAYMENT_FIELD in selected_option else {}),
        **characteristic_stack_context(details),
        **lifecycle_context,
        **_fixed_cast_lifecycle_cost_context(costs),
        **({PAID_SACRIFICE_STAT_CONTEXT: costs.paid_creature_stats.to_dict()} if costs.paid_creature_stats is not None else {}),
        **({PAID_SACRIFICE_MANA_VALUE_CONTEXT: costs.paid_sacrifice_mana_value.to_dict()}
           if costs.paid_sacrifice_mana_value is not None else {}),
        **(
            {
                EVOKE_PAYMENT_FIELD: copy.deepcopy(
                    selected_option[EVOKE_PAYMENT_FIELD]
                )
            }
            if selected_option.get(EVOKE_PAYMENT_FIELD) is not None
            else {}
        ),
        **(
            {
                "cast_method": method_spec.method,
                (
                    "morph_spec_fingerprint"
                    if method_spec.method == MORPH_CAST_METHOD
                    else "face_down_method_spec_fingerprint"
                ): method_spec.fingerprint,
            }
            if method_spec is not None
            else {}
        ),
        **(
            {"target_schema_override": details["target_schema_override"]}
            if details.get("target_schema_override") is not None
            else {}
        ),
        **(
            {
                "cast_option_effects": copy.deepcopy(
                    list(selected_option.get("effects", []))
                ),
                "dynamic_effects": copy.deepcopy(
                    list(selected_option.get("effects", []))
                ),
            }
            if "effects" in selected_option
            else {}
        ),
    }
