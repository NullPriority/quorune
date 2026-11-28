"""Close the three-line targeted attack d20 table and its quoted ability."""

from dataclasses import replace

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, ability_fragment_from_dict
from ..rules.source_references import SourceReferenceSpec
from ..rules.targeted_attack_die_copy import (
    ATTACK_CONDITION, CLEANUP_CAPABILITY, CLEANUP_CONDITION, CLEANUP_EFFECT, CLEANUP_TEMPLATE, GRANTED_TEXT,
    ROLL_CAPABILITY, ROLL_TEMPLATE, TARGET_SCHEMA, roll_effect,
)
from . import regex_working_set as re
from .attached_granted_ability_nodes import attached_granted_ability_plan
from .ir_model import OracleFaceIR, OracleNode, SourceSpan


_HEAD = re.compile(r"Whenever (?P<source>.+?) attacks, choose target creature you control, then roll a d20\.", re.I)
_FIRST = (
    "1—14 | Create a tapped and attacking token that's a copy of that creature, "
    "except it's not legendary and it has \"" + GRANTED_TEXT + "\""
)
_SECOND = "15—20 | Create one of those tokens. You may roll again."
_MECHANIC = "cr-603-handling-triggered-abilities"


def _normalize(text):
    return " ".join(text.replace("’", "'").split()).casefold()


def targeted_attack_die_copy_face(*, record, face_id, face_name, oracle_text, material_rows, registry, profile):
    if registry is None or len(material_rows) != 3:
        return None
    head, first, second = material_rows
    match = _HEAD.fullmatch(head[1])
    if (match is None or (match["source"].casefold() != "this creature"
            and not SourceReferenceSpec(face_name or record.name).matches(match["source"]))
            or _normalize(first[1]) != _normalize(_FIRST) or _normalize(second[1]) != _normalize(_SECOND)):
        return None
    outcome = registry.closure((ROLL_CAPABILITY, CLEANUP_CAPABILITY), profile=profile)
    cleanup = registry.closure((CLEANUP_CAPABILITY,), profile=profile)
    if not outcome.trusted or outcome.blockers or not cleanup.trusted or cleanup.blockers:
        return None
    child_start = first[2].start + first[0].casefold().index(GRANTED_TEXT.casefold())
    child_span = SourceSpan(child_start, child_start + len(GRANTED_TEXT), first[2].line)
    parent_id = f"{face_id}:n1"
    child_id = f"{parent_id}:quoted-ability:1"
    child = OracleNode(
        node_id=child_id, kind="triggered_ability", text=oracle_text[child_span.start:child_span.end], span=child_span,
        active_zone="battlefield", event="step.begin", event_condition=CLEANUP_CONDITION,
        lowerable=True, exact=True, template_id=CLEANUP_TEMPLATE, effects=(CLEANUP_EFFECT,), mechanics=(_MECHANIC,),
        capability_dependencies=cleanup.requested, capability_closure=cleanup.reachable,
        capability_profile=cleanup.profile, capability_fingerprint=cleanup.fingerprint,
    )
    plan = attached_granted_ability_plan(
        node=child, quoted_text=GRANTED_TEXT, oracle_id=record.oracle_id, face_id=face_id,
        source_line=child_span.line, card_name="Copied creature", keywords=(), node_id=child_id,
    )
    if plan is None:
        return None
    grant = ability_fragment_from_dict(plan.fragment)
    span = SourceSpan(head[2].start, second[2].end, head[2].line)
    parent = OracleNode(
        node_id=parent_id, kind="triggered_ability", text=oracle_text[span.start:span.end], span=span,
        active_zone="battlefield", event="creature.attacks", event_condition=ATTACK_CONDITION,
        lowerable=True, exact=True, template_id=ROLL_TEMPLATE, effects=(roll_effect(grant),), target_schema=TARGET_SCHEMA,
        mechanics=(_MECHANIC, "cr-115-targets"), runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        capability_dependencies=outcome.requested, capability_closure=outcome.reachable,
        capability_profile=outcome.profile, capability_fingerprint=outcome.fingerprint,
    )
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text,
                        nodes=(parent, replace(child, kind=plan.node_kind)), residuals=())
