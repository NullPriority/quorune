"""Exact grant of a combat-damage library result to a blocked creature."""

from dataclasses import replace

from ..rules.blocked_damage_cast import (
    GRANT_DEPS, GRANT_MECHANIC, GRANT_TEMPLATE, TRIGGER_TEMPLATE, damage_condition, damage_window,
    grant_effect, target_schema, window_dependencies,
)
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, SourceSpan


_GRANT = re.compile(
    r'Until end of turn, target blocked creature you control gets \+X/\+0 and gains trample and '
    r'"(?P<quoted>Whenever this creature deals combat damage to a player, '
    r'look at that many cards from the top of your library\. Exile up to one nonland card from among them '
    r'and put the rest on the bottom of your library in a random order\. '
    r'You may cast the exiled card without paying its mana cost\.)"', re.I,
)


def composed_temporary_ability_nodes(**scope):
    from .temporary_quoted_ability_nodes import temporary_quoted_ability_nodes

    return blocked_damage_grant_nodes(**scope) or temporary_quoted_ability_nodes(**scope)


def blocked_damage_grant_nodes(*, record, face_id, node_id, line, material_line, span,
                               printed_card_types, capability_registry, capability_profile,
                               residuals, **_unused):
    match = _GRANT.fullmatch(material_line)
    if (match is None or record.layout != "normal" or "{X}" not in (record.mana_cost or "")
            or not set(printed_card_types).intersection({"instant", "sorcery"})):
        return None
    quoted = match["quoted"]
    offset = line.find(quoted)
    if offset < 0:
        return None
    from .attached_granted_ability_nodes import attached_granted_ability_plan
    from .temporary_quoted_ability_nodes import _temporary_grant_outer_node

    child_span = SourceSpan(span.start + offset, span.start + offset + len(quoted), span.line)
    result = damage_window()
    registry_scope = dict(capability_registry=capability_registry, capability_profile=capability_profile)
    gate = explicit_capabilities_gate(window_dependencies(result), **registry_scope)
    closure = gate.closure
    child = OracleNode(
        node_id=node_id + ":granted", kind="triggered_ability", text=quoted, span=child_span,
        active_zone="battlefield", event="damage.dealt.self", event_condition=damage_condition(),
        effects=(result,), template_id=TRIGGER_TEMPLATE, lowerable=True, exact=True,
        mechanics=("fixed-library-selection", "trigger-event-normalized-damage", "cr-603-handling-triggered-abilities"),
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
    plan = attached_granted_ability_plan(
        node=child, quoted_text=quoted, oracle_id=record.oracle_id, face_id=face_id, source_line=span.line,
        card_name="Granted creature", keywords=(),
    )
    if plan is None or plan.node_kind != "granted_triggered_ability":
        return None
    outer = _temporary_grant_outer_node(
        node_id=node_id,line=line,span=span,paid=False,template=GRANT_TEMPLATE,cost=None,
        effects=(grant_effect(plan.fragment),),schema=target_schema(),
        mechanics=(GRANT_MECHANIC,"temporary-quoted-ability-grant","cr-115-targets","cr-611-continuous-effects"),
        dependencies=GRANT_DEPS,residuals=residuals,**registry_scope,
    )
    return outer, replace(child,kind=plan.node_kind,exact=not gate.blockers)
