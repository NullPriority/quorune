from __future__ import annotations

"""One closed permission to cast a bounded spell from hand during resolution."""

from collections.abc import Mapping
from dataclasses import dataclass
from .mana_value_limit import ManaValueLimitSpec
from .replacement.immutable import FrozenMap, thaw_value

BOUNDED_HAND_CAST_FIELD = 'bounded_hand_cast'
BOUNDED_HAND_CAST_CAPABILITY = 'casting.resolution.fixed_bounded_hand'
BOUNDED_HAND_CAST_MECHANIC = 'fixed-bounded-hand-free-cast'
LEGENDARY_WIZARD_HAND_CAST_VERSION = 3
DAMAGED_PLAYER_HAND_CAST_VERSION = 4
ANNOUNCED_X_HAND_CAST_VERSION = 5
ANNOUNCED_X_HAND_CAST_CAPABILITY = "casting.resolution.announced_x_hand_cast_after_damage"
DAMAGED_PLAYER_HAND_CAST_CAPABILITY = "casting.resolution.inspected_damage_recipient_hand"
DAMAGED_PLAYER_HAND_CAST_OWNER = "$context.target"
SOURCE_COMBAT_DAMAGE_PLAYER_CONDITION = FrozenMap({"all": [
    {"field": "target_kind", "op": "eq", "value": "player"},
    {"field": "combat", "op": "truthy", "value": True},
    {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
]})
DAMAGED_PLAYER_HAND_CAST_DEPENDENCIES = (
    DAMAGED_PLAYER_HAND_CAST_CAPABILITY, BOUNDED_HAND_CAST_CAPABILITY,
    "trigger.event.normalized_damage", "trigger.effect.fixed_event", "trigger.placement.apnap",
)
LEGENDARY_WIZARD_HAND_CAST_CAPABILITY = "casting.resolution.legendary_wizard_bounded_hand"
LEGENDARY_WIZARD_HAND_CAST_CONDITION = FrozenMap({"all": [
    {"field": "player", "op": "eq", "value": "$source.controller"},
    {"field": "step", "op": "eq", "value": "beginning_combat"},
]})
LEGENDARY_WIZARD_HAND_CAST_DEPENDENCIES = (
    LEGENDARY_WIZARD_HAND_CAST_CAPABILITY, BOUNDED_HAND_CAST_CAPABILITY,
    "quantity_expression.public_query_effect_amount", "trigger.effect.fixed_event", "trigger.placement.apnap",
)


@dataclass(frozen=True, slots=True)
class BoundedHandCastSpec:
    mana_value_limit: ManaValueLimitSpec | None
    schema_version: int = 1
    shared_card_types: tuple[str, ...] = ()
    hand_owner: str | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int:
            raise ValueError('Hand free cast requires a supported schema version')
        if self.schema_version == DAMAGED_PLAYER_HAND_CAST_VERSION:
            if (self.mana_value_limit is not None or self.shared_card_types != ()
                    or type(self.hand_owner) is not str or not self.hand_owner
                    or self.hand_owner.strip() != self.hand_owner
                    or self.hand_owner.startswith("$") and self.hand_owner != DAMAGED_PLAYER_HAND_CAST_OWNER):
                raise ValueError("The inspected hand cast requires its retained damage recipient")
            return
        if self.hand_owner is not None:
            raise ValueError("Controller-hand casts cannot select a different hand owner")
        if self.schema_version in (1, LEGENDARY_WIZARD_HAND_CAST_VERSION, ANNOUNCED_X_HAND_CAST_VERSION):
            if not isinstance(self.mana_value_limit, ManaValueLimitSpec) or self.shared_card_types != ():
                raise ValueError('Hand free cast requires its closed fixed mana-value limit')
            return
        if self.schema_version != 2 or self.mana_value_limit is not None:
            raise ValueError('Shared-type hand free cast has incompatible permission fields')
        from .characteristic_evaluation import _CARD_TYPES

        types = self.shared_card_types
        if (type(types) is not tuple or not types or any(type(card_type) is not str for card_type in types)
                or types != tuple(sorted(set(types))) or not set(types) <= _CARD_TYPES):
            raise ValueError('Shared-type hand free cast requires a canonical captured card type set')

    def to_dict(self):
        if self.schema_version == DAMAGED_PLAYER_HAND_CAST_VERSION:
            return {"schema_version": DAMAGED_PLAYER_HAND_CAST_VERSION, "hand_owner": self.hand_owner}
        if self.schema_version == 2:
            return {'schema_version': 2, 'shared_card_types': list(self.shared_card_types)}
        return {'schema_version': self.schema_version, 'mana_value_limit': self.mana_value_limit.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError('Hand free cast permission must be a mapping')
        if (set(value) == {"schema_version", "hand_owner"}
                and value["schema_version"] == DAMAGED_PLAYER_HAND_CAST_VERSION):
            return cls(None, value["schema_version"], hand_owner=value["hand_owner"])
        if (set(value) == {'schema_version', 'mana_value_limit'}
                and value['schema_version'] in (1, LEGENDARY_WIZARD_HAND_CAST_VERSION, ANNOUNCED_X_HAND_CAST_VERSION)):
            return cls(ManaValueLimitSpec.from_dict(value['mana_value_limit']), value['schema_version'])
        if (set(value) == {'schema_version', 'shared_card_types'} and value['schema_version'] == 2
                and isinstance(value['shared_card_types'], (list, tuple))):
            return cls(None, value['schema_version'], tuple(value['shared_card_types']))
        raise ValueError('Hand free cast permission has unknown or missing fields')

    def effect(self):
        return {'op': 'choose_objects', 'player': '$controller', BOUNDED_HAND_CAST_FIELD: self.to_dict()}

    @classmethod
    def from_effect(cls, effect, *, actor='$controller'):
        if (not isinstance(effect, Mapping) or set(effect) != {'op', 'player', BOUNDED_HAND_CAST_FIELD}
                or effect['op'] != 'choose_objects' or effect['player'] != actor):
            raise ValueError('Hand free cast requires one complete controller instruction')
        return cls.from_dict(effect[BOUNDED_HAND_CAST_FIELD])

    def compiled(self):
        return ('fixed-bounded-hand-free-cast-v1', (self.effect(),), None, (BOUNDED_HAND_CAST_MECHANIC,))


def legendary_wizard_hand_cast_effect():
    from .characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from .object_predicate import ObjectQuerySpec
    from .query_effect_amount_model import PublicQueryAmountSpec

    quantity = CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=("battlefield",), subtypes_any=("wizard",), supertypes_all=("legendary",)),
    )
    maximum = PublicQueryAmountSpec(quantity, coefficient=2).to_dict()
    return {
        "op": "choose_objects", "player": "$controller",
        BOUNDED_HAND_CAST_FIELD: {
            "schema_version": LEGENDARY_WIZARD_HAND_CAST_VERSION,
            "mana_value_limit": {"schema_version": 1, "maximum": maximum},
        },
    }


