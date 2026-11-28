"""Closed reductions over typed current-turn history, never presentation logs."""

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from .creature_subtypes import canonical_creature_subtype
from .model import COMMITTED_OCCURRENCE_HISTORY_KINDS, PUBLIC_MOVEMENT_HISTORY_KINDS
from .turn_history_model import (
    ARTIFACT_SUBTYPE_ENTRY_EVENT,
    CAST_CREATURE_COUNTERED_EVENT, CREATURE_COUNTER_PLACEMENT_EVENT, DRAWN_HAND_CARD_EVENT, PERMANENT_COUNTER_RECEIVED_EVENT,
    PLUS_ONE_COUNTERS_PLACED_EVENT,
)

PUBLIC_HISTORY_QUERY_CAPABILITY='condition.history.fixed_public_query'
COUNTER_PLACEMENT_HISTORY_CAPABILITY = "condition.history.plus_one_counter_placement"
COUNTER_PLACEMENT_HISTORY_QUERY_VERSION = 7
SOURCE_COUNTER_RECEIPT_CAPABILITY = "condition.history.source_counter_receipt"
SOURCE_COUNTER_RECEIPT_QUERY_VERSION = 8
NONTOKEN_CREATURE_ENTRY_QUERY_VERSION = 9
OPPONENT_EVENT_THRESHOLD_QUERY_VERSION = 10
OPPONENT_EVENT_THRESHOLD_CAPABILITY = "condition.history.opponent_event_threshold"
_OPPONENT_THRESHOLD_REDUCTION = "count_players_at_least_two"
# Version 10 uses the event to distinguish these existing wire shapes.
CREATURE_COUNTER_PLACEMENT_QUERY_VERSION = 10
CREATURE_COUNTER_PLACEMENT_HISTORY_CAPABILITY = "condition.history.creature_counter_placement"
NONTOKEN_CREATURE_ENTRY_HISTORY_CAPABILITY = "condition.history.nontoken_creature_entries"
ARTIFACT_SUBTYPE_ENTRY_QUERY_VERSION = 12
ARTIFACT_SUBTYPE_ENTRY_HISTORY_CAPABILITY = "condition.history.artifact_subtype_entry"
OPPONENT_LIFE_LOSS_HISTORY_CAPABILITY = "condition.history.opponent_life_loss_including_departed"
COMMITTED_OCCURRENCE_HISTORY_CAPABILITY = "condition.history.committed_occurrences"
TOKEN_CREATION_FACT = "controller_tokens_created_this_turn"
SPELL_ORIGIN_HISTORY_CAPABILITY='condition.history.spell_origin'
GRAVEYARD_TYPE_HISTORY_CAPABILITY='condition.history.graveyard_card_types'
PERMANENT_DEATH_HISTORY_CAPABILITY = "zone.turn_history.permanent_death"
OWN_GRAVEYARD_CREATURE_DEATH_CAPABILITY = "condition.history.nontoken_creature_graveyard_owner"
OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION = 11
PUBLIC_MOVEMENT_HISTORY_CAPABILITY = "condition.history.public_movement"
_EVENTS=frozenset({'spell_cast','creature_attacked','creature_died','permanent_sacrificed','card_discarded',
    'player_gained_life','player_lost_life','permanent_entered','permanent_left','card_arrived_graveyard',
    'permanent_died', 'player_damaged', CAST_CREATURE_COUNTERED_EVENT, DRAWN_HAND_CARD_EVENT, PLUS_ONE_COUNTERS_PLACED_EVENT,
    PERMANENT_COUNTER_RECEIVED_EVENT, CREATURE_COUNTER_PLACEMENT_EVENT}) | COMMITTED_OCCURRENCE_HISTORY_KINDS | PUBLIC_MOVEMENT_HISTORY_KINDS
_TYPES=frozenset({'artifact','battle','creature','enchantment','instant','kindred','land','planeswalker','sorcery'})


