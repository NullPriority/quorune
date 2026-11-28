"""Source-spanned numeric Ripple over the ordinary self-spell cast event."""
from . import regex_working_set as re
from .fixed_numbers import fixed_number
from ..ripple_model import RippleSpec

_KEYWORD=re.compile(r'Ripple (?P<count>0|[1-9][0-9]*)(?: \((?P<reminder>[^()]+)\))?\.?',re.I)
_REMINDER=re.compile(r'When you cast this spell, you may reveal the top (?P<count>[a-z]+|[0-9]+) cards of your library\. '
    r'You may cast spells with the same name as this spell from among those cards without paying their mana costs\. '
    r'Put the rest on the bottom of your library\.',re.I)


def ripple_spec_from_line(line):
    match=_KEYWORD.fullmatch(' '.join(line.strip().split()))
    if match is None:return None
    count=int(match['count'])
    if match['reminder'] is not None:
        reminder=_REMINDER.fullmatch(match['reminder'])
        if reminder is None:return None
        try:reminder_count=fixed_number(reminder['count'])
        except (KeyError,TypeError,ValueError):return None
        if reminder_count!=count:return None
    return RippleSpec(count)


def ripple_keyword_node(*,node_id,line,material_line,span,capability_registry,
        capability_profile,residuals,mechanics=()):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..rules.ripple import ripple_instruction,ripple_event_condition,RIPPLE_TRIGGER_CAPABILITIES
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode,append_residual
    spec=ripple_spec_from_line(material_line)
    if mechanics!=('ripple',) or spec is None or ripple_spec_from_line(line)!=spec:return None
    gate=explicit_capabilities_gate(RIPPLE_TRIGGER_CAPABILITIES,
        capability_registry=capability_registry,capability_profile=capability_profile)
    blocked=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Ripple requires its current self-spell cast, revealed-library and resolving-cast owners',
        blockers=gate.blockers),) if gate.blockers else ()
    closure=gate.closure
    return OracleNode(node_id=node_id,kind='triggered_ability',text=line,span=span,
        active_zone='stack',event='spell.cast',event_condition=ripple_event_condition(),
        lowerable=True,exact=not blocked,template_id='ripple-fixed-count-v1',
        effects=(ripple_instruction(spec),),target_schema=None,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=('ripple','trigger-event-normalized-spell-cast','cr-603-handling-triggered-abilities'),
        residual_ids=blocked,capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)


_CAST_RIPPLE_GRANT = re.compile(
    r"Spells you cast have (?P<grant>ripple (?P<count>0|[1-9][0-9]*))\."
    r"(?: \(Whenever you cast a spell, you may reveal the top (?P<reminder>[a-z]+|[0-9]+) cards of your library\. "
    r"You may cast spells with the same name as that spell from among the revealed cards without paying their mana costs\. "
    r"Put the rest on the bottom of your library\.\))?",
    re.IGNORECASE,
)


def battlefield_ripple_grant_nodes(
    *, record, face_id, node_id, line, span, printed_card_types,
    capability_registry, capability_profile, residuals,
):
    """Compile the static carrier together with its non-intrinsic cast trigger body."""
    from dataclasses import replace
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, GrantedTriggeredAbilitySpec
    from ..semantic_runtime.battlefield_ripple_grants import BattlefieldRippleGrantHandler, BattlefieldRippleGrantSpec
    from .attached_granted_ability_nodes import GRANTED_TRIGGERED_ABILITY_KIND, attached_granted_program_ability_id
    from .static_runtime_nodes import runtime_handler_node

    match = _CAST_RIPPLE_GRANT.fullmatch(line)
    if match is None or "artifact" not in printed_card_types:
        return None
    count = int(match["count"])
    if match["reminder"] is not None:
        try:
            reminder_count = fixed_number(match["reminder"].casefold())
        except (KeyError, TypeError, ValueError):
            return None
        if reminder_count != count:
            return None
    keyword = match["grant"]
    start, end = match.span("grant")
    child_span = replace(span, start=span.start + start, end=span.start + end)
    child = ripple_keyword_node(
        node_id=f"{node_id}:granted", line=keyword, material_line=keyword, span=child_span,
        capability_registry=capability_registry, capability_profile=capability_profile,
        residuals=residuals, mechanics=("ripple",),
    )
    kind = GRANTED_TRIGGERED_ABILITY_KIND
    ability_id = attached_granted_program_ability_id(kind=kind, face_id=face_id, line=span.line)
    spec = BattlefieldRippleGrantSpec(
        RippleSpec(count),
        GrantedTriggeredAbilitySpec(ability_id, f"{record.oracle_id}:{ability_id}", "spell.cast", f"Ripple {count}"),
    )
    handler = BattlefieldRippleGrantHandler()
    descriptor = {"handler_id": handler.handler_id, "schema_version": handler.schema_version,
                  "event": handler.event, "requires_complete_card_program": True, "grant": spec.to_dict()}
    outer = runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=("battlefield-ripple-grant-v1", descriptor, handler.capability_dependencies),
        kind="static_ability", event=handler.event, active_zone="battlefield",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Battlefield Ripple requires the live stack grant and its independent Ripple body",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    return outer, replace(child, kind=kind, runtime_coverage=())
