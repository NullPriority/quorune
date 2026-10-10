from __future__ import annotations

"""Complete scalar definitions over already-closed fixed result owners."""

from dataclasses import replace
from copy import deepcopy
import hashlib
import re
from typing import Any, Callable, Mapping

from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_KIND, SCALAR_AMOUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec,
)
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, CastXAmountSpec, PublicQueryAmountError
from ..rules.source_references import SourceReferenceSpec
from ..counter_names import normalized_counter_name
from .declared_effect_amounts import declared_effect_amount_template, _result_slot
from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate


_ZONE_EVENTS = {"permanent.enter", "artifact.enter", "creature.enter", "creature.dies",
                "permanent.leave", "permanent.graveyard", "artifact.graveyard"}
_AMOUNT_EVENTS = {"life.gained", "damage.dealt", "combat.damage.player"}
_HISTORY = {
    "the amount of life you gained this turn": "life_gained",
    "the amount of life you lost this turn": "life_lost",
    "the number of spells you've cast this turn": "spells_cast",
    "the number of spells you cast this turn": "spells_cast",
    "the number of cards you've drawn this turn": "cards_drawn",
    "the number of cards you drew this turn": "cards_drawn",
    "the number of cards you've discarded this turn": "cards_discarded",
    "the number of cards you discarded this turn": "cards_discarded",
    "the number of permanents you've sacrificed this turn": "permanents_sacrificed",
    "the number of creatures that died this turn": "creatures_died",
    "the number of creatures that entered under your control this turn": "creatures_entered",
}


def _characteristic(value: str, *, source_name: str, body: str, schema: Any,
                    event: str | None, source_event: bool) -> ScalarEffectAmountSpec | None:
    counter=re.fullmatch(r'(?:the )?number of (?P<counter>[A-Za-z0-9+/-]+) counters? on (?P<reference>.+)',value,re.I)
    if counter is not None:
        source=rf'(?:this (?:artifact|aura|creature|enchantment|equipment|land|permanent|token)|{SourceReferenceSpec(source_name).regex_pattern})'
        direct=bool(re.fullmatch(source,counter['reference'],re.I))
        pronoun=counter['reference'].casefold() in {'it','him','her'}
        if not direct and not (pronoun and source_event):return None
        try:return ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,counter_name=normalized_counter_name(counter['counter']),schema_version=2)
        except ValueError:return None
    match = re.fullmatch(r"(?P<reference>.+?) (?P<field>power|toughness|mana value)", value, re.I)
    if match is None:
        return None
    reference = match["reference"].casefold()
    field = match["field"].casefold().replace(" ", "_")
    source = rf"(?:this (?:creature|permanent|artifact|land|enchantment)|{SourceReferenceSpec(source_name).regex_pattern})"
    if re.fullmatch(source + "['’]s", match["reference"], re.I):
        origin = ScalarAmountOrigin.SOURCE
    elif reference == "its" and re.match(source + r" (?:gets|deals|gains)\b", body, re.I):
        origin = ScalarAmountOrigin.SOURCE
    elif (reference == "its" or re.fullmatch(r"that (?:creature|permanent|artifact|land)['’]s", reference)) and (
        isinstance(schema, Mapping) and schema.get("zones") == ["battlefield"]
        and schema.get("count") == 1 and not schema.get("up_to")
        and len(re.findall(r"\btarget\b", body, re.I)) == 1
    ):
        origin = ScalarAmountOrigin.TARGET
    elif reference in {"its", "that creature's", "that permanent's", "that artifact's", "that land's"} and isinstance(schema, Mapping) and schema.get("zones") == ["battlefield"]:
        return None
    elif reference == "its" and source_event:
        origin = ScalarAmountOrigin.SOURCE
    elif reference in {"its", "that creature's", "that permanent's", "that artifact's", "that land's"} and event in _ZONE_EVENTS:
        origin = ScalarAmountOrigin.EVENT_OBJECT
    else:
        return None
    return ScalarEffectAmountSpec(origin, characteristic=field)


