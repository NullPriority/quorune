"""All counters of one named kind, or all kinds from zero or one target."""
from dataclasses import dataclass
from . import regex_working_set as re
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from ..counter_names import normalized_counter_name

NAMED_TARGET_ALL_COUNTER_MECHANIC = 'all-named-target-counter-removal'
OPTIONAL_TARGET_ALL_COUNTER_MECHANIC = 'optional-target-all-counter-removal'

_KIND = r"[+-]\d+/[+-]\d+|[A-Za-z][A-Za-z'-]*(?: [A-Za-z][A-Za-z'-]*){0,2}"


@dataclass(frozen=True, slots=True)
class TargetAllCounterTemplate:
    target_spec: DirectPermanentTargetSpec
    counter_name: str | None = None
    optional: bool = False

    def __post_init__(self):
        if not isinstance(self.target_spec, DirectPermanentTargetSpec) or type(self.optional) is not bool:
            raise ValueError('All-counter variants require one typed target and exact optionality')
        if self.counter_name is not None and (type(self.counter_name) is not str or not self.counter_name
                or normalized_counter_name(self.counter_name) != self.counter_name):
            raise ValueError('Named target removal requires one canonical counter kind')
        if self.optional == (self.counter_name is not None):
            raise ValueError('Represent either named mandatory or unnamed optional removal')

    @property
    def effects(self):
        effect = {'op': 'remove_all_counters', 'card': '$target.0', 'source': '$source'}
        if self.counter_name is not None:effect.update(schema_version=3, counter=self.counter_name)
        return (effect,)

    @property
    def target_schema(self):
        schema = self.target_spec.to_target_schema()
        if self.optional:
            schema.pop('count')
            schema['up_to'] = 1
        return schema

    @property
    def mechanics(self):
        return ('cr-122-counters', 'cr-115-targets', OPTIONAL_TARGET_ALL_COUNTER_MECHANIC
                if self.optional else NAMED_TARGET_ALL_COUNTER_MECHANIC)

    def compiled(self):
        template = 'remove-all-counters-optional-target-v1' if self.optional else 'remove-all-named-counters-target-v3'
        return template, self.effects, self.target_schema, self.mechanics


def target_all_counter_variant_template(text):
    if type(text) is not str:return None
    value = ' '.join(text.strip().split())
    optional = re.fullmatch(r'Remove all counters from up to one (?P<target>target .+?)\.?', value, re.I)
    if optional is not None:
        target = direct_permanent_target_spec(optional['target'])
        return TargetAllCounterTemplate(target, optional=True) if target is not None else None
    named = re.fullmatch(rf'Remove all (?P<kind>{_KIND}) counters from (?P<target>target .+?)\.?', value, re.I)
    if named is None:return None
    target = direct_permanent_target_spec(named['target'])
    return TargetAllCounterTemplate(target, normalized_counter_name(named['kind'])) if target is not None else None
