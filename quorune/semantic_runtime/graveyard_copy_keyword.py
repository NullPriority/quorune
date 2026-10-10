from __future__ import annotations

"""Complete-card marker for graveyard source-copy keyword activation."""

from dataclasses import dataclass
from typing import Mapping
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .component_registry import exact_fields
from .context import SemanticNodeError

GRAVEYARD_COPY_KEYWORD_CAPABILITY='activation.graveyard_copy_keyword.fixed_mana'
GRAVEYARD_COPY_KEYWORD_HANDLER='ability.activated.graveyard-copy-keyword.v1'


@dataclass(frozen=True,slots=True)
class GraveyardCopyKeywordHandler:
    handler_id: str=GRAVEYARD_COPY_KEYWORD_HANDLER
    schema_version: int=1
    family: str='ability.activated.graveyard_copy_keyword'
    event: str='activate'
    rule_references: tuple[str,...]=('702.128a','702.129a','707.9','602.2b')
    capability_dependencies: tuple[str,...]=(GRAVEYARD_COPY_KEYWORD_CAPABILITY,)

    def validate(self,value):
        if not isinstance(value,Mapping):raise SemanticNodeError('Graveyard copy keyword descriptor must be an object')
        exact_fields(value,{'handler_id','schema_version','event','keyword',REQUIRES_COMPLETE_CARD_PROGRAM_FIELD},field='graveyard copy keyword')
        if value['handler_id']!=self.handler_id or type(value['schema_version']) is not int or value['schema_version']!=1 or value['event']!=self.event or value[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True:
            raise SemanticNodeError('Graveyard copy keyword identity or admission is malformed')
        if type(value['keyword']) is not str or value['keyword'] not in {'embalm','eternalize'}:raise SemanticNodeError('Graveyard copy keyword is unsupported')
        return value['keyword']

    def lower(self,value,context):
        self.validate(value)
        return ()
