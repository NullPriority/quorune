from __future__ import annotations

"""Correlated subtype sets from pinned CR205.3, independent of card identity."""

from types import MappingProxyType
from .basic_land_types import LAND_SUBTYPES
from .creature_subtypes import CREATURE_SUBTYPES


ARTIFACT_SUBTYPES=frozenset('attraction blood bobblehead book clue contraption equipment food fortification gold '
    'incubator infinity junk lander map mutagen powerstone spacecraft stone treasure vehicle vibranium'.split())
ENCHANTMENT_SUBTYPES=frozenset('aura background cartouche case class curse plan role room rune saga shard shrine'.split())
PLANESWALKER_SUBTYPES=frozenset('ajani aminatou angrath arlinn ashiok bahamut basri bolas calix chandra comet dack '
    'dakkon daretti davriel dellian dihada domri dovin ellywick elminster elspeth estrid freyalise garruk gideon grist '
    'guff huatli jace jared jaya jeska kaito karn kasmina kaya kiora koth liliana lolth lukka minsc mordenkainen '
    'nahiri narset niko nissa nixilis oko quintorius ral rowan saheeli samut sarkhan serra sivitri sorin szat tamiyo '
    'tasha teferi teyo tezzeret tibalt tyvar ugin urza venser vivien vraska vronos will windgrace wrenn xenagos '
    'yanggu yanling zariel'.split())
SPELL_SUBTYPES=frozenset('adventure arcane lesson omen trap'.split())
CARD_TYPE_SUBTYPES=MappingProxyType({key:frozenset(str(v).casefold().replace('’',"'") for v in values) for key,values in {
    'artifact':ARTIFACT_SUBTYPES,'enchantment':ENCHANTMENT_SUBTYPES,
    'land':LAND_SUBTYPES,'creature':CREATURE_SUBTYPES,'kindred':CREATURE_SUBTYPES,
    'planeswalker':PLANESWALKER_SUBTYPES,'battle':frozenset({'siege'}),
    'instant':SPELL_SUBTYPES,'sorcery':SPELL_SUBTYPES}.items()})


def correlated_subtypes(subtypes,card_types):
    allowed=frozenset().union(*(CARD_TYPE_SUBTYPES.get(str(kind).casefold(),frozenset()) for kind in card_types))
    return {word for word in subtypes if str(word).casefold().replace('’',"'") in allowed}