@dataclass(frozen=True,slots=True)
class PublicHistoryQuerySpec:
    event:str
    relation:str='controller'
    types_all:tuple[str,...]=()
    types_any:tuple[str,...]=()
    excluded_types:tuple[str,...]=()
    reduction:str='count'
    schema_version:int=1
    origin:str|None=None
    creature_subtype: str | None = None
    token: bool | None = None
    artifact_subtype: str | None = None
    exclude_source: bool = False
    excluded_name: str | None = None
    excluded_subtype: str | None = None
    source_only: bool = False

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in {
            1, 2, 3, 4, 5, 6, COUNTER_PLACEMENT_HISTORY_QUERY_VERSION, SOURCE_COUNTER_RECEIPT_QUERY_VERSION,
            NONTOKEN_CREATURE_ENTRY_QUERY_VERSION, OPPONENT_EVENT_THRESHOLD_QUERY_VERSION,
            CREATURE_COUNTER_PLACEMENT_QUERY_VERSION,
            OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION,
            ARTIFACT_SUBTYPE_ENTRY_QUERY_VERSION,
        }:
            raise ValueError("Unsupported public history query version")
        relations = ({"graveyard_owner"} if self.schema_version == OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION
                     else {"controller", "any_player", "opponents"})
        if self.event not in _EVENTS or self.relation not in relations:
            raise ValueError("Unknown history event or principal relation")
        threshold_query = self.is_opponent_threshold_query
        if self.reduction not in {'count', 'sum_amount', 'max_player_amount', _OPPONENT_THRESHOLD_REDUCTION}:
            raise ValueError('Unknown history reduction')
        if (threshold_query != (self.reduction == _OPPONENT_THRESHOLD_REDUCTION)
                or self.event == DRAWN_HAND_CARD_EVENT and not threshold_query):
            raise ValueError("An opponent threshold requires its explicit history query version")
        life=self.event in {'player_gained_life','player_lost_life'}
        if life != (self.reduction in {'sum_amount', 'max_player_amount'}):raise ValueError('Only life history carries amount reductions')
        if self.reduction=='max_player_amount' and self.relation=='controller':raise ValueError('Per-player maximum requires a player set')
        for field in ('types_all','types_any','excluded_types'):
            values=getattr(self,field)
            if not isinstance(values,(tuple,list)) or any(type(v) is not str or v not in _TYPES for v in values) or len(values)!=len(set(values)):
                raise ValueError('History type predicates require distinct canonical card types')
            object.__setattr__(self,field,tuple(sorted(values)))
        if threshold_query and (
            self.relation != "opponents" or self.types_any or self.excluded_types
            or (self.event, self.types_all) not in {
                (DRAWN_HAND_CARD_EVENT, ()), ("permanent_entered", ("land",)),
            }
        ):
            raise ValueError("Opponent thresholds require committed draws or land entries")
        if self.event == "player_damaged" and (
            self.schema_version != 1 or self.relation != "controller"
            or self.types_all != ("creature",) or self.types_any or self.excluded_types
        ):
            raise ValueError("Creature damage history requires the exact player recipient and source type")
        typed_arrival=self.event=='card_arrived_graveyard' and any((self.types_all,self.types_any,self.excluded_types))
        if (life or self.event in (COMMITTED_OCCURRENCE_HISTORY_KINDS | {
                "card_discarded", "card_arrived_exile", "artifact_subtype_sacrificed",
                ARTIFACT_SUBTYPE_ENTRY_EVENT, CAST_CREATURE_COUNTERED_EVENT})) and any(
            (self.types_all, self.types_any, self.excluded_types)
        ):
            raise ValueError('This history event does not seal type predicates')
        if self.event=='creature_attacked' and any((self.types_all,self.types_any,self.excluded_types)):
            raise ValueError('Attack history mixes type/subtype terms and has no general type filter')
        if set(self.types_all)&set(self.excluded_types):raise ValueError('History query has contradictory type predicates')
        if self.origin is not None:
            origins={'hand','library','graveyard','exile','command','outside'} if self.event=='spell_cast' else {
                'battlefield','hand','library','stack','exile','command','outside'} if self.event=='card_arrived_graveyard' else set()
            if type(self.origin) is not str or self.origin not in origins:raise ValueError('History origin requires one closed cast or arrival zone')
        if (self.schema_version==2)!=(typed_arrival or self.origin is not None):
            raise ValueError('Qualified history queries require version2; version1 keeps its original fields')
        death_query = self.event == "permanent_died"
        if ((death_query and self.schema_version not in (3, 4, 5, OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION))
                or (not death_query and self.schema_version == 3)):
            raise ValueError("Permanent death queries require their explicit version")
        if self.schema_version == OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION and (
            not death_query or self.types_all != ("creature",) or self.token is not False
            or self.types_any or self.excluded_types or self.creature_subtype is not None
        ):
            raise ValueError("The graveyard-owner history query requires nontoken creature deaths")
        if death_query:
            if self.schema_version != 5 and self.creature_subtype is None and self.token is None:
                raise ValueError("Permanent death queries require a subtype or token predicate")
            if self.creature_subtype is not None and canonical_creature_subtype(self.creature_subtype) != self.creature_subtype:
                raise ValueError("Death queries require a canonical creature subtype")
            if self.token is not None and type(self.token) is not bool:
                raise ValueError("Death queries require a Boolean token predicate")
        elif self.schema_version == NONTOKEN_CREATURE_ENTRY_QUERY_VERSION:
            _validate_nontoken_creature_entry_query(self)
        elif self.creature_subtype is not None or self.token is not None:
            raise ValueError("Only permanent death queries can filter subtype or token status")
        if type(self.exclude_source) is not bool:
            raise ValueError("History source exclusion must be Boolean")
        artifact_entry = self.event == ARTIFACT_SUBTYPE_ENTRY_EVENT
        if (self.schema_version == ARTIFACT_SUBTYPE_ENTRY_QUERY_VERSION) != artifact_entry:
            raise ValueError("Artifact subtype entry requires its exact version")
        if artifact_entry:
            if self.artifact_subtype != "food" or self.relation != "controller" or self.exclude_source:
                raise ValueError("Artifact entry history requires the controller's effective Food")
        elif self.event == "artifact_subtype_sacrificed":
            from .type_subtype_vocabulary import ARTIFACT_SUBTYPES

            if (self.schema_version != 4 or type(self.artifact_subtype) is not str
                    or self.artifact_subtype not in ARTIFACT_SUBTYPES or self.exclude_source):
                raise ValueError("Artifact sacrifice queries require one canonical subtype")
        elif self.artifact_subtype is not None:
            raise ValueError("Only artifact subtype sacrifice queries carry that subtype")
        elif self.schema_version == 4 and not (death_query and self.exclude_source and self.relation == "controller"):
            raise ValueError("Qualified death history requires explicit source exclusion")
        if self.schema_version == 5:
            from .death_cast_permission_model import DeathCastPermissionSpec
            DeathCastPermissionSpec(excluded_name=self.excluded_name, excluded_subtype=self.excluded_subtype)
            if (not death_query or self.creature_subtype is not None or self.token is not None
                    or self.exclude_source or self.origin is not None or self.artifact_subtype is not None
                    or self.reduction != 'count' or self.types_all != ('creature',)
                    or self.types_any or self.excluded_types):
                raise ValueError('Version-five exclusions require an explicit creature death count')
        elif self.excluded_name is not None or self.excluded_subtype is not None:
            raise ValueError('Death name/subtype exclusions require query version five')
        if self.schema_version == 6 and (self.event, self.relation, self.reduction) != (
                "player_lost_life", "opponents", "sum_amount"):
            raise ValueError("Version six requires the original opponents’ committed life loss")
        if self.exclude_source and self.schema_version != 4:
            raise ValueError("Source exclusion requires version-four history")
        placement_query = self.event == PLUS_ONE_COUNTERS_PLACED_EVENT
        receipt_query = self.event == PERMANENT_COUNTER_RECEIVED_EVENT
        creature_placement = self.event == CREATURE_COUNTER_PLACEMENT_EVENT
        if creature_placement and self.schema_version != CREATURE_COUNTER_PLACEMENT_QUERY_VERSION:
            raise ValueError("Creature counter placements require their explicit query version")
        if creature_placement and (
            self.relation != "controller" or self.types_all != ("creature",) or self.types_any or self.excluded_types
        ):
            raise ValueError("Creature counter placement history requires its placing controller and creature scope")
        if (self.schema_version == COUNTER_PLACEMENT_HISTORY_QUERY_VERSION) != placement_query:
            raise ValueError("Counter placement history requires its explicit query version")
        if (self.schema_version == SOURCE_COUNTER_RECEIPT_QUERY_VERSION) != receipt_query:
            raise ValueError("Source counter receipts require their explicit query version")
        if type(self.source_only) is not bool or (self.source_only and not (placement_query or receipt_query)):
            raise ValueError("Only counter placement history supports source-incarnation matching")
        if placement_query and (
            self.relation != "controller" or self.types_all not in ((), ("creature",))
            or self.types_any or self.excluded_types or (self.source_only and self.types_all)
        ):
            raise ValueError("Counter placement history requires the placing controller and a closed recipient scope")
        if receipt_query and (self.relation != 'any_player' or not self.source_only
                              or self.types_all or self.types_any or self.excluded_types):
            raise ValueError("A counter receipt query requires only the exact source incarnation")

    def to_dict(self):
        return {'schema_version':self.schema_version,'event':self.event,'relation':self.relation,'types_all':list(self.types_all),
            'types_any':list(self.types_any),'excluded_types':list(self.excluded_types),'reduction':self.reduction,
            **({'origin':self.origin} if self.schema_version==2 else {}),
            **({"creature_subtype": self.creature_subtype, "token": self.token} if self.event == "permanent_died" else {}),
            **({"token": self.token} if self.schema_version == NONTOKEN_CREATURE_ENTRY_QUERY_VERSION else {}),
            **({"exclude_source": True} if self.exclude_source else {}),
            **({"artifact_subtype": self.artifact_subtype} if self.artifact_subtype is not None else {}),
            **({'excluded_name':self.excluded_name,'excluded_subtype':self.excluded_subtype} if self.schema_version==5 else {}),
            **({"source_only": self.source_only} if self.schema_version in {
                COUNTER_PLACEMENT_HISTORY_QUERY_VERSION, SOURCE_COUNTER_RECEIPT_QUERY_VERSION,
            } else {})}

    @classmethod
    def from_dict(cls,value):
        fields = {'schema_version','event','relation','types_all','types_any','excluded_types','reduction'}
        version = value.get("schema_version") if isinstance(value, Mapping) else None
        if version == 2:
            fields.add("origin")
        elif version in (3, OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION):
            fields.update(("creature_subtype", "token"))
        elif version == 4:
            if value.get("event") == "artifact_subtype_sacrificed":
                fields.add("artifact_subtype")
            else:
                fields.update(("creature_subtype", "token", "exclude_source"))
        elif version == 5:
            fields.update(('creature_subtype','token','excluded_name','excluded_subtype'))
        elif version == NONTOKEN_CREATURE_ENTRY_QUERY_VERSION:
            fields.add("token")
        elif version in (COUNTER_PLACEMENT_HISTORY_QUERY_VERSION, SOURCE_COUNTER_RECEIPT_QUERY_VERSION):
            fields.add("source_only")
        elif version == ARTIFACT_SUBTYPE_ENTRY_QUERY_VERSION:
            fields.add("artifact_subtype")
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError('Public history queries have a closed field set')
        return cls(**dict(value))

    @property
    def is_opponent_threshold_query(self) -> bool:
        return (self.schema_version == OPPONENT_EVENT_THRESHOLD_QUERY_VERSION
                and self.event != CREATURE_COUNTER_PLACEMENT_EVENT)

    @property
    def capability_dependencies(self):
        return (PUBLIC_HISTORY_QUERY_CAPABILITY,
            *((OPPONENT_EVENT_THRESHOLD_CAPABILITY,) if self.is_opponent_threshold_query else ()),
            *((NONTOKEN_CREATURE_ENTRY_HISTORY_CAPABILITY,) if self.schema_version == NONTOKEN_CREATURE_ENTRY_QUERY_VERSION else ()),
            *((COUNTER_PLACEMENT_HISTORY_CAPABILITY,) if self.event == PLUS_ONE_COUNTERS_PLACED_EVENT else ()),
            *((SOURCE_COUNTER_RECEIPT_CAPABILITY,) if self.event == PERMANENT_COUNTER_RECEIVED_EVENT else ()),
            *((CREATURE_COUNTER_PLACEMENT_HISTORY_CAPABILITY,) if self.event == CREATURE_COUNTER_PLACEMENT_EVENT else ()),
            *((COMMITTED_OCCURRENCE_HISTORY_CAPABILITY,) if self.event in COMMITTED_OCCURRENCE_HISTORY_KINDS else ()),
            *((PERMANENT_DEATH_HISTORY_CAPABILITY,) if self.event == "permanent_died" else ()),
            *(('condition.history.qualified_death_exclusion',) if self.schema_version==5 else ()),
            *((OPPONENT_LIFE_LOSS_HISTORY_CAPABILITY,) if self.schema_version == 6 else ()),
            *((PUBLIC_MOVEMENT_HISTORY_CAPABILITY,) if self.event in PUBLIC_MOVEMENT_HISTORY_KINDS or self.exclude_source
              or self.schema_version == OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION else ()),
            *((OWN_GRAVEYARD_CREATURE_DEATH_CAPABILITY,)
              if self.schema_version == OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION else ()),
            *((ARTIFACT_SUBTYPE_ENTRY_HISTORY_CAPABILITY,) if self.event == ARTIFACT_SUBTYPE_ENTRY_EVENT else ()),
            *((SPELL_ORIGIN_HISTORY_CAPABILITY,) if self.event=='spell_cast' and self.origin is not None else ()),
            *((GRAVEYARD_TYPE_HISTORY_CAPABILITY,) if self.event=='card_arrived_graveyard' and any((self.types_all,self.types_any,self.excluded_types)) else ()))


