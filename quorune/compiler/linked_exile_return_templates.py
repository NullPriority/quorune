from __future__ import annotations

"""One closed blink grammar across existing spell, trigger and activation shells."""

import hashlib
from . import regex_working_set as re
from typing import Any, Mapping

from ..linked_exile_return_model import (
    LinkedExileReturnSpec, LINKED_ABILITY_BINDING_PREFIX, LINKED_ABILITY_TIMING,
    LINKED_BATCH_DEPARTURE_RETURN_VERSION, LINKED_FADING_EXILE_VERSION,
    LINKED_FADING_HAND_BINDING, LinkedFadingScope,
    LINKED_PUBLIC_ALL_CREATURES_END_STEP_VERSION, LINKED_PUBLIC_SET_END_STEP_VERSION,
    LINKED_PUBLIC_SET_NEXT_COMBAT_VERSION, LinkedPublicSetScope,
    LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION, LINKED_TYPE_ENTRY_COUNTERS_VERSION,
    LINKED_CREATURE_ENTRY_COUNTER_VERSION,
    linked_type_entry_counter_target_schema,
)
from ..rules.linked_batch_departure_return import linked_batch_departure_target_schema
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .affected_player_discard_templates import fixed_affected_player_hand_exile_effect_template
from .direct_target import direct_permanent_target_spec
from .exile_templates import targeted_exile_effect_template
from .fixed_homogeneous_target_sets import _singularize_subject
from .fixed_target_effect_sequences import _keyword_list
from .linked_batch_departure_return import linked_batch_departure_scope


_INSTRUCTION = re.compile(
    r"^Exile (?P<subject>.+?)(?:, then |\. (?:Then )?)"
    r"(?:(?P<early>At the beginning of (?:the next|your next) end step, )?)"
    r"[Rr]eturn (?P<reference>it|that card|those cards|them) to the battlefield"
    r"(?P<tapped> tapped)?(?P<transformed> transformed)?(?: under (?P<controller>its owner's|their owner's|their owners'|your) control)?"
    r"(?: with (?P<count>a|one|two|three) (?P<counter>\+1/\+1|flying|vigilance|lifelink) counters? on (?:it|them))?"
    r"(?P<late> at the beginning of (?:the next|your next) end step)?\.?(?P<tail>.*)$",
    re.IGNORECASE,
)
_TYPE_ENTRY_COUNTER_RETURN = (
    "exile any number of target creatures and/or planeswalkers you control. "
    "at the beginning of the next end step, return each of them to the battlefield under its owner's control. "
    "each of them enters with an additional +1/+1 counter on it if it's a creature and an additional loyalty "
    "counter on it if it's a planeswalker."
)
_SINGLE_CREATURE_ENTRY_COUNTER_RETURN = (
    "exile target permanent you control. return that card to the battlefield under its owner's control "
    "at the beginning of the next end step. if it enters as a creature, it enters with an additional "
    "+1/+1 counter on it."
)
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_LINKED_LEAVE_BODY = re.compile(r"return the exiled card to the battlefield under its owner['’]s control\.", re.I)


