"""One versioned instruction sacrifices its original controlled permanent."""
from collections.abc import Mapping
from dataclasses import dataclass

from .replacement.immutable import FrozenMap, freeze_value

SOURCE_SACRIFICE_FIELD = 'fixed_source_sacrifice_version'
SOURCE_SACRIFICE_CAPABILITY = 'permanent.sacrifice.fixed_source'
SOURCE_SACRIFICE_MECHANIC = 'fixed-source-sacrifice'
SELF_MOVE_CAPABILITY = 'zone.self_move.activated'
FORMER_ATTACHMENT_SACRIFICE_VERSION = 2
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


def fixed_source_sacrifice_dependencies(effect, *, resolved=False):
    if not isinstance(effect, Mapping):
        return ()
    if SOURCE_SACRIFICE_FIELD not in effect:
        if (effect.get('op'), effect.get('origin'), effect.get('destination')) == (
                'self_zone_move', 'battlefield', 'graveyard'):
            raise ValueError('Battlefield source sacrifice lost its explicit instruction version')
        return ()
    from_unattachment = effect[SOURCE_SACRIFICE_FIELD] == FORMER_ATTACHMENT_SACRIFICE_VERSION
    expected = former_attachment_sacrifice_effect() if from_unattachment else fixed_source_sacrifice_effect()
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
    return (SOURCE_SACRIFICE_CAPABILITY, SELF_MOVE_CAPABILITY,
        *((UNATTACHMENT_TRIGGER_CAPABILITY, 'trigger.placement.apnap') if from_unattachment else ()))


@dataclass(frozen=True, slots=True)
class FixedSourceSacrificeIntent:
    actor: str
    source_stack_ref: str
    object_id: str
    logical_object_id: str
    card_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        if any(type(value) is not str or not value or value != value.strip()
               or value.startswith('$') for value in (self.actor, self.source_stack_ref,
                   self.object_id, self.logical_object_id, self.card_ref, self.reason)):
            raise ValueError('Source sacrifice requires complete materialized source identities')
        if not isinstance(self.replacement_selections, (list, tuple)):
            raise ValueError('Source sacrifice replacement selections must be an array')
        frozen = tuple(freeze_value(value) for value in self.replacement_selections)
        if any(not isinstance(value, (str, FrozenMap)) or isinstance(value, str) and not value
               for value in frozen):
            raise ValueError('Source sacrifice replacement selections must be named or structured')
        object.__setattr__(self, 'replacement_selections', frozen)
