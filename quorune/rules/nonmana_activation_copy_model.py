"""Immutable copy authority for a successfully committed nonmana activation."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..model import StackItem
from ..replacement.immutable import FrozenMap, thaw_value

ABILITY_ACTIVATION_COPY_EVENT = "ability.nonmana.activated"
ABILITY_ACTIVATION_COPY_CAPABILITY = "stack.copy.paid_nonmana_activation"
ACTIVATION_COPY_CONTEXT = "activation_copy_source"
COPY_SOURCE_VERSION = 2
TRIGGER_COPY_SOURCE_VERSION = 3
PAYMENT_VERSION = 19


@dataclass(frozen=True, slots=True)
class ActivationCopySource:
    stack_item: FrozenMap
    target_schema: FrozenMap | None
    departed: bool = False
    schema_version: int = COPY_SOURCE_VERSION

    def __post_init__(self):
        kinds = {COPY_SOURCE_VERSION: "activated_ability", TRIGGER_COPY_SOURCE_VERSION: "triggered_ability"}
        if type(self.schema_version) is not int or self.schema_version not in kinds or type(self.departed) is not bool:
            raise ValueError("Ability copy source requires its exact version and departure state")
        if not isinstance(self.stack_item, Mapping) or self.target_schema is not None and not isinstance(self.target_schema, Mapping):
            raise ValueError("Activation copy source lacks its represented stack object or target schema")
        object.__setattr__(self, "stack_item", FrozenMap(self.stack_item))
        if self.target_schema is not None:
            object.__setattr__(self, "target_schema", FrozenMap(self.target_schema))
        item = self.item()
        if (item.kind != kinds[self.schema_version] or item.card_object_id is not None
                or any(type(value) is not str or not value for value in
                       (item.ref, item.stack_id, item.controller, item.source_object_id, item.semantic_key))
                or any(type(value) is not str or not value for value in (*item.targets, *item.modes))
                or item.x_value is not None and (type(item.x_value) is not int or item.x_value < 0)):
            raise ValueError("Activation copy source requires the original nonmana ability and its announced choices")

    @property
    def logical_object_id(self):
        return self.stack_item["stack_id"]

    @property
    def characteristics(self):
        return None

    def item(self):
        return StackItem.from_dict(thaw_value(self.stack_item))

    def to_dict(self):
        return {"schema_version": self.schema_version, "stack_item": thaw_value(self.stack_item),
                "target_schema": thaw_value(self.target_schema), "departed": self.departed}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "stack_item", "target_schema", "departed"}:
            raise ValueError("Activation copy source fields are closed")
        return cls(**dict(value))
