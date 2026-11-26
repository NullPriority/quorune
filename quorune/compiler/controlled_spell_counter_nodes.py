"""Compile the exact controlled spell counter with its ordinary optional result."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.controlled_spell_counter import (
    CONTROLLED_SPELL_COUNTER_CAPABILITY,
    CONTROLLED_SPELL_COUNTER_EVENT,
    controlled_spell_counter_condition,
)
from . import regex_working_set as re
from .dependency_gate import dependency_gate
from .fixed_counter_trigger_nodes import fixed_typed_event_effect_trigger_node
from .ir_model import OracleNode, append_residual
from .optional_effect_templates import fixed_optional_effect_template


_TRIGGER = re.compile(
    r"Whenever a spell or ability you control counters a spell, "
    r"(?P<body>you may draw a card\. If you do, discard a card\.)",
    re.I,
)


def _controlled_spell_counter_node(match, *, node_id, line, span, trusted_mechanics,
                                   capability_registry, capability_profile, residuals, **_):
    _, effects, target_schema, effect_mechanics = fixed_optional_effect_template(
        match["body"], compile_effect=lambda _: (None, (), None, ()),
    ).compiled()
    mechanics = ("cr-603-handling-triggered-abilities", "fixed-typed-event-effect-trigger", *effect_mechanics)
    gate = dependency_gate(
        mechanics=mechanics, effects=effects, target_schema=target_schema,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, explicit_capabilities=(CONTROLLED_SPELL_COUNTER_CAPABILITY,),
    )
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(
            residuals, kind="dependency_contract", text=line, span=span, blockers=gate.blockers,
            reason="A controlled spell counter requires a committed effect occurrence and closed optional loot",
        ),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event=CONTROLLED_SPELL_COUNTER_EVENT, event_condition=controlled_spell_counter_condition(),
        lowerable=True, exact=not residual_ids, template_id="fixed-controlled-spell-counter-loot-v1",
        effects=effects, mechanics=mechanics, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (), capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )


def fixed_typed_or_controlled_spell_counter_trigger_node(*, material_line, binding, **kwargs):
    match = _TRIGGER.fullmatch(material_line) if binding is None else None
    if match is not None:
        return _controlled_spell_counter_node(match, **kwargs)
    return fixed_typed_event_effect_trigger_node(material_line=material_line, binding=binding, **kwargs)
