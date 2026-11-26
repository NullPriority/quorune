"""Immutable fixed search data, shared by compilation and runtime validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap, thaw_value
from .continuous_effect_model import ContinuousEffectDuration,ContinuousEffectError

FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID = "fixed-counted-library-search"
FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID = "library.search.fixed_counted"
DIFFERENT_NAME_CREATURE_SEARCH_CAPABILITY = "library.search.two_creatures_with_different_names"
DIFFERENT_NAME_SEARCH_FIELD = "different_names"
SHARED_LAND_TYPE_SEARCH_FIELD = "shared_land_type"
SHARED_LAND_TYPE_SEARCH_CAPABILITY = "library.search.two_basic_lands_shared_type"
_SHARED_LAND_TYPE_SEARCH_SELECTOR = FrozenMap({"types": ["land"], "supertypes": ["basic"]})
DISJUNCTIVE_LIBRARY_SEARCH_MECHANIC_ID = "disjunctive-counted-library-search"
DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID = "library.search.counted_disjunction"
QUERY_BOUND_SEARCH_VERSION = 8
TARGET_NAME_LIBRARY_SEARCH_VERSION = 10
RECRUITING_LIBRARY_SEARCH_VERSION = 12
ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION = 14
EXILED_TARGET_SEARCH_VERSION = 16
FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION = 17
CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION = 23
SIMULTANEOUS_CREATURE_SEARCH_VERSIONS = {
    ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION, FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION,
    CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION,
}
COMMITTED_ATTACKING_EXILE_SEARCH_VERSION = 18
TARGET_NAME_MULTIZONE_EXILE_VERSION = 19
LINKED_ARTIFACT_SEARCH_VERSION = 22
TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION = 21
TARGET_PLAYER_BASIC_LAND_SEARCH_CAPABILITY = "library.search.target_player.basic_to_battlefield_tapped"
TARGET_PLAYER_BASIC_LAND_SEARCH_MECHANIC = "fixed-target-player-basic-land-search"
TARGET_PLAYER_BASIC_LAND_SEARCH_CAPABILITIES = (
    TARGET_PLAYER_BASIC_LAND_SEARCH_CAPABILITY,
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    "target.revalidate_resolution",
)
CREATURE_SUBSET_MANA_VALUE_SEARCH_CAPABILITY = "library.search.creature_subset_total_mana_value"
CREATURE_SUBSET_MANA_VALUE_FIELD = "combined_mana_value_maximum"
PRIVATE_SEARCH_RANDOM_DISCARD_VERSION = 20
PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY = "library.search.hand_random_discard_shuffle"
PRIVATE_SEARCH_RANDOM_DISCARD_MECHANIC = "private-library-search-random-discard"
BARGAIN_SEARCH_VERSION = 24
PAID_ARTIFACT_SEARCH_VERSION = 25
PAID_CREATURE_SEARCH_VERSION = 26
DRAGON_GRAVEYARD_COPY_SEARCH_VERSION = 27
FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_CAPABILITY = "library.search.fixed_creature_battlefield_group"
TARGET_NAME_SEARCH_SNAPSHOT = "_target_name_search_snapshot"
TARGET_NAME_PERMANENT_TYPES = ("artifact", "battle", "creature", "enchantment", "land", "planeswalker")
QUERY_BOUND_SEARCH_CAPABILITY = "library.search.public_query_bound"
QUERY_BOUND_SEARCH_MECHANIC = "public-query-bounded-library-search"
QUERY_BOUND_SEARCH_RESOLVED = "_resolved_query_bound"
SEARCH_ALTERNATIVES = "alternatives"
PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE = "printed_mana_cost_0_or_1"
PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID = (
    "library.search.artifact_printed_mana_cost_0_or_1"
)
PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID = (
    "printed-mana-cost-artifact-search"
)
LEGACY_MANA_COST_0_OR_1_SEARCH_PREDICATE = "mana_cost_0_or_1"
_MANA_VALUE_BOUNDS = frozenset({"equal", "minimum", "maximum"})


def semantic_search_maximum(effect, minimum, option_count):
    from .named_search_model import LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION

    if effect.get("schema_version") in (
        LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, RECRUITING_LIBRARY_SEARCH_VERSION,
        ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION, TARGET_NAME_MULTIZONE_EXILE_VERSION,
        CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION,
    ):
        return option_count
    count = effect.get("count") or {}
    return min(option_count, max(minimum, int(count.get("maximum", minimum))))


def printed_mana_cost_artifact_search_candidate_matches(
    record: Any,
    *,
    zone: str,
) -> bool:
    """Match the printed-cost chapter predicate only in a library.

    The historical ``mana_cost_0_or_1`` predicate is deliberately separate:
    persisted descriptors keep its top-level-record semantics. New printed-
    cost descriptors use the front face of a modal or transforming DFC while
    it is in the library (CR 712.6a).
    """
    if zone != "library":
        return False
    if record.layout in {"modal_dfc", "transform"}:
        if len(record.faces) != 2 or not isinstance(record.faces[0], Mapping):
            return False
        mana_cost = record.faces[0].get("mana_cost")
    else:
        mana_cost = record.mana_cost
    return str(mana_cost or "") in {"{0}", "{1}"}


def mana_cost_search_predicate_matches(
    predicate: str,
    record: Any,
    *,
    zone: str,
) -> bool | None:
    """Evaluate the two bounded exact-cost predicates without conflating them.

    The legacy selector intentionally reads the original top-level field with
    no added zone/face interpretation. New output is library-scoped and uses
    current library characteristics.
    """
    if predicate == LEGACY_MANA_COST_0_OR_1_SEARCH_PREDICATE:
        return record.mana_cost in {"{0}", "{1}"}
    if predicate == PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE:
        return printed_mana_cost_artifact_search_candidate_matches(record, zone=zone)
    return None


def printed_mana_cost_artifact_search_public_results(
    effect: Mapping[str, Any],
    *,
    reveal: bool,
    moved: Sequence[Any],
) -> tuple[Any, ...] | None:
    """Return face-up public results for the nonrevealing printed-cost search.

    ``None`` means this is not that search; an empty tuple means it is, but no
    moved card is publishable. The private result and shuffle remain owned by
    the search resolver.
    """
    selector = effect.get("selector")
    if (
        not isinstance(selector, Mapping)
        or selector.get("predicate") != PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE
        or reveal
    ):
        return None
    public_zones = {"battlefield", "graveyard", "exile", "command"}
    return tuple(
        card for card in moved
        if card.zone in public_zones and not card.face_down
    )


@dataclass(frozen=True, slots=True)
class SemanticSearchResultPublication:
    public_count: int
    public_details: Mapping[str, Any]
    private_details: Mapping[str, Any]
    public_changed_objects: tuple[Any, ...]
    nonrevealing_printed_cost_search: bool


def project_semantic_search_result_publication(
    effect: Mapping[str, Any],
    *,
    source: str,
    destination_spec: str,
    destination: str,
    reveal: bool,
    moved: Sequence[Any],
    allocation_details: Mapping[str, Any],
) -> SemanticSearchResultPublication:
    """Build the historical public/private search payloads in one bounded owner."""
    public_choice = reveal or destination in {
        "battlefield",
        "graveyard",
        "exile",
    }
    public_details: dict[str, Any] = {
        "source": source,
        "destination": destination_spec,
        "count": len(moved),
        "revealed": reveal,
    }
    name_multizone = effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION
    if effect.get("schema_version") == 6 or name_multizone:
        public_details["searched_zones"] = list(effect["zone"])
    if reveal:
        public_details.update(allocation_details)

    public_order = (
        sorted(moved, key=lambda card: card.ref)
        if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION
        else moved
    )
    printed_cost_public_cards = printed_mana_cost_artifact_search_public_results(
        effect,
        reveal=reveal,
        moved=moved,
    )
    nonrevealing_printed_cost_search = printed_cost_public_cards is not None
    actual_public_zones = nonrevealing_printed_cost_search or (
        public_choice
        and effect.get("schema_version") in {
            9,
            *SIMULTANEOUS_CREATURE_SEARCH_VERSIONS,
            COMMITTED_ATTACKING_EXILE_SEARCH_VERSION,
            TARGET_NAME_MULTIZONE_EXILE_VERSION,
            DRAGON_GRAVEYARD_COPY_SEARCH_VERSION,
            TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION,
            LINKED_ARTIFACT_SEARCH_VERSION,
            BARGAIN_SEARCH_VERSION,
            PAID_ARTIFACT_SEARCH_VERSION,
            PAID_CREATURE_SEARCH_VERSION,
        }
    )
    public_cards = public_order
    if nonrevealing_printed_cost_search:
        public_cards = printed_cost_public_cards or ()
    elif actual_public_zones:
        public_cards = tuple(
            card for card in moved
            if card.zone in {"battlefield", "graveyard", "exile", "command"}
            and not card.face_down
        )

    restrict_public_results = nonrevealing_printed_cost_search or (
        public_choice and (name_multizone or effect.get("schema_version") in {
            PAID_ARTIFACT_SEARCH_VERSION, PAID_CREATURE_SEARCH_VERSION,
        })
    )
    public_count = len(moved)
    if restrict_public_results:
        public_count = len(public_cards)
        public_details["count"] = public_count
    if public_choice:
        public_details["objects"] = [card.ref for card in public_cards]
        if len(public_cards) == 1:
            public_details["object"] = public_cards[0].ref

    public_changed_objects = public_cards if (
        restrict_public_results or (public_choice and is_bargain_search_effect(effect))
        or effect.get("schema_version") == DRAGON_GRAVEYARD_COPY_SEARCH_VERSION
    ) else public_order
    private_details = {
        **public_details,
        **allocation_details,
        **({"count": len(moved)} if name_multizone or nonrevealing_printed_cost_search else {}),
        "objects": [card.ref for card in moved],
    }
    return SemanticSearchResultPublication(
        public_count=public_count,
        public_details=public_details,
        private_details=private_details,
        public_changed_objects=tuple(public_changed_objects),
        nonrevealing_printed_cost_search=nonrevealing_printed_cost_search,
    )


LIBRARY_SEARCH_RULE_CAPABILITY='library.search.restriction.static'
LIBRARY_SEARCH_TEMP_CAPABILITY='library.search.prohibition.resolution_until_end_of_turn'
LIBRARY_SEARCH_RULE_HANDLER='permission.action.library-search-rule.v1'
LIBRARY_SEARCH_TEMP_OPERATION='prohibit_library_search_until_end_of_turn'
LIBRARY_SEARCH_TEMP_KIND='resolution_library_search_prohibition'


FETCH_ENTRY_PLACEMENT_VERSION = 1
FETCH_ENTRY_PLACEMENT_FIELD = "fetch_entry_placement_version"


def read_fetch_entry_placement_version(data: Mapping[str, Any]) -> int | None:
    if FETCH_ENTRY_PLACEMENT_FIELD not in data:
        return None
    value = data[FETCH_ENTRY_PLACEMENT_FIELD]
    if type(value) is not int or value != FETCH_ENTRY_PLACEMENT_VERSION:
        raise ValueError("Invalid fetch entry placement version")
    return value


def fetch_entry_placement_fields(version: int | None) -> dict[str, int]:
    if version is None:
        return {}
    fields = {FETCH_ENTRY_PLACEMENT_FIELD: version}
    read_fetch_entry_placement_version(fields)
    return fields


def decode_fixed_tapped_battlefield_search(effect: Any) -> FixedCountedLibrarySearchTemplate | None:
    """Recognize the existing closed one-result placement instruction."""
    required={'op','zone','selector','count','destination','shuffle_after','enters_tapped_override'}
    optional={'searching_player','reveal'}
    if (not isinstance(effect,Mapping) or not required<=set(effect) or set(effect)-required-optional
            or effect['op']!='search' or effect['zone']!='library' or effect['destination']!='battlefield'
            or effect['shuffle_after'] is not True or effect['enters_tapped_override'] is not True
            or effect.get('searching_player','$controller')!='$controller'
            or effect.get('reveal',False) is not False):return None
    count=effect['count']
    if (not isinstance(count,Mapping) or set(count)!={'minimum','maximum'}
            or any(type(count[k]) is not int or count[k]!=1 for k in count)):return None
    selector=effect['selector'];fields={'types':'types_all','types_any':'types_any','subtypes_any':'subtypes_any','supertypes':'supertypes_all','colors_any':'colors_any'}
    if not isinstance(selector,Mapping) or not selector or set(selector)-set(fields):return None
    if any(not isinstance(v,(list,tuple)) or any(type(x) is not str for x in v) for v in selector.values()):return None
    try:
        query=ObjectQuerySpec(zones=('library',),**{fields[k]:tuple(v) for k,v in selector.items()})
        normalized={k:list(getattr(query,fields[k])) for k in selector}
        return FixedCountedLibrarySearchTemplate(count=1,optional_count=False,
            selector=FrozenMap(normalized),destination='battlefield',reveal=False,enters_tapped=True)
    except (TypeError,ValueError):return None


@dataclass(frozen=True,slots=True)
class LibrarySearchRuleSpec:
    mode:str
    player_scope:str='all'
    top_count:int|None=None

    def __post_init__(self):
        if self.mode not in {'prohibit','top'} or self.player_scope not in {'all','opponents'}:
            raise ValueError('Library search rule mode or player scope is unsupported')
        if self.mode=='top':
            if type(self.top_count) is not int or self.top_count<1:raise ValueError('Library search window must be a positive fixed integer')
        elif self.top_count is not None:raise ValueError('Search prohibition cannot carry a window')

    def to_dict(self):return {'schema_version':1,'mode':self.mode,'player_scope':self.player_scope,'top_count':self.top_count}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','mode','player_scope','top_count'}:
            raise ValueError('Library search rule fields are closed')
        if type(value['schema_version']) is not int or value['schema_version']!=1:raise ValueError('Library search rule version is unsupported')
        return cls(value['mode'],value['player_scope'],value['top_count'])


@dataclass(frozen=True,slots=True)
class ProhibitLibrarySearchIntent:
    actor:str
    source_id:str

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.actor,self.source_id)):raise ValueError('Search prohibition requires an actor and resolving source')


@dataclass(frozen=True,slots=True)
class ResolvedLibrarySearchProhibition:
    effect_id:str
    source_id:str
    timestamp:int
    controller:str
    duration:ContinuousEffectDuration=ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if (any(type(v) is not str or not v for v in (self.effect_id,self.source_id,self.controller))
            or type(self.timestamp) is not int or self.timestamp<0 or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN):
            raise ContinuousEffectError('Resolved library prohibition requires identity and end-of-turn duration')

    def to_dict(self):return {'effect_kind':LIBRARY_SEARCH_TEMP_KIND,'effect_id':self.effect_id,'source_id':self.source_id,
        'timestamp':self.timestamp,'controller':self.controller,'duration':self.duration.value}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'effect_kind','effect_id','source_id','timestamp','controller','duration'} or value['effect_kind']!=LIBRARY_SEARCH_TEMP_KIND:
            raise ContinuousEffectError('Resolved library prohibition fields are closed')
        return cls(value['effect_id'],value['source_id'],value['timestamp'],value['controller'],ContinuousEffectDuration(value['duration']))
_PERMANENT_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)
_CARD_TYPES = _PERMANENT_TYPES | {"instant", "kindred", "sorcery"}
SEARCH_LAND_SUBTYPES = frozenset(
    {"plains", "island", "swamp", "mountain", "forest", "cave", "desert", "gate", "town"}
)
SEARCH_NONCREATURE_SUBTYPES = frozenset(
    {"arcane", "aura", "equipment", "lesson", "plan", "trap", "vehicle"}
)
SEARCH_SUBTYPES = CREATURE_SUBTYPES | SEARCH_LAND_SUBTYPES | SEARCH_NONCREATURE_SUBTYPES


def search_query_is_representable(query: ObjectQuerySpec) -> bool:
    """Keep the existing selector codec lossless within its closed vocabulary."""
    if not isinstance(query, ObjectQuerySpec):
        return False
    if (
        set((*query.types_all, *query.types_any)) - _CARD_TYPES
        or set(query.subtypes_any) - SEARCH_SUBTYPES
        or set(query.supertypes_all) - {"basic", "legendary", "snow"}
        or set(query.colors_any) - {"W", "U", "B", "R", "G"}
    ):
        return False
    return query == ObjectQuerySpec(
        types_all=query.types_all, types_any=query.types_any,
        subtypes_any=query.subtypes_any, supertypes_all=query.supertypes_all,
        colors_any=query.colors_any,
    )


def search_selector(query: ObjectQuerySpec) -> dict[str, list[str]]:
    if not search_query_is_representable(query):
        raise ValueError("Search query cannot be represented by the closed selector codec")
    fields = {
        "types": query.types_all,
        "types_any": query.types_any,
        "subtypes_any": query.subtypes_any,
        "supertypes": query.supertypes_all,
        "colors_any": query.colors_any,
    }
    return {name: list(values) for name, values in fields.items() if values}


def counted_search_mana_value_slot(path: tuple[str, ...]) -> bool:
    return len(path) == 3 and path[:2] == ("selector", "mana_value") and path[2] in _MANA_VALUE_BOUNDS


@dataclass(frozen=True, slots=True)
class TargetNameLibrarySearchSpec:
    permanent_to_battlefield: bool

    def __post_init__(self):
        if type(self.permanent_to_battlefield) is not bool:
            raise ValueError("Target-name search result kind must be boolean")

    def target_schema(self):
        return {"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                "types_any": ["creature"], **({"token": False} if self.permanent_to_battlefield else {})}

    def effect(self):
        permanent = self.permanent_to_battlefield
        return {
            "op": "search", "schema_version": TARGET_NAME_LIBRARY_SEARCH_VERSION, "zone": "library",
            "name_target": "$target.0", "selector": {"types_any": list(TARGET_NAME_PERMANENT_TYPES)} if permanent else {},
            "count": {"minimum": 0, "maximum": 1 if permanent else 3},
            "destination": "battlefield" if permanent else "hand", "reveal": not permanent,
            "shuffle_after": True, "shuffle_before_placement": False,
        }

    def compiled(self):
        return ("target-creature-name-library-search-v1", (self.effect(),), self.target_schema(),
                (FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, "cr-115-targets"))

    def capabilities(self):
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, "target.revalidate_resolution")

    @classmethod
    def from_effect(cls, effect, *, materialized=False):
        if not isinstance(effect, Mapping):
            raise ValueError("Target-name search requires its typed instruction")
        spec = cls(effect.get("destination") == "battlefield")
        expected = spec.effect()
        values = dict(effect)
        if materialized:
            target = values.get("name_target")
            if type(target) is not str or not target or target != target.strip() or target.startswith("$"):
                raise ValueError("Target-name search requires its materialized creature")
            expected["name_target"] = target
            if TARGET_NAME_SEARCH_SNAPSHOT in values:
                snapshot = values.pop(TARGET_NAME_SEARCH_SNAPSHOT)
                if (not isinstance(snapshot, Mapping)
                        or set(snapshot) != {"reference", "logical_object_id", "names"}
                        or snapshot["reference"] != target
                        or type(snapshot["logical_object_id"]) is not str or not snapshot["logical_object_id"]
                        or not isinstance(snapshot["names"], (list, tuple))
                        or any(type(name) is not str or not name or name != name.strip() for name in snapshot["names"])
                        or len(set(snapshot["names"])) != len(snapshot["names"])):
                    raise ValueError("Target-name search snapshot is malformed")
        if (FrozenMap(values) != FrozenMap(expected) or type(effect["schema_version"]) is not int
                or type(effect["reveal"]) is not bool or type(effect["shuffle_after"]) is not bool
                or type(effect["shuffle_before_placement"]) is not bool
                or any(type(value) is not int for value in effect["count"].values())):
            raise ValueError("Target-name search requires its exact source and result shape")
        return spec


@dataclass(frozen=True, slots=True)
class RecruitingLibrarySearchSpec:
    subtype: str

    def __post_init__(self):
        if type(self.subtype) is not str or canonical_creature_subtype(self.subtype) != self.subtype:
            raise ValueError("Recruiting search needs one canonical creature subtype")

    def effect(self):
        return {
            "op": "search", "schema_version": RECRUITING_LIBRARY_SEARCH_VERSION, "zone": "library",
            "selector": {"types_any": ["creature", "kindred"], "subtypes_any": [self.subtype]},
            "count": {"minimum": 0, "maximum": "all_eligible"},
            "destination": "library_top", "reveal": True,
            "shuffle_before_placement": True, "shuffle_after": False,
        }

    @classmethod
    def from_effect(cls, effect):
        if not isinstance(effect, Mapping):
            raise ValueError("Recruiting search requires its typed instruction")
        selector = effect.get("selector")
        subtypes = selector.get("subtypes_any") if isinstance(selector, Mapping) else None
        if not isinstance(subtypes, (list, tuple)) or len(subtypes) != 1:
            raise ValueError("Recruiting search needs one canonical creature subtype")
        spec = cls(subtypes[0])
        if (FrozenMap(effect) != FrozenMap(spec.effect()) or type(effect["schema_version"]) is not int
                or type(effect["count"]["minimum"]) is not int or effect["reveal"] is not True
                or effect["shuffle_before_placement"] is not True or effect["shuffle_after"] is not False):
            raise ValueError("Recruiting search needs its exact reveal, shuffle and ordered top result")
        return spec


@dataclass(frozen=True, slots=True)
class AnyNumberCreatureSubtypeSearch:
    subtype: str

    def __post_init__(self):
        if type(self.subtype) is not str or canonical_creature_subtype(self.subtype) != self.subtype:
            raise ValueError("An any-number search requires one canonical creature subtype")

    def effect(self):
        return {
            "op": "search",
            "schema_version": ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION,
            "zone": "library",
            "selector": {"types_any": ["creature"], "subtypes_any": [self.subtype]},
            "count": {"minimum": 0, "maximum": "all_eligible"},
            "destination": "battlefield",
            "reveal": False,
            "shuffle_after": True,
        }

    @classmethod
    def from_effect(cls, effect):
        if not isinstance(effect, Mapping):
            raise ValueError("An any-number search requires its typed instruction")
        selector = effect.get("selector")
        subtypes = selector.get("subtypes_any") if isinstance(selector, Mapping) else None
        if not isinstance(subtypes, (list, tuple)) or len(subtypes) != 1:
            raise ValueError("An any-number search requires one canonical creature subtype")
        spec = cls(subtypes[0])
        if (
            FrozenMap(effect) != FrozenMap(spec.effect())
            or type(effect["schema_version"]) is not int
            or type(effect["count"]["minimum"]) is not int
            or effect["reveal"] is not False
            or effect["shuffle_after"] is not True
        ):
            raise ValueError("An any-number search requires its exact private selector and battlefield result")
        return spec

    def compiled(self):
        return (
            "creature-subtype-any-number-library-battlefield-v1", (self.effect(),), None,
            (FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,),
        )


@dataclass(frozen=True, slots=True)
class CreatureSubsetManaValueSearch:
    """The supported aggregate bound of a private creature search."""

    maximum: int

    def __post_init__(self):
        if type(self.maximum) is not int or self.maximum != 6:
            raise ValueError("The supported creature subset search has a total mana value limit of six")

    def effect(self) -> dict[str, Any]:
        return {
            "op": "search",
            "schema_version": CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION,
            "zone": "library",
            "selector": {"types_any": ["creature"], "mana_value": {"maximum": self.maximum}},
            "count": {"minimum": 0, "maximum": "all_eligible"},
            CREATURE_SUBSET_MANA_VALUE_FIELD: self.maximum,
            "destination": "battlefield",
            "reveal": False,
            "shuffle_after": True,
        }

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> CreatureSubsetManaValueSearch:
        if not isinstance(effect, Mapping):
            raise ValueError("Creature subset search requires its typed instruction")
        spec = cls(effect.get(CREATURE_SUBSET_MANA_VALUE_FIELD))
        if (
            FrozenMap(effect) != FrozenMap(spec.effect())
            or type(effect["schema_version"]) is not int
            or type(effect["selector"]["mana_value"]["maximum"]) is not int
            or type(effect["count"]["minimum"]) is not int
            or effect["reveal"] is not False or effect["shuffle_after"] is not True
        ):
            raise ValueError("Creature subset search requires its exact private selector, bound and battlefield result")
        return spec


def fixed_creature_group_search_effect():
    return {
        "op": "search", "schema_version": FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION, "zone": "library",
        "selector": {"types_any": ["creature"]}, "count": {"minimum": 0, "maximum": 2},
        "destination": "battlefield", "reveal": False, "shuffle_after": True,
    }


def validate_fixed_creature_group_search(effect):
    if (not isinstance(effect, Mapping) or FrozenMap(effect) != FrozenMap(fixed_creature_group_search_effect())
            or type(effect["schema_version"]) is not int
            or any(type(value) is not int for value in effect["count"].values())
            or effect["reveal"] is not False or effect["shuffle_after"] is not True):
        raise ValueError("Fixed creature group search requires up to two creatures and one simultaneous battlefield entry")


def committed_attacking_exile_search_effect(player, count):
    if (type(player) is not str or not player or player.startswith("$")
            or type(count) is not int or count < 0):
        raise ValueError("Committed attacker search requires a resolved player and nonnegative count")
    return {
        "op": "search", "schema_version": COMMITTED_ATTACKING_EXILE_SEARCH_VERSION, "zone": "library",
        "searching_player": player, "selector": {"types": ["land"], "supertypes": ["basic"]},
        "count": {"minimum": 0, "maximum": count}, "destination": "battlefield",
        "enters_tapped_override": True, "reveal": False, "shuffle_after": True,
    }


def validate_committed_attacking_exile_search(effect):
    if not isinstance(effect, Mapping) or not isinstance(effect.get("count"), Mapping):
        raise ValueError("Committed attacker search was not bound by its exile result")
    expected = committed_attacking_exile_search_effect(effect.get("searching_player"), effect["count"].get("maximum"))
    if (FrozenMap(effect) != FrozenMap(expected) or type(effect["schema_version"]) is not int
            or type(effect["count"]["minimum"]) is not int or effect["enters_tapped_override"] is not True
            or effect["reveal"] is not False or effect["shuffle_after"] is not True):
        raise ValueError("Committed attacker search requires its exact fixed count and tapped basic land result")


def target_player_basic_land_search_effect(searching_player: str = "$target.0") -> dict[str, Any]:
    """Describe one mandatory private search by its original target."""
    if (type(searching_player) is not str or not searching_player
            or (searching_player.startswith("$") and searching_player != "$target.0")):
        raise ValueError("The targeted basic-land search requires one searching player")
    return {
        "op": "search", "schema_version": TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION,
        "searching_player": searching_player, "zone": "library",
        "selector": {"types": ["land"], "supertypes": ["basic"]},
        "count": {"minimum": 1, "maximum": 1}, "destination": "battlefield",
        "enters_tapped_override": True, "reveal": False, "shuffle_after": True,
    }


def validate_target_player_basic_land_search(
    effect: Mapping[str, Any], *, searching_player: str = "$target.0",
) -> None:
    expected = target_player_basic_land_search_effect(searching_player)
    if (not isinstance(effect, Mapping) or FrozenMap(effect) != FrozenMap(expected)
            or type(effect["schema_version"]) is not int
            or any(type(value) is not int for value in effect["count"].values())
            or effect["enters_tapped_override"] is not True
            or effect["reveal"] is not False or effect["shuffle_after"] is not True):
        raise ValueError("Targeted search requires one basic land, tapped entry, and a mandatory shuffle")


def private_search_before_random_discard_effect() -> dict[str, Any]:
    search = FixedCountedLibrarySearchTemplate(
        count=1, optional_count=False, selector=FrozenMap({}), destination="hand", reveal=False,
    )
    return {
        **search.effect(),
        "schema_version": PRIVATE_SEARCH_RANDOM_DISCARD_VERSION,
        "shuffle_after": False,
    }


def validate_private_search_before_random_discard(effect: Mapping[str, Any]) -> None:
    if not isinstance(effect, Mapping):
        raise ValueError("Private search requires a typed search descriptor")
    value = thaw_value(effect)
    if (
        value != private_search_before_random_discard_effect()
        or type(value["schema_version"]) is not int
        or any(type(count) is not int for count in value["count"].values())
        or any(value[field] is not False for field in ("reveal", "shuffle_after", "shuffle_before_placement"))
    ):
        raise ValueError("Private search requires one unrestricted card to hand with a delayed shuffle")


def is_bargain_search_effect(value) -> bool:
    return (
        isinstance(value, Mapping) and value.get("op") == "search"
        and value.get("schema_version") == BARGAIN_SEARCH_VERSION
    )


def is_counted_library_search(effect: Mapping[str, Any]) -> bool:
    from .named_search_model import LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION

    selector = effect.get("selector")
    return SHARED_LAND_TYPE_SEARCH_FIELD in effect or effect.get("schema_version") in (
        2, 3, 4, 5, QUERY_BOUND_SEARCH_VERSION, TARGET_NAME_LIBRARY_SEARCH_VERSION,
        LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION, RECRUITING_LIBRARY_SEARCH_VERSION,
        ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION, EXILED_TARGET_SEARCH_VERSION,
        FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION, COMMITTED_ATTACKING_EXILE_SEARCH_VERSION,
        TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION,
        CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION,
        PRIVATE_SEARCH_RANDOM_DISCARD_VERSION,
        BARGAIN_SEARCH_VERSION,
        PAID_ARTIFACT_SEARCH_VERSION, PAID_CREATURE_SEARCH_VERSION,
    ) or (
        isinstance(selector, Mapping) and SEARCH_ALTERNATIVES in selector
    )


def counted_search_bound_dependencies(effect: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate disjunctions and persisted X bounds before search opens."""
    if effect.get("op") != "search":
        return ()
    if effect.get("schema_version") == TARGET_NAME_LIBRARY_SEARCH_VERSION:
        return TargetNameLibrarySearchSpec.from_effect(effect).capabilities()
    if effect.get("schema_version") == PRIVATE_SEARCH_RANDOM_DISCARD_VERSION:
        validate_private_search_before_random_discard(effect)
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, PRIVATE_SEARCH_RANDOM_DISCARD_CAPABILITY)
    from .named_search_model import (
        LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION, decode_literal_library_search,
    )

    if effect.get("schema_version") in (LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION):
        decode_literal_library_search(effect)
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,)
    if effect.get("schema_version") == ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION:
        AnyNumberCreatureSubtypeSearch.from_effect(effect)
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,)
    if (
        effect.get("schema_version") == CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION
        or CREATURE_SUBSET_MANA_VALUE_FIELD in effect
    ):
        CreatureSubsetManaValueSearch.from_effect(effect)
        return FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, CREATURE_SUBSET_MANA_VALUE_SEARCH_CAPABILITY
    if effect.get("schema_version") == COMMITTED_ATTACKING_EXILE_SEARCH_VERSION:
        raise ValueError("An attacker search count must come from committed exile")
    if effect.get("schema_version") == TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION:
        validate_target_player_basic_land_search(effect)
        return TARGET_PLAYER_BASIC_LAND_SEARCH_CAPABILITIES
    if effect.get("schema_version") == FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION:
        validate_fixed_creature_group_search(effect)
        return FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_CAPABILITY
    if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION:
        RecruitingLibrarySearchSpec.from_effect(effect)
        from .library_order_model import TOP_ORDER_CAPABILITY

        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, TOP_ORDER_CAPABILITY)
    if effect.get('schema_version') == QUERY_BOUND_SEARCH_VERSION:
        return QueryBoundLibrarySearchSpec.from_effect(effect).capabilities()
    if effect.get('schema_version') == 7:
        from .qualified_library_search_model import QualifiedLibrarySearchSpec
        return QualifiedLibrarySearchSpec.from_effect(effect).capabilities()
    if effect.get('schema_version') == 5:
        from .search_allocation_model import SearchAllocationSpec
        return SearchAllocationSpec.from_effect(effect).capabilities()
    if effect.get('schema_version')==4:
        from .conditional_search_model import ConditionalSearchSpec
        return ConditionalSearchSpec.from_effect(effect).capabilities()
    if SHARED_LAND_TYPE_SEARCH_FIELD in effect:
        FixedCountedLibrarySearchTemplate.from_effect(effect)
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, SHARED_LAND_TYPE_SEARCH_CAPABILITY)
    if DIFFERENT_NAME_SEARCH_FIELD in effect:
        FixedCountedLibrarySearchTemplate.from_effect(effect)
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, DIFFERENT_NAME_CREATURE_SEARCH_CAPABILITY)
    selector = effect.get("selector")
    if effect.get("schema_version") == 3 or isinstance(selector, Mapping) and SEARCH_ALTERNATIVES in selector:
        FixedCountedLibrarySearchTemplate.from_effect(effect)
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID)
    if is_up_to_two_forest_search(effect):
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, UP_TO_TWO_FOREST_BATCH_CAPABILITY)
    from .entry_x_amount_model import ENTRY_X_AMOUNT_KIND, EntryXAmountSpec, entry_x_amount_dependencies
    from .query_effect_amount_model import CAST_X_AMOUNT_KIND, CastXAmountSpec, cast_x_runtime_dependencies

    dependencies = (*cast_x_runtime_dependencies(effect), *entry_x_amount_dependencies(effect))
    bounds = selector.get("mana_value") if isinstance(selector, Mapping) else None
    symbolic_bound = isinstance(bounds, Mapping) and any(isinstance(value, Mapping) for value in bounds.values())
    if not dependencies and not symbolic_bound:
        return ()
    if not isinstance(bounds, Mapping) or len(bounds) != 1:
        raise ValueError("A counted search X requires one mana value bound")
    comparison, amount = next(iter(bounds.items()))
    if comparison not in _MANA_VALUE_BOUNDS or not isinstance(amount, Mapping):
        raise ValueError("A counted search X must occupy its mana value bound")
    kind = amount.get("kind")
    if kind == CAST_X_AMOUNT_KIND:
        spec = CastXAmountSpec.from_dict(amount)
    elif kind == ENTRY_X_AMOUNT_KIND:
        spec = EntryXAmountSpec.from_dict(amount)
    else:
        raise ValueError("A counted search bound requires announced or sealed entry X")
    if spec.coefficient <= 0:
        raise ValueError("A counted search bound requires a positive X coefficient")
    fixed = {**effect, "selector": {**selector, "mana_value": {comparison: 2 * spec.coefficient}}}
    FixedCountedLibrarySearchTemplate.from_effect(fixed)
    return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, *dependencies)


