"""CR107.3m result amounts read only from sealed self-entry information."""
from dataclasses import dataclass
from collections.abc import Mapping
from .ravenous_model import EntryXInformation,ENTRY_X_FIELD
from .query_effect_amount_model import PublicQueryAmountError

ENTRY_X_AMOUNT_KIND='source_entry_x_amount'
ENTRY_X_AMOUNT_CAPABILITY='quantity_expression.source_entry_x'
ENTRY_X_AMOUNT_MECHANIC='source-entry-x-result-amount'
ENTRY_HALF_X_FOOD_CAPABILITY = 'quantity_expression.source_entry_half_x_food'


@dataclass(frozen=True,slots=True)
class EntryXAmountSpec:
    coefficient: int=1
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in {1, 2}
                or type(self.coefficient) is not int or self.coefficient not in {-3,-2,-1,1,2,3}
                or (self.schema_version == 2 and self.coefficient != 1)):
            raise PublicQueryAmountError('Entry X amount requires one versioned bounded coefficient')

    def to_dict(self):return {'kind':ENTRY_X_AMOUNT_KIND,'schema_version':self.schema_version,'coefficient':self.coefficient}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'kind','schema_version','coefficient'} or value['kind']!=ENTRY_X_AMOUNT_KIND:
            raise PublicQueryAmountError('Entry X amount has a closed schema')
        return cls(coefficient=value['coefficient'],schema_version=value['schema_version'])


def entry_x_amount_dependencies(value):
    if isinstance(value,Mapping):
        if value.get('kind')==ENTRY_X_AMOUNT_KIND:
            spec = EntryXAmountSpec.from_dict(value)
            return (ENTRY_X_AMOUNT_CAPABILITY, ENTRY_HALF_X_FOOD_CAPABILITY) if spec.schema_version == 2 else (ENTRY_X_AMOUNT_CAPABILITY,)
        return tuple(sorted({cap for child in value.values() for cap in entry_x_amount_dependencies(child)}))
    if isinstance(value,(tuple,list)):
        return tuple(sorted({cap for child in value for cap in entry_x_amount_dependencies(child)}))
    return ()


def entry_half_x_food_effect():
    from .fixed_token_production import food_token_effect

    return {**food_token_effect(2), "quantity": EntryXAmountSpec(schema_version=2).to_dict()}


def entry_x_amount_runtime_dependencies(program):
    required=entry_x_amount_dependencies(program.effects)
    if required and (program.event!='permanent.enter.self' or program.active_zone!='battlefield'):
        raise PublicQueryAmountError('Entry X result requires its self-entry program envelope')
    if ENTRY_HALF_X_FOOD_CAPABILITY in required:
        from .replacement.immutable import thaw_value

        if (thaw_value(program.effects) != [entry_half_x_food_effect()]
                or program.target_schema is not None or program.event_condition is not None):
            raise PublicQueryAmountError("Rounded half-entry-X requires one exact untargeted Food result")
    return required


def resolve_entry_x_amount(value, item):
    return resolve_entry_x_for_occurrence(value, source_object_id=item.source_object_id, context=item.context)


def resolve_entry_x_for_occurrence(value, *, source_object_id, context):
    spec = EntryXAmountSpec.from_dict(value)
    info = EntryXInformation.from_dict(context.get(ENTRY_X_FIELD))
    if (info.object_id != source_object_id or info.logical_object_id != context.get("source_logical_object_id")
            or context.get("event") != "permanent.enter"):
        raise PublicQueryAmountError("Entry X result lost its original committed entry identity")
    if spec.schema_version == 2:
        return (info.amount + 1) // 2
    return spec.coefficient * info.amount
