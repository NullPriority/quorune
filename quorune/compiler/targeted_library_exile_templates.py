"""Consume one complete mandatory search/exile/shuffle targeted-player clause."""
from . import regex_working_set as re
from ..library_search_model import (
    TARGET_OPPONENT_EXILE_LIFE_SEARCH_VERSION, TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION,
)
from ..rules.targeted_library_exile import TargetLibraryExileSpec, TargetNameMultizoneExileSpec
from .fixed_numbers import fixed_number

_TARGET_NAME_MULTIZONE = re.compile(
    r"Exile target (?P<qualifier>artifact|nonblack creature|nonbasic land|enchantment)\. "
    r"Search its controller['’]s graveyard, hand, and library for all cards with the same name as "
    r"that (?P<subject>artifact|creature|land|enchantment) and exile them\. Then that player shuffles\.",
    re.I,
)

_TARGETED = re.compile(
    r"Search target player['’]s library for (?P<count>a|one|two|three|four|five|six|seven|eight|nine|[1-9][0-9]*) "
    r"card(?P<plural>s)? and exile (?P<pronoun>it|them)\. Then that player shuffles\.", re.I)

_PRIVATE_PLAY = re.compile(
    r"Search target opponent['’]s library for a card and exile it face down\. Then that player shuffles\. "
    r"You may play that card for as long as it remains exiled\.", re.I,
)

_EXILE_MANA_VALUE_LIFE = re.compile(
    r"Search target opponent['’]s library for a card and exile it\. "
    r"You gain life equal to its mana value\. Then that player shuffles\.",
    re.I,
)


def targeted_library_exile_spec(text):
    normalized = ' '.join(text.strip().split())
    if _PRIVATE_PLAY.fullmatch(normalized):
        return TargetLibraryExileSpec(1, TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION)
    if _EXILE_MANA_VALUE_LIFE.fullmatch(normalized):
        return TargetLibraryExileSpec(1, TARGET_OPPONENT_EXILE_LIFE_SEARCH_VERSION)
    match = _TARGETED.fullmatch(normalized)
    if match is None:
        return None
    count = 1 if match['count'].casefold() == 'a' else fixed_number(match['count'])
    if bool(match['plural']) != (count != 1) or match['pronoun'].casefold() != ('it' if count == 1 else 'them'):
        return None
    try:
        return TargetLibraryExileSpec(count)
    except (TypeError, ValueError):
        return None


class TargetedLibraryExileTemplate:
    def __init__(self, spec):
        self.spec = spec

    def compiled(self):
        from ..rules.targeted_library_exile import TARGET_LIBRARY_EXILE_MECHANIC
        template = {
            TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION: 'targeted-private-library-exile-play-v1',
            TARGET_OPPONENT_EXILE_LIFE_SEARCH_VERSION: 'targeted-library-exile-mana-value-life-v1',
        }.get(self.spec.schema_version, 'targeted-fixed-library-exile-v1')
        return (template, (self.spec.effect(),),
            self.spec.target_schema(), (TARGET_LIBRARY_EXILE_MECHANIC,))

class TargetNameMultizoneExileTemplate:
    def __init__(self, spec: TargetNameMultizoneExileSpec):
        self.spec = spec

    def compiled(self):
        from ..rules.targeted_library_exile import TARGET_LIBRARY_EXILE_MECHANIC

        return ("target-name-multizone-exile-v1", (self.spec.effect(),), self.spec.target_schema(),
                (TARGET_LIBRARY_EXILE_MECHANIC,))


def targeted_library_exile_template(text):
    match = _TARGET_NAME_MULTIZONE.fullmatch(" ".join(text.strip().split()))
    if match is not None:
        qualifier = match["qualifier"].casefold()
        if qualifier.split()[-1] == match["subject"].casefold():
            return TargetNameMultizoneExileTemplate(TargetNameMultizoneExileSpec(qualifier))
        return None
    spec = targeted_library_exile_spec(text)
    if spec is None or spec.schema_version == TARGET_OPPONENT_EXILE_LIFE_SEARCH_VERSION:
        return None
    return TargetedLibraryExileTemplate(spec)


def targeted_library_exile_life_spell_template(text):
    spec = targeted_library_exile_spec(text)
    if spec is not None and spec.schema_version == TARGET_OPPONENT_EXILE_LIFE_SEARCH_VERSION:
        return TargetedLibraryExileTemplate(spec).compiled()
    return None
