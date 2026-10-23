from __future__ import annotations

from dataclasses import dataclass
from ..counter_names import ALL_COUNTER_AMOUNTS
from ..rules.all_kind_counter_shapes import ALL_KIND_COUNTER_CAPABILITY,validate_all_kind_counter_effect
from .context import SemanticNodeError
from .intents import IntentPlan,PlaceCountersIntent


@dataclass(frozen=True,slots=True)
class AllKindCounterDoublingHandler:
    handler_id:str='generic.all-kind-counter-doubling.v1'
    schema_version:int=1
    family:str='effect.counter-all-kind-doubling'
    operation:str='double_counters_on_targets'
    rule_references:tuple[str,...]=('115.3','115.6','122.6','701.10e','616.1')
    capability_dependencies:tuple[str,...]=(ALL_KIND_COUNTER_CAPABILITY,)

    def lower(self,effect,context):
        try:validate_all_kind_counter_effect(effect,resolved=True)
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc
        refs=effect['cards'];source=effect['source'];reason=effect.get('reason',context.default_reason)
        selections=effect.get('_replacement_selections',())
        if (not isinstance(refs,(list,tuple)) or any(type(ref) is not str or not ref for ref in refs)
                or len(set(refs))!=len(refs) or len(refs)>effect['maximum_targets']
                or type(source) is not str or not source or type(reason) is not str or not reason
                or not isinstance(selections,(list,tuple))):raise SemanticNodeError('All-kind counter subjects/source/replacements are malformed')
        if not refs:return IntentPlan(self.operation,self.handler_id,(),result_shape='by_player')
        return IntentPlan(self.operation,self.handler_id,(PlaceCountersIntent(actor=context.actor,object_refs=tuple(refs),
            counter_name=None,amount=ALL_COUNTER_AMOUNTS,reason=reason,source_ref=source,replacement_selections=tuple(selections)),))
