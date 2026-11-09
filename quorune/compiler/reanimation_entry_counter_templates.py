"""Compose fixed entry counters with the existing exact targeted return."""
from dataclasses import dataclass
import re
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number
from .reanimation_templates import (
    FixedTargetReanimationTemplate, fixed_target_reanimation_effect_template, relative_target_reanimation_template,
)
from ..creature_subtypes import canonical_creature_subtype
from ..reanimation_entry_counters import (
    CONDITIONAL_REANIMATION_COUNTER_MECHANIC, CONDITIONAL_REANIMATION_COUNTER_VERSION,
    REANIMATION_ENTRY_COUNTER_MECHANIC, fixed_reanimation_entry_counter,
)
from ..keyword_counters import keyword_counter_mechanic


@dataclass(frozen=True,slots=True)
class FixedReanimationEntryCounterTemplate:
    base: FixedTargetReanimationTemplate
    counter_name: str
    amount: int

    def __post_init__(self):
        if not isinstance(self.base,FixedTargetReanimationTemplate):
            raise ValueError('Entry-counter reanimation requires its typed scalar base')
        fixed_reanimation_entry_counter({'counter_name':self.counter_name,'amount':self.amount})

    def compiled(self):
        return _entry_counter_result(self.base.compiled(), self.counter_name, self.amount)


def _entry_counter_result(compiled, name, amount, *, required_type=None):
    template, effects, schema, mechanics = compiled
    counter = {"counter_name": name, "amount": amount}
    version = 3
    condition_mechanics = ()
    if required_type is not None:
        counter["required_entry_type"] = required_type
        version = CONDITIONAL_REANIMATION_COUNTER_VERSION
        condition_mechanics = (CONDITIONAL_REANIMATION_COUNTER_MECHANIC,)
    effect = {**effects[0], "schema_version": version, "entry_counter": counter, "source": "$stack"}
    keyword = keyword_counter_mechanic(name)
    dependencies = (*mechanics, REANIMATION_ENTRY_COUNTER_MECHANIC, *condition_mechanics, *((keyword,) if keyword else ()))
    return (
        f"{template}-entry-{name.replace(' ', '-')}-{amount}-v{version}",
        (effect,), schema, tuple(dict.fromkeys(dependencies)),
    )


def fixed_reanimation_entry_counter_template(text):
    match=re.fullmatch(rf'(?P<base>.+? to|.+? onto) the battlefield(?P<tapped> tapped)?'
        rf'(?P<control> under (?:your|its owner[\'’]s) control)? with '
        rf'(?P<count>an|{FIXED_COUNT_PATTERN}) (?:additional )?(?P<name>.+?) counter(?P<plural>s)? on it\.?',text.strip(),re.I)
    if match is None:return None
    base=fixed_target_reanimation_effect_template(match['base']+' the battlefield'+(match['tapped'] or '')+(match['control'] or '')+'.')
    if base is None:return None
    name=' '.join(match['name'].casefold().split());amount=fixed_number(match['count'])
    if (amount==1)==bool(match['plural']):return None
    try:return FixedReanimationEntryCounterTemplate(base,name,amount)
    except ValueError:return None


_CONDITIONAL_ENTRY = re.compile(
    rf"(?P<base>Return .+? to the battlefield\.) If (?:a|an) (?P<quality>[A-Za-z][A-Za-z'’-]*) "
    rf"enters this way, it enters with (?P<count>an|{FIXED_COUNT_PATTERN}) additional "
    r"(?P<counter>.+?) counter(?P<plural>s)? on it\.",
    re.IGNORECASE,
)


def conditional_reanimation_entry_counter_template(text, *, card_name=None):
    if not text.casefold().startswith("return "):
        return None
    match = _CONDITIONAL_ENTRY.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    quality = match["quality"].casefold()
    required_type = "creature" if quality == "creature" else canonical_creature_subtype(quality)
    if required_type is None:
        return None
    count = fixed_number(match["count"])
    if (count == 1) == bool(match["plural"]):
        return None
    name = " ".join(match["counter"].casefold().split())
    try:
        fixed_reanimation_entry_counter({"counter_name": name, "amount": count})
    except ValueError:
        return None
    if card_name is not None:
        compiled = relative_target_reanimation_template(
            match["base"], card_name=card_name, event="attack_with_entry_counters",
        )
    else:
        fixed = fixed_target_reanimation_effect_template(match["base"])
        compiled = fixed.compiled() if fixed is not None else None
    return _entry_counter_result(compiled, name, count, required_type=required_type) if compiled else None
