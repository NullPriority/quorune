"""Lower a fixed ordinary ninjutsu grant to the controller's hand creatures."""

from dataclasses import replace

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, ability_fragment_to_dict
from ..combat_entry_activations import compile_fixed_ninjutsu
from ..granted_activation_model import GRANTED_NINJUTSU_VERSION, GrantedActivatedAbilitySpec
from ..semantic_runtime.hand_ninjutsu_grants import HAND_NINJUTSU_RECIPIENT, HandNinjutsuGrantHandler
from . import regex_working_set as re
from .combat_entry_activation_nodes import fixed_ninjutsu_keyword_node
from .static_runtime_nodes import runtime_handler_node

_GRANT = re.compile(r"Each creature card in your hand has ninjutsu (?P<cost>(?:\{[^{}]+\})+)\.", re.IGNORECASE)


def compile_hand_ninjutsu_nodes(*, record, face_id, node_id, line, material_line, span,
                                capability_registry, capability_profile, residuals):
    match = _GRANT.fullmatch(material_line.strip())
    if match is None or capability_registry is None:
        return None
    inner_text = f"Ninjutsu {match['cost'].upper()}"
    spec = compile_fixed_ninjutsu(material_line=inner_text, oracle_line=inner_text, line_index=span.line - 1)
    if spec is None:
        return None
    inner = fixed_ninjutsu_keyword_node(
        node_id=f"{node_id}:granted", line=inner_text, material_line=inner_text, span=span,
        mechanics=("ninjutsu",), capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    )
    if inner is None or not inner.exact:
        return None
    from .attached_granted_ability_nodes import GRANTED_ACTIVATED_ABILITY_KIND, attached_granted_program_ability_id

    ability_id = attached_granted_program_ability_id(kind=GRANTED_ACTIVATED_ABILITY_KIND, face_id=face_id, line=span.line)
    ability = spec.to_activated_ability()
    fragment = GrantedActivatedAbilitySpec(
        ability_id=ability_id, semantic_key=f"{record.oracle_id}:{ability_id}",
        cost_text=spec.cost_text, effect_text=ability.effect_text, mana=tuple(ability.mana.items()),
        schema_version=GRANTED_NINJUTSU_VERSION,
    )
    handler = HandNinjutsuGrantHandler()
    descriptor = {
        "handler_id": handler.handler_id, "schema_version": handler.schema_version, "event": handler.event,
        "predicate": HAND_NINJUTSU_RECIPIENT.to_dict(),
        "modifier": {"add_ability_fragments": [ability_fragment_to_dict(fragment)]},
    }
    outer = runtime_handler_node(
        node_id=node_id, line=line, span=span, kind="static_ability", event=handler.event,
        compiled=("fixed-hand-ninjutsu-grant-v1", descriptor, handler.capability_dependencies),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="The hand grant requires its typed ninjutsu activation and ability layer",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    return outer, replace(inner, kind=GRANTED_ACTIVATED_ABILITY_KIND)