def linked_face_ability_compilers(record, material_rows, effect_template, compile_ability, *,
                                  card_name, card_types, permanent, trigger_node, match_source):
    """Assemble linked source abilities against the prepared face rows."""
    from .entry_x_results import entry_result_trigger_node_compiler
    from .linked_loyalty_exile_templates import linked_loyalty_activation_compiler

    compile_ability = linked_loyalty_activation_compiler(
        compile_ability, material_rows=material_rows, card_name=card_name, card_types=card_types,
    )
    trigger_compiler = entry_result_trigger_node_compiler(
        record, trigger_node=trigger_node, ordinary_effect=effect_template,
        match_source=match_source, material_rows=material_rows,
    )
    trigger_compiler = linked_ability_trigger_compiler(
        trigger_compiler, trigger_node=trigger_node, material_rows=material_rows,
        card_name=card_name, match_source=match_source, permanent=permanent,
    )
    from .linked_library_imprint import linked_artifact_library_trigger_compiler, linked_library_imprint_trigger_compiler

    trigger_compiler = linked_library_imprint_trigger_compiler(
        trigger_compiler, trigger_node=trigger_node, material_rows=material_rows,
        card_name=card_name, match_source=match_source, permanent=permanent,
    )
    trigger_compiler = linked_artifact_library_trigger_compiler(
        trigger_compiler, trigger_node=trigger_node, material_rows=material_rows,
        card_name=card_name, match_source=match_source, permanent=permanent,
    )

    from .faction_trigger_templates import faction_face_ability_compiler

    compile_ability = faction_face_ability_compiler(compile_ability, material_rows=material_rows, card_types=card_types)

    from .private_opponent_library_cast_templates import private_opponent_library_cast_trigger_compiler

    trigger_compiler = private_opponent_library_cast_trigger_compiler(
        trigger_compiler, permanent=permanent, trigger_node=trigger_node, match_source=match_source,
    )
    return linked_fading_compilers(
        compile_ability, trigger_compiler, trigger_node=trigger_node, material_rows=material_rows,
        card_name=card_name, match_source=match_source, card_types=card_types,
    )


def linked_ability_trigger_compiler(fallback, *, trigger_node, material_rows, card_name, match_source, permanent):
    """Bind one unambiguous printed exile and separate return to two source spans."""
    if not permanent:
        return fallback
    leaving = [(text, span) for _, text, span in material_rows if "return the exiled card" in text.casefold()]
    if len(leaving) != 1:
        return fallback
    leave_text, leave_span = leaving[0]
    departure = match_source(leave_text, card_name=card_name)
    if departure is None or departure["event"].casefold() != "leaves the battlefield":
        return fallback
    exiling = [(text, span) for _, text, span in material_rows if re.search(r"\bexile\b", text, re.I)]
    if len(exiling) != 1:
        return fallback
    entry_text, entry_span = exiling[0]
    entry = match_source(entry_text, card_name=card_name)
    if entry is None or entry["event"].casefold() != "enters":
        return fallback
    body = entry["body"]
    scope = linked_batch_departure_scope(body, departure["body"])
    if scope is None:
        if _LINKED_LEAVE_BODY.fullmatch(departure["body"]) is None:
            return fallback
        optional = body.casefold().startswith("you may ")
        exile = targeted_exile_effect_template(body[8:] if optional else body)
        if exile is None:
            return fallback
        target_schema = exile.target_schema
        mechanics = (LINKED_EXILE_RETURN_MECHANIC, *exile.mechanics)
        spec = LinkedExileReturnSpec(timing=LINKED_ABILITY_TIMING, schema_version=3)
    else:
        optional = False
        target_schema = linked_batch_departure_target_schema(scope)
        mechanics = (LINKED_EXILE_RETURN_MECHANIC, "exile", *(("cr-115-targets",) if target_schema else ()))
        spec = LinkedExileReturnSpec(
            timing=LINKED_ABILITY_TIMING, schema_version=LINKED_BATCH_DEPARTURE_RETURN_VERSION,
            tapped=scope.tapped_return, batch_departure_scope=scope,
        )
    seed = f"{card_name}:{entry_span.line}:{leave_span.line}"
    pair = LINKED_ABILITY_BINDING_PREFIX + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    common = {"op": LINKED_EXILE_RETURN_OPERATION, "binding_id": pair, "spec": spec.to_dict()}
    effect = {**common, "phase": "exile", "cards": "$targets"}
    effects = ({"op": "offer_optional_effect", "player": "$controller", "effects": [effect]},) if optional else (effect,)
    if optional:
        mechanics = (*mechanics, "fixed-optional-effect-choice")
    templates = {
        entry_span.line: ("linked-ability-target-exile-v1" if scope is None else "linked-batch-exile-v1",
                          effects, target_schema, mechanics),
        leave_span.line: ("linked-ability-owner-return-v1", ({**common, "phase": "return"},), None, (LINKED_EXILE_RETURN_MECHANIC,)),
    }

    def compile_trigger(**values):
        compiled = templates.get(values["span"].line)
        if compiled is None:
            return fallback(**values)
        return trigger_node(**values, effect_template=lambda _body, **_source: compiled)

    return compile_trigger