def _validate_nontoken_creature_entry_query(spec):
    if (spec.event != "permanent_entered" or spec.relation != "controller" or spec.reduction != "count"
            or spec.types_all != ("creature",) or spec.types_any or spec.excluded_types
            or spec.token is not False or spec.creature_subtype is not None):
        raise ValueError("The entry query requires the controller's committed nontoken creature arrivals")


def current_nontoken_creature_entry_query():
    return PublicHistoryQuerySpec(
        event="permanent_entered", types_all=("creature",), token=False,
        schema_version=NONTOKEN_CREATURE_ENTRY_QUERY_VERSION,
    )


def _entry_token_history_is_complete(history):
    if getattr(history, "permanent_entry_token_history_version", None) != 1:
        return False
    entries = tuple(event for event in history.events if event.kind == "permanent_entered")
    return (all(type(event.token) is bool for event in entries)
            and len(entries) == len({event.object_incarnation for event in entries}))


def opponent_event_threshold_query(*, land_entries: bool) -> PublicHistoryQuerySpec:
    return PublicHistoryQuerySpec(
        "permanent_entered" if land_entries else DRAWN_HAND_CARD_EVENT,
        relation="opponents",
        types_all=("land",) if land_entries else (),
        reduction=_OPPONENT_THRESHOLD_REDUCTION,
        schema_version=OPPONENT_EVENT_THRESHOLD_QUERY_VERSION,
    )


