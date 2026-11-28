"""Compile the complete attached planeswalker printed ability and its typed child."""

from ..ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE, GrantedTriggeredAbilitySpec, ability_fragment_to_dict,
)
from ..rules.equipped_planeswalker import (
    EQUIPPED_DAMAGE_COVERAGE, EQUIPPED_DAMAGE_DEPENDENCIES, EQUIPPED_PLANESWALKER_DEPENDENCIES,
    EQUIPPED_PLANESWALKER_HANDLER, EQUIPPED_PLANESWALKER_TEMPLATE,
    equipped_damage_condition, equipped_damage_effect,
)
from . import regex_working_set as re
from .ir_model import OracleNode
from .static_runtime_nodes import runtime_handler_node


def loyalty_attachment_template(text, *, source_name):
    from ..rules.loyalty_attachment import (
        LOYALTY_ATTACHMENT_MECHANIC, LOYALTY_ATTACHMENT_TEMPLATE, loyalty_attachment_effects, loyalty_attachment_target,
    )

    if re.fullmatch(
        rf"Attach {re.escape(source_name)} to up to one target creature you control\. "
        r"Put a \+1/\+1 counter on that creature\.", text, re.I,
    ) is None:
        return None
    return (LOYALTY_ATTACHMENT_TEMPLATE, loyalty_attachment_effects(), loyalty_attachment_target(),
            (LOYALTY_ATTACHMENT_MECHANIC,))


def equipped_planeswalker_nodes(*, record, face_id, node_id, line, material_line, span, source_name,
        capability_registry, capability_profile, residuals):
    source = re.escape(source_name)
    match = re.fullmatch(
        rf'As long as {source} is attached to a creature, {source} can[’\x27]t be attacked and has '
        rf'"(?P<quote>Whenever equipped creature deals combat damage during your turn, '
        rf'put that many loyalty counters on {source}\.)"',
        material_line, re.I,
    )
    if match is None:
        return None
    from .attached_granted_ability_nodes import GRANTED_TRIGGERED_ABILITY_KIND, attached_granted_program_ability_id

    child_id = attached_granted_program_ability_id(
        kind=GRANTED_TRIGGERED_ABILITY_KIND, face_id=face_id, line=span.line,
    )
    quote = match["quote"]
    fragment = GrantedTriggeredAbilitySpec(
        ability_id=child_id, semantic_key=f"{record.oracle_id}:{child_id}", event="damage.dealt", label=quote,
    )
    descriptor = {
        "handler_id": EQUIPPED_PLANESWALKER_HANDLER, "schema_version": 1, "event": "characteristics.evaluate",
        "fragment": ability_fragment_to_dict(fragment),
    }
    child_dependencies = EQUIPPED_DAMAGE_DEPENDENCIES
    outer = runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=(EQUIPPED_PLANESWALKER_TEMPLATE, descriptor,
                  tuple(dict.fromkeys((*EQUIPPED_PLANESWALKER_DEPENDENCIES, *child_dependencies)))),
        kind="static_ability", event="characteristics.evaluate",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Attached planeswalker requires the live declaration and equipped combat authorities.",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    closure = capability_registry.closure(child_dependencies, profile=capability_profile) if capability_registry else None
    if not outer.exact or closure is None or not closure.trusted:
        return (outer,)
    child = OracleNode(
        node_id=f"{node_id}:granted", kind=GRANTED_TRIGGERED_ABILITY_KIND, text=quote, span=span,
        template_id=EQUIPPED_PLANESWALKER_TEMPLATE, active_zone="battlefield", event="damage.dealt",
        lowerable=True, exact=True, event_condition=equipped_damage_condition(), effects=(equipped_damage_effect(),),
        mechanics=("cr-122-counters",), runtime_coverage=(EQUIPPED_DAMAGE_COVERAGE,),
        capability_dependencies=child_dependencies, capability_closure=closure.reachable,
        capability_profile=closure.profile, capability_fingerprint=closure.fingerprint,
    )
    return outer, child
