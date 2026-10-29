from __future__ import annotations

"""Pure declared-choice and paid-cost serialization for one real cast spell."""

import copy
from typing import Any
from ..action_proposals import thaw_json
from ...evoke import EVOKE_PAYMENT_FIELD
from ...kicker import KICKER_MECHANIC_ID
from ...morph import MORPH_CAST_METHOD
from .paid_entry_copy_costs import paid_entry_copy_cast_context
from .multikicker_costs import multikicker_cast_context
from .replicate_costs import replicate_cast_context
from .conspire_costs import conspire_cast_context
from .characteristic_methods import characteristic_stack_context


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


def declared_spell_context(proposal,details,selected_option,costs,*,provenance,lifecycle_context,
        method_spec,used_uncounterable,intrinsic_uncounterable,used_improvise):
    return {
        **provenance,
        **paid_entry_copy_cast_context(selected_option),
        **multikicker_cast_context(selected_option),
        **replicate_cast_context(selected_option),
        **conspire_cast_context(selected_option),
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
        "granted_improvise": used_improvise,
        "cost_option": proposal.cost_option_id,
        'kicked': selected_option.get('kind') == KICKER_MECHANIC_ID,
        **characteristic_stack_context(details),
        **lifecycle_context,
        **_fixed_cast_lifecycle_cost_context(costs),
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
