"""Lower one unnamed counter removal without choosing its kind at announcement."""
from dataclasses import dataclass
from . import regex_working_set as re
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from ..chosen_counter_removal_model import (
    ChosenCounterRemovalSpec, CHOSEN_COUNTER_REMOVAL_MECHANIC, CHOSEN_COUNTER_REMOVAL_TEMPLATE,
)


@dataclass(frozen=True, slots=True)
class ChosenCounterRemovalTemplate:
    target_spec: DirectPermanentTargetSpec

    @property
    def target_schema(self):
        return self.target_spec.to_target_schema()

    def compiled(self):
        return (CHOSEN_COUNTER_REMOVAL_TEMPLATE, (ChosenCounterRemovalSpec().instruction(),),
                self.target_schema, ('cr-122-counters', 'cr-115-targets', CHOSEN_COUNTER_REMOVAL_MECHANIC))


def chosen_counter_removal_effect_template(text):
    if type(text) is not str:return None
    match = re.fullmatch(r'Remove a counter from (?P<target>target (?:nonland )?permanent)\.?',
                        ' '.join(text.strip().split()), re.I)
    if match is None:return None
    target = direct_permanent_target_spec(match['target'])
    return ChosenCounterRemovalTemplate(target) if target is not None else None
