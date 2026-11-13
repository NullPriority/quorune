"""Closed payloads for control acquired by a player other than the resolver."""
from collections.abc import Mapping

RECIPIENT_CONTROL_CAPABILITY='continuous.control.fixed_recipient'
RECIPIENT_CONTROL_MECHANIC='fixed-recipient-control'
GIVE_CONTROL_OPERATION='give_control'
CHOOSE_CONTROL_RECIPIENT_OPERATION='choose_control_recipient'


def validate_recipient_control_effect(effect,*,allow_reason=False,resolved=False):
    fields={'op','schema_version','card','controller','source'}
    if (not isinstance(effect,Mapping) or not fields<=set(effect)
            or set(effect)-fields-({'reason'} if allow_reason else set())
            or effect['op']!=GIVE_CONTROL_OPERATION or type(effect['schema_version']) is not int or effect['schema_version'] not in {1,2}):
        raise ValueError('Recipient control requires its closed versioned fields')
    for field in ('card','controller'):
        value=effect[field]
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
    effect={'op':GIVE_CONTROL_OPERATION,'schema_version':2 if isinstance(controller,Mapping) else 1,
        'card':card,'controller':controller,'source':source}
    validate_recipient_control_effect(effect)
    return effect
