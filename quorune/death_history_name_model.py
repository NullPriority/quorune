"""Pure additive death-name fact and journal codec validation."""
def validate_event_rules_names(kind,names):
    if names is None:return
    if (kind!='permanent_died' or not isinstance(names,tuple)
            or any(type(name) is not str or not name or name!=name.strip() for name in names)
            or names!=tuple(sorted(set(names)))):
        raise ValueError('Death rules names require canonical immutable public names')


def decode_event_rules_names(payload):
    if 'rules_names' not in payload:return None
    value=payload['rules_names']
    if not isinstance(value,(tuple,list)):raise ValueError('Present death rules names cannot be null or malformed')
    names=tuple(value);validate_event_rules_names(payload.get('kind'),names)
    return names


def validate_death_name_history(version,death_version,events):
    if version is not None and (type(version) is not int or version!=1 or death_version!=1):
        raise ValueError('Death-name history requires complete version-one permanent death history')
    for event in events:
        if event.kind=='permanent_died' and ((event.rules_names is not None)!=(version==1)):
            raise ValueError('Death names disagree with their explicit journal provenance')


def death_name_history_fields(version):
    return {'death_name_history_version':version} if version is not None else {}




def decode_death_name_history_version(data):
    if 'death_name_history_version' not in data:return None
    version=data['death_name_history_version']
    if type(version) is not int or version!=1:
        raise ValueError('Present death-name journal marker must be version one')
    return version
