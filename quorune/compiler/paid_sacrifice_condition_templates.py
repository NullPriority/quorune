from __future__ import annotations

"""Compose a fixed result with a characteristic of the printed sacrifice price."""

import re

from ..paid_sacrifice_conditions import (
    PAID_SACRIFICE_CONDITION_MECHANIC, PAID_SACRIFICE_CONDITION_VERSION,
    PaidSacrificeCharacteristicCondition, paid_sacrifice_trait_field,
)
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC, RESOLUTION_CONDITION_OPERATION
from ..rules.casting_additional_costs import fixed_sacrifice_price_matches_subject


_PAID_TRAIT_RESULT = re.compile(
    r"(?P<prefix>.+?\.) If the sacrificed (?P<subject>artifact|creature|permanent) was "
    r"(?P<article>an? )?(?P<trait>[A-Za-z]+(?:[ '-][A-Za-z]+)*), (?P<result>.+)\.",
    re.IGNORECASE,
)


def paid_sacrifice_condition_template(text, *, cost_schema, compile_fixed):
    match = _PAID_TRAIT_RESULT.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    try:
        condition = PaidSacrificeCharacteristicCondition(match["subject"].casefold(), match["trait"].casefold())
    except ValueError:
        return None
    if (not fixed_sacrifice_price_matches_subject(cost_schema, condition.subject)
            or bool(match["article"]) == (paid_sacrifice_trait_field(condition.trait) == "supertypes")):
        return None
    body = match["result"]
    replacing = body.casefold().endswith(" instead")
    if replacing:
        body = body[:-len(" instead")]
    prefix_id, prefix, schema, prefix_ids = compile_fixed(match["prefix"])
    child_id, children, child_schema, child_ids = compile_fixed(body + ".")
    if prefix_id is None or len(prefix) != 1 or child_id is None or len(children) != 1 or child_schema is not None:
        return None
    if replacing and (schema is not None or prefix[0].get("op") != "draw" or children[0].get("op") != "draw"):
        return None
    wrapper = {
        "op": RESOLUTION_CONDITION_OPERATION, "schema_version": PAID_SACRIFICE_CONDITION_VERSION,
        "player": "$controller", "condition": condition.to_dict(), "cast_fact": condition.reference(),
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