def is_legendary_wizard_hand_cast_effect(effect) -> bool:
    if not isinstance(effect, Mapping):
        return False
    permission = effect.get(BOUNDED_HAND_CAST_FIELD)
    return isinstance(permission, Mapping) and permission.get("schema_version") == LEGENDARY_WIZARD_HAND_CAST_VERSION


def damaged_player_hand_cast_effect():
    return BoundedHandCastSpec(
        None, DAMAGED_PLAYER_HAND_CAST_VERSION, hand_owner=DAMAGED_PLAYER_HAND_CAST_OWNER,
    ).effect()


def is_damaged_player_hand_cast_effect(effect) -> bool:
    permission = effect.get(BOUNDED_HAND_CAST_FIELD) if isinstance(effect, Mapping) else None
    return isinstance(permission, Mapping) and permission.get("schema_version") == DAMAGED_PLAYER_HAND_CAST_VERSION


def announced_x_hand_cast_effect():
    return {
        "op": "choose_objects", "player": "$controller",
        BOUNDED_HAND_CAST_FIELD: {"schema_version": ANNOUNCED_X_HAND_CAST_VERSION,
                                 "mana_value_limit": {"schema_version": 1, "maximum": "$x"}},
    }


def is_announced_x_hand_cast_effect(effect) -> bool:
    permission = effect.get(BOUNDED_HAND_CAST_FIELD) if isinstance(effect, Mapping) else None
    return isinstance(permission, Mapping) and permission.get("schema_version") == ANNOUNCED_X_HAND_CAST_VERSION


def bounded_hand_cast_amount_slot(operation, path):
    return operation == "choose_objects" and path == (BOUNDED_HAND_CAST_FIELD, "mana_value_limit", "maximum")


def _hand_cast_limit(effect):
    permission = effect.get(BOUNDED_HAND_CAST_FIELD) if isinstance(effect, Mapping) else None
    return permission.get("mana_value_limit") if isinstance(permission, Mapping) else None


def has_announced_x_hand_cast_amount(effect):
    from .query_effect_amount_model import CAST_X_AMOUNT_KIND

    limit = _hand_cast_limit(effect)
    amount = limit.get("maximum") if isinstance(limit, Mapping) else None
    return isinstance(amount, Mapping) and amount.get("kind") == CAST_X_AMOUNT_KIND


def with_hand_cast_version(effect, version):
    return {**effect, BOUNDED_HAND_CAST_FIELD: {**effect[BOUNDED_HAND_CAST_FIELD], "schema_version": version}}


