"""Closed choices for one controlled creature or one of two permanent types."""

from collections.abc import Mapping

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


def token_then_vigilance_counter_dependencies(effects, target_schema, mechanics, cost_schema):
    from ..counter_placement_targets import controlled_creature_counter_choice_dependencies
    from .fixed_effect_clause_shapes import FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities

    if (len(effects) != 2 or target_schema is not None or cost_schema is not None
            or not {"cr-111-tokens", "cr-122-counters", "vigilance",
                    "fixed-effect-clause-sequence"} <= set(mechanics)):
        return ()
    token, choice = map(thaw_value, effects)
    characteristics = token.get("characteristics")
    if (token.get("controller") != "$controller" or type(token.get("quantity")) is not int
            or token["quantity"] != 1 or not isinstance(characteristics, Mapping)
            or set(characteristics) != {"type_line", "literal_token_name_version", "colors", "power", "toughness"}
            or type(characteristics["type_line"]) is not str
            or not characteristics["type_line"].startswith("Token Creature — ")
            or "tapped" in token):
        return ()
    token_caps = fixed_token_creation_node_capabilities(
        effects=(token,), target_schema=None, mechanic_ids={"cr-111-tokens"},
    )
    try:
        counter_caps = controlled_creature_counter_choice_dependencies(choice)
    except (TypeError, ValueError):
        return ()
    if not token_caps or not counter_caps:
        return ()
    return (FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY, *token_caps, *counter_caps)


def uses_token_then_vigilance_choice(effects):
    from ..counter_placement_targets import (
        CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD,
        CONTROLLED_CREATURE_VIGILANCE_COUNTER_CHOICE_VERSION,
    )

    return any(effect.get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD)
               == CONTROLLED_CREATURE_VIGILANCE_COUNTER_CHOICE_VERSION for effect in effects)


def token_then_vigilance_runtime_dependencies(program):
    if not uses_token_then_vigilance_choice(program.effects):
        return ()
    required = permanent_resolution_choice_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanics=program.coverage, cost_schema=program.cost_schema,
    )
    if not required:
        raise ValueError("A vigilance counter choice requires one preceding fixed creature-token instruction")
    return required


def permanent_resolution_choice_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    from ..counter_placement_targets import (
        CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD,
        CONTROLLED_CREATURE_VIGILANCE_COUNTER_CHOICE_VERSION,
    )

    if uses_token_then_vigilance_choice(effects):
        if effects[-1].get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD) != CONTROLLED_CREATURE_VIGILANCE_COUNTER_CHOICE_VERSION:
            return ()
        return token_then_vigilance_counter_dependencies(effects, target_schema, mechanics, cost_schema)
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
