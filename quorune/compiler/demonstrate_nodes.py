"""Consume the entire intrinsic Demonstrate keyword, including its printed reminder."""
from . import regex_working_set as re

_REMINDER = re.compile(
    r"When you cast this spell, you may copy it\. If you do, choose an opponent to also copy it\."
    r"(?: Players may choose new targets for their copies\.)?", re.I)


def is_demonstrate_line(line):
    text=' '.join(line.strip().split())
    match=re.fullmatch(r'Demonstrate(?: \((?P<reminder>[^()]+)\))?\.?',text,re.I)
    return bool(match is not None and (match['reminder'] is None or _REMINDER.fullmatch(match['reminder'])))


def demonstrate_keyword_node(*,node_id,line,material_line,span,capability_registry,
        capability_profile,residuals,mechanics=()):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..rules.demonstrate import (
        DEMONSTRATE_DEPENDENCIES,demonstrate_instruction,demonstrate_condition,
    )
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode,append_residual
    if mechanics!=('demonstrate',) or not is_demonstrate_line(material_line) or not is_demonstrate_line(line):
        return None
    gate=explicit_capabilities_gate(DEMONSTRATE_DEPENDENCIES,
        capability_registry=capability_registry,capability_profile=capability_profile)
    blocked=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
        reason='Demonstrate requires its current cast snapshot, canonical copies and principal-bound copy choices',
        blockers=gate.blockers),) if gate.blockers else ()
    closure=gate.closure
    return OracleNode(node_id=node_id,kind='triggered_ability',text=line,span=span,
        active_zone='stack',event='spell.cast',event_condition=demonstrate_condition(),
        lowerable=True,exact=not blocked,template_id='demonstrate-intrinsic-cast-v1',
        effects=(demonstrate_instruction(),),target_schema=None,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=('demonstrate','trigger-event-normalized-spell-cast','cr-603-handling-triggered-abilities'),
        residual_ids=blocked,capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
