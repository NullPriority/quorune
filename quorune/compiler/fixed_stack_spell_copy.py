from __future__ import annotations

"""A closed leaf for one explicitly retargetable instant/sorcery spell copy."""

from . import regex_working_set as re
from ..stack_spell_copy_model import FixedStackSpellCopySpec, PermanentSpellCopySpec


def fixed_stack_spell_copy_template(text):
    if re.fullmatch(
        r"Copy target creature spell you control, except it isn't legendary if the spell is legendary\."
        r"(?: \(A copy of a creature spell becomes a token\.\))?", text.strip(), re.I,
    ):
        spec = PermanentSpellCopySpec("nonlegendary")
        return "copy-nonlegendary-creature-spell-v1", spec.effects, spec.target_schema, ("cr-115-targets",)
    qualified = fixed_qualified_stack_copy_template(text)
    if qualified is not None:
        return qualified
    if re.fullmatch(
        r"Copy target instant or sorcery spell, then return it to its owner['’]s hand\. "
        r"You may choose new targets for the copy\.", text.strip(), re.I,
    ):
        from ..rules.opponent_spell_or_permanent_return import copy_and_return_spell_effects

        copy = FixedStackSpellCopySpec()
        return ("fixed-instant-sorcery-copy-and-owner-hand-return-v1", copy_and_return_spell_effects(),
                copy.target_schema, copy.mechanics)
    match = re.fullmatch(r'Copy target instant or sorcery spell(?P<controlled> you control)?\. '
        r'You may choose new targets for the copy\.', text.strip(), re.I)
    if match is None:
        return None
    spec = FixedStackSpellCopySpec(bool(match['controlled']))
    return ('fixed-instant-sorcery-copy-controlled-v1' if spec.controlled_only else 'fixed-instant-sorcery-copy-v1',
        spec.effects, spec.target_schema, spec.mechanics)


def fixed_qualified_stack_copy_template(text):
    from ..stack_spell_copy_model import FixedQualifiedStackCopySpec, mixed_stack_copy_spec

    if re.fullmatch(
        r"Copy target instant spell, sorcery spell, activated ability, or triggered ability\. "
        r"You may choose new targets for the copy\.",
        " ".join(text.split()), re.I,
    ):
        spec = mixed_stack_copy_spec()
    elif re.fullmatch(
        r'Copy target permanent spell you control three times\.(?: \(The copies become tokens\.\))?',
        text.strip(), re.I,
    ):
        spec = FixedQualifiedStackCopySpec('permanent_spell', schema_version=2, copy_count=3)
    elif re.fullmatch(r'Copy target permanent spell you control\.', text.strip(), re.I):
        spec = FixedQualifiedStackCopySpec('permanent_spell')
    elif re.fullmatch(
        r"Copy target activated or triggered ability you control from another legendary source that's not a commander\. "
        r"You may choose new targets for the copy\.(?: \(Mana abilities can't be targeted\.\))?",
        text.strip(), re.I,
    ):
        spec = FixedQualifiedStackCopySpec('activated_or_triggered_ability', 'legendary_noncommander')
    else:
        match = re.fullmatch(r'Copy target (?P<kind>activated or triggered ability|triggered ability) you control'
            r'(?: from (?P<source>an artifact|a colorless) source)?\. '
            r'You may choose new targets for the copy\.', text.strip(), re.I)
        if match is None:
            return None
        kind = 'triggered_ability' if match['kind'].casefold() == 'triggered ability' else 'activated_or_triggered_ability'
        source = {'an artifact': 'artifact', 'a colorless': 'colorless'}.get((match['source'] or '').casefold())
        try:spec = FixedQualifiedStackCopySpec(kind, source)
        except ValueError:return None
    return ('fixed-qualified-stack-copy-v1', spec.effects, spec.target_schema, spec.mechanics)


def cast_event_spell_copy_template(binding,text):
    from ..event_spell_copy_model import EVENT_SPELL_COPY_MECHANIC,event_spell_copy_effect
    if binding.event.value!='spell.cast':return None
    from ..rules.prior_qualified_cast_copy import PRIOR_CAST_COPY_MECHANIC, prior_cast_copy_condition, prior_cast_copy_instruction

    if (binding.event_condition == prior_cast_copy_condition() and re.fullmatch(
        r"Copy it for each other instant and sorcery spell you['’]ve cast before it this turn\. "
        r"You may choose new targets for the copies\.", " ".join(text.split()), re.I,
    )):
        return ("copy-prior-qualified-controller-casts-v1", (prior_cast_copy_instruction(),),
                None, (PRIOR_CAST_COPY_MECHANIC,)), False
    from .cast_spell_suspend_templates import cast_spell_suspend_template
    if suspended_copy := cast_spell_suspend_template(binding, text):
        return suspended_copy
    from ..stack_spell_copy_model import beast_or_bird_creature_cast_condition

    if binding.event_condition == beast_or_bird_creature_cast_condition() and re.fullmatch(
        r"Copy it, except the copy is an artifact in addition to its other types\."
        r"(?: \(The copy becomes a token\.\))?", text.strip(), re.I,
    ):
        spec = PermanentSpellCopySpec("artifact")
        return ("copy-artifact-creature-cast-v1", spec.effects, None, (EVENT_SPELL_COPY_MECHANIC,)), False
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
