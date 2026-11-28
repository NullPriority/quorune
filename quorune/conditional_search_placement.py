"""Resolve placement once, then reuse the ordinary search movement continuation."""
from dataclasses import replace
from .conditional_search_model import (
    ConditionalSearchSpec, OPTIONAL_CONDITIONAL_PLACEMENT_FIELD, OPTIONAL_CONDITIONAL_SEARCH_VERSION,
)
from .errors import GameRuleError
from .continuous_conditions import FixedPublicStateConditionSpec
from .resolution_conditions import ResolutionConditionBinding


def materialize_conditional_search(host,context,values):
    if context.effect.get("schema_version") == OPTIONAL_CONDITIONAL_SEARCH_VERSION:
        return replace(context, effect=ConditionalSearchSpec.from_effect(context.effect).search.effect())
    if context.effect.get('schema_version')!=4:return context
    spec=ConditionalSearchSpec.from_effect(context.effect);p=spec.placement;matched=False
    if values:
        card=host._resolve_object(context.seat,values[0],zones={'library'},owned_only=True)
        if p['kind']=='public_state':
            matched=host._fixed_public_state_condition_holds(ResolutionConditionBinding(controller=context.seat),
                FixedPublicStateConditionSpec.from_dict(p['condition']),require_available=True)
        else:
            data=host._effective_card_data(card)
            if p['kind']=='mana_value_at_most':
                from .public_quantity_reductions import public_mana_value
                matched=public_mana_value(data)<=p['maximum']
            else:matched=p['card_type'] in host._type_parts(str(data.get('type_line') or ''))[0]
    search=replace(spec.search,destination='battlefield' if matched else 'hand',enters_tapped=p['enters_tapped'] if matched else False)
    return replace(context,effect=search.effect())



def begin_optional_search_placement(host, context, values, *, choice_id):
    """Reveal the found Plains, then offer a reached destination to its searching player."""
    if context.effect.get("schema_version") != OPTIONAL_CONDITIONAL_SEARCH_VERSION or not values:
        return False
    from .zone_transitions import ZoneTransitionOwner

    card = host._resolve_object(context.seat, values[0], zones={"library"}, owned_only=True)
    spec = ConditionalSearchSpec.from_effect(context.effect)
    ZoneTransitionOwner(host).reveal_library_search_results(
        context.seat, (card.object_id,), public=True, announce_public_results=True,
    )
    if not host._fixed_public_state_condition_holds(
        ResolutionConditionBinding(controller=context.seat),
        FixedPublicStateConditionSpec.from_dict(spec.placement["condition"]), require_available=True,
    ):
        return False
    options = [
        {"id": "battlefield", "label": "Put it onto the battlefield tapped"},
        {"id": "hand", "label": "Put it into your hand"},
    ]
    branches = {
        option["id"]: [{
            **context.effect,
            "_search_completion": {
                "actor": context.seat, "choice_id": choice_id, "selected_refs": list(values),
                OPTIONAL_CONDITIONAL_PLACEMENT_FIELD: {
                    "destination": option["id"], "object_id": card.object_id,
                    "logical_object_id": card.logical_object_id,
                },
            },
        }]
        for option in options
    }
    host._begin_semantic_choice(
        item=context.item,
        effect={"op": "choose_option", "player": context.seat,
                "prompt": "Choose where to put the revealed Plains.",
                "options": options, "then_by_choice": branches},
        remaining=context.continuation["remaining"], destination=context.continuation.get("destination"),
        note=str(context.continuation.get("note") or ""),
        instruction_pointer=int(context.frame["instruction_pointer"]),
    )
    return True


def materialize_optional_search_completion(host, effect, completion, selected, seat):
    """Revalidate the originally revealed library incarnation before the move."""
    from collections.abc import Mapping

    if effect.get("schema_version") != OPTIONAL_CONDITIONAL_SEARCH_VERSION:
        raise GameRuleError("The optional placement requires its typed search")
    if (not isinstance(completion, Mapping)
            or set(completion) != {"destination", "object_id", "logical_object_id"}
            or completion.get("destination") not in ("battlefield", "hand")
            or type(completion.get("object_id")) is not str or type(completion.get("logical_object_id")) is not str
            or len(selected) != 1):
        raise GameRuleError("The optional placement result is malformed")
    card = host._resolve_object(seat, selected[0], zones={"library"}, owned_only=True)
    if ((card.object_id, card.logical_object_id) != (completion["object_id"], completion["logical_object_id"])
            or not set(host.seats) <= set(card.revealed_to)):
        raise GameRuleError("The originally revealed search result is no longer available")
    spec = ConditionalSearchSpec.from_effect(effect)
    return replace(spec.search, destination=completion["destination"],
                   enters_tapped=completion["destination"] == "battlefield").effect()
