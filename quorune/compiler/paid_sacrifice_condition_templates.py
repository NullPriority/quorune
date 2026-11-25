from __future__ import annotations

"""Compose a fixed result with a characteristic of the printed sacrifice price."""

from . import regex_working_set as re

from ..paid_sacrifice_conditions import (
    PAID_SACRIFICE_CONDITION_MECHANIC, PAID_SACRIFICE_CONDITION_VERSION,
    PAID_SUSPECTED_CONDITION_KIND, PaidSacrificeCharacteristicCondition,
    activation_paid_legendary_amass_result_is_closed, activation_paid_suspected_draw_is_closed,
    activation_paid_subtype_result_is_closed, activation_paid_thrull_counters_result_is_closed,
    paid_sacrifice_trait_field,
)
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC, RESOLUTION_CONDITION_OPERATION
from ..rules.casting_additional_costs import fixed_sacrifice_price_matches_subject
from ..rules.activation.paid_characteristic_price import activation_paid_creature_choice, activation_paid_suspected_choice


_PAID_TRAIT_RESULT = re.compile(
    r"(?P<prefix>.+?\.) If the sacrificed (?P<subject>artifact|creature|permanent) was "
    r"(?P<article>an? )?(?P<trait>[A-Za-z]+(?:[ '-][A-Za-z]+)*), (?P<result>.+)\.",
    re.IGNORECASE,
)


def paid_sacrifice_condition_template(text, *, cost_schema, compile_fixed, activation=False, source_name=None):
    match = _PAID_TRAIT_RESULT.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    trait = match["trait"].casefold()
    suspected = activation and trait == "suspected"
    try:
        condition = PaidSacrificeCharacteristicCondition(
            match["subject"].casefold(), trait,
            PAID_SUSPECTED_CONDITION_KIND if suspected else "paid_sacrifice_characteristic",
        )
    except ValueError:
        return None
    legendary = condition.trait == "legendary"
    if suspected:
        valid_price = activation_paid_suspected_choice(cost_schema) is not None
        article_mismatch = bool(match["article"])
    else:
        valid_price = (activation_paid_creature_choice(cost_schema, legendary=legendary) is not None if activation
                       else fixed_sacrifice_price_matches_subject(cost_schema, condition.subject))
        article_mismatch = bool(match["article"]) == (paid_sacrifice_trait_field(condition.trait) == "supertypes")
    if not valid_price or article_mismatch:
        return None
    body = match["result"]
    replacing = body.casefold().endswith(" instead")
    if replacing:
        body = body[:-len(" instead")]
    compiled_prefix = compile_fixed(match["prefix"])
    if compiled_prefix[0] is None and activation and condition.trait == "thrull" and source_name:
        from .counter_removal_templates import fixed_source_counter_removal_effect_template

        compiled_prefix = fixed_source_counter_removal_effect_template(
            match["prefix"], card_name=source_name,
        ) or compiled_prefix
    prefix_id, prefix, schema, prefix_ids = compiled_prefix
    child_id, children, child_schema, child_ids = compile_fixed(body + ".")
    if prefix_id is None or len(prefix) != 1 or child_id is None or len(children) != 1 or child_schema is not None:
        return None
    reference = condition.reference(activation=activation)
    if suspected:
        if not replacing or not activation_paid_suspected_draw_is_closed(condition, reference, (), children, prefix, schema):
            return None
    elif activation:
        if replacing:
            return None
        if legendary:
            if not activation_paid_legendary_amass_result_is_closed(condition, reference, prefix, children, schema):
                return None
        elif schema is not None or not (
            activation_paid_subtype_result_is_closed(condition, reference, prefix, children)
            or cost_schema.get("activation_limit") == "once_per_turn"
            and activation_paid_thrull_counters_result_is_closed(condition, reference, prefix, children)
        ):
            return None
    if replacing and (schema is not None or prefix[0].get("op") != "draw" or children[0].get("op") != "draw"):
        return None
    wrapper = {
        "op": RESOLUTION_CONDITION_OPERATION, "schema_version": PAID_SACRIFICE_CONDITION_VERSION,
        "player": "$controller", "condition": condition.to_dict(), "cast_fact": reference,
        "effects": list(children), "mechanic_ids": list(child_ids),
        "prefix_mechanic_ids": [] if replacing else list(prefix_ids),
    }
    if replacing:
        wrapper.update(otherwise_effects=list(prefix), otherwise_mechanic_ids=list(prefix_ids))
    return (
        "fixed-paid-sacrifice-characteristic-v1",
        (wrapper,) if replacing else (*prefix, wrapper),
        schema,
        tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, PAID_SACRIFICE_CONDITION_MECHANIC, *prefix_ids, *child_ids))),
    )
