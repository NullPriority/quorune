from __future__ import annotations

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..object_predicate import ObjectQuerySpec
from ..static_player_restriction_model import (
    STATIC_PLAYER_RESTRICTION_CAPABILITY, StaticPlayerRestrictionKind, StaticPlayerRestrictionSpec,
)
from .static_cast_rule_templates import _descriptor
from .activation_restriction_templates import _fixed_descriptor
from .static_runtime_nodes import runtime_handler_node
from ..semantic_runtime.static_cast_rules import (
    STATIC_CAST_PROHIBITION_HANDLER_ID, STATIC_CAST_LIMIT_HANDLER_ID,
    StaticCastRuleKind, StaticCastPlayerScope,
)


def static_player_restriction_template(text: str):
    """Lower fixed player restrictions through existing cast/activation owners."""
    normalized = ' '.join(text.casefold().split())
    kind = None
    abilities = False
    attached = normalized == "as long as this equipment is attached to a creature, your opponents can't cast spells during your turn."
    if normalized == 'players can cast spells only during their own turns.':
        kind = StaticPlayerRestrictionKind.ACTOR_OTHER_TURN
    elif normalized == 'players can cast spells and activate abilities only during their own turns.':
        kind = StaticPlayerRestrictionKind.ACTOR_OTHER_TURN
        abilities = True
    elif normalized == "your opponents can't cast spells from anywhere other than their hands.":
        kind = StaticPlayerRestrictionKind.OPPONENT_NONHAND
    elif normalized == "during your turn, your opponents can't cast spells or activate abilities of artifacts, creatures, or enchantments.":
        kind = StaticPlayerRestrictionKind.SOURCE_TURN_OPPONENTS
        abilities = True
    elif attached:
        kind = StaticPlayerRestrictionKind.SOURCE_TURN_OPPONENTS
    elif normalized == "each player who has cast a nonartifact spell this turn can't cast additional nonartifact spells.":
        kind = StaticPlayerRestrictionKind.NONARTIFACT_LIMIT
    if kind is None:
        return None
    spec=StaticPlayerRestrictionSpec(kind,schema_version=2,requires_source_attached_creature=True) if attached else StaticPlayerRestrictionSpec(kind)
    limited = kind is StaticPlayerRestrictionKind.NONARTIFACT_LIMIT
    opponents = kind in {StaticPlayerRestrictionKind.OPPONENT_NONHAND, StaticPlayerRestrictionKind.SOURCE_TURN_OPPONENTS}
    cast = _descriptor(handler_id=STATIC_CAST_LIMIT_HANDLER_ID if limited else STATIC_CAST_PROHIBITION_HANDLER_ID,
        kind=StaticCastRuleKind.CAST_LIMIT if limited else StaticCastRuleKind.CAST_PROHIBITION,
        scope=StaticCastPlayerScope.SOURCE_OPPONENTS if opponents else StaticCastPlayerScope.ALL_PLAYERS,
        queries=(ObjectQuerySpec(excluded_types=('artifact', 'land') if limited else ('land',)).to_dict(),),
        maximum_per_turn=1 if limited else None)
    cast.update(player_restriction=spec.to_dict())
    handlers = [cast]
    caps = [STATIC_PLAYER_RESTRICTION_CAPABILITY, 'casting.limit.once_per_turn_static' if limited else 'casting.prohibition.fixed_public']
    if abilities:
        city = kind is StaticPlayerRestrictionKind.ACTOR_OTHER_TURN
        activation = _fixed_descriptor(relation='actor' if city else 'query',
            controller_relation='any' if city else 'source_opponents',
            predicate=ObjectQuerySpec() if city else ObjectQuerySpec(zones=('battlefield',), types_any=('artifact', 'creature', 'enchantment')))
        activation.update(player_restriction=spec.to_dict())
        handlers.append(activation)
        caps.append('activation.restriction.fixed_public_query')
    return 'static-player-restriction-v1', tuple(handlers), tuple(caps)


def static_player_restriction_nodes(*, node_id, line, material_line, span, capability_registry,
        capability_profile, residuals):
    compiled = static_player_restriction_template(material_line)
    if compiled is None:
        return None
    return tuple(runtime_handler_node(node_id=node_id+':player-restriction:'+('cast' if index == 0 else 'activation'), line=line, span=span,
        compiled=(compiled[0], handler, compiled[2]), kind='static_ability', event=handler['event'],
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Static player restrictions require complete casting and activation authority',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
        for index, handler in enumerate(compiled[1]))
