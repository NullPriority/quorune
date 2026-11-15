"""Read one current-target self-replacement before ordinary result composition."""

from copy import deepcopy
import re

from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..target_replacement_model import (
    CURRENT_TARGET_REPLACEMENT_MECHANIC, CURRENT_TARGET_REPLACEMENT_VERSION,
    TargetReplacementCondition,
)


def current_target_replacement_condition(text):
    text = " ".join(text.casefold().replace("’", "'").split())
    text = re.sub(r"^that creature is\b|^it is\b", "it's", text)
    if text == "it's night":
        return TargetReplacementCondition("night")
    queries = ()
    subtype = re.fullmatch(r"it's an? (.+)", text)
    canonical = canonical_creature_subtype(subtype[1]) if subtype is not None else None
    if canonical is not None:
        queries = (ObjectQuerySpec(zones=("battlefield",), subtypes_any=(canonical,)),)
    elif text == "it's blocking":
        queries = (ObjectQuerySpec(zones=("battlefield",),
                   state_predicate=PermanentStatePredicateSpec(blocking=True)),)
    elif text == "it's an enchantment creature or legendary creature":
        queries = (ObjectQuerySpec(zones=("battlefield",), types_all=("enchantment", "creature")),
                   ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), supertypes_all=("legendary",)))
    elif text in {"it's white", "it's blue", "it's black", "it's red", "it's green"}:
        color = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}[text[5:]]
        queries = (ObjectQuerySpec(zones=("battlefield",), colors_any=(color,)),)
    return TargetReplacementCondition("target", queries) if queries else None


def _replacement_results(text, compile_component):
    if re.search(r"\b(?:target|may|instead|if|this way|that much)\b", text, re.I):
        return None
    stats = re.fullmatch(r"(?:it|that creature) (?P<result>gets .+)\.", text, re.I)
    if stats is not None:
        return compile_component("Target creature " + stats["result"] + ".")
    counters = re.fullmatch(r"(?P<counters>Put (?:a|one|two|three|[1-9][0-9]*) \+1/\+1 counters? on it)(?: and it (?P<stats>gets .+))?\.", text, re.I)
    if counters is None:
        return None
    counter_text = re.sub(r"on it$", "on target creature", counters["counters"], flags=re.I) + "."
    counter = compile_component(counter_text)
    if counters["stats"] is None:
        return counter
    characteristics = compile_component("Target creature " + counters["stats"] + ".")
    if counter[0] is None or characteristics[0] is None or counter[2] != characteristics[2]:
        return None
    return ("fixed-counter-then-target-characteristics", (*counter[1], *characteristics[1]), counter[2],
            tuple(dict.fromkeys((*counter[3], *characteristics[3]))))


def current_target_replacement_template(text, *, compile_component):
    match = re.fullmatch(r"(?P<base>Target creature gets [+-][0-9]+/[+-][0-9]+ until end of turn\.) If (?P<condition>.+?), (?P<result>.+)\.", text, re.I)
    if match is None:
        return None
    predicate = current_target_replacement_condition(match["condition"])
    result = match["result"]
    if result.casefold().startswith("instead "):
        result = result[len("instead "):]
    elif result.casefold().endswith(" instead"):
        result = result[:-len(" instead")]
    else:
        return None
    if predicate is None:
        return None
    normal = compile_component(match["base"])
    alternate = _replacement_results(result + ".", compile_component)
    if normal[0] is None or alternate is None or alternate[0] is None or normal[2] != alternate[2]:
        return None
    wrapper = {"op": "apply_if_public_condition", "schema_version": CURRENT_TARGET_REPLACEMENT_VERSION,
               "player": "$controller", "condition": predicate.to_dict(), "condition_target": "$target.0",
               "target_binding": deepcopy(normal[2]), "effects": deepcopy(list(alternate[1])),
               "mechanic_ids": list(alternate[3]), "prefix_mechanic_ids": [],
               "otherwise_effects": deepcopy(list(normal[1])), "otherwise_mechanic_ids": list(normal[3])}
    mechanics = tuple(dict.fromkeys((CURRENT_TARGET_REPLACEMENT_MECHANIC, "fixed-resolution-public-condition",
                                    *normal[3], *alternate[3])))
    from ..rules.target_replacements import current_target_replacement_capabilities
    if not current_target_replacement_capabilities(effects=(wrapper,), target_schema=normal[2], mechanic_ids=mechanics):
        return None
    return "current-target-self-replacement-v1", (wrapper,), deepcopy(normal[2]), mechanics
