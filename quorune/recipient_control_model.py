"""Closed payloads for control acquired by a player other than the resolver."""
from collections.abc import Mapping

RECIPIENT_CONTROL_CAPABILITY='continuous.control.fixed_recipient'
RECIPIENT_CONTROL_MECHANIC='fixed-recipient-control'
GIVE_CONTROL_OPERATION='give_control'
CHOOSE_CONTROL_RECIPIENT_OPERATION='choose_control_recipient'
GIVE_CONTROL_SET_OPERATION='give_control_set'
TWO_CONTROLLER_CREATURE_TRANSFER_FIELD = "two_controller_creature_transfer_version"


def two_controller_creature_transfer_effect(opponent="$target.0", source="$source"):
    return {"op": "gain_control_set", "opponent": opponent, "source": source,
            TWO_CONTROLLER_CREATURE_TRANSFER_FIELD: 1}


def two_controller_creature_transfer_target():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


def two_controller_creature_transfer_query():
    from .object_predicate import ObjectQuerySpec

    return ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


def validate_two_controller_creature_transfer(effect, *, resolved_source=None):
    if not isinstance(effect, Mapping):
        raise ValueError("Two-controller creature transfer requires its typed instruction")
    opponent = effect.get("opponent")
    if resolved_source is None:
        expected = two_controller_creature_transfer_effect()
    else:
        if type(opponent) is not str or not opponent or opponent.startswith("$"):
            raise ValueError("Two-controller creature transfer requires its bound opponent")
        expected = two_controller_creature_transfer_effect(opponent, resolved_source)
    if effect != expected or type(effect[TWO_CONTROLLER_CREATURE_TRANSFER_FIELD]) is not int:
        raise ValueError("Two-controller creature transfer requires the complete printed result")
    return opponent


def validate_recipient_control_effect(effect,*,allow_reason=False,resolved=False):
    fields={'op','schema_version','card','controller','source'}
    if (not isinstance(effect,Mapping) or not fields<=set(effect)
            or set(effect)-fields-({'reason'} if allow_reason else set())
            or effect['op']!=GIVE_CONTROL_OPERATION or type(effect['schema_version']) is not int or effect['schema_version'] not in {1,2,3,4}):
        raise ValueError('Recipient control requires its closed versioned fields')
    for field in ('card','controller'):
        value=effect[field]
        if field == "controller" and effect["schema_version"] == 4 and not resolved:
            from .target_controller_reference import is_damage_dealer_controller_reference
            if not is_damage_dealer_controller_reference(value):
                raise ValueError("Control requires the original committed damage dealer controller")
            continue
        if field=='controller' and effect['schema_version']==3 and not resolved:
            from .player_maximum_model import PlayerMaximumSpec
            PlayerMaximumSpec.from_reference(value)
            continue
        if field=='controller' and effect['schema_version']==2 and not resolved:
            from .target_controller_reference import controller_reference_index
            if controller_reference_index(value)!=0:
                raise ValueError('Recipient control requires its single announced target controller')
            continue
        if value is not None and (type(value) is not str or not value):
            raise ValueError('Recipient control requires explicit subject and player references')
    if type(effect['source']) is not str or not effect['source']:
        raise ValueError('Recipient control requires its resolving source provenance')
    if 'reason' in effect and (type(effect['reason']) is not str or not effect['reason']):
        raise ValueError('Recipient control requires a nonempty reason')


def recipient_control_effect(*,card,controller,source='$source'):
    from .player_maximum_model import PLAYER_MAXIMUM_REFERENCE
    from .target_controller_reference import is_damage_dealer_controller_reference
    if is_damage_dealer_controller_reference(controller):
        version = 4
    elif isinstance(controller, Mapping):
        version = 3 if controller.get("kind") == PLAYER_MAXIMUM_REFERENCE else 2
    else:
        version = 1
    effect={'op':GIVE_CONTROL_OPERATION,'schema_version':version,
        'card':card,'controller':controller,'source':source}
    validate_recipient_control_effect(effect)
    return effect


def validate_recipient_control_set_effect(effect,*,allow_reason=False):
    fields={'op','schema_version','scope','controller','exclude_source','source'}
    if (not isinstance(effect,Mapping) or not fields<=set(effect)
            or set(effect)-fields-({'reason'} if allow_reason else set())
            or effect['op']!=GIVE_CONTROL_SET_OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=1
            or effect['scope']!='other_controlled_permanents'):
        raise ValueError('Recipient control set requires its closed actor-controlled scope')
    for field in ('controller','exclude_source'):
        if effect[field] is not None and (type(effect[field]) is not str or not effect[field]):
            raise ValueError('Recipient control set requires explicit player and source references')
    if type(effect['source']) is not str or not effect['source']:
        raise ValueError('Recipient control set requires resolving source provenance')
    if 'reason' in effect and (type(effect['reason']) is not str or not effect['reason']):
        raise ValueError('Recipient control set requires a nonempty reason')
