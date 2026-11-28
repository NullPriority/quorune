"""Closed selections from the resolving controller's graveyard."""
from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..creature_subtypes import canonical_creature_subtype
from ..object_query import ObjectQuerySpec, object_matches_query
from ..public_zone_moves import (
    PublicZoneMoveSetSpec, PublicZoneOrigin, PublicZoneDestination,
    PublicZoneRelationAxis, PublicZoneSeatRelation, snapshot_public_zone_move_set,
    PublicBattlefieldController, PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSION,
    PUBLIC_CHOSEN_GRAVEYARD_TAPPED_LAND_VERSION, PUBLIC_EACH_GRAVEYARD_CHOSEN_RETURN_VERSION,
)
from .different_name_graveyard_return import (
    CAPABILITY as DIFFERENT_NAME_RETURN_CAPABILITY, CHOSEN_MOVE_VERSION as DIFFERENT_NAME_MOVE_VERSION,
    PERMANENT_QUERY, PERMANENT_TYPES, SELECTION_VERSION as DIFFERENT_NAME_SELECTION_VERSION,
)
from .graveyard_card_targets import GraveyardCardTargetKind

OPERATION = 'choose_own_graveyard_return'
MECHANIC = 'own-graveyard-return-choice'
CAPABILITY = 'zone.return.own_graveyard_choice'
BOUNDED_BATTLEFIELD_RETURN_CAPABILITY = 'zone.return.own_graveyard_bounded_battlefield_choice'
BOUNDED_BATTLEFIELD_RETURN_VERSION = 4
COMMITTED_GRAVEYARD_RETURN_VERSION = 7
COMMITTED_GRAVEYARD_RETURN_CAPABILITY = 'resolution.effect.committed_sacrifice_graveyard_return'
COMMITTED_GRAVEYARD_RETURN_RECIPE = FrozenMap({
    'op': OPERATION, 'player': '$controller',
    'selection': {
        'schema_version': COMMITTED_GRAVEYARD_RETURN_VERSION, 'types_any': ['creature'],
        'committed_count': {'kind': 'committed_sacrifice_count', 'schema_version': 1},
    },
})
TAPPED_LAND_RETURN_VERSION = 6
TAPPED_LAND_RETURN_CAPABILITY = 'zone.return.own_graveyard_tapped_land_choice'
RETURN_PAIR_FIELD = "graveyard_return_pair"
PAIRED_RETURN_CAPABILITIES = (CAPABILITY, "zone.move.fixed_public_set", "attachment.aura.typed_restriction")
EACH_GRAVEYARD_RETURN_FIELD = "each_graveyard_return_version"
EACH_GRAVEYARD_RETURN_MECHANIC = "each-graveyard-return-after-mill"
EACH_GRAVEYARD_RETURN_CAPABILITY = "zone.reanimate.each_graveyard_choice"
EACH_GRAVEYARD_RETURN_QUERY = ObjectQuerySpec(
    zones=("graveyard",), types_any=("creature", "planeswalker"), token=False,
)


def each_graveyard_return_effect():
    return {"op": "choose_option", "player": "$controller", EACH_GRAVEYARD_RETURN_FIELD: 1}


