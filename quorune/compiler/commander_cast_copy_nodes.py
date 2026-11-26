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

def cast_source_copy_node(*, printed_card_types, source_name, **scope):
    """Lower the exact cast-origin copy production proved by the source line."""
    for compiler in (_fixed_source_target_copy_node, _creature_set_spell_copy_node):
        node = compiler(source_name=source_name, **scope)
        if node is not None:
            return node
    for compiler in (_direct_cast_token_node, _cast_target_token_node):
        node = compiler(printed_card_types=printed_card_types, **scope)
        if node is not None:
            return node
    return commander_cast_copy_node(**scope)


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
    from ..event_spell_copy_model import cast_spell_token_condition
    from ..rules.event_spell_copy import event_spell_copy_runtime_dependencies
    from ..token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec

    recipe = TokenCopyRecipeSpec("event_spell",TokenCopyExceptionSpec(remove_legendary=True),schema_version=4)
    effect = {"op":"create_token","controller":"$controller","quantity":1,"copy_spec":recipe.to_dict()}
    return _cast_copy_trigger_node(node_id=node_id, line=line, span=span, condition=cast_spell_token_condition(),
        effect=effect, active_zone="stack", template_id="self-cast-permanent-token-copy-v1",
        dependencies=event_spell_copy_runtime_dependencies,
        dependency_reason="Direct cast token copies require the exact permanent spell and canonical token creation",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)


def _cast_target_token_node(*, printed_card_types, node_id, line, material_line, span,
                            capability_registry, capability_profile, residuals):
    if not re.fullmatch(
        r"Whenever you cast a spell that targets only a single artifact or creature you control, "
        r"create a token that['’]s a copy of that artifact or creature, except it['’]s not legendary\.",
        material_line.strip(), re.I,
    ):
        return None
    if not {t.casefold() for t in printed_card_types}.intersection(
        {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
    ):
        return None
    from ..rules.cast_target_copy import (
        cast_target_copy_condition, cast_target_copy_effect, cast_target_copy_runtime_dependencies,
    )
    return _cast_copy_trigger_node(node_id=node_id, line=line, span=span, condition=cast_target_copy_condition(),
        effect=cast_target_copy_effect(), active_zone="battlefield", template_id="sole-cast-target-token-copy-v1",
        dependencies=cast_target_copy_runtime_dependencies,
        dependency_reason="Cast-target token copies require the original sole qualifying battlefield target",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)


def _fixed_source_target_copy_node(*, source_name, node_id, line, material_line, span,
                                   capability_registry, capability_profile, residuals):
    from ..event_spell_copy_model import EVENT_SPELL_COPY_MECHANIC
    from ..rules.cast_source_spell_copy import (
        source_cast_copy_condition, source_cast_copy_effect, source_cast_copy_program_dependencies,
    )

    names = tuple(dict.fromkeys((source_name, source_name.split(",", 1)[0], "this creature", "this permanent")))
    for reference in names:
        target = re.escape(reference)
        if re.fullmatch(
            rf"Whenever a player casts a spell that targets only a single creature other than {target}, "
            rf"you may copy that spell\. The copy targets {target}\.",
            material_line.strip(), re.I,
        ):
            return _cast_copy_trigger_node(
                node_id=node_id, line=line, span=span, condition=source_cast_copy_condition(),
                effect=source_cast_copy_effect(), active_zone="battlefield",
                template_id="cast-spell-copy-fixed-source-target-v1", dependencies=source_cast_copy_program_dependencies,
                dependency_reason="Fixed spell copy requires the original cast and each legal source-target slot",
                capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
                mechanics=(EVENT_SPELL_COPY_MECHANIC, "cr-707-copying-objects", "cr-115-targets",
                           "trigger-event-normalized-spell-cast", "cr-603-handling-triggered-abilities"),
            )
    return None


def _creature_set_spell_copy_node(*, source_name, node_id, line, material_line, span,
                                  capability_registry, capability_profile, residuals):
    from ..event_spell_copy_model import EVENT_SPELL_COPY_MECHANIC
    from ..rules.cast_creature_set_spell_copy import (
        ABILITY_CONTROLLER, OBSERVED_CASTER, cast_creature_set_copy_condition,
        cast_creature_set_copy_effect, cast_creature_set_copy_program_dependencies,
    )

    names = "|".join(re.escape(name) for name in dict.fromkeys((
        source_name, source_name.split(",", 1)[0], "this creature", "this permanent",
    )))
    copy_result = r"that spell for each other creature"
    if re.fullmatch(
        rf"Whenever you cast an instant or sorcery spell that targets only (?:{names}), "
        rf"copy {copy_result} you control that the spell could target\. "
        r"Each copy targets a different one of those creatures\.",
        material_line.strip(), re.I,
    ):
        scope = ABILITY_CONTROLLER
    elif re.fullmatch(
        rf"Whenever a player casts an instant or sorcery spell that targets only (?:{names}), "
        rf"that player copies {copy_result} they control that the spell could target\. "
        r"Each copy targets a different one of those creatures\.",
        material_line.strip(), re.I,
    ):
        scope = OBSERVED_CASTER
    else:
        return None
    return _cast_copy_trigger_node(
        node_id=node_id, line=line, span=span, condition=cast_creature_set_copy_condition(scope),
        effect=cast_creature_set_copy_effect(scope), active_zone="battlefield",
        template_id="cast-spell-copy-for-eligible-creatures-v1",
        dependencies=cast_creature_set_copy_program_dependencies,
        dependency_reason="Creature-set spell copies require the actual cast, all legal target slots and ordered placement",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
        mechanics=(EVENT_SPELL_COPY_MECHANIC, "cr-707-copying-objects", "cr-115-targets",
                   "trigger-event-normalized-spell-cast", "cr-603-handling-triggered-abilities"),
    )


def _cast_copy_trigger_node(*, node_id, line, span, condition, effect, active_zone, template_id,
                     dependencies, dependency_reason, capability_registry, capability_profile, residuals, mechanics=None):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..fixed_token_production import FIXED_TOKEN_PRODUCTION_MECHANIC_ID
    from ..semantics import SemanticProgram
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode, append_residual

    program = SemanticProgram(
        key=node_id, label=line, effects=[effect], active_zone=active_zone,
        event="spell.cast", event_condition=condition,
    )
    gate = explicit_capabilities_gate(dependencies(program),
        capability_registry=capability_registry,capability_profile=capability_profile)
    blocked = (append_residual(residuals,kind="dependency_contract",text=line,span=span,
        reason=dependency_reason,blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(node_id=node_id,kind="triggered_ability",text=line,span=span,active_zone=active_zone,
        event="spell.cast",event_condition=condition,target_schema=None,effects=(effect,),
        lowerable=True,exact=not blocked,template_id=template_id,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),residual_ids=blocked,
        mechanics=mechanics if mechanics is not None else (
            FIXED_TOKEN_PRODUCTION_MECHANIC_ID,"fixed-token-copy","cr-707-copying-objects",
            "cr-111-tokens","trigger-event-normalized-spell-cast","cr-603-handling-triggered-abilities"),
        capability_dependencies=gate.capabilities,capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