def linked_fading_compilers(activation_compiler, departure_compiler, *, trigger_node,
                           material_rows, card_name, match_source, card_types):
    """Join a single fade-counter activation to its printed owner return."""
    if "enchantment" not in card_types or len(material_rows) != 3:
        return activation_compiler, departure_compiler
    (_, fading, _), (_, activation, activation_span), (_, departure, departure_span) = material_rows
    if re.fullmatch(r"Fading [1-9][0-9]*", fading, re.I) is None:
        return activation_compiler, departure_compiler
    source = SourceReferenceSpec(card_name).regex_pattern
    paid = re.fullmatch(rf"Remove a fade counter from (?:this enchantment|{source}): (?P<body>.+)", activation, re.I)
    leaving = match_source(departure, card_name=card_name)
    if paid is None or leaving is None or leaving["event"].casefold() != "leaves the battlefield":
        return activation_compiler, departure_compiler
    owner_return = re.fullmatch(
        r"each player returns to (?P<destination>the battlefield|their hand) all cards they own exiled with it\.",
        leaving["body"], re.I,
    )
    if owner_return is None:
        return activation_compiler, departure_compiler
    body = paid["body"]
    if owner_return["destination"].casefold() == "their hand":
        hand = re.fullmatch(
            r"(Target opponent exiles a card from their hand\.) Activate only as a sorcery\.", body, re.I,
        )
        if hand is None:
            return activation_compiler, departure_compiler
        scope = LinkedFadingScope.OPPONENT_HAND
        _, (choice,), target_schema, mechanics = fixed_affected_player_hand_exile_effect_template(hand[1])
    else:
        public = re.fullmatch(r"Exile target (land|creature)\.", body, re.I)
        if public is None:
            return activation_compiler, departure_compiler
        scope = LinkedFadingScope(public[1].casefold())
        exile = targeted_exile_effect_template(body)
        target_schema, mechanics = exile.target_schema, exile.mechanics
    seed = f"{card_name}:{activation_span.line}:{departure_span.line}"
    pair = LINKED_ABILITY_BINDING_PREFIX + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    spec = LinkedExileReturnSpec(
        timing=LINKED_ABILITY_TIMING, schema_version=LINKED_FADING_EXILE_VERSION, fading_scope=scope,
    )
    common = {"op": LINKED_EXILE_RETURN_OPERATION, "binding_id": pair, "spec": spec.to_dict()}
    effect = (
        {**choice, LINKED_FADING_HAND_BINDING: pair}
        if scope is LinkedFadingScope.OPPONENT_HAND
        else {**common, "phase": "exile", "cards": "$targets"}
    )
    compiled_activation = (
        "linked-fading-activation-v1", (effect,), target_schema, (LINKED_EXILE_RETURN_MECHANIC, *mechanics),
    )
    compiled_return = (
        "linked-fading-departure-v1", ({**common, "phase": "return"},), None, (LINKED_EXILE_RETURN_MECHANIC,),
    )

    def compile_activation(**values):
        if values["span"].line == activation_span.line:
            values["effect_template"] = lambda _body, **_source: compiled_activation
        return activation_compiler(**values)

    def compile_departure(**values):
        if values["span"].line == departure_span.line:
            return trigger_node(**values, effect_template=lambda _body, **_source: compiled_return)
        return departure_compiler(**values)

    return compile_activation, compile_departure


