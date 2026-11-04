"""Immutable initial-target roles for CR700.13 crime classification."""
from collections.abc import Mapping
from dataclasses import dataclass

CRIME_CAPABILITY='player.action.crime'
CRIME_EVENT='player.committed_crime'
CRIME_HISTORY_KIND='crime_committed'
CRIME_FACT='controller_committed_crime_this_turn'


def crime_fact_dependencies(value):
    if isinstance(value,Mapping):
        own={CRIME_CAPABILITY} if value.get('fact')==CRIME_FACT else set()
        if own:
            from .continuous_conditions import FixedPublicStateConditionSpec
            FixedPublicStateConditionSpec.from_dict(value)
        return tuple(sorted({*own,*(cap for child in value.values() for cap in crime_fact_dependencies(child))}))
    if isinstance(value,(list,tuple)):
        return tuple(sorted({cap for child in value for cap in crime_fact_dependencies(child)}))
    return ()


@dataclass(frozen=True,slots=True)
class CrimeTarget:
    reference: str
    category: str
    principal: str
    zone: str | None=None

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.reference,self.principal))
                or type(self.category) is not str or self.category not in {'player','permanent','spell','ability','card'}
                or self.category=='permanent' and self.zone!='battlefield'
                or self.category=='card' and self.zone not in {'hand','library','graveyard','exile','command','outside'}
                or self.category in {'player','spell','ability'} and self.zone is not None):
            raise ValueError('Crime target roles require closed category, principal and zone facts')

    @classmethod
    def from_target_snapshot(cls,value):
        if not isinstance(value,Mapping):raise ValueError('Crime targets require an authoritative target snapshot')
        category=value.get('category')
        return cls(value.get('ref'),category,value.get('owner') if category=='card' else value.get('controller'),
            value.get('zone') if category in {'card','permanent'} else None)

    def qualifies(self,actor):
        return self.principal!=actor and (self.category!='card' or self.zone=='graveyard')


@dataclass(frozen=True,slots=True)
class CrimeOccurrence:
    player: str
    stack_identity: str
    stack_ref: str
    action_kind: str
    qualifying_targets: tuple[CrimeTarget,...]

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.player,self.stack_identity,self.stack_ref))
                or type(self.action_kind) is not str or self.action_kind not in {'spell','activated_ability','triggered_ability'}
                or not isinstance(self.qualifying_targets,tuple) or not self.qualifying_targets
                or any(not isinstance(row,CrimeTarget) or not row.qualifies(self.player) for row in self.qualifying_targets)
                or len({row.reference for row in self.qualifying_targets})!=len(self.qualifying_targets)):
            raise ValueError('A crime occurrence requires one initial action and its qualifying target roles')

    def to_context(self):
        return {'player':self.player,'stack':self.stack_ref,'stack_identity':self.stack_identity,
            'stack_kind':self.action_kind,'qualifying_target_count':len(self.qualifying_targets)}


def classify_crime(*,actor,stack_identity,stack_ref,action_kind,targets,participants):
    if (not isinstance(participants,tuple) or not participants or any(type(p) is not str or not p for p in participants)
            or len(set(participants))!=len(participants) or actor not in participants
            or any(type(value) is not str or not value for value in (actor,stack_identity,stack_ref))
            or not isinstance(targets,tuple) or any(not isinstance(row,CrimeTarget) or row.principal not in participants for row in targets)
            or len({row.reference for row in targets})!=len(targets)
            or type(action_kind) is not str or action_kind not in {'spell','activated_ability','triggered_ability','spell_copy','activated_ability_copy','triggered_ability_copy'}):
        raise ValueError('Crime classification needs complete initial targets and known participant roles')
    if action_kind in {'spell_copy','activated_ability_copy'}:return None
    if action_kind=='triggered_ability_copy':action_kind='triggered_ability'
    qualified=tuple(row for row in targets if row.qualifies(actor))
    return CrimeOccurrence(actor,stack_identity,stack_ref,action_kind,qualified) if qualified else None
