"""Lower a closed printed graveyard Unearth grant."""

from dataclasses import replace

from . import regex_working_set as re
from .static_runtime_nodes import runtime_handler_node
from .unearth_nodes import ordinary_unearth_keyword_node
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, ability_fragment_to_dict
from ..granted_activation_model import GRANTED_UNEARTH_VERSION, GrantedActivatedAbilitySpec
from ..semantic_runtime.graveyard_unearth_grants import GraveyardUnearthGrantHandler
from ..rules.graveyard_unearth_grant import GraveyardUnearthGrant, graveyard_unearth_recipient
from ..unearth import compile_ordinary_unearth_ability


_GRANT = re.compile(
    r"Each (?:(?P<subtype>Sliver) )?(?P<card_type>creature|artifact) card in your graveyard"
    r"(?: that's a (?P<party>Cleric, Rogue, Warrior, and/or Wizard))? has "
    r"(?P<ability>unearth (?:\{[^{}]+\})+)\.", re.IGNORECASE,
)


def compile_graveyard_unearth_grant(*, material_line, oracle_line, line_index):
    match = _GRANT.fullmatch(material_line.strip())
    if match is None:
        return None
    subtype = match["subtype"]
    party = match["party"] is not None
    try:
        recipient = graveyard_unearth_recipient(
            match["card_type"].casefold(), subtype=subtype.casefold() if subtype else None, party=party,
        )
    except ValueError:
        return None
    ability = compile_ordinary_unearth_ability(
        material_line=match["ability"], oracle_line=oracle_line, line_index=line_index,
    )
    return GraveyardUnearthGrant(recipient, ability) if ability is not None else None


def compile_graveyard_unearth_nodes(*, record, face_id, node_id, line, material_line, span,
                                   capability_registry, capability_profile, residuals):
    spec = compile_graveyard_unearth_grant(material_line=material_line, oracle_line=line,
                                         line_index=span.line - 1)
    if spec is None or capability_registry is None:
        return None
    from .attached_granted_ability_nodes import GRANTED_ACTIVATED_ABILITY_KIND, attached_granted_program_ability_id

    ability_id = attached_granted_program_ability_id(
        kind=GRANTED_ACTIVATED_ABILITY_KIND, face_id=face_id, line=span.line,
    )
    ability = spec.ability.to_activated_ability()
    fragment = GrantedActivatedAbilitySpec(
        ability_id=ability_id, semantic_key=f"{record.oracle_id}:{ability_id}",
        cost_text=ability.cost_text, effect_text=ability.effect_text, mana=tuple(ability.mana.items()),
        sorcery_speed=True, schema_version=GRANTED_UNEARTH_VERSION,
    )
    handler = GraveyardUnearthGrantHandler()
    descriptor = {"handler_id": handler.handler_id, "schema_version": handler.schema_version,
                  "event": handler.event, "predicate": spec.recipient.to_dict(),
                  "modifier": {"add_ability_fragments": [ability_fragment_to_dict(fragment)]}}
    inner_text = f"Unearth {ability.cost_text}"
    inner = ordinary_unearth_keyword_node(
        node_id=f"{node_id}:granted", line=inner_text, material_line=inner_text, span=span,
        mechanics=("unearth",), capability_registry=capability_registry, capability_profile=capability_profile,
        residuals=residuals,
    )
    if inner is None or not inner.exact:
        return None
    outer = runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=("fixed-graveyard-unearth-grant-v1", descriptor, handler.capability_dependencies),
        kind="static_ability", event=handler.event, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="The graveyard grant depends on the typed Unearth activation",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    return outer, replace(inner, kind=GRANTED_ACTIVATED_ABILITY_KIND)
