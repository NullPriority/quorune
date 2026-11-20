from __future__ import annotations

"""Closed source and attachment public-object condition grammar."""

from . import regex_working_set as re
from ..continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec


def object_public_state_condition(
    normalized: str,
    *,
    source_pattern: str,
    compile_object_query,
    compile_count_condition,
) -> FixedPublicStateConditionSpec | None:
    subject = source_pattern
    if re.fullmatch(rf"(?:it['’]s|{subject} is) renowned",normalized,re.I):
        from ..continuous_conditions import FixedPublicStateFact
        return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
            amount=1,fact=FixedPublicStateFact.SOURCE_RENOWNED,schema_version=4)
    source_state = re.fullmatch(
        rf"(?:it['’]s|{subject} is) (?P<state>attacking|blocking|enchanted|"
        r"equipped|modified|monstrous|tapped|untapped)",
        normalized,
        re.IGNORECASE,
    )
    if source_state is not None:
        state = source_state.group("state").casefold()
        state_fields = (
            {"tapped": state == "tapped"}
            if state in {"tapped", "untapped"}
            else {state: True}
        )
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
            predicate=ObjectQuerySpec(
                zones=("battlefield",),
                state_predicate=PermanentStatePredicateSpec(**state_fields),
            ),
            schema_version=2,
        )
    source_quality = re.fullmatch(
        rf"(?:it['’]s|{subject} is) (?P<quality>(?:a |an )?"
        r"[A-Za-z][A-Za-z' -]+)",
        normalized,
        re.IGNORECASE,
    )
    if source_quality is not None:
        predicate = compile_object_query(
            "permanent",
            source_quality.group("quality"),
        )
        if predicate is not None:
            return FixedPublicStateConditionSpec(
                FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
                predicate=predicate,
                schema_version=2,
            )
    attached_state = re.fullmatch(
        r"(?P<relation>enchanted|equipped) "
        r"(?P<subject>creature|permanent|land) is (?P<quality>.+)",
        normalized,
        re.IGNORECASE,
    )
    if attached_state is None:
        return compile_count_condition(normalized)
    attached_quality = attached_state.group("quality").casefold()
    if attached_quality in {
        "attacking",
        "blocking",
        "enchanted",
        "equipped",
        "modified",
        "monstrous",
        "tapped",
        "untapped",
    }:
        attached_subject = attached_state.group("subject").casefold()
        state_fields = (
            {"tapped": attached_quality == "tapped"}
            if attached_quality in {"tapped", "untapped"}
            else {attached_quality: True}
        )
        predicate = ObjectQuerySpec(
            zones=("battlefield",),
            types_all=(
                ()
                if attached_subject == "permanent"
                else (attached_subject,)
            ),
            state_predicate=PermanentStatePredicateSpec(**state_fields),
        )
    else:
        predicate = compile_object_query(
            attached_state.group("subject"),
            attached_state.group("quality"),
        )
    if predicate is None:
        return None
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY,
        predicate=predicate,
        schema_version=2,
    )
