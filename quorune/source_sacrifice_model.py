"""One versioned instruction sacrifices its original controlled permanent."""
from collections.abc import Mapping
from dataclasses import dataclass

from .replacement.immutable import FrozenMap, freeze_value

SOURCE_SACRIFICE_FIELD = 'fixed_source_sacrifice_version'
SOURCE_SACRIFICE_CAPABILITY = 'permanent.sacrifice.fixed_source'
SOURCE_SACRIFICE_MECHANIC = 'fixed-source-sacrifice'
SELF_MOVE_CAPABILITY = 'zone.self_move.activated'
FORMER_ATTACHMENT_SACRIFICE_VERSION = 2
CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION = 3
REFLEXIVE_SEARCH_SACRIFICE_VERSION = 4
CAST_PARTICIPANT_FIELD = 'cast_participant'
CAST_PARTICIPANT_BINDING = '$context.player'
UNATTACHMENT_EVENT = 'attachment.unattached'
UNATTACHMENT_TRIGGER_CAPABILITY = 'trigger.event.normalized_public_action'
UNATTACHMENT_SNAPSHOT = 'unattachment_snapshot'
SOURCE_UNATTACHMENT_VARIANT = 'source_equipment_unattached'


def fixed_source_sacrifice_effect():
    return {'op': 'self_zone_move', 'origin': 'battlefield',
            'destination': 'graveyard', 'tapped': False, 'source_form': 'permanent',
            SOURCE_SACRIFICE_FIELD: 1}


def former_attachment_sacrifice_effect():
    return {**fixed_source_sacrifice_effect(), SOURCE_SACRIFICE_FIELD: FORMER_ATTACHMENT_SACRIFICE_VERSION,
        UNATTACHMENT_SNAPSHOT: '$context.' + UNATTACHMENT_SNAPSHOT}


def cast_participant_sacrifice_draw_effect():
    return {**fixed_source_sacrifice_effect(),
        SOURCE_SACRIFICE_FIELD: CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION,
        CAST_PARTICIPANT_FIELD: CAST_PARTICIPANT_BINDING}


def reflexive_search_sacrifice_effect(child):
    from .rules.source_sacrifice_reflexive_search import FIELD

    return {**fixed_source_sacrifice_effect(),
            SOURCE_SACRIFICE_FIELD: REFLEXIVE_SEARCH_SACRIFICE_VERSION,
            FIELD: child.to_dict()}


def _materialized_identity(value):
    return type(value) is str and bool(value) and value == value.strip() and not value.startswith('$')


def fixed_source_sacrifice_dependencies(effect, *, resolved=False):
    if not isinstance(effect, Mapping):
        return ()
    if SOURCE_SACRIFICE_FIELD not in effect:
        if (effect.get('op'), effect.get('origin'), effect.get('destination')) == (
                'self_zone_move', 'battlefield', 'graveyard'):
            raise ValueError('Battlefield source sacrifice lost its explicit instruction version')
        return ()
    version = effect[SOURCE_SACRIFICE_FIELD]
    from_unattachment = version == FORMER_ATTACHMENT_SACRIFICE_VERSION
    draw_after_cast = version == CAST_PARTICIPANT_SACRIFICE_DRAW_VERSION
    expected = fixed_source_sacrifice_effect()
    reflexive_dependencies = ()
    if from_unattachment:
        expected = former_attachment_sacrifice_effect()
    elif draw_after_cast:
        if not resolved and '_replacement_selections' in effect:
            raise ValueError('Source sacrifice draw cannot preselect a replacement')
        expected = cast_participant_sacrifice_draw_effect()
        if resolved:
            participant = effect.get(CAST_PARTICIPANT_FIELD)
            if not _materialized_identity(participant):
                raise ValueError('Source sacrifice draw needs its materialized cast participant')
            expected[CAST_PARTICIPANT_FIELD] = participant
    elif version == REFLEXIVE_SEARCH_SACRIFICE_VERSION:
        from .rules.source_sacrifice_reflexive_search import FIELD, validate_child

        if not resolved and "_replacement_selections" in effect:
            raise ValueError("Source sacrifice cannot preselect a replacement")
        child, reflexive_dependencies = validate_child(effect.get(FIELD))
        expected = reflexive_search_sacrifice_effect(child)
    if from_unattachment and resolved:
        snapshot = effect.get(UNATTACHMENT_SNAPSHOT)
        if not isinstance(snapshot, Mapping):
            raise ValueError('Unattachment sacrifice requires a bound attachment snapshot')
        expected[UNATTACHMENT_SNAPSHOT] = snapshot
    if (set(effect) - {'_replacement_selections'} != set(expected)
            or type(effect[SOURCE_SACRIFICE_FIELD]) is not int
            or type(effect.get('tapped')) is not bool
            or any(effect.get(key) != value for key, value in expected.items())):
        raise ValueError('Source sacrifice requires its closed versioned battlefield instruction')
    selections = effect.get('_replacement_selections', ())
    if not isinstance(selections, (list, tuple)):
        raise ValueError('Source sacrifice replacement selections must be an array')
    return (SOURCE_SACRIFICE_CAPABILITY, SELF_MOVE_CAPABILITY, *reflexive_dependencies,
        *((UNATTACHMENT_TRIGGER_CAPABILITY, 'trigger.placement.apnap') if from_unattachment else ()),
        *(('trigger.event.normalized_spell_cast', 'zone.draw.library_to_hand') if draw_after_cast else ()))


@dataclass(frozen=True, slots=True)
class FixedSourceSacrificeIntent:
    actor: str
    source_stack_ref: str
    object_id: str
    logical_object_id: str
    card_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    cast_participant: str | None = None
    reflexive_child: FrozenMap | None = None

    def __post_init__(self):
        if not all(map(_materialized_identity, (self.actor, self.source_stack_ref,
                self.object_id, self.logical_object_id, self.card_ref, self.reason))):
            raise ValueError('Source sacrifice requires complete materialized source identities')
        if self.cast_participant is not None and not _materialized_identity(self.cast_participant):
            raise ValueError('Source sacrifice draw needs its materialized cast participant')
        if self.reflexive_child is not None and (
            not isinstance(self.reflexive_child, FrozenMap) or self.cast_participant is not None
        ):
            raise ValueError("Reflexive sacrifice requires its one immutable consequence")
        if not isinstance(self.replacement_selections, (list, tuple)):
            raise ValueError('Source sacrifice replacement selections must be an array')
        frozen = tuple(freeze_value(value) for value in self.replacement_selections)
        if any(not isinstance(value, (str, FrozenMap)) or isinstance(value, str) and not value
               for value in frozen):
            raise ValueError('Source sacrifice replacement selections must be named or structured')
        object.__setattr__(self, 'replacement_selections', frozen)
