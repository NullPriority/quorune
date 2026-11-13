"""Closed reference shape for information about one announced target's controller."""
from collections.abc import Mapping

TARGET_CONTROLLER_INFORMATION_KIND='target_controller_information'
TARGET_CONTROLLER_INFORMATION_CAPABILITY='resolution.target_controller_information'
TARGET_CONTROLLER_INFORMATION_MECHANIC='fixed-target-controller-information'


def target_controller_reference(index=0):
    return {'kind':TARGET_CONTROLLER_INFORMATION_KIND,'schema_version':1,'index':index}


def controller_reference_index(value):
    if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','index'}
            or value['kind']!=TARGET_CONTROLLER_INFORMATION_KIND or type(value['schema_version']) is not int
            or value['schema_version']!=1 or type(value['index']) is not int or value['index']<0):
        raise ValueError('Target controller information requires one closed target index')
    return value['index']