def controller_food_entry_query() -> PublicHistoryQuerySpec:
    return PublicHistoryQuerySpec(
        ARTIFACT_SUBTYPE_ENTRY_EVENT,
        schema_version=ARTIFACT_SUBTYPE_ENTRY_QUERY_VERSION,
        artifact_subtype="food",
    )


def current_plus_one_counter_placement_query(
    *, source_only: bool = False, was_creature: bool = False,
) -> PublicHistoryQuerySpec:
    return PublicHistoryQuerySpec(
        PLUS_ONE_COUNTERS_PLACED_EVENT,
        types_all=("creature",) if was_creature else (),
        schema_version=COUNTER_PLACEMENT_HISTORY_QUERY_VERSION,
        source_only=source_only,
    )


def current_creature_counter_placement_query():
    return PublicHistoryQuerySpec(event=CREATURE_COUNTER_PLACEMENT_EVENT, types_all=("creature",),
                                  schema_version=CREATURE_COUNTER_PLACEMENT_QUERY_VERSION)


def current_source_counter_receipt_query() -> PublicHistoryQuerySpec:
    return PublicHistoryQuerySpec(
        PERMANENT_COUNTER_RECEIVED_EVENT,
        relation='any_player',
        schema_version=SOURCE_COUNTER_RECEIPT_QUERY_VERSION,
        source_only=True,
    )


