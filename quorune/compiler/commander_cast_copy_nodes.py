from __future__ import annotations

"""Complete source-spanned production for commander-cast-scaled spell copies."""

from . import regex_working_set as re
from ..ability_fragments import SpellCastKeywordTriggerKind, SpellCastKeywordTriggerSpec, ability_fragment_to_dict
from ..commander_cast_count_model import COMMANDER_CAST_COUNT_CAPABILITY
from ..semantic_runtime.commander_cast_copies import COMMANDER_CAST_COPY_CAPABILITY, COMMANDER_CAST_COPY_HANDLER
from .static_runtime_nodes import runtime_handler_node


def commander_cast_copy_node(*, node_id, line, material_line, span,
                            capability_registry, capability_profile, residuals):
    match = re.fullmatch(r"When you cast this spell, copy it for each time you['’]ve cast "
        r"your commander from the command zone this game\."
        r"(?P<retarget> You may choose new targets for the copies\.)?", material_line.strip(), re.I)
    if match is None:
        return None
    kind = (SpellCastKeywordTriggerKind.COMMANDER_CAST_COPY_NEW_TARGETS if match['retarget']
        else SpellCastKeywordTriggerKind.COMMANDER_CAST_COPY)
    descriptor = {'handler_id': COMMANDER_CAST_COPY_HANDLER, 'schema_version': 1, 'event': 'spell.cast',
        'fragment': ability_fragment_to_dict(SpellCastKeywordTriggerSpec(kind))}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('commander-cast-scaled-spell-copy-v1', descriptor,
            (COMMANDER_CAST_COPY_CAPABILITY, COMMANDER_CAST_COUNT_CAPABILITY)),
        kind='triggered_ability', event='spell.cast', active_zone='stack',
        runtime_coverage=('typed_commander_cast_copy_resolution',),
        dependency_reason='Commander-cast spell copies need a resolution-time public count and immutable copy template',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)

def cast_source_copy_node(*, printed_card_types, **scope):
    """Keep the source spell's direct token result separate from stack copies."""
    direct = _direct_cast_token_node(printed_card_types=printed_card_types, **scope)
    return direct if direct is not None else commander_cast_copy_node(**scope)


def _direct_cast_token_node(*, printed_card_types, node_id, line, material_line, span,
                           capability_registry, capability_profile, residuals):
    if not re.fullmatch(
        r"When you cast this spell, create a token that['’]s a copy of it, except "
        r"(?:it isn['’]t|it is not|the token isn['’]t) legendary\.",
        material_line.strip(), re.I,
    ):
        return None
    types = {value.casefold() for value in printed_card_types}
    if (not types.intersection({"artifact","battle","creature","enchantment","planeswalker"})
            or types.intersection({"instant","sorcery","land"})):
        return None
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..event_spell_copy_model import cast_spell_token_condition
    from ..fixed_token_production import FIXED_TOKEN_PRODUCTION_MECHANIC_ID
    from ..rules.event_spell_copy import event_spell_copy_runtime_dependencies
    from ..token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode, append_residual
    from types import SimpleNamespace

    recipe = TokenCopyRecipeSpec("event_spell",TokenCopyExceptionSpec(remove_legendary=True),schema_version=4)
    effect = {"op":"create_token","controller":"$controller","quantity":1,"copy_spec":recipe.to_dict()}
    condition = cast_spell_token_condition()
    scope = SimpleNamespace(effects=(effect,),event="spell.cast",event_condition=condition,
                            target_schema=None,active_zone="stack",coverage=())
    gate = explicit_capabilities_gate(event_spell_copy_runtime_dependencies(scope),
        capability_registry=capability_registry,capability_profile=capability_profile)
    blocked = (append_residual(residuals,kind="dependency_contract",text=line,span=span,
        reason="Direct cast token copies require the exact permanent spell and canonical token creation",
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(node_id=node_id,kind="triggered_ability",text=line,span=span,active_zone="stack",
        event="spell.cast",event_condition=condition,target_schema=None,effects=(effect,),
        lowerable=True,exact=not blocked,template_id="self-cast-permanent-token-copy-v1",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),residual_ids=blocked,
        mechanics=(FIXED_TOKEN_PRODUCTION_MECHANIC_ID,"fixed-token-copy","cr-707-copying-objects",
                   "cr-111-tokens","trigger-event-normalized-spell-cast","cr-603-handling-triggered-abilities"),
        capability_dependencies=gate.capabilities,capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
