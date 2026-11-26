"""Closed choices for one controlled creature or one of two permanent types."""

from ..affected_permanents import AffectedPermanentSetSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value


COUNTER_KEYWORDS_VERSION = 3
COUNTER_KEYWORDS_CAPABILITY = "counter.choice.controlled_creature_keywords"
COUNTER_KEYWORDS = ("Vigilance", "Trample")
COUNTER_KEYWORDS_MECHANICS = (
    "cr-611-continuous-effects", "vigilance", "trample",
)
COUNTER_KEYWORDS_DEPENDENCIES = (
    COUNTER_KEYWORDS_CAPABILITY,
    "counter.producer.fixed_effect",
    "continuous.resolution.fixed_characteristics_until_end_of_turn",
    "combat.attack.vigilance",
    "combat.damage.assignment.trample",
)

TYPE_DESTRUCTION_FIELD = "chosen_permanent_type_destruction"
TYPE_DESTRUCTION_CAPABILITY = "permanent.destroy.chosen_type_set"
TYPE_DESTRUCTION_TYPES = ("artifact", "enchantment")
TYPE_DESTRUCTION_MECHANICS = ("destroy", "destroy-fixed-set")
TYPE_DESTRUCTION_DEPENDENCIES = (TYPE_DESTRUCTION_CAPABILITY, "permanent.destroy.fixed_set")


def controlled_counter_keywords_choice():
    from ..counter_placement_targets import (
        CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD, controlled_creature_counter_choice_effect,
    )

    base = controlled_creature_counter_choice_effect()
    return {
        **base,
        CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD: COUNTER_KEYWORDS_VERSION,
        "prompt": "Choose a creature you control to get a +1/+1 counter, vigilance, and trample.",
        "then": [*base["then"], *(
            {"op": "grant_keyword_until_end_of_turn", "card": "$selected.0", "keyword": keyword} for keyword in COUNTER_KEYWORDS
        )],
    }


def controlled_counter_keywords_dependencies(value):
    from ..counter_placement_targets import CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD

    data = thaw_value(value)
    if (data != controlled_counter_keywords_choice()
            or any(type(data[key]) is not int for key in (
                "minimum", "maximum", CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD,
            ))
            or type(data["then"][0]["amount"]) is not int):
        raise ValueError("The controlled creature choice requires its exact counter and keyword grants")
    return COUNTER_KEYWORDS_DEPENDENCIES


def controlled_counter_keywords_results(ref):
    return (
        {"op": "place_counters", "card": ref, "counter": "+1/+1", "amount": 1, "source": "$source"},
        *({"op": "grant_keyword_until_end_of_turn", "card": ref, "keyword": keyword}
          for keyword in COUNTER_KEYWORDS),
    )


def permanent_type_destruction_result(kind):
    if kind not in TYPE_DESTRUCTION_TYPES:
        raise ValueError("Choose artifact or enchantment")
    query = ObjectQuerySpec(zones=("battlefield",), types_all=(kind,))
    return {"op": "destroy_all", "source": "$source", "set": AffectedPermanentSetSpec(query=query).to_dict()}


def permanent_type_destruction_choice():
    return {
        "op": "choose_option", "player": "$controller", TYPE_DESTRUCTION_FIELD: 1,
        "prompt": "Choose artifact or enchantment.",
        "options": [{"id": kind, "label": kind.title()} for kind in TYPE_DESTRUCTION_TYPES],
        "then_by_choice": {kind: [permanent_type_destruction_result(kind)] for kind in TYPE_DESTRUCTION_TYPES},
    }


def permanent_type_destruction_payload_dependencies(value):
    if TYPE_DESTRUCTION_FIELD not in value:
        return ()
    if type(value[TYPE_DESTRUCTION_FIELD]) is not int or thaw_value(value) != permanent_type_destruction_choice():
        raise ValueError("The choice requires both fixed permanent types and their exact destruction")
    return TYPE_DESTRUCTION_DEPENDENCIES


def permanent_resolution_choice_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    from ..counter_placement_targets import CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD

    counter = any(
        effect.get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD) == COUNTER_KEYWORDS_VERSION for effect in effects
    )
    destruction = any(TYPE_DESTRUCTION_FIELD in effect for effect in effects)
    if not counter and not destruction:
        return None
    if counter == destruction or len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    effect, = effects
    try:
        if counter and set(COUNTER_KEYWORDS_MECHANICS) <= set(mechanics):
            return controlled_counter_keywords_dependencies(effect)
        if destruction and set(TYPE_DESTRUCTION_MECHANICS) <= set(mechanics):
            return permanent_type_destruction_payload_dependencies(effect)
    except (TypeError, ValueError):
        return ()
    return ()