def _counted_search_leaf_is_closed(
    selector: Mapping[str, Any], *, destination: str,
) -> bool:
    """Validate the fixed query data shared with the existing search owner."""
    if not isinstance(selector, Mapping) or set(selector) - {
        "types", "types_any", "subtypes_any", "supertypes", "colors_any", "names", "mana_value",
    }:
        return False
    ordinary = {
        key: value for key, value in selector.items()
        if key not in {"names", "mana_value"}
    }
    if any(
        not isinstance(value, (list, tuple)) or not value
        or any(type(item) is not str or not item for item in value)
        for value in ordinary.values()
    ):
        return False
    try:
        query = ObjectQuerySpec(
            types_all=tuple(ordinary.get("types", ())),
            types_any=tuple(ordinary.get("types_any", ())),
            subtypes_any=tuple(ordinary.get("subtypes_any", ())),
            supertypes_all=tuple(ordinary.get("supertypes", ())),
            colors_any=tuple(ordinary.get("colors_any", ())),
        )
    except (TypeError, ValueError):
        return False
    if not search_query_is_representable(query):
        return False
    if search_selector(query) != {key: list(value) for key, value in ordinary.items()}:
        return False
    types = set((*query.types_all, *query.types_any))
    if types - _CARD_TYPES:
        return False
    if destination == "battlefield" and (not types or types - _PERMANENT_TYPES):
        return False
    if "names" in selector:
        names = selector["names"]
        if (
            not isinstance(names, (list, tuple)) or len(names) != 1
            or type(names[0]) is not str or not names[0].strip()
            or "\n" in names[0]
        ):
            return False
    if "mana_value" in selector:
        constraint = selector["mana_value"]
        if (
            not isinstance(constraint, Mapping)
            or set(constraint) not in ({"equal"}, {"minimum"}, {"maximum"})
            or any(type(value) is not int or value < 0 for value in constraint.values())
        ):
            return False
    return True