def opponent_life_loss_including_departed_query() -> PublicHistoryQuerySpec:
    return PublicHistoryQuerySpec("player_lost_life", relation="opponents",
                                  reduction="sum_amount", schema_version=6)


def current_own_graveyard_creature_death_query() -> PublicHistoryQuerySpec:
    return PublicHistoryQuerySpec(
        "permanent_died", relation="graveyard_owner", types_all=("creature",), token=False,
        schema_version=OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION,
    )


def _matching_own_graveyard_creature_deaths(history, controller):
    arrivals = tuple(event for event in history.events if event.kind == "permanent_arrived_graveyard"
                     and event.actor == controller and "creature" in event.types)
    arrivals_by_incarnation = {event.object_incarnation: event for event in arrivals}
    deaths = tuple(event for event in history.events if event.kind == "permanent_died"
                   and event.object_incarnation in arrivals_by_incarnation)
    if (len(arrivals) != len(arrivals_by_incarnation) or len(arrivals) != len(deaths)
            or {event.object_incarnation for event in deaths} != set(arrivals_by_incarnation)
            or any(event.types != arrivals_by_incarnation[event.object_incarnation].types for event in deaths)):
        return None
    return tuple(event for event in deaths if event.token is False)


def matching_public_history_events(state,controller,spec,*,source_incarnation=None):
    """Return sealed matching occurrences, or unavailable history."""
    history=getattr(state,'turn_history',None)
    if history is None or history.schema_version!=1 or history.turn_sequence!=state.turn_sequence:return None
    if spec.event == DRAWN_HAND_CARD_EVENT and history.draw_identity_history_version != 1:
        return None
    if spec.schema_version == NONTOKEN_CREATURE_ENTRY_QUERY_VERSION and not _entry_token_history_is_complete(history):
        return None
    if spec.event == PERMANENT_COUNTER_RECEIVED_EVENT:
        if (history.permanent_counter_receipt_history_version != 1
                or type(source_incarnation) is not str or not source_incarnation):
            return None
        return tuple(event for event in history.events if event.kind == PERMANENT_COUNTER_RECEIVED_EVENT
                     and event.object_incarnation == source_incarnation)
    if (spec.event == PLUS_ONE_COUNTERS_PLACED_EVENT
            and history.plus_one_counter_placement_history_version != 1):
        return None
    if (spec.event == CREATURE_COUNTER_PLACEMENT_EVENT
            and history.creature_counter_placement_history_version != 1):
        return None
    if spec.event in COMMITTED_OCCURRENCE_HISTORY_KINDS and history.committed_occurrence_history_version != 1:
        return None
    if spec.event == CAST_CREATURE_COUNTERED_EVENT and history.creature_spell_counter_history_version != 1:
        return None
    if spec.event == "permanent_died" and history.permanent_death_history_version != 1:
        return None
    if (PUBLIC_MOVEMENT_HISTORY_CAPABILITY in spec.capability_dependencies
            and getattr(history, "public_movement_history_version", None) != 1):
        return None
    if spec.schema_version == OWN_GRAVEYARD_CREATURE_DEATH_QUERY_VERSION:
        return _matching_own_graveyard_creature_deaths(history, controller)
    if (spec.exclude_source or spec.source_only) and (type(source_incarnation) is not str or not source_incarnation):
        return None
    if spec.event=='permanent_left' and history.departure_history_version!=1:return None
    if spec.event=='card_arrived_graveyard' and history.arrival_history_version!=1:return None
    if spec.event=='spell_cast' and spec.origin is not None and history.spell_origin_history_version!=1:return None
    if GRAVEYARD_TYPE_HISTORY_CAPABILITY in spec.capability_dependencies and history.graveyard_type_history_version!=1:return None
    seats = {seat for seat, player in state.players.items()
             if player.in_game or spec.schema_version == 6}
    allowed={controller} if spec.relation=='controller' else seats-{controller} if spec.relation=='opponents' else set(state.players)
    matched=[]
    for event in history.events:
        if event.kind!=spec.event:continue
        seat=event.target if spec.event in {'player_gained_life','player_lost_life','player_damaged'} else event.actor
        if seat not in allowed:continue
        if spec.exclude_source and event.object_incarnation == source_incarnation:
            continue
        if spec.source_only and event.object_incarnation != source_incarnation:
            continue
        if spec.artifact_subtype is not None and event.target != spec.artifact_subtype:
            continue
        if spec.origin is not None:
            if event.origin is None:return None
            if event.origin!=spec.origin:continue
        types=set(event.types)
        if not set(spec.types_all)<=types or spec.types_any and types.isdisjoint(spec.types_any) or not types.isdisjoint(spec.excluded_types):continue
        if spec.token is not None and event.token is not spec.token:
            continue
        if spec.creature_subtype is not None and spec.creature_subtype not in event.creature_subtypes:
            continue
        if spec.schema_version == 5:
            from .death_cast_permission_model import DeathCastPermissionSpec
            predicate = DeathCastPermissionSpec(excluded_name=spec.excluded_name,excluded_subtype=spec.excluded_subtype)
            qualifies = predicate.matches_departure(was_creature='creature' in types,
                rules_names=event.rules_names,creature_subtypes=event.creature_subtypes)
            if qualifies is None:return None
            if not qualifies:continue
        matched.append(event)
    return tuple(matched)


