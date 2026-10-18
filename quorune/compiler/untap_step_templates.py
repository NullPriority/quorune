from __future__ import annotations

"""Closed Oracle grammar for static CR 502 participation."""

import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype, canonical_creature_subtype_surface
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
from ..untap_step import UntapInstruction
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .public_state_queries import fixed_battlefield_query_subject


UntapStepHandlerTemplate = tuple[str, Mapping[str, Any], str]


_GLOBAL_PROHIBITION = re.compile(
    r"^(?P<kind>Creatures|Permanents) don['’]t untap during their "
    r"controllers['’] untap steps?\.?$",
    re.IGNORECASE,
)
_ATTACHED_PROHIBITION = re.compile(
    r"^(?:(?P<aura>Enchanted)|(?P<equipment>Equipped)) "
    r"(?P<kind>artifact|battle|creature|enchantment|land|permanent|planeswalker) doesn['’]t untap during its "
    r"controller['’]s untap step\.?$",
    re.IGNORECASE,
)
_OTHER_PLAYER_UNTAP = re.compile(
    r"^Untap all (?P<kind>creatures|permanents) you control during "
    r"each other player['’]s untap step\.?$",
    re.IGNORECASE,
)
_QUERY_PROHIBITION = re.compile(
    r"^(?P<subject>.+?) don['’]t untap during their "
    r"controllers['’] untap steps?\.?$",
    re.IGNORECASE,
)
_QUERY_OTHER_PLAYER_UNTAP = re.compile(
    r"^Untap (?:all|each) (?P<subject>.+?) during "
    r"each other player['’]s untap step\.?$",
    re.IGNORECASE,
)


def _predicate(*, creature: bool) -> dict[str, Any]:
    return ObjectQuerySpec(
        zones=("battlefield",),
        types_all=(("creature",) if creature else ()),
    ).canonical_dict()


def _fixed_untap_query_subject(subject: str):
    """Keep untap-only numeric and counter surfaces out of layer generators."""
    text=' '.join(subject.casefold().split())
    if text=='nonbasic lands':
        return 'any',ObjectQuerySpec(zones=('battlefield',),types_all=('land',),excluded_supertypes=('basic',)),False
    if text=='legendary creatures':
        return 'any',ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),supertypes_all=('legendary',)),False
    subtype=canonical_creature_subtype_surface(text)
    if subtype is None and text.endswith('ies'):
        subtype=canonical_creature_subtype(text[:-3]+'y')
    if subtype is not None:
        return 'any',ObjectQuerySpec(zones=('battlefield',),subtypes_all=(subtype,)),False
    numeric=re.fullmatch(rf'creatures with power (?P<count>{FIXED_COUNT_PATTERN}) or (?P<comparison>greater|less)',text)
    if numeric is not None:
        predicate=TargetNumericCharacteristicSpec(TargetNumericCharacteristic.POWER,
            TargetNumericComparison.AT_LEAST if numeric['comparison']=='greater' else TargetNumericComparison.AT_MOST,
            fixed_number(numeric['count']))
        return 'any',ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),numeric_characteristic=predicate),False
    counter=re.fullmatch(r'creatures with (?P<counter>[a-z]+(?: [a-z]+)?) counters on them',text)
    if counter is not None:
        return 'any',ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),
            state_predicate=PermanentStatePredicateSpec(counter_name=counter['counter'],minimum_counter_count=1)),False
    colors=re.fullmatch(r'(?P<first>white|blue|black|red|green) and/or (?P<second>white|blue|black|red|green) creatures you control',text)
    if colors is not None:
        symbols={'white':'W','blue':'U','black':'B','red':'R','green':'G'}
        return 'source_controller',ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),
            colors_any=tuple(sorted({symbols[colors['first']],symbols[colors['second']]}))),False
    return fixed_battlefield_query_subject(subject)


def _descriptor(
    *,
    instruction: str,
    subject_relation: str,
    controller_relation: str,
    creature: bool,
    turn_relation: str,
    source_state: str = "any",
    maximum: int | None = None,
    predicate: ObjectQuerySpec | None = None,
) -> dict[str, Any]:
    return {
        "handler_id": "participation.untap-step.static.v1",
        "schema_version": 1,
        "event": "untap.step",
        "condition": {
            "turn_relation": turn_relation,
            "source_state": source_state,
        },
        "subject": {
            "relation": subject_relation,
            "controller_relation": controller_relation,
            "predicate": (
                predicate.canonical_dict()
                if predicate is not None
                else _predicate(creature=creature)
            ),
        },
        "instruction": {
            "kind": instruction,
            "maximum": maximum,
        },
    }


