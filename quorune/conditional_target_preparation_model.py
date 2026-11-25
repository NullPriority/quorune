"""Immutable condition and closed transport payload for target preparation."""
from collections.abc import Mapping
from dataclasses import dataclass
from .continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind
from .replacement.immutable import thaw_value

RESOLUTION_TARGET_COLOR_FIELD = "resolution_target_color"
RESOLUTION_TARGET_COLOR_MECHANIC = "fixed-resolution-target-color"
RESOLUTION_TARGET_COLOR_CAPABILITY = "resolution.effect.fixed_current_target_color"
_RESOLUTION_TARGET_COLOR_OPERANDS = {"counter_stack_target": "stack", "destroy": "card"}


def resolution_target_color_primitive(effect):
    """Decode one current-color guard over a primitive and its single target."""
    if not isinstance(effect, Mapping):
        raise ValueError("The color check requires a closed effect")
    operation = effect.get("op")
    field = _RESOLUTION_TARGET_COLOR_OPERANDS.get(operation)
    if (field is None or set(effect) != {"op", field, RESOLUTION_TARGET_COLOR_FIELD}
            or effect[field] != "$target.0"):
        raise ValueError("The color check requires one primitive with its original target slot")
    return field, fixed_resolution_target_color(effect[RESOLUTION_TARGET_COLOR_FIELD])


def fixed_resolution_target_color(value):
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "color"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["color"]) is not str or value["color"] not in {"W", "U", "B", "R", "G"}):
        raise ValueError("A resolved target color requires one canonical color and version")
    return value["color"]


CONDITIONAL_PREPARATION_VERSION = 16
CONDITIONAL_PREPARATION_MECHANIC = 'conditional-target-power-preparation'
CONDITIONAL_PREPARATION_CAPABILITY = 'resolution.effect_program.conditional_target_preparation'


@dataclass(frozen=True, slots=True)
class PreparationCondition:
    scope: str
    condition: FixedPublicStateConditionSpec | None = None

    def __post_init__(self):
        if self.scope == 'night':
            if self.condition is not None:raise ValueError('Night has no extra predicate')
        elif self.scope == 'controller':
            from .resolution_conditions import resolution_condition_is_closed
            if (not isinstance(self.condition, FixedPublicStateConditionSpec)
                    or any(not resolution_condition_is_closed(c) for c in self.condition.leaf_conditions())):
                raise ValueError('Preparation controller predicate must be closed')
        elif self.scope == 'target':
            if (not isinstance(self.condition, FixedPublicStateConditionSpec)
                    or self.condition.kind not in {FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY, FixedPublicStateConditionKind.ALL}
                    or any(c.kind is not FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY
                        for c in self.condition.leaf_conditions())):
                raise ValueError('Preparation target predicate must be a public characteristic query')
        else:raise ValueError('Preparation condition scope is unsupported')

    def to_dict(self):
        return {'kind': 'bound_preparation_condition', 'schema_version': 1, 'scope': self.scope,
            'condition': self.condition.to_dict() if self.condition is not None else None}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'scope', 'condition'}
                or value['kind'] != 'bound_preparation_condition' or type(value['schema_version']) is not int
                or value['schema_version'] != 1):
            raise ValueError('Preparation condition has an explicit closed schema')
        return cls(value['scope'], FixedPublicStateConditionSpec.from_dict(value['condition'])
            if value['condition'] is not None else None)


def preparation_results_are_closed(effects, *, subject):
    if not isinstance(effects, (list, tuple)) or not 1 <= len(effects) <= 3:return False
    for effect in effects:
        if not isinstance(effect, Mapping) or effect.get('card') != subject:return False
        operation = effect.get('op')
        if operation == 'modify_stats_until_end_of_turn':
            if (set(effect) != {'op', 'card', 'power', 'toughness'} or type(effect['power']) is not int
                    or type(effect['toughness']) is not int or (effect['power'], effect['toughness']) == (0, 0)):return False
        elif operation == 'grant_keyword_until_end_of_turn':
            if set(effect) != {'op', 'card', 'keyword'} or type(effect['keyword']) is not str or not effect['keyword']:return False
        elif operation == 'place_counters':
            if (set(effect) != {'op', 'card', 'counter', 'amount', 'source'} or effect['counter'] != '+1/+1'
                    or type(effect['amount']) is not int or effect['amount'] <= 0):return False
            source = effect['source']
            if source is not None and (type(source) is not str or not source or source.startswith('$') and source != '$source'):return False
        else:return False
    return True


def validate_preparation_instruction(effect):
    fields = {'op', 'schema_version', 'player', 'condition', 'condition_target', 'target_binding',
        'effects', 'mechanic_ids', 'prefix_mechanic_ids', 'otherwise_effects', 'otherwise_mechanic_ids'}
    if (not isinstance(effect, Mapping) or set(effect) != fields or effect['op'] != 'apply_if_public_condition'
            or type(effect['schema_version']) is not int or effect['schema_version'] != CONDITIONAL_PREPARATION_VERSION
            or type(effect['player']) is not str or not effect['player'] or effect['prefix_mechanic_ids']):
        raise ValueError('Conditional preparation instruction fields are closed')
    subject = effect['condition_target']
    if subject is not None and (type(subject) is not str or not subject or subject.startswith('$') and subject != '$target.0'):
        raise ValueError('Conditional preparation requires the original first target')
    binding = thaw_value(effect['target_binding'])
    if binding != {'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'],
            'controller_relation': 'you', 'count': 1} or type(binding.get('count')) is not int:
        raise ValueError('Conditional preparation requires one own creature binding')
    for branch, key in (('effects', 'mechanic_ids'), ('otherwise_effects', 'otherwise_mechanic_ids')):
        ids = effect[key]
        if (not isinstance(ids, (list, tuple)) or len(set(ids)) != len(ids)
                or any(type(m) is not str or not m for m in ids) or 'fixed-resolution-public-condition' in ids):
            raise ValueError('Conditional preparation branch mechanics are malformed')
        children = effect[branch]
        if branch == 'otherwise_effects' and children in ([], ()) and not ids:continue
        if not preparation_results_are_closed(children, subject=subject):
            raise ValueError('Conditional preparation branch is not a closed fixed target result')
    return PreparationCondition.from_dict(effect['condition']), tuple(effect['effects'])