def scalar_effect_amount_template(text: str, *, source_name: str, compile_fixed: Callable,
                                 event: str | None = None, source_event: bool = False):
    """Only replace result slots after complete consumption and fixed-shape proof."""
    normalized = text.strip()
    definition = re.fullmatch(r"(?P<body>[^.]+?), where X is (?:equal to )?(?P<value>.+?)\.?", normalized, re.I)
    producer = None
    multiplier = 1
    if definition is not None:
        body, quantity = definition["body"], definition["value"].rstrip(".")
    elif (each:=re.fullmatch(r'(?P<head>Draw a card|(?:You |Target player |Target opponent )?gain 1 life|Create (?:a|one) .+? token) for each (?P<counter>[A-Za-z0-9+/-]+) counters? on (?P<reference>.+?)\.',normalized,re.I)) is not None:
        body=re.sub(r'^Draw a card$','Draw X cards',each['head'],flags=re.I)
        body=re.sub(r'\bgain 1 life\b','gain X life',body,flags=re.I)
        body=re.sub(r'^Create (?:a|one) (.+) token$',r'Create X \1 tokens',body,flags=re.I)+'.'
        quantity=f"the number of {each['counter']} counters on {each['reference']}"
    elif event in _AMOUNT_EVENTS and re.search(r"\bthat (?:many|much)\b", normalized, re.I):
        if re.search(r"\b(?:where|if|unless|for each|this way)\b", normalized, re.I):
            return None
        body = re.sub(r"\bthat (?:many|much)\b", "X", normalized, flags=re.I)
        producer = ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT)
        quantity = ""
    else:
        match = re.fullmatch(r"(?P<head>.+?) equal to (?P<twice>twice )?(?P<quantity>.+?)(?P<tail> to .+| until end of turn)?\.?", normalized, re.I)
        if match is None:
            return None
        body = match["head"] + " X" + (match["tail"] or "") + "."
        quantity = match["quantity"].rstrip(".")
        multiplier = 2 if match["twice"] else 1
        body = re.sub(r"\bdraw cards X\b", "Draw X cards", body, flags=re.I)
        body = re.sub(r"\b(deals?) damage X\b", r"\1 X damage", body, flags=re.I)
        body = re.sub(r"\b(gain|gains|lose|loses) life X\b", r"\1 X life", body, flags=re.I)
    def canonical_fixed(text):
        result = compile_fixed(text)
        if len(result[1]) == 1 and result[1][0].get("op") == "modify_stats_until_end_of_turn" and result[1][0].get("card") == "$source":
            effect = result[1][0]
            source_result = FixedSourceCharacteristicsTemplate(source_kind="query-stat-modifier",
                power=effect["power"], toughness=effect["toughness"])
            return source_result.compiled()
        return result
    fixed = declared_effect_amount_template(body, source_name=source_name,
        compile_fixed=canonical_fixed, cast_x_available=True)
    if fixed is None:
        return None
    if producer is None:
        history_fact = _HISTORY.get(" ".join(quantity.casefold().split()))
        producer = (ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY, history_fact=history_fact)
                    if history_fact else _characteristic(quantity, source_name=source_name, body=body,
                        schema=fixed[2], event=event, source_event=source_event))
    if producer is None:
        return None
    identity = "scalar:x:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24] if definition else None
    def project(value):
        if isinstance(value, Mapping):
            if value.get("kind") == CAST_X_AMOUNT_KIND:
                sign = CastXAmountSpec.from_dict(value).coefficient
                return replace(producer, coefficient=sign * multiplier, binding_id=identity).to_dict()
            return {key: project(child) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            return type(value)(project(child) for child in value)
        return deepcopy(value)
    return fixed[0], tuple(project(effect) for effect in fixed[1]), fixed[2], tuple(dict.fromkeys((*fixed[3], SCALAR_AMOUNT_MECHANIC)))


def scalar_amount_shape_context(effects, mechanics):
    """Project only validated result slots, retaining the original fixed owner."""
    count = 0
    def project(value, operation, path):
        nonlocal count
        if isinstance(value, Mapping) and value.get("kind") == SCALAR_AMOUNT_KIND:
            if not _result_slot(operation, path):
                raise PublicQueryAmountError("Scalar amount is outside a result slot")
            spec = ScalarEffectAmountSpec.from_dict(value)
            count += 1
            fixed = 2 * spec.coefficient
            return str(fixed) if path in {("characteristics", "power"), ("characteristics", "toughness")} else fixed
        if isinstance(value, Mapping):
            return {key: project(child, operation, (*path, key)) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            return type(value)(project(child, operation, path) for child in value)
        return deepcopy(value)
    try:
        fixed = tuple(project(effect, str(effect.get("op") or ""), ()) for effect in effects)
    except PublicQueryAmountError:
        return None
    if not count:
        return None
    return fixed, set(mechanics) - {SCALAR_AMOUNT_MECHANIC, "declared-effect-amount"}