def counted_search_selector_is_closed(selector: Mapping[str, Any], *, destination: str) -> bool:
    if not isinstance(selector, Mapping) or SEARCH_ALTERNATIVES not in selector:
        return _counted_search_leaf_is_closed(selector, destination=destination)
    leaves = selector[SEARCH_ALTERNATIVES]
    if set(selector) != {SEARCH_ALTERNATIVES} or not isinstance(leaves, (list, tuple)) or not 2 <= len(leaves) <= 4:
        return False
    if not all(leaf and _counted_search_leaf_is_closed(leaf, destination=destination) for leaf in leaves):
        return False
    return len({FrozenMap(leaf) for leaf in leaves}) == len(leaves)


UP_TO_TWO_FOREST_BATCH_CAPABILITY = "library.search.up_to_two_forests_to_battlefield"
_TWO_FOREST_SELECTOR = FrozenMap({"types": ["land"], "subtypes_any": ["forest"]})


def up_to_two_forest_search():
    return FixedCountedLibrarySearchTemplate(2, True, _TWO_FOREST_SELECTOR, "battlefield", False)


def is_up_to_two_forest_search(effect):
    if not isinstance(effect, Mapping) or effect.get("schema_version") != 2:
        return False
    count = effect.get("count")
    if (type(effect["schema_version"]) is not int or not isinstance(count, Mapping)
            or any(type(count.get(key)) is not int for key in ("minimum", "maximum"))):
        return False
    expected = up_to_two_forest_search().effect()
    identities = {key: value for key, value in effect.items()
                  if key not in {"_replacement_selections", "_search_completion"}}
    return FrozenMap(identities) == FrozenMap(expected)