OPPONENT_CAST_COLOR_DRAW = "draw_if_opponent_cast_colors_this_turn"
SPELL_COLOR_HISTORY_FIELD = "spell_color_history_version"


def uses_sealed_opponent_cast_colors(effect: Mapping[str, Any]) -> bool:
    return effect.get("op") == OPPONENT_CAST_COLOR_DRAW and SPELL_COLOR_HISTORY_FIELD in effect


def current_opponent_spell_colors(state, controller, *, include_departed: bool) -> frozenset[str] | None:
    """Read the colors sealed on this turn's committed opposing spell casts."""
    history = getattr(state, "turn_history", None)
    if getattr(history, "spell_color_history_version", None) != 1:
        return None
    spec = PublicHistoryQuerySpec(event="spell_cast", relation="any_player")
    occurrences = matching_public_history_events(state, controller, spec)
    if occurrences is None:
        return None
    colors = set()
    for event in occurrences:
        if event.actor == controller or not (include_departed or state.players[event.actor].in_game):
            continue
        if event.spell_colors is None:
            return None
        colors.update(event.spell_colors)
    return frozenset(colors)


def public_history_query_value(state,controller,spec,*,source_incarnation=None):
    events=matching_public_history_events(state,controller,spec,source_incarnation=source_incarnation)
    if events is None:return None
    if spec.reduction == _OPPONENT_THRESHOLD_REDUCTION:
        counts = {}
        for event in events:
            counts[event.actor] = counts.get(event.actor, 0) + 1
        return sum(count >= 2 for count in counts.values())
    if spec.reduction=='count':return len(events)
    if spec.reduction=='sum_amount':return sum(event.amount for event in events)
    totals={}
    for event in events:
        seat=event.target if spec.event in {'player_gained_life','player_lost_life','player_damaged'} else event.actor
        totals[seat]=totals.get(seat,0)+event.amount
    return max(totals.values(),default=0)


def public_history_query_dependencies(value):
    required=set()
    def visit(node):
        if isinstance(node,Mapping):
            if 'history_query' in node or node.get('kind') in {'history_count_at_least','history_count_at_most'}:
                if (set(node)!={'schema_version','kind','amount','counter_name','history_query'} or type(node['schema_version']) is not int
                        or node['schema_version']!=8 or node['kind'] not in {'history_count_at_least','history_count_at_most'}
                        or node['counter_name'] is not None or type(node['amount']) is not int or node['amount']<0):
                    raise ValueError('History conditions have one closed nonnegative threshold')
                required.update(PublicHistoryQuerySpec.from_dict(node['history_query']).capability_dependencies)
            if node.get("fact") == TOKEN_CREATION_FACT:
                from .continuous_conditions import FixedPublicStateConditionSpec

                FixedPublicStateConditionSpec.from_dict(node)
                required.add(COMMITTED_OCCURRENCE_HISTORY_CAPABILITY)
            for child in node.values():visit(child)
        elif isinstance(node,(tuple,list)):
            for child in node:visit(child)
    visit(value)
    return tuple(sorted(required))