def _announced_x_hand_cast_dependencies(effect):
    from .query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY

    bound = _hand_cast_limit(effect)
    if not has_announced_x_hand_cast_amount(effect) or CastXAmountSpec.from_dict(bound["maximum"]) != CastXAmountSpec():
        raise ValueError("The hand cast ceiling requires one unscaled announced X")
    projected = {
        **effect,
        BOUNDED_HAND_CAST_FIELD: {
            **effect[BOUNDED_HAND_CAST_FIELD],
            "mana_value_limit": {**bound, "maximum": 0},
        },
    }
    if BoundedHandCastSpec.from_effect(projected).schema_version != ANNOUNCED_X_HAND_CAST_VERSION:
        raise ValueError("The announced hand cast requires its scoped version")
    return (BOUNDED_HAND_CAST_CAPABILITY, DECLARED_AMOUNT_CAPABILITY)


def bounded_hand_cast_payload_dependencies(effect):
    if not isinstance(effect, Mapping) or BOUNDED_HAND_CAST_FIELD not in effect:
        return ()
    if is_announced_x_hand_cast_effect(effect):
        from .query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY

        permission = effect[BOUNDED_HAND_CAST_FIELD]
        if (type(permission["schema_version"]) is not int
                or FrozenMap(effect) != FrozenMap(announced_x_hand_cast_effect())
                or type(permission["mana_value_limit"]["schema_version"]) is not int):
            raise ValueError("An announced-X hand cast requires the original spell's bound value")
        return (ANNOUNCED_X_HAND_CAST_CAPABILITY, BOUNDED_HAND_CAST_CAPABILITY, DECLARED_AMOUNT_CAPABILITY)
    if is_legendary_wizard_hand_cast_effect(effect):
        permission = effect[BOUNDED_HAND_CAST_FIELD]
        if (type(permission["schema_version"]) is not int
                or FrozenMap(effect) != FrozenMap(legendary_wizard_hand_cast_effect())):
            raise ValueError("Legendary Wizard hand cast requires its exact live public mana-value limit")
        return LEGENDARY_WIZARD_HAND_CAST_DEPENDENCIES
    if is_damaged_player_hand_cast_effect(effect):
        spec = BoundedHandCastSpec.from_effect(effect)
        if spec.hand_owner != DAMAGED_PLAYER_HAND_CAST_OWNER:
            raise ValueError("The inspected hand cast requires the committed damage recipient")
        return DAMAGED_PLAYER_HAND_CAST_DEPENDENCIES
    if has_announced_x_hand_cast_amount(effect):
        return _announced_x_hand_cast_dependencies(effect)
    if BoundedHandCastSpec.from_effect(effect).schema_version != 1:
        raise ValueError("A scoped hand free cast requires its original bound stack instruction")
    return (BOUNDED_HAND_CAST_CAPABILITY,)


def bounded_hand_cast_node_capabilities(*, effects, target_schema, mechanic_ids=()):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        return bounded_hand_cast_payload_dependencies(effects[0])
    except (ValueError, TypeError, KeyError):
        return ()


def is_closed_bounded_hand_cast_program(program):
    return bool(BOUNDED_HAND_CAST_MECHANIC in program.coverage
        and bounded_hand_cast_node_capabilities(effects=program.effects, target_schema=program.target_schema))


@dataclass(frozen=True, slots=True)
class ResolveBoundedHandCastIntent:
    actor: str
    stack_ref: str
    spec: BoundedHandCastSpec
    options_fingerprint: str
    response: FrozenMap | None
    replacement_selections: tuple = ()

    def __post_init__(self):
        if (type(self.actor) is not str or not self.actor or type(self.stack_ref) is not str or not self.stack_ref
                or not isinstance(self.spec, BoundedHandCastSpec)
                or type(self.options_fingerprint) is not str or len(self.options_fingerprint) != 64
                or any(c not in '0123456789abcdef' for c in self.options_fingerprint)
                or self.response is not None and not isinstance(self.response, FrozenMap)
                or not isinstance(self.replacement_selections, tuple)):
            raise ValueError('Hand free cast requires its original stack, permission, options and response')
        if self.response is not None and any(type(k) is not str or k.startswith('_') for k in self.response):
            raise ValueError('Internal hand free cast fields cannot be submitted')

    def identity(self):
        return {'actor': self.actor, 'stack_ref': self.stack_ref, 'spec': self.spec.to_dict(),
                'options_fingerprint': self.options_fingerprint, 'response': thaw_value(self.response)}


def validate_bounded_hand_cast_identity(value):
    if (not isinstance(value, Mapping)
            or set(value) != {'actor', 'stack_ref', 'spec', 'options_fingerprint', 'response'}):
        raise ValueError('The resolving hand free cast identity is malformed')
    response = value['response']
    if response is not None and not isinstance(response, Mapping):
        raise ValueError('The resolving hand free cast response is malformed')
    return ResolveBoundedHandCastIntent(value['actor'], value['stack_ref'], BoundedHandCastSpec.from_dict(value['spec']),
        value['options_fingerprint'], FrozenMap(response) if response is not None else None).identity()