@dataclass(frozen=True, slots=True)
class FixedCountedLibrarySearchTemplate:
    count: int
    optional_count: bool
    selector: FrozenMap
    destination: str
    reveal: bool
    enters_tapped: bool = False
    different_names: bool = False
    shared_land_type: bool = False

    def __post_init__(self) -> None:
        if type(self.count) is not int or not 1 <= self.count <= 10:
            raise ValueError("Fixed search count must be between one and ten")
        if any(
            type(value) is not bool
            for value in (self.optional_count, self.reveal, self.enters_tapped, self.different_names, self.shared_land_type)
        ):
            raise ValueError("Fixed search flags must be booleans")
        if (
            type(self.destination) is not str
            or self.destination not in {"hand", "battlefield", "graveyard", "library_top"}
        ):
            raise ValueError("Fixed search destination or cardinality is unsupported")
        if self.different_names and (self.count != 2 or not self.optional_count or self.destination != "hand"
                or not self.reveal or self.enters_tapped or FrozenMap(self.selector) != FrozenMap({"types": ["creature"]})):
            raise ValueError("Different names require the complete up-to-two creature search and reveal")
        if self.shared_land_type and (self.count != 2 or not self.optional_count or self.destination != "battlefield"
                or self.reveal or not self.enters_tapped or FrozenMap(self.selector) != _SHARED_LAND_TYPE_SEARCH_SELECTOR):
            raise ValueError("Shared land type requires the complete up-to-two basic-land search and tapped entry")
        if self.enters_tapped and self.destination != "battlefield":
            raise ValueError("Only battlefield search results may enter tapped")
        if not counted_search_selector_is_closed(self.selector, destination=self.destination):
            raise ValueError("Fixed search selector is outside the closed vocabulary")
        object.__setattr__(self, "selector", FrozenMap(self.selector))
        if (self.count > 1 and self.destination != "hand"
                and not (self._tapped_land_batch() or self.up_to_two_forest_batch or self.shared_land_type)):
            raise ValueError("Fixed search destination or cardinality is unsupported")

    @property
    def up_to_two_forest_batch(self):
        return (self.count == 2 and self.optional_count and self.destination == "battlefield"
                and not self.reveal and not self.enters_tapped and self.selector == _TWO_FOREST_SELECTOR)

    def _tapped_land_batch(self) -> bool:
        return (
            self.destination == "battlefield" and self.enters_tapped and self.disjunctive
            and all(tuple(leaf.get("types", ())) == ("land",) for leaf in self.selector[SEARCH_ALTERNATIVES])
        )

    @property
    def disjunctive(self) -> bool:
        return SEARCH_ALTERNATIVES in self.selector

    def effect(self) -> dict[str, Any]:
        value = {
            "op": "search",
            "schema_version": 3 if self.disjunctive else 2,
            "zone": "library",
            "selector": thaw_value(self.selector),
            "count": {"minimum": 0 if self.optional_count else self.count, "maximum": self.count},
            "destination": self.destination,
            "reveal": self.reveal,
            "shuffle_after": self.destination != "library_top",
            "shuffle_before_placement": self.destination == "library_top",
        }
        if self.enters_tapped:
            value["enters_tapped_override"] = True
        if self.different_names:
            value[DIFFERENT_NAME_SEARCH_FIELD] = True
        if self.shared_land_type:
            value[SHARED_LAND_TYPE_SEARCH_FIELD] = True
        return value

    def compiled(self) -> tuple[str, tuple[dict[str, Any], ...], None, tuple[str, ...]]:
        effect = self.effect()
        mechanics = (FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,)
        if self.disjunctive:
            mechanics = (*mechanics, DISJUNCTIVE_LIBRARY_SEARCH_MECHANIC_ID)
        return (
            f"fixed-counted-library-search-v{effect['schema_version']}", (effect,), None, mechanics,
        )

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> "FixedCountedLibrarySearchTemplate":
        if isinstance(effect, Mapping) and effect.get('schema_version') == 5:
            from .search_allocation_model import SearchAllocationSpec
            return SearchAllocationSpec.from_effect(effect).search
        if isinstance(effect,Mapping) and effect.get('schema_version')==4:
            from .conditional_search_model import ConditionalSearchSpec
            return ConditionalSearchSpec.from_effect(effect).search
        fields = {
            "op", "schema_version", "zone", "selector", "count", "destination",
            "reveal", "shuffle_after", "shuffle_before_placement",
        }
        if (
            not isinstance(effect, Mapping)
            or set(effect) not in (fields, fields | {"enters_tapped_override"}, fields | {DIFFERENT_NAME_SEARCH_FIELD},
                                  fields | {"enters_tapped_override", SHARED_LAND_TYPE_SEARCH_FIELD})
            or type(effect.get("schema_version")) is not int
            or effect["schema_version"] not in (2, 3)
        ):
            raise ValueError("Counted search instruction fields are incomplete or unknown")
        count = effect["count"]
        if (
            not isinstance(count, Mapping) or set(count) != {"minimum", "maximum"}
            or type(count["minimum"]) is not int or type(count["maximum"]) is not int
            or count["minimum"] not in {0, count["maximum"]}
        ):
            raise ValueError("Counted search bounds are malformed")
        if not isinstance(effect["selector"], Mapping):
            raise ValueError("Counted search selector must be a mapping")
        value = cls(
            count=count["maximum"], optional_count=count["minimum"] == 0,
            selector=FrozenMap(effect["selector"]), destination=effect["destination"],
            reveal=effect["reveal"],
            enters_tapped=effect.get("enters_tapped_override", False),
            different_names=effect.get(DIFFERENT_NAME_SEARCH_FIELD, False),
            shared_land_type=effect.get(SHARED_LAND_TYPE_SEARCH_FIELD, False),
        )
        if value.effect() != thaw_value(effect):
            raise ValueError("Counted search instruction contradicts its typed contract")
        return value