def scope_linked_return_bindings(value: Any, scope: str) -> Any:
    """Separate identical instructions in independently selected modes."""
    if isinstance(value, Mapping):
        result = {key: scope_linked_return_bindings(child, scope) for key, child in value.items()}
        if result.get("op") == LINKED_EXILE_RETURN_OPERATION:
            identity = result.get("binding_id")
            if type(identity) is not str or not identity.startswith("blink:"):
                raise ValueError("Linked instruction scope requires a valid binding")
            result["binding_id"] = "blink:" + hashlib.sha256((scope + ":" + identity).encode("utf-8")).hexdigest()[:24]
        return result
    if isinstance(value, (list, tuple)):
        return [scope_linked_return_bindings(child, scope) for child in value]
    return value


_PUBLIC_END_STEP_SET_TEXT = {
    ("exile all creatures. at the beginning of the next end step, return those cards to the battlefield "
     "under their owners' control."): LinkedPublicSetScope.ALL_CREATURES,
    ("exile each creature you control. return those cards to the battlefield under their owner's control "
     "at the beginning of the next end step."): LinkedPublicSetScope.CONTROLLED_CREATURES,
    ("exile all nonland permanents target player controls. return the exiled cards to the battlefield "
     "under their owner's control at the beginning of the next end step."): LinkedPublicSetScope.TARGET_PLAYER_NONLANDS,
}


_NEXT_COMBAT_HASTE_TEXT = (
    "exile all creatures you control. at the beginning of the next combat, return those cards to the battlefield "
    "under their owner's control and those creatures gain haste until end of turn."
)


def _public_set_return_template(normalized):
    text = normalized.casefold().replace("’", "'")
    if text == _NEXT_COMBAT_HASTE_TEXT:
        spec = LinkedExileReturnSpec(
            timing="next_combat", keywords=("Haste",), schema_version=LINKED_PUBLIC_SET_NEXT_COMBAT_VERSION,
        )
        template = "linked-public-creatures-next-combat-v1"
    else:
        scope = _PUBLIC_END_STEP_SET_TEXT.get(text)
        if scope is None:
            return None
        version = (LINKED_PUBLIC_ALL_CREATURES_END_STEP_VERSION if scope is LinkedPublicSetScope.ALL_CREATURES
                   else LINKED_PUBLIC_SET_END_STEP_VERSION)
        spec = LinkedExileReturnSpec(timing="next_end_step", schema_version=version, public_end_step_scope=scope)
        template = "linked-public-set-next-end-step-v1"
    scope = spec.public_set_scope
    binding = "blink:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    common = {"op": LINKED_EXILE_RETURN_OPERATION, "binding_id": binding, "spec": spec.to_dict()}
    effects = ({**common, "phase": "exile", "cards": scope.player_reference}, {**common, "phase": "return"})
    schema = scope.target_schema()
    mechanics = (LINKED_EXILE_RETURN_MECHANIC, *(("cr-115-targets",) if schema else ()))
    return template, effects, schema, mechanics


