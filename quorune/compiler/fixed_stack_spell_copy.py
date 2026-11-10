from __future__ import annotations

"""A closed leaf for one explicitly retargetable instant/sorcery spell copy."""

import re
from ..stack_spell_copy_model import FixedStackSpellCopySpec


def fixed_stack_spell_copy_template(text):
    match = re.fullmatch(r'Copy target instant or sorcery spell(?P<controlled> you control)?\. '
        r'You may choose new targets for the copy\.', text.strip(), re.I)
    if match is None:
        return None
    spec = FixedStackSpellCopySpec(bool(match['controlled']))
    return ('fixed-instant-sorcery-copy-controlled-v1' if spec.controlled_only else 'fixed-instant-sorcery-copy-v1',
        spec.effects, spec.target_schema, spec.mechanics)


def cast_event_spell_copy_template(binding,text):
    from ..event_spell_copy_model import EVENT_SPELL_COPY_MECHANIC,event_spell_copy_effect
    if binding.event.value!='spell.cast':return None
    match=re.fullmatch(r'Copy (?:that spell|it)\.(?P<retarget> You may choose new targets for the copy\.)?',text.strip(),re.I)
    if match is None:return None
    return ('cast-event-spell-copy-v1',(event_spell_copy_effect(retain_targets_only=not bool(match['retarget'])),),None,(EVENT_SPELL_COPY_MECHANIC,)),False


_NEXT_CAST_COPY = re.compile(
    r"When you next cast (?P<scope>an instant or sorcery spell|a Lesson spell|a spell)"
    r"(?: with mana value (?P<maximum>\d+) or less)? this turn, copy (?:it|that spell)"
    r"(?P<twice> twice)?(?:\. You may| and you may) choose new targets for the (?P<suffix>copy|copies)\.",
    re.I,
)
_NEXT_CAST_COPY_DIRECT = re.compile(
    r"Copy the next (?P<scope>instant or sorcery spell|spell)"
    r"(?: with mana value (?P<maximum>\d+) or less)? you cast this turn when you cast it"
    r"\. You may choose new targets for the copy\.",
    re.I,
)


def next_cast_copy_template(text):
    from ..event_spell_copy_model import NEXT_CAST_COPY_MECHANIC, NextCastCopySpec, next_cast_copy_effect

    normalized = " ".join(text.split())
    normalized = re.sub(r"^[A-Za-z][A-Za-z'’ -]* [—–] ", "", normalized, count=1)
    match = _NEXT_CAST_COPY.fullmatch(normalized) or _NEXT_CAST_COPY_DIRECT.fullmatch(normalized)
    if match is None:
        return None
    copies = 2 if match.groupdict().get("twice") else 1
    if match.groupdict().get("suffix", "copy").casefold() != ("copy" if copies == 1 else "copies"):
        return None
    scope = match["scope"].casefold()
    scope = "instant_or_sorcery" if "instant" in scope else "lesson" if "lesson" in scope else "any"
    maximum = int(match["maximum"]) if match["maximum"] is not None else None
    spec = NextCastCopySpec(scope, maximum, copies)
    return ("fixed-next-cast-spell-copy-v1", (next_cast_copy_effect(spec),), None,
            (NEXT_CAST_COPY_MECHANIC, "cr-603-handling-triggered-abilities"))