@dataclass(frozen=True, slots=True)
class QueryBoundLibrarySearchSpec:
    """A closed basic-land search with an applied public-state maximum."""
    amount: Any
    destination: str = 'battlefield'
    reveal: bool = False
    enters_tapped: bool = True

    def __post_init__(self):
        from .query_effect_amount_model import PublicQueryAmountSpec
        from .characteristic_fragments import (
            CharacteristicQuantitySpec,CharacteristicQuantityScope,CharacteristicQuantityReduction,
        )
        from .live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
        if (not isinstance(self.amount,PublicQueryAmountSpec) or self.amount.schema_version!=1
                or self.amount.coefficient!=1 or self.amount.binding_id is not None):
            raise ValueError('Query-bounded search requires one unbound positive public quantity')
        allowed=(
            CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
                ObjectQuerySpec(zones=('battlefield',),types_all=('land',))),
            CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
                ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),tapped=True)),
            CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
                ObjectQuerySpec(zones=('battlefield',),types_all=('enchantment',),subtypes_all=('shrine',))),
            CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,
                ObjectQuerySpec(zones=('battlefield',)),schema_version=2,
                reduction=CharacteristicQuantityReduction.DISTINCT_COLORS),
        )
        q=self.amount.quantity
        if q not in allowed and not (isinstance(q,LiveBattlefieldQuantitySpec) and q.schema_version==3):
            raise ValueError('Query-bounded search maximum is outside the closed public profiles')
        if (type(self.reveal) is not bool or type(self.enters_tapped) is not bool
                or (self.destination,self.reveal,self.enters_tapped) not in {
                    ('battlefield',False,True),('hand',True,False)}):
            raise ValueError('Query-bounded search requires its complete printed placement')

    def effect(self):
        value={'op':'search','schema_version':QUERY_BOUND_SEARCH_VERSION,'zone':'library',
            'selector':{'types':['land'],'supertypes':['basic']},
            'count':{'minimum':0,'maximum':None},'destination':self.destination,
            'reveal':self.reveal,'shuffle_after':True,'shuffle_before_placement':False,
            'bound':self.amount.to_dict()}
        if self.enters_tapped:value['enters_tapped_override']=True
        return value

    def resolved_effect(self,value,frozen_amount):
        from .query_effect_amount_model import PublicQueryAmountSpec
        if (type(value) is not int or value<0 or not isinstance(frozen_amount,PublicQueryAmountSpec)
                or frozen_amount.schema_version!=2 or frozen_amount.quantity!=self.amount.quantity
                or frozen_amount.coefficient!=1):
            raise ValueError('Resolved query search limit is malformed')
        return {**self.effect(),'count':{'minimum':0,'maximum':value},
            QUERY_BOUND_SEARCH_RESOLVED:frozen_amount.to_dict()}

    @classmethod
    def from_effect(cls,effect,*,allow_resolved=False):
        from .query_effect_amount_model import PublicQueryAmountSpec
        fields={'op','schema_version','zone','selector','count','destination','reveal',
            'shuffle_after','shuffle_before_placement','bound'}
        if (not isinstance(effect,Mapping) or type(effect.get('schema_version')) is not int
                or effect['schema_version']!=QUERY_BOUND_SEARCH_VERSION or effect.get('op')!='search'
                or effect.get('zone')!='library'
                or effect.get('selector')!={'types':['land'],'supertypes':['basic']}
                or effect.get('shuffle_after') is not True or effect.get('shuffle_before_placement') is not False):
            raise ValueError('Query-bounded search requires its complete version8 instruction')
        expected=fields|({'enters_tapped_override'} if effect.get('destination')=='battlefield' else set())
        resolved=QUERY_BOUND_SEARCH_RESOLVED in effect
        if set(effect)!=expected|({QUERY_BOUND_SEARCH_RESOLVED} if resolved else set()):
            raise ValueError('Query-bounded search has unknown or missing fields')
        spec=cls(PublicQueryAmountSpec.from_dict(effect['bound']),effect['destination'],
            effect['reveal'],effect.get('enters_tapped_override',False))
        count=effect['count']
        if (not isinstance(count,Mapping) or set(count)!={'minimum','maximum'}
                or type(count['minimum']) is not int or count['minimum']!=0):
            raise ValueError('Query-bounded search cardinality is malformed')
        if not resolved:
            if count['maximum'] is not None:raise ValueError('Unresolved search cannot inject a literal maximum')
        else:
            if not allow_resolved or type(count['maximum']) is not int or count['maximum']<0:
                raise ValueError('Resolved search maximum is internal continuation data')
            frozen=PublicQueryAmountSpec.from_dict(effect[QUERY_BOUND_SEARCH_RESOLVED])
            if (frozen.schema_version!=2 or frozen.quantity!=spec.amount.quantity or frozen.coefficient!=1
                    or not frozen.binding_id.startswith('query-search:')
                    or not frozen.binding_id.removeprefix('query-search:').isdigit()):
                raise ValueError('Resolved search lost its quantity and instruction identity')
        return spec

    def capabilities(self):
        from .characteristic_fragments import characteristic_quantity_capabilities
        return tuple(dict.fromkeys((QUERY_BOUND_SEARCH_CAPABILITY,
            FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
            "quantity_expression.public_query_effect_amount",*characteristic_quantity_capabilities(self.amount.to_dict()))))

    def compiled(self):
        return ('public-query-bounded-basic-land-search-v1',(self.effect(),),None,
            (FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,QUERY_BOUND_SEARCH_MECHANIC))