def prospective_entry_linked_return_template(text):
    normalized = " ".join(text.split())
    body = normalized.casefold().replace("’", "'")
    if body == _TYPE_ENTRY_COUNTER_RETURN:
        version = LINKED_TYPE_ENTRY_COUNTERS_VERSION
    elif body == _SINGLE_CREATURE_ENTRY_COUNTER_RETURN:
        version = LINKED_CREATURE_ENTRY_COUNTER_VERSION
    else:
        return None
    spec = LinkedExileReturnSpec(timing="next_end_step", schema_version=version)
    binding = "blink:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    common = {"op": LINKED_EXILE_RETURN_OPERATION, "binding_id": binding, "spec": spec.to_dict()}
    effects = ({**common, "phase": "exile", "cards": "$targets"}, {**common, "phase": "return"})
    return ("linked-exile-return-prospective-entry-counters-v1", effects,
            linked_type_entry_counter_target_schema(version), (LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets"))


def linked_exile_return_effect_template(text: str, *, card_name: str,
                                      source_is_permanent: bool | None = None) -> tuple[str, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]] | None:
    normalized = " ".join(text.strip().removeprefix("• ").split())
    if prospective := prospective_entry_linked_return_template(normalized):
        return prospective
    if normalized.casefold().startswith("you may "):
        inner = linked_exile_return_effect_template(normalized[8:], card_name=card_name, source_is_permanent=source_is_permanent)
        if inner is None:
            return None
        template, effects, schema, mechanics = inner
        # Both phases belong to one optional instruction, not two decisions.
        wrapper = {"op": "offer_optional_effect", "player": "$controller",
                   "effects": [dict(effect) for effect in effects]}
        return template, (wrapper,), schema, ("fixed-optional-effect-choice", *mechanics)
    if public_set := _public_set_return_template(normalized):
        return public_set
    parse_text = normalized
    if normalized.casefold() == ("exile target creature. return the exiled card to the battlefield "
                                 "under its owner's control at the beginning of the next end step."):
        parse_text = "Exile target creature. Return that card to the battlefield under its owner's control at the beginning of the next end step."
    match = _INSTRUCTION.fullmatch(parse_text)
    if match is None or match["early"] and match["late"]:
        return None
    subject = match["subject"]
    self_type = source_self_permanent_type(subject)
    transformed = bool(match["transformed"])
    self_subject = self_type is not None or SourceReferenceSpec(card_name).matches(subject) or (
        transformed and subject.casefold() == "this saga")
    if transformed and not self_subject:
        return None
    schema = None
    cards: Any = "$source.zone_object"
    if self_subject:
        if source_is_permanent is not True or match["reference"].casefold() not in {"it", "that card"}:
            return None
    else:
        # Cardinality and source exclusion are independent qualifiers. Capture
        # both before canonicalizing an "other" subject to "another target".
        cardinality = re.fullmatch(r"(?P<count>up to (?:one|two|three|four|five|six)|two|three|four|five|six|any number of) (?P<other>other )?target (?P<quality>.+)", subject, re.I)
        if cardinality:
            count_word = cardinality["count"].casefold()
            minimum = 0 if count_word.startswith(("up to ", "any number")) else _COUNTS[count_word]
            maximum = 999 if count_word == "any number of" else _COUNTS[count_word.removeprefix("up to ")]
            subject = ("another target " if cardinality["other"] else "target ") + _singularize_subject(cardinality["quality"])
        else:
            minimum = maximum = 1
        target = direct_permanent_target_spec(subject)
        if target is None:
            return None
        schema = target.to_target_schema()
        if minimum == 0:
            schema.pop("count")
            schema["up_to"] = maximum
        else:
            schema["count"] = maximum
        cards = "$targets"
    timing_text = match["early"] or match["late"] or ""
    timing = "controller_next_end_step" if "your next" in timing_text.casefold() else "next_end_step" if timing_text else "immediate"
    tail = match["tail"].strip()
    keywords: tuple[str, ...] = ()
    if tail:
        keyword = re.fullmatch(r"(?:It|That creature) gains (?P<keywords>.+?) until end of turn\.", tail, re.I)
        if keyword is None:
            return None
        keywords = _keyword_list(keyword["keywords"]) or ()
        if not keywords:
            return None
    try:
        spec = LinkedExileReturnSpec(timing=timing, controller="actor" if (match["controller"] or "").casefold() == "your" else "owner",
                                    transformed=transformed, schema_version=4 if transformed else 1,
                                    tapped=bool(match["tapped"]), keywords=keywords,
                                    entry_counters=((match["counter"].casefold(), {"a":1,"one":1,"two":2,"three":3}[match["count"].casefold()]),) if match["counter"] else ())
    except ValueError:
        return None
    binding = "blink:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    effects = (
        {"op": LINKED_EXILE_RETURN_OPERATION, "phase": "exile", "cards": cards, "binding_id": binding, "spec": spec.to_dict()},
        {"op": LINKED_EXILE_RETURN_OPERATION, "phase": "return", "binding_id": binding, "spec": spec.to_dict()},
    )
    return "linked-exile-return-v1", effects, schema, (LINKED_EXILE_RETURN_MECHANIC, *(("cr-115-targets",) if schema else ()))