def is_each_graveyard_return(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_option" and EACH_GRAVEYARD_RETURN_FIELD in effect


def each_graveyard_return_move(cards, *, stack_ref):
    spec = PublicZoneMoveSetSpec(
        query=EACH_GRAVEYARD_RETURN_QUERY,
        origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.BATTLEFIELD,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ANY,
        battlefield_controller=PublicBattlefieldController.ACTOR,
        schema_version=PUBLIC_EACH_GRAVEYARD_CHOSEN_RETURN_VERSION, chosen_cards=cards,
    )
    return {"op": "move_public_zone_set", "source": stack_ref, "set": spec.to_dict()}


def uses_each_graveyard_return_stage(effect):
    return is_each_graveyard_return(effect) or (
        isinstance(effect, Mapping) and effect.get("op") == "add_subtype" and "schema_version" in effect
    )


def _each_graveyard_return_spell_dependencies(effects, target_schema, mechanics):
    from ..zone_object_subtype_grants import LOCKED_CREATURE_SUBTYPE_CAPABILITY, locked_creature_subtype_from_effect
    from .mill_capability_shapes import fixed_mill_node_capabilities

    if len(effects) != 3 or target_schema is not None or EACH_GRAVEYARD_RETURN_MECHANIC not in mechanics:
        return ()
    mill, choose, subtype = effects
    if (
        set(mill) != {"op", "scope", "count"} or mill.get("op") != "mill"
        or mill.get("scope") != "all_active_players"
        or choose != each_graveyard_return_effect() or type(choose[EACH_GRAVEYARD_RETURN_FIELD]) is not int
    ):
        return ()
    try:
        locked_creature_subtype_from_effect(subtype)
    except (TypeError, ValueError):
        return ()
    mill_capabilities = fixed_mill_node_capabilities(effects=(mill,), target_schema=None, mechanic_ids=mechanics)
    if not mill_capabilities:
        return ()
    return (*mill_capabilities, EACH_GRAVEYARD_RETURN_CAPABILITY, "zone.move.fixed_public_set",
            LOCKED_CREATURE_SUBTYPE_CAPABILITY)
DIFFERENT_NAME_RETURN_CAPABILITIES = (
    *PAIRED_RETURN_CAPABILITIES, DIFFERENT_NAME_RETURN_CAPABILITY, "zone.change.destination_replacement",
)


def paired_graveyard_return_effect():
    return {"op": "choose_option", "player": "$controller", RETURN_PAIR_FIELD: {"schema_version": 1}}


def is_paired_graveyard_return(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_option" and RETURN_PAIR_FIELD in effect


def paired_graveyard_return_move(cards):
    spec = PublicZoneMoveSetSpec(
        query=ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False),
        origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.BATTLEFIELD,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ANY,
        battlefield_controller=PublicBattlefieldController.OWNER,
        schema_version=PUBLIC_CHOSEN_GRAVEYARD_RETURN_VERSION, chosen_cards=cards,
    )
    return {"op": "move_public_zone_set", "source": "$source", "set": spec.to_dict()}


def chosen_tapped_land_move(card):
    spec = PublicZoneMoveSetSpec(
        query=ObjectQuerySpec(zones=("graveyard",), types_all=("land",), token=False),
        origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.BATTLEFIELD,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ACTOR,
        battlefield_controller=PublicBattlefieldController.OWNER, battlefield_tapped=True,
        schema_version=PUBLIC_CHOSEN_GRAVEYARD_TAPPED_LAND_VERSION, chosen_cards=(card,),
    )
    return {"op": "move_public_zone_set", "source": "$source", "set": spec.to_dict()}


def chosen_different_name_permanent_move(cards):
    spec = PublicZoneMoveSetSpec(
        query=PERMANENT_QUERY,
        origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.BATTLEFIELD,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ACTOR,
        battlefield_controller=PublicBattlefieldController.OWNER,
        schema_version=DIFFERENT_NAME_MOVE_VERSION, chosen_cards=cards,
    )
    return {"op": "move_public_zone_set", "source": "$source", "set": spec.to_dict()}


GREATEST_POWER_CAPABILITY = 'quantity_expression.public_characteristic_aggregate'
QUALIFIED_CHOICE_CAPABILITY = 'zone.return.own_graveyard_subtype_qualified_choice'
_CREATURE_OR_PLANESWALKER = ('creature', 'planeswalker')
MANDATORY_CREATURE_OR_PLANESWALKER_VERSION = 5
DEFENDING_NONLAND_RETURN_VERSION = 6
DEFENDING_NONLAND_RETURN_CAPABILITY = "zone.return.defending_nonland_graveyard_growth"
_DEFENDING_FIELDS = frozenset({"graveyard_owner", "defender", "defender_chosen"})
_RETAINED_MANDATORY_CREATURE_OR_PLANESWALKER_VERSION = 4
_DISJOINT_RETURN_VERSIONS = frozenset({
    BOUNDED_BATTLEFIELD_RETURN_VERSION,
    _RETAINED_MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
    MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
    TAPPED_LAND_RETURN_VERSION,
    DIFFERENT_NAME_SELECTION_VERSION,
    COMMITTED_GRAVEYARD_RETURN_VERSION,
})
_PUBLIC_FIELDS = frozenset({'op', 'player', 'selection'})
_CHOICE_FIELDS = frozenset({'_choice_actor', '_legal_refs', '_legal_logical_ids', '_stack_label'})
_TYPES = frozenset({GraveyardCardTargetKind.CREATURE_CARD.types_any,
    GraveyardCardTargetKind.LAND_CARD.types_any, ('creature', 'land'),
    GraveyardCardTargetKind.PERMANENT_CARD.types_any})


@dataclass(frozen=True, slots=True)
class GraveyardReturnSelection:
    types_any: tuple[str, ...]
    schema_version: int = 1
    excluded_creature_subtype: str | None = None
    mana_value_max: int | None = None
    committed_count: int | None = None

    def __post_init__(self):
        supported_versions = {1, 2, 3, DEFENDING_NONLAND_RETURN_VERSION} | _DISJOINT_RETURN_VERSIONS
        if type(self.schema_version) is not int or self.schema_version not in supported_versions:
            raise ValueError('Graveyard return requires a supported selection version')
        if type(self.types_any) is not tuple or any(type(value) is not str for value in self.types_any):
            raise ValueError('Graveyard return needs one closed card-type predicate')
        if self.committed_count is not None and not self.committed_creature_return:
            raise ValueError('A committed graveyard return requires a positive sacrifice count')
        if self.defending_nonland:
            if self.excluded_creature_subtype is not None or self.mana_value_max is not None:
                raise ValueError("Defending return requires its exact nonland card domain")
            return
        if self.schema_version in _DISJOINT_RETURN_VERSIONS:
            if not (self.mandatory_hand_return or self.returns_to_battlefield):
                raise ValueError('Graveyard return requires an exact mandatory hand or bounded battlefield selection')
            return
        if self.schema_version == 3:
            subtype = self.excluded_creature_subtype
            if (self.types_any != _CREATURE_OR_PLANESWALKER or type(subtype) is not str
                    or canonical_creature_subtype(subtype) != subtype):
                raise ValueError('Qualified return needs an excluded creature subtype and a planeswalker branch')
        elif self.types_any not in _TYPES or self.excluded_creature_subtype is not None:
            raise ValueError('Graveyard return needs one closed positive card-type predicate')
        if self.mana_value_max is not None:
            raise ValueError('Hand return cannot carry a battlefield mana-value maximum')
        if self.greatest_power and self.types_any != ('creature',):
            raise ValueError('Greatest-power return selects only creature cards')

    @property
    def defending_nonland(self) -> bool:
        return self.schema_version == DEFENDING_NONLAND_RETURN_VERSION and not self.types_any

    @property
    def greatest_power(self) -> bool:
        return self.schema_version == 2

    @property
    def returns_to_battlefield(self) -> bool:
        return self.bounded_battlefield_return or self.returns_tapped_land or self.different_name_permanents or self.committed_creature_return

    @property
    def different_name_permanents(self) -> bool:
        return (
            self.schema_version == DIFFERENT_NAME_SELECTION_VERSION and self.types_any == PERMANENT_TYPES
            and self.excluded_creature_subtype is None and self.mana_value_max is None
        )

    @property
    def committed_creature_return(self) -> bool:
        return (
            self.schema_version == COMMITTED_GRAVEYARD_RETURN_VERSION
            and self.types_any == ('creature',)
            and self.excluded_creature_subtype is None and self.mana_value_max is None
            and type(self.committed_count) is int and self.committed_count > 0
        )

    @property
    def returns_tapped_land(self) -> bool:
        return (
            self.schema_version == TAPPED_LAND_RETURN_VERSION
            and self.types_any == ('land',)
            and self.mana_value_max is None and self.excluded_creature_subtype is None
        )

    @property
    def bounded_battlefield_return(self) -> bool:
        return (
            self.schema_version == BOUNDED_BATTLEFIELD_RETURN_VERSION
            and self.types_any == ('creature',)
            and self.excluded_creature_subtype is None
            and type(self.mana_value_max) is int
            and 0 <= self.mana_value_max <= 20
        )

    @property
    def mandatory_hand_return(self) -> bool:
        return (
            self.schema_version in {
                _RETAINED_MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
                MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
            }
            and self.types_any == _CREATURE_OR_PLANESWALKER
            and self.excluded_creature_subtype is None
            and self.mana_value_max is None
        )

    @property
    def required(self) -> bool:
        return self.greatest_power or self.mandatory_hand_return or self.defending_nonland or self.committed_creature_return

    def to_dict(self):
        value = {'schema_version': self.schema_version, 'types_any': list(self.types_any)}
        if self.excluded_creature_subtype is not None:
            value['excluded_creature_subtype'] = self.excluded_creature_subtype
        if self.bounded_battlefield_return:
            value['mana_value_max'] = self.mana_value_max
        if self.committed_creature_return:
            value['committed_count'] = self.committed_count
        return value

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError('Graveyard return selection fields are closed')
        fields = {'schema_version', 'types_any'}
        if value.get('schema_version') == 3:
            fields.add('excluded_creature_subtype')
        if value.get('schema_version') == BOUNDED_BATTLEFIELD_RETURN_VERSION and 'mana_value_max' in value:
            if type(value['mana_value_max']) is not int:
                raise ValueError('Battlefield return requires an explicit integer mana-value maximum')
            fields.add('mana_value_max')
        if value.get('schema_version') == COMMITTED_GRAVEYARD_RETURN_VERSION and 'committed_count' in value:
            if type(value['committed_count']) is not int:
                raise ValueError('A committed graveyard return requires an exact integer sacrifice count')
            fields.add('committed_count')
        if set(value) != fields or not isinstance(value['types_any'], (list, tuple)):
            raise ValueError('Graveyard return selection fields are closed')
        return cls(tuple(value['types_any']), schema_version=value['schema_version'],
                   excluded_creature_subtype=value.get('excluded_creature_subtype'),
                   mana_value_max=value.get('mana_value_max'), committed_count=value.get('committed_count'))

    @property
    def query(self):
        if self.defending_nonland:
            return ObjectQuerySpec(zones=("graveyard",), excluded_types=("land",), token=False)
        if self.returns_to_battlefield:
            return ObjectQuerySpec(zones=('graveyard',), types_any=self.types_any,
                                   token=False, mana_value_max=self.mana_value_max)
        return ObjectQuerySpec(zones=('graveyard',), types_any=self.types_any)

    def matches(self, row) -> bool:
        return object_matches_query(row, self.query) and (
            self.excluded_creature_subtype is None
            or 'planeswalker' in row.types
            or self.excluded_creature_subtype not in row.subtypes
        )

    def public_set(self):
        return PublicZoneMoveSetSpec(
            self.query, PublicZoneOrigin.GRAVEYARD,
            PublicZoneDestination.BATTLEFIELD if self.returns_to_battlefield else PublicZoneDestination.OWNER_HAND,
            relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ACTOR,
            schema_version=2, battlefield_controller=(
                PublicBattlefieldController.OWNER if self.returns_to_battlefield else None
            ),
        )


def mandatory_creature_or_planeswalker_return_effect():
    selection = GraveyardReturnSelection(
        _CREATURE_OR_PLANESWALKER, schema_version=MANDATORY_CREATURE_OR_PLANESWALKER_VERSION,
    )
    return {'op': OPERATION, 'player': '$controller', 'selection': selection.to_dict()}


def optional_tapped_land_return_effect():
    selection = GraveyardReturnSelection(('land',), schema_version=TAPPED_LAND_RETURN_VERSION)
    return {'op': OPERATION, 'player': '$controller', 'selection': selection.to_dict()}


def different_name_permanent_return_effect():
    selection = GraveyardReturnSelection(PERMANENT_TYPES, schema_version=DIFFERENT_NAME_SELECTION_VERSION)
    return {'op': OPERATION, 'player': '$controller', 'selection': selection.to_dict()}


def uses_different_name_graveyard_return(effect):
    selection = effect.get("selection")
    return (effect.get("op") == OPERATION and isinstance(selection, Mapping)
            and selection.get("schema_version") == DIFFERENT_NAME_SELECTION_VERSION
            and "committed_count" not in selection)


def defending_nonland_return_effect():
    return {"op": OPERATION, "player": "$controller",
            "selection": GraveyardReturnSelection((), schema_version=DEFENDING_NONLAND_RETURN_VERSION).to_dict(),
            "graveyard_owner": "$controller", "defender": "$context.defending_player", "defender_chosen": False}


def defending_nonland_chooser_is_current(actor, owner, defender, active_seats):
    if actor not in active_seats or owner not in active_seats:
        return False
    if defender in active_seats:
        return actor == defender
    return actor != owner or not any(seat != owner for seat in active_seats)


def _validate_defending_nonland_choice(effect, *, actor, continuation):
    from ..replacement.immutable import FrozenMap

    if actor is None:
        if (FrozenMap(effect) != FrozenMap(defending_nonland_return_effect())
                or effect.get("defender_chosen") is not False):
            raise ValueError("Defending return requires its original attack and graveyard roles")
    else:
        allowed = _PUBLIC_FIELDS | _DEFENDING_FIELDS | (_CHOICE_FIELDS if continuation else frozenset())
        if (set(effect) != allowed or effect["player"] != actor
                or type(effect["graveyard_owner"]) is not str or not effect["graveyard_owner"]
                or type(effect["defender"]) is not str or not effect["defender"]
                or type(effect["defender_chosen"]) is not bool
                or not effect["defender_chosen"] and actor != effect["graveyard_owner"]
                or continuation and (not effect["defender_chosen"] or effect["_choice_actor"] != actor)):
            raise ValueError("Defending return chooser or owned graveyard changed")
    return GraveyardReturnSelection.from_dict(effect["selection"])


def committed_graveyard_return_effect(count):
    selection = GraveyardReturnSelection(
        ('creature',), schema_version=COMMITTED_GRAVEYARD_RETURN_VERSION, committed_count=count,
    )
    return {'op': OPERATION, 'player': '$controller', 'selection': selection.to_dict()}


def validate_return_choice(effect, *, actor=None, continuation=False):
    selection = effect.get("selection") if isinstance(effect, Mapping) else None
    if (isinstance(selection, Mapping) and selection.get("schema_version") == DEFENDING_NONLAND_RETURN_VERSION
            and selection.get("types_any") in ([], ()) and effect.get("op") == OPERATION):
        return _validate_defending_nonland_choice(effect, actor=actor, continuation=continuation)
    fields = _PUBLIC_FIELDS | _CHOICE_FIELDS if continuation else _PUBLIC_FIELDS
    if (not isinstance(effect, Mapping) or set(effect) != fields or effect.get('op') != OPERATION
            or effect.get('player') != (actor if actor is not None else '$controller')):
        raise ValueError('Graveyard return instruction fields or resolving player changed')
    if continuation and effect['_choice_actor'] != actor:
        raise ValueError('Graveyard return chooser changed')
    return GraveyardReturnSelection.from_dict(effect['selection'])


def graveyard_return_candidates(host, actor, effect, *, stack_item=None):
    selection = validate_return_choice(effect, actor=actor, continuation='_choice_actor' in effect)
    if selection.committed_creature_return:
        program = host.semantics.get(stack_item.semantic_key) if stack_item is not None else None
        if (
            stack_item is None or stack_item.kind not in {'spell', 'spell_copy'}
            or stack_item.controller != actor or stack_item.targets
            or stack_item.context.get('currently_resolving') is not True
            or program is None or COMMITTED_GRAVEYARD_RETURN_CAPABILITY not in program.capability_dependencies
            or not host.semantic_program_is_current_trusted(program)
        ):
            raise ValueError('Committed graveyard return requires its original resolving sacrifice spell')

    rows = None
    if selection.excluded_creature_subtype is not None:
        rows = tuple(row for row in host.public_zone_move_object_rows(actor) if selection.matches(row))
    snapshot = snapshot_public_zone_move_set(
        host, actor=effect["graveyard_owner"] if selection.defending_nonland else actor,
        spec=selection.public_set(), object_rows=rows,
    )
    return tuple(value.ref for value in snapshot.objects
        if host.state.cards[value.object_id].is_card_object)


def graveyard_return_choice_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    if any(uses_each_graveyard_return_stage(effect) for effect in effects):
        return _each_graveyard_return_spell_dependencies(effects, target_schema, mechanic_ids)
    if MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:
        return ()
    if is_paired_graveyard_return(effects[0]):
        effect = effects[0]
        marker = effect[RETURN_PAIR_FIELD]
        if (effect != paired_graveyard_return_effect() or type(marker["schema_version"]) is not int):
            return ()
        return PAIRED_RETURN_CAPABILITIES
    try:
        selection = validate_return_choice(effects[0])
    except (TypeError, ValueError):
        return ()
    if selection.committed_creature_return:
        # A materialized count is resolution state, never a standalone Oracle instruction.
        return ()
    if selection.defending_nonland:
        return (CAPABILITY, DEFENDING_NONLAND_RETURN_CAPABILITY,
                "continuous.resolution.fixed_characteristics_until_end_of_turn")
    if selection.different_name_permanents:
        return DIFFERENT_NAME_RETURN_CAPABILITIES
    if selection.returns_tapped_land:
        return (CAPABILITY, TAPPED_LAND_RETURN_CAPABILITY, 'zone.move.fixed_public_set')
    if selection.bounded_battlefield_return:
        return (CAPABILITY, BOUNDED_BATTLEFIELD_RETURN_CAPABILITY)
    if selection.greatest_power:
        return (CAPABILITY, GREATEST_POWER_CAPABILITY)
    if selection.excluded_creature_subtype is not None:
        return (CAPABILITY, QUALIFIED_CHOICE_CAPABILITY)
    return (CAPABILITY,)


def each_graveyard_return_spell_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(uses_each_graveyard_return_stage(value) for value in nested_effects((program.effects, program.handlers))):
        return ()
    required = _each_graveyard_return_spell_dependencies(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.cost_schema is not None):
        raise ValueError("Graveyard selection requires its whole resolving spell and both companion stages")
    return required


def graveyard_return_choice_runtime_dependencies(program):
    if required := each_graveyard_return_spell_runtime_dependencies(program):
        return required

    def walk(value):
        if isinstance(value, Mapping):
            if is_paired_graveyard_return(value):
                from ..controller_creature_keyword_absence import is_controller_end_step_schedule

                required = graveyard_return_choice_node_capabilities(
                    effects=(value,), target_schema=program.target_schema, mechanic_ids=program.coverage,
                )
                if (
                    not required or (program.event, program.active_zone) != ("step.begin", "battlefield")
                    or program.handlers or tuple(program.effects) != (value,)
                    or not is_controller_end_step_schedule(program.event_condition)
                ):
                    raise ValueError("Paired graveyard return requires its original controller end-step choice")
                return set(required)
            if value.get('op') == OPERATION:
                if FrozenMap(value) == COMMITTED_GRAVEYARD_RETURN_RECIPE:
                    from .any_number_sacrifice import any_number_sacrifice_runtime_dependencies

                    required = any_number_sacrifice_runtime_dependencies(program)
                    if COMMITTED_GRAVEYARD_RETURN_CAPABILITY not in required:
                        raise ValueError('Committed graveyard return lost its sacrifice authority')
                    return {CAPABILITY, COMMITTED_GRAVEYARD_RETURN_CAPABILITY}
                required = graveyard_return_choice_node_capabilities(
                    effects=(value,), target_schema=None, mechanic_ids=program.coverage)
                if DEFENDING_NONLAND_RETURN_CAPABILITY in required and (
                    (program.event, program.active_zone) != ("creature.attacks", "battlefield")
                    or program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
                    or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
                    or program.handlers or tuple(program.effects) != (value,)
                ):
                    raise ValueError("Defending return requires its original source attack")
                if not required or (
                    BOUNDED_BATTLEFIELD_RETURN_CAPABILITY in required and program.target_schema is not None
                ):
                    raise ValueError('Graveyard choice lacks a closed owned instruction')
                if TAPPED_LAND_RETURN_CAPABILITY in required:
                    _require_surveil_then_land_return(program)
                if DIFFERENT_NAME_RETURN_CAPABILITY in required and (
                    (program.event, program.active_zone) != ("resolve", "stack")
                    or not program.ability_id.startswith("spell:")
                    or tuple(program.effects) != (value,) or program.handlers
                    or program.target_schema is not None or program.event_condition is not None
                    or program.cost_schema is not None or program.trigger_limit is not None
                ):
                    raise ValueError("Different-name permanent return requires one untargeted resolving spell instruction")
                return set(required)
            return {cap for child in value.values() for cap in walk(child)}
        if isinstance(value, (tuple, list)):
            return {cap for child in value for cap in walk(child)}
        return set()
    required = walk((program.effects, program.handlers))
    if DEFENDING_NONLAND_RETURN_CAPABILITY in program.capability_dependencies and DEFENDING_NONLAND_RETURN_CAPABILITY not in required:
        raise ValueError("Defending return authority requires its original nonland choice")
    return tuple(sorted(required))


def _require_surveil_then_land_return(program):
    from ..compiler.fixed_public_action_event_bindings import fixed_source_combat_damage_recipient_condition
    from ..replacement.immutable import thaw_value

    if ((program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or program.handlers or program.target_schema is not None
            or thaw_value(program.event_condition) != fixed_source_combat_damage_recipient_condition("battle")
            or thaw_value(program.effects) != [
                {"op": "surveil", "player": "$controller", "count": 1}, optional_tapped_land_return_effect(),
            ]):
        raise ValueError("Tapped land return requires the original untargeted combat damage and Surveil sequence")
