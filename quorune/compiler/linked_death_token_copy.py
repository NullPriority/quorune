"""Lower one committed creature death and its copiable, granted-only leave ability."""

from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_to_dict
from ..rules.linked_death_token_copy import (
    DEPENDENCIES, DEATH_EVENT, LEAVE_EVENT, RETURN_LABEL, death_copy_condition,
    linked_death_copy_effect, linked_death_return_effect,
)
from . import regex_working_set as re
from .attached_granted_ability_nodes import GRANTED_TRIGGERED_ABILITY_KIND, attached_granted_program_ability_id
from .fixed_counter_trigger_nodes import fixed_counter_trigger_binding
from .ir_model import OracleNode


_PRINTED = re.compile(
    r'Whenever another nontoken creature you control dies, exile it\. If you do, '
    r'create a token that[\'\u2019]s a copy of that creature, except it[\'\u2019]s a Spirit '
    r'in addition to its other types and it has "(?P<return>When this token leaves the battlefield, '
    r'return the exiled card to its owner[\'\u2019]s graveyard\.)"',
    re.I,
)


def linked_death_token_copy_nodes(*, record, face_id, node_id, line, material_line, span, source_name, registry, profile):
    match = _PRINTED.fullmatch(material_line.strip())
    if match is None or registry is None:
        return None
    binding = fixed_counter_trigger_binding(material_line, card_name=source_name)
    if binding is None or binding.event.value != DEATH_EVENT or binding.event_condition != death_copy_condition():
        return None
    closure = registry.closure(DEPENDENCIES, profile=profile)
    if not closure.trusted or closure.blockers:
        return None
    child_id = f"{node_id}:token-ability:1"
    ability_id = attached_granted_program_ability_id(
        kind=GRANTED_TRIGGERED_ABILITY_KIND, face_id=face_id, line=span.line, node_id=child_id,
    )
    fragment = ability_fragment_to_dict(GrantedTriggeredAbilitySpec(
        ability_id, f"{record.oracle_id}:{ability_id}", LEAVE_EVENT, RETURN_LABEL,
    ))
    shared = {
        "span": span, "active_zone": "battlefield", "lowerable": True, "exact": True,
        "mechanics": ("cr-603-handling-triggered-abilities", "trigger-event-normalized-zone-change"),
        "capability_dependencies": tuple(sorted(DEPENDENCIES)), "capability_closure": closure.reachable,
        "capability_profile": closure.profile, "capability_fingerprint": closure.fingerprint,
    }
    return (
        OracleNode(node_id=node_id, kind="triggered_ability", text=line, event=DEATH_EVENT,
            event_condition=binding.event_condition, template_id="committed-death-linked-copy-v1",
            effects=(linked_death_copy_effect(fragment),), **shared),
        OracleNode(node_id=child_id, kind=GRANTED_TRIGGERED_ABILITY_KIND, text=match["return"], event=LEAVE_EVENT,
            template_id="granted-committed-death-copy-return-v1", effects=(linked_death_return_effect(),), **shared),
    )
