from __future__ import annotations

"""Pure replacement lowering from immutable external entry-counter source data."""

from dataclasses import dataclass, replace
from ..entry_designations import ENTRY_DESIGNATION_CAPABILITY
from ..external_entry_counter_model import (
    EXTERNAL_CHOSEN_SUBTYPE_ENTRY_COUNTER_CAPABILITY, EXTERNAL_CHOSEN_SUBTYPE_ENTRY_COUNTER_HANDLER,
    EXTERNAL_ENTRY_COUNTER_CAPABILITY, EXTERNAL_ENTRY_COUNTER_HANDLER,
    ExternalEntryCounterSpec, ExternalEntryCounterSourceSnapshot,
    EXTERNAL_NONTOKEN_ENTRY_COUNTER_CAPABILITY, EXTERNAL_NONTOKEN_ENTRY_COUNTER_HANDLER,
)
from ..replacement import ReplacementClass,ReplacementEffect
from ..replacement.operations import AddAffectedObjectCounter,GrantAffectedObjectSubtype
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class ExternalEntryCounterHandler:
    handler_id: str=EXTERNAL_ENTRY_COUNTER_HANDLER
    schema_version: int=1
    family: str='replacement.zone.external_entry_counter'
    event: str='zone.change'
    rule_references: tuple[str,...]=('122.6','122.6a','614.1c','614.5','614.12','616.1')
    capability_dependencies: tuple[str,...]=(EXTERNAL_ENTRY_COUNTER_CAPABILITY,'counter.placement.quantity_replacement')

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','entry_counter'},field='external entry counter')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=self.schema_version or descriptor['event']!=self.event):
            raise SemanticNodeError('External entry-counter handler identity, version or event changed')
        try:return ExternalEntryCounterSpec.from_dict(descriptor['entry_counter'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def _spec_with_source(self, spec, source_context):
        return spec

    def source_replacement_effect(self,descriptor,*,source_ref,source_controller,component_id,source_context):
        spec=self.validate(descriptor)
        if not isinstance(source_context,ExternalEntryCounterSourceSnapshot) or source_context.controller!=source_controller:
            raise SemanticNodeError('External entry counters require the current typed source snapshot')
        if any(type(v) is not str or not v for v in (source_ref,source_controller,component_id)):
            raise SemanticNodeError('External entry counters require current source and component identities')
        if spec.amount_source=='fixed_one' and source_context.amount!=1:
            raise SemanticNodeError('Fixed-one entry counter source snapshot has an inconsistent amount')
        spec = self._spec_with_source(spec, source_context)
        if spec is None:
            return None
        amount=source_context.nonnegative_amount
        if amount==0 and not spec.add_mutant:return None
        predicate={'contains_all':['creature',*([spec.required_subtype] if spec.required_subtype else [])]}
        if spec.excluded_subtype:predicate['contains_none']=[spec.excluded_subtype]
        return ReplacementEffect(effect_id=f'{self.handler_id}:{source_ref}:{component_id}',source_id=source_ref,
            event_kind='zone.change',replacement_class=ReplacementClass.OTHER,
            conditions={'destination':{'eq':'battlefield'},'destination_controller':{'eq':source_controller},
                'object_ref':{'not_in':[source_ref]},'object_types':predicate},
            operations=((*((GrantAffectedObjectSubtype('mutant'),) if spec.add_mutant else ()),
                *((AddAffectedObjectCounter('+1/+1',amount,source_controller,source_ref),) if amount>0 else ()))),
            label=f'{source_ref}: enter with additional +1/+1 counters')



@dataclass(frozen=True, slots=True)
class ExternalChosenSubtypeEntryCounterHandler(ExternalEntryCounterHandler):
    handler_id: str = EXTERNAL_CHOSEN_SUBTYPE_ENTRY_COUNTER_HANDLER
    schema_version: int = 2
    rule_references: tuple[str, ...] = ("122.6", "122.6a", "607.2d", "614.1c", "614.5", "614.12", "616.1")
    capability_dependencies: tuple[str, ...] = (
        EXTERNAL_ENTRY_COUNTER_CAPABILITY, "counter.placement.quantity_replacement",
        ENTRY_DESIGNATION_CAPABILITY, EXTERNAL_CHOSEN_SUBTYPE_ENTRY_COUNTER_CAPABILITY,
    )

    def validate(self, descriptor):
        spec = ExternalEntryCounterHandler.validate(self, descriptor)
        if spec != ExternalEntryCounterSpec():
            raise SemanticNodeError("Chosen subtype entry counters require the exact fixed additional counter")
        return spec

    def _spec_with_source(self, spec, source_context):
        subtype = source_context.chosen_creature_type
        return replace(spec, required_subtype=subtype) if subtype is not None else None


@dataclass(frozen=True, slots=True)
class ExternalNontokenEntryCounterHandler(ExternalEntryCounterHandler):
    handler_id: str = EXTERNAL_NONTOKEN_ENTRY_COUNTER_HANDLER
    schema_version: int = 3
    capability_dependencies: tuple[str, ...] = (
        EXTERNAL_ENTRY_COUNTER_CAPABILITY, "counter.placement.quantity_replacement",
        EXTERNAL_NONTOKEN_ENTRY_COUNTER_CAPABILITY,
    )

    def validate(self, descriptor):
        spec = ExternalEntryCounterHandler.validate(self, descriptor)
        if spec != ExternalEntryCounterSpec():
            raise SemanticNodeError("Nontoken entry counters require the exact fixed additional counter")
        return spec

    def source_replacement_effect(self, descriptor, *, source_ref, source_controller, component_id, source_context):
        effect = ExternalEntryCounterHandler.source_replacement_effect(
            self, descriptor, source_ref=source_ref, source_controller=source_controller,
            component_id=component_id, source_context=source_context,
        )
        return replace(effect, conditions={**effect.conditions, "object_kind": {"eq": "card"}})