def static_untap_step_handler(
    text: str,
    *,
    source_name: str,
) -> UntapStepHandlerTemplate | None:
    """Lower the closed ordinary static prohibition/additional family."""

    normalized = text.strip()
    optional = re.fullmatch(
        r"You may choose not to untap (?P<source>.+?) during your untap step\.?",
        normalized, re.IGNORECASE,
    )
    if optional is not None and (
        source_self_permanent_type(optional["source"]) is not None
        or SourceReferenceSpec(source_name).matches(optional["source"])
    ):
        descriptor = _descriptor(
            instruction="optional", subject_relation="source",
            controller_relation="source_controller", creature=False,
            turn_relation="subject_controller",
        )
        descriptor["handler_id"] = "participation.untap-step.optional-source.v1"
        return (
            "untap-step-optional-source-v1", descriptor,
            "untap.step.optional_source",
        )
    global_match = _GLOBAL_PROHIBITION.fullmatch(normalized)
    if global_match is not None:
        kind = global_match.group("kind").casefold()
        return (
            f"untap-step-prohibit-global-{kind}-v1",
            _descriptor(
                instruction=UntapInstruction.PROHIBIT.value,
                subject_relation="query",
                controller_relation="any",
                creature=kind == "creatures",
                turn_relation="subject_controller",
            ),
            "untap.step.static_participation",
        )

    attached_match = _ATTACHED_PROHIBITION.fullmatch(normalized)
    if attached_match is not None:
        relation = (
            attached_match.group("aura")
            or attached_match.group("equipment")
        ).casefold()
        kind = attached_match.group("kind").casefold()
        if attached_match.group("equipment") is not None and kind != "creature":
            return None
        return (
            f"untap-step-prohibit-{relation}-{kind}-v1",
            _descriptor(
                instruction=UntapInstruction.PROHIBIT.value,
                subject_relation="attached_object",
                controller_relation="any",
                creature=kind == "creature",
                turn_relation="subject_controller",
                predicate=ObjectQuerySpec(zones=('battlefield',),types_all=() if kind=='permanent' else (kind,)),
            ),
            "untap.step.static_participation",
        )

    other_player = _OTHER_PLAYER_UNTAP.fullmatch(normalized)
    if other_player is not None:
        kind = other_player.group("kind").casefold()
        return (
            f"untap-step-additional-other-player-{kind}-v1",
            _descriptor(
                instruction=UntapInstruction.ADDITIONAL.value,
                subject_relation="query",
                controller_relation="source_controller",
                creature=kind == "creatures",
                turn_relation="other_player",
            ),
            "untap.step.static_participation",
        )

    query_prohibition = _QUERY_PROHIBITION.fullmatch(normalized)
    if query_prohibition is not None:
        parsed = _fixed_untap_query_subject(
            query_prohibition.group("subject")
        )
        if (
            parsed is not None
            and parsed[0] in {"any", "source_controller"}
            and not parsed[2]
        ):
            relation, predicate, exclude_source = parsed
            assert not exclude_source
            return (
                "untap-step-prohibit-fixed-query-v1",
                _descriptor(
                    instruction=UntapInstruction.PROHIBIT.value,
                    subject_relation="query",
                    controller_relation=relation,
                    creature=False,
                    turn_relation="subject_controller",
                    predicate=predicate,
                ),
                "untap.step.static_participation",
            )

    query_other_player = _QUERY_OTHER_PLAYER_UNTAP.fullmatch(normalized)
    if query_other_player is not None:
        parsed = _fixed_untap_query_subject(
            query_other_player.group("subject")
        )
        if parsed is not None:
            relation, predicate, exclude_source = parsed
            if relation == "source_controller" and not exclude_source:
                return (
                    "untap-step-additional-other-player-fixed-query-v1",
                    _descriptor(
                        instruction=UntapInstruction.ADDITIONAL.value,
                        subject_relation="query",
                        controller_relation=relation,
                        creature=False,
                        turn_relation="other_player",
                        predicate=predicate,
                    ),
                    "untap.step.static_participation",
                )

    if type(source_name) is not str or not source_name:
        return None
    escaped_name = re.escape(source_name)
    source_reference = (
        rf"(?:{escaped_name}|this (?:artifact|creature|land|permanent))"
    )
    if re.fullmatch(rf"Untap {source_reference} during each other player['’]s untap step\.?",
            normalized,re.IGNORECASE) is not None:
        return ('untap-step-additional-source-v1',_descriptor(instruction=UntapInstruction.ADDITIONAL.value,
            subject_relation='source',controller_relation='any',creature=False,
            turn_relation='other_player'),'untap.step.static_participation')
    if re.fullmatch(
        rf"{source_reference} doesn['’]t untap during "
        rf"(?:your|its controller['’]s) untap step\.?",
        normalized,
        re.IGNORECASE,
    ) is not None:
        return (
            "untap-step-prohibit-source-v1",
            _descriptor(
                instruction=UntapInstruction.PROHIBIT.value,
                subject_relation="source",
                controller_relation="any",
                creature=False,
                turn_relation="subject_controller",
            ),
            "untap.step.static_participation",
        )
    return None


def static_untap_step_limit_handler(
    text: str,
    *,
    source_name: str,
) -> UntapStepHandlerTemplate | None:
    """Pin a closed unsupported selection limit without promoting it."""

    if type(source_name) is not str or not source_name:
        return None
    source_reference = (
        rf"(?:{re.escape(source_name)}|this artifact)"
    )
    match = re.fullmatch(
        rf"As long as {source_reference} is untapped, players "
        rf"can['’]t untap more than (?P<count>{FIXED_COUNT_PATTERN}) "
        rf"permanents during their untap steps\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    maximum = fixed_number(match.group("count"))
    return (
        f"untap-step-limit-{maximum}-typed-fail-closed-v1",
        _descriptor(
            instruction=UntapInstruction.LIMIT.value,
            subject_relation="query",
            controller_relation="any",
            creature=False,
            turn_relation="subject_controller",
            source_state="untapped",
            maximum=maximum,
        ),
        "untap.step.static_participation",
    )


__all__ = [
    "static_untap_step_handler",
    "static_untap_step_limit_handler",
    "UntapStepHandlerTemplate",
]
