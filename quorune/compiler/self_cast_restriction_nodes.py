from __future__ import annotations

"""Complete intrinsic prior-spell and current Snow-land casting clauses."""

from . import regex_working_set as re

from ..cast_timing import CAST_PERMISSION_ACTIVE_ZONE, OpponentUpkeepCastWindow, OpponentUpkeepWindow
from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec, block_declaration_timing_condition
from ..public_history_query_model import opponent_life_loss_including_departed_query
from ..rules.source_references import SourceReferenceSpec
from ..semantic_runtime.self_cast_restrictions import SelfCastRestrictionHandler
from ..semantic_runtime.context import SemanticNodeError
from .public_state_queries import fixed_public_state_condition
from .oracle_source_text import without_ability_word_label
from .static_runtime_nodes import runtime_handler_node


def _opponent_life_loss_cast_condition(material, *, source_name):
    if not material.casefold().rstrip(".").endswith("an opponent lost life this turn"):
        return None
    subject = SourceReferenceSpec(source_name).regex_pattern
    if not re.fullmatch(
        rf"(?:You can['’]t cast (?:this spell|{subject}) unless|Cast this spell only if) an opponent lost life this turn\.?",
        material, re.I,
    ):
        return None
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.HISTORY_COUNT_AT_LEAST, amount=1, schema_version=8,
        history_query=opponent_life_loss_including_departed_query(),
    )


def self_cast_restriction_nodes(*, node_id, line, material_line, span, source_name,
        capability_registry, capability_profile, residuals):
    material = without_ability_word_label(' '.join(material_line.strip().split()))
    if material is None:return None
    combat = re.fullmatch(r"Cast this spell only during the declare attackers step"
        r"(?P<attacked> and only if you've been attacked this step)?\.?",
        material, re.I)
    if combat is not None:
        from ..declare_attack_cast_model import DeclareAttackCastSpec
        from ..semantic_runtime.declare_attack_cast import DeclareAttackCastRestrictionHandler
        handler = DeclareAttackCastRestrictionHandler()
        descriptor = {'handler_id': handler.handler_id, 'schema_version': 1,
            'event': handler.event, 'restriction': DeclareAttackCastSpec(bool(combat['attacked'])).to_dict()}
        handler.validate(descriptor)
        return (runtime_handler_node(node_id=node_id, line=line, span=span,
            compiled=('declare-attackers-cast-restriction-v1', descriptor, handler.capability_dependencies),
            kind='static_ability', event=handler.event, active_zone=CAST_PERMISSION_ACTIVE_ZONE,
            dependency_reason='Printed combat casting requires its declared-player occurrence and shared cast owner',
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals),)
    block_window = {
        "cast this spell only during combat after blockers are declared": False,
        "cast this spell only during the declare blockers step": True,
    }.get(material.casefold().rstrip("."))
    surface = material.casefold().replace("’", "'").rstrip(".")
    upkeep_window = {
        "cast this spell only during an opponent's upkeep": OpponentUpkeepWindow.DURING,
        "cast this spell only during an opponent's turn after their upkeep step": OpponentUpkeepWindow.AFTER,
    }.get(surface)
    if upkeep_window is not None:
        condition = OpponentUpkeepCastWindow(upkeep_window)
        version = 4
    elif surface == "cast this spell only during your turn":
        condition = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_TURN)
        version = 3
    elif (condition := _opponent_life_loss_cast_condition(material, source_name=source_name)) is not None:
        version = 2
    elif block_window is not None:
        condition = block_declaration_timing_condition(declare_blockers_only=block_window)
        version = 2
    else:
        match = re.fullmatch(r"Cast this spell only if (?P<condition>.+?)\.?", material, re.I)
        if match is None:
            return None
        condition = fixed_public_state_condition(
            match['condition'].casefold().replace('another spell', 'a spell'), source_name=source_name)
        if condition is None:
            return None
        version = 1 if match['condition'].casefold() in {
            "you've cast another spell this turn", 'you control a snow land'} else 2
    handler = SelfCastRestrictionHandler()
    descriptor = {'handler_id': handler.handler_id, 'schema_version': handler.schema_version,
        'event': handler.event, 'source_condition': condition.to_dict()}
    if version > 1:
        descriptor['intrinsic_condition_version'] = version
    try:handler.validate(descriptor)
    except SemanticNodeError:return None
    return (runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=({
            1: 'fixed-self-cast-restriction-v1',
            2: 'fixed-self-cast-public-condition-v2',
            3: 'fixed-self-cast-own-turn-v3',
            4: 'fixed-self-cast-opponent-upkeep-v4',
        }[version], descriptor, handler.capability_dependencies),
        kind='static_ability', event=handler.event, active_zone=CAST_PERMISSION_ACTIVE_ZONE,
        dependency_reason='Intrinsic cast restrictions require their public-condition and proposal owners',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals),)
