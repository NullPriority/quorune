from __future__ import annotations

"""Source-zone land permissions sharing an existing casting-grant allowance."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib

from ..replacement.immutable import FrozenMap,thaw_value
from ..semantic_runtime.action_permissions import land_play_permission_options
from .turn_cast_grant_model import TURN_CAST_GRANT_FIELD,validate_turn_cast_grant_use


@dataclass(frozen=True,slots=True)
class SourceLandPlayChoice:
    permission_id: str
    numeric_permission: str
    source_use: FrozenMap

    def __post_init__(self):
        if not isinstance(self.source_use,FrozenMap):object.__setattr__(self,'source_use',FrozenMap(self.source_use))
        spec=validate_turn_cast_grant_use(self.source_use)
        if not spec.allow_land_play or not self.permission_id or not self.numeric_permission:
            raise ValueError('Source land choice requires a shared land grant and numeric allowance')


def source_land_play_choices(host,player,card):
    from .zone_action_permissions import compiled_land_play_permission
    from .casting.turn_cast_grants import current_turn_cast_grants,finalize_turn_cast_grant_option
    numeric=land_play_permission_options(host,player)
    if not numeric:return ()
    independent=compiled_land_play_permission(host,player,card,include_source_grants=False)
    grants=current_turn_cast_grants(host,player,card,for_land=True)
    result=[]
    if independent:result.append(numeric[0])
    for use in grants:
        for permission in numeric[:1]:
            price={'id':'land-play:'+permission,TURN_CAST_GRANT_FIELD:deepcopy(use)}
            finalize_turn_cast_grant_option(price)
            result.append(SourceLandPlayChoice(price['id'],permission,FrozenMap(price[TURN_CAST_GRANT_FIELD])))
    return tuple(result)


def source_land_offer_variants(host,player,card,offer):
    variants=[]
    for index,choice in enumerate(source_land_play_choices(host,player,card)):
        row=deepcopy(offer)
        identity=choice.permission_id if isinstance(choice,SourceLandPlayChoice) else choice
        row['land_play_permission']=identity
        if index:row['id']+=':permission-'+hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
        if isinstance(choice,SourceLandPlayChoice):
            row['permission_source']=choice.source_use['source_ref']
            row['label']+=' using '+choice.source_use['source_label']
        variants.append(row)
    return variants


def commit_source_land_choice(host,player,choice):
    from .casting.turn_cast_grants import commit_turn_cast_grant_use,underlying_cast_cost_option_id
    use=thaw_value(choice.source_use)
    option={'id':choice.permission_id,TURN_CAST_GRANT_FIELD:use}
    underlying_cast_cost_option_id(option)
    if host.state.active_player!=player:
        raise ValueError('Shared land allowance must be used during its controller turn')
    commit_turn_cast_grant_use(host,option,caster=player)
