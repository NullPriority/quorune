from __future__ import annotations

"""Read live source-owned Landwalk exceptions without changing any ability."""

from ..ability_fragments import canonical_ability_fragments
from ..attachments import attached_object_identity
from ..landwalk import LANDWALK_KEYWORDS
from ..landwalk_permission_model import LandwalkBlockPermission


def current_landwalk_block_exceptions(host, blocker) -> frozenset[str]:
    ignored=set()
    for source in sorted(host.state.cards.values(), key=lambda card:card.ref):
        if source.zone != "battlefield" or source.phased_out:
            continue
        fragments=canonical_ability_fragments(host._effective_ability_fragments(source))
        for permission in fragments:
            if not isinstance(permission,LandwalkBlockPermission):
                continue
            if permission.scope == "attached_blocker":
                attached=attached_object_identity(host.state.cards,source)
                if attached is None or (attached.object_id,attached.logical_object_id) != (blocker.object_id,blocker.logical_object_id):
                    continue
            ignored.update(LANDWALK_KEYWORDS if permission.keyword is None else (permission.keyword,))
    return frozenset(ignored)
