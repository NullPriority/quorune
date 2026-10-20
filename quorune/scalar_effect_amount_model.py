from __future__ import annotations

"""Closed scalar producers consumed by the existing result-amount boundary."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .query_effect_amount_model import PublicQueryAmountError
from .counter_names import normalized_counter_name
from .public_characteristic_aggregate_model import PublicCharacteristicAggregateSpec
from .multikicker_model import FixedManaMultikickerSpec

SCALAR_AMOUNT_KIND = "scalar_effect_amount"
SCALAR_AMOUNT_CAPABILITY = "quantity_expression.scalar_effect_amount"
SCALAR_AMOUNT_MECHANIC = "scalar-effect-amount"
SCALAR_REFERENCE_CONTEXT = "scalar_reference_snapshots"
TARGET_HAND_COUNT_MECHANIC='target-player-hand-count-amount'
TARGET_HAND_COUNT_CAPABILITY='quantity_expression.target_player_hand_count'


class ScalarAmountOrigin(StrEnum):
    MULTIKICKER_COUNT = 'multikicker_count'
    TARGET_HAND_COUNT='target_player_hand_count'
    PUBLIC_AGGREGATE = 'public_characteristic_aggregate'
    SOURCE = "source_characteristic"
    TARGET = "target_characteristic"
    EVENT_OBJECT = "event_object_characteristic"
    EVENT_AMOUNT = "committed_event_amount"
    HISTORY = "public_turn_history"


CHARACTERISTICS = frozenset({"power", "toughness", "mana_value"})
HISTORY_FACTS = frozenset({
    "life_gained", "life_lost", "spells_cast", "cards_drawn",
    "permanents_sacrificed", "creatures_died", "creatures_entered",
    "cards_discarded",
})


@dataclass(frozen=True, slots=True)
class ScalarEffectAmountSpec:
    origin: ScalarAmountOrigin
    characteristic: str | None = None
    history_fact: str | None = None
    coefficient: int = 1
    binding_id: str | None = None
    schema_version: int = 1
    counter_name: str | None = None
    aggregate: PublicCharacteristicAggregateSpec | None = None
    multikicker: FixedManaMultikickerSpec | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in {1,2,3,4,5}:
            raise PublicQueryAmountError("Unsupported scalar effect amount version")
        if (self.schema_version == 5) != (self.origin is ScalarAmountOrigin.MULTIKICKER_COUNT):
            raise PublicQueryAmountError('Multikicker count requires its explicit scalar version')
        if self.schema_version == 5:
            if not isinstance(self.multikicker, FixedManaMultikickerSpec):
                raise PublicQueryAmountError('Multikicker count requires its linked fixed cost instance')
        elif self.multikicker is not None:
            raise PublicQueryAmountError('Other scalar origins cannot carry a Multikicker instance')
        if (self.schema_version==4)!=(self.origin is ScalarAmountOrigin.TARGET_HAND_COUNT):
            raise PublicQueryAmountError('Target hand count requires its explicit scalar version')
        if (self.schema_version==3)!=(self.origin is ScalarAmountOrigin.PUBLIC_AGGREGATE):
            raise PublicQueryAmountError('Aggregate scalar origin requires its explicit version')
        if self.schema_version==3:
            if not isinstance(self.aggregate,PublicCharacteristicAggregateSpec):
                raise PublicQueryAmountError('Aggregate scalar requires its typed public query')
        elif self.aggregate is not None:
            raise PublicQueryAmountError('Legacy scalar amounts cannot carry an aggregate')
        counter_origin = self.schema_version == 2
        if counter_origin:
            if self.origin is not ScalarAmountOrigin.SOURCE or self.characteristic is not None or self.history_fact is not None:
                raise PublicQueryAmountError("Counter scalar requires only its source origin")
            if type(self.counter_name) is not str or normalized_counter_name(self.counter_name) != self.counter_name:
                raise PublicQueryAmountError("Counter scalar requires a canonical counter name")
        elif self.counter_name is not None:
            raise PublicQueryAmountError("Legacy scalar amounts do not carry counters")
        if not isinstance(self.origin, ScalarAmountOrigin):
            raise PublicQueryAmountError("Scalar amount requires a typed origin")
        if type(self.coefficient) is not int or self.coefficient not in {-2, -1, 1, 2}:
            raise PublicQueryAmountError("Scalar result coefficient is outside the closed vocabulary")
        if self.binding_id is not None and (type(self.binding_id) is not str or not self.binding_id):
            raise PublicQueryAmountError("Scalar declaration identity must be nonempty")
        characteristic_origin = self.origin in {
            ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.TARGET, ScalarAmountOrigin.EVENT_OBJECT,
        }
        if not counter_origin and characteristic_origin != (type(self.characteristic) is str and self.characteristic in CHARACTERISTICS):
            raise PublicQueryAmountError("Scalar characteristic does not match its origin")
        if (not characteristic_origin or counter_origin) and self.characteristic is not None:
            raise PublicQueryAmountError("This scalar origin has no characteristic")
        if self.origin is ScalarAmountOrigin.HISTORY:
            if type(self.history_fact) is not str or self.history_fact not in HISTORY_FACTS:
                raise PublicQueryAmountError("Scalar history fact is unsupported")
        elif self.history_fact is not None:
            raise PublicQueryAmountError("Only history amounts carry a history fact")

    @property
    def producer_identity(self) -> dict[str, Any]:
        return {"origin": self.origin.value, "characteristic": self.characteristic,
                "history_fact": self.history_fact, **({"counter_name":self.counter_name} if self.schema_version==2 else {}),
                **({'aggregate':self.aggregate.to_dict()} if self.schema_version==3 else {}),
                **({'multikicker':self.multikicker.to_dict()} if self.schema_version==5 else {})}

    def to_dict(self) -> dict[str, Any]:
        return {"kind": SCALAR_AMOUNT_KIND, "schema_version": self.schema_version,
                **self.producer_identity, "coefficient": self.coefficient,
                "binding_id": self.binding_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScalarEffectAmountSpec":
        fields = {"kind", "schema_version", "origin", "characteristic", "history_fact", "coefficient", "binding_id"}
        if isinstance(value,Mapping) and value.get('schema_version')==2:
            fields.add('counter_name')
        if isinstance(value,Mapping) and value.get('schema_version')==3:
            fields.add('aggregate')
        if isinstance(value,Mapping) and value.get('schema_version')==5:
            fields.add('multikicker')
        if not isinstance(value, Mapping) or set(value) != fields or value.get("kind") != SCALAR_AMOUNT_KIND:
            raise PublicQueryAmountError("Scalar amount fields are incomplete or unknown")
        try:
            origin = ScalarAmountOrigin(value["origin"])
        except (TypeError, ValueError) as exc:
            raise PublicQueryAmountError("Scalar amount origin is unsupported") from exc
        try:
            multikicker = FixedManaMultikickerSpec.from_dict(value['multikicker']) if value['schema_version']==5 else None
            aggregate=PublicCharacteristicAggregateSpec.from_dict(value['aggregate']) if value['schema_version']==3 else None
            return cls(origin=origin, characteristic=value["characteristic"], history_fact=value["history_fact"],
                       coefficient=value["coefficient"], binding_id=value["binding_id"], schema_version=value["schema_version"],counter_name=value.get('counter_name'),aggregate=aggregate,multikicker=multikicker)
        except (TypeError,ValueError) as exc:
            raise PublicQueryAmountError(str(exc)) from exc


def scalar_amount_specs(value: Any) -> tuple[ScalarEffectAmountSpec, ...]:
    if isinstance(value, Mapping):
        if value.get("kind") == SCALAR_AMOUNT_KIND:
            return (ScalarEffectAmountSpec.from_dict(value),)
        return tuple(spec for child in value.values() for spec in scalar_amount_specs(child))
    if isinstance(value, (list, tuple)):
        return tuple(spec for child in value for spec in scalar_amount_specs(child))
    return ()


def direct_player_amount_target_is_closed(schema):
    return (isinstance(schema,Mapping) and set(schema)<={'zones','categories','count','player_relation'}
        and schema.get('zones')==['player'] and schema.get('categories')==['player']
        and type(schema.get('count')) is int and schema['count']==1
        and schema.get('player_relation','any') in {'any','opponent'})


def scope_scalar_amount_bindings(value: Any, source_scope: str) -> Any:
    if isinstance(value, Mapping):
        result = {key: scope_scalar_amount_bindings(child, source_scope) for key, child in value.items()}
        if result.get("kind") == SCALAR_AMOUNT_KIND and result.get("binding_id"):
            result["binding_id"] = f"{source_scope}:{result['binding_id']}"
        return result
    if isinstance(value, (list, tuple)):
        return type(value)(scope_scalar_amount_bindings(child, source_scope) for child in value)
    return value
