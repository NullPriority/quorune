"""Compile a whole named search and its conditional shuffle as one unit."""

from copy import deepcopy
from dataclasses import dataclass
from . import regex_working_set as re

from ..named_search_model import NamedZoneSearchSpec, NAMED_ZONE_SEARCH_MECHANIC

_SEARCH = re.compile(
    r"(?P<optional>You may search|Search) your library and/or graveyard for a card named "
    r"(?P<name>.+?), reveal it, (?:and |then )?put it into your hand\. "
    r"If you search(?:ed)? your library this way, shuffle\.", re.I)
_START = re.compile(r"(?:You may search|Search) your library and/or graveyard\b", re.I)


@dataclass(frozen=True, slots=True)
class NamedZoneSearchTemplate:
    spec: NamedZoneSearchSpec

    def compiled(self):
        return "named-library-graveyard-search-v1", (self.spec.instruction(),), None, (NAMED_ZONE_SEARCH_MECHANIC,)


def named_zone_search_template(text):
    match = _SEARCH.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    name = match["name"]
    if not name[0].isupper() or any(word in name.casefold() for word in (" named ", " or ", "this way", "chosen name")):
        return None
    try:
        return NamedZoneSearchTemplate(NamedZoneSearchSpec(name, match["optional"].casefold().startswith("you may")))
    except ValueError:
        return None


def named_zone_search_sequence_template(text, *, compile_prefix):
    text = " ".join(text.strip().split())
    starts = tuple(_START.finditer(text))
    if len(starts) != 1 or starts[0].start() == 0:
        return None
    split = starts[0].start();prefix = text[:split].strip()
    search = named_zone_search_template(text[split:])
    if search is None or not prefix.endswith("."):
        return None
    first = compile_prefix(prefix)
    if first[0] is None or not first[1] or len(first[1]) > 7:
        return None
    _name, tail, _schema, mechanics = search.compiled()
    effects = (*deepcopy(first[1]), *tail)
    mechanics = tuple(dict.fromkeys((*mechanics, *first[3])))
    from ..rules.named_searches import named_zone_search_node_capabilities
    if not named_zone_search_node_capabilities(effects=effects, target_schema=first[2], mechanic_ids=mechanics):
        return None
    return "named-zone-search-after-owned-prefix-v1", effects, deepcopy(first[2]), mechanics
