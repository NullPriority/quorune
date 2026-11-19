"""Consume one complete mandatory search/exile/shuffle targeted-player clause."""
from . import regex_working_set as re
from ..rules.targeted_library_exile import TargetLibraryExileSpec
from .fixed_numbers import fixed_number

_TARGETED = re.compile(
    r"Search target player['’]s library for (?P<count>a|one|two|three|four|five|six|seven|eight|nine|[1-9][0-9]*) "
    r"card(?P<plural>s)? and exile (?P<pronoun>it|them)\. Then that player shuffles\.", re.I)


def targeted_library_exile_spec(text):
    match = _TARGETED.fullmatch(' '.join(text.strip().split()))
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
        return ('targeted-fixed-library-exile-v1', (self.spec.effect(),),
            self.spec.target_schema(), (TARGET_LIBRARY_EXILE_MECHANIC,))

def targeted_library_exile_template(text):
    spec = targeted_library_exile_spec(text)
    return TargetedLibraryExileTemplate(spec) if spec is not None else None
