from __future__ import annotations

from dataclasses import dataclass
from ..library_search_model import (LIBRARY_SEARCH_RULE_CAPABILITY,LIBRARY_SEARCH_TEMP_CAPABILITY,LIBRARY_SEARCH_RULE_HANDLER,
    LIBRARY_SEARCH_TEMP_OPERATION,LibrarySearchRuleSpec,ProhibitLibrarySearchIntent)
from .component_registry import exact_fields
from .context import SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True,slots=True)
class LibrarySearchRuleHandler:
    handler_id:str=LIBRARY_SEARCH_RULE_HANDLER
    schema_version:int=1
    family:str='permission.action.library-search-rule'
    event:str='action.permission'
    rule_references:tuple[str,...]=('101.2','701.23a','701.23b','701.23d','701.23f','701.23h','611.3b')
    capability_dependencies:tuple[str,...]=(LIBRARY_SEARCH_RULE_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','requires_complete_card_program','rule'},field='library search rule')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version']!=1
            or descriptor['event']!=self.event or descriptor['requires_complete_card_program'] is not True):
            raise SemanticNodeError('Library search rule identity or complete-source contract changed')
        try:return LibrarySearchRuleSpec.from_dict(descriptor['rule'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        from .action_permissions import StaticActionPermission,ActionPermissionKind
        rule=self.validate(descriptor)
        return tuple(StaticActionPermission(kind=ActionPermissionKind.LIBRARY_SEARCH_RULE,player=player,
            source_ref=context.source_ref,source_logical_object_id=context.source_logical_object_id,
            source_semantic_key=context.source_semantic_key,library_search_rule=rule)
            for player in context.active_seats if rule.player_scope=='all' or player!=context.source_controller)


@dataclass(frozen=True,slots=True)
class ProhibitLibrarySearchHandler:
    operation:str=LIBRARY_SEARCH_TEMP_OPERATION
    handler_id:str='generic.prohibit-library-search-this-turn.v1'
    schema_version:int=1
    family:str='library.search.prohibition.temporary'
    rule_references:tuple[str,...]=('101.2','609.3','701.23a','611.2c','514.2')
    capability_dependencies:tuple[str,...]=(LIBRARY_SEARCH_TEMP_CAPABILITY,)

    def lower(self,effect,context):
        if set(effect)!={'op','actor'} or effect['op']!=self.operation or effect['actor']!=context.actor or context.source is None:
            raise SemanticNodeError('Resolved library prohibition needs its owned resolving context')
        return IntentPlan(operation=self.operation,handler_id=self.handler_id,
            intents=(ProhibitLibrarySearchIntent(context.actor,context.source.stack_ref),))
