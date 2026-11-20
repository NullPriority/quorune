from __future__ import annotations

"""Complete fixed draw-then-current-hand putback clauses."""

from dataclasses import dataclass
from ..fixed_hand_putback_model import (
    FixedHandPutbackSpec, PutbackPlacement as Placement, FIXED_HAND_PUTBACK_MECHANIC,
)

_CLAUSES = {
    'draw two cards, then put two cards from your hand on the bottom of your library.':(2,2,Placement.BOTTOM),
    'draw three cards, then put a card from your hand on the bottom of your library.':(3,1,Placement.BOTTOM),
    'draw two cards, then shuffle a card from your hand into your library.':(2,1,Placement.SHUFFLE),
    'draw three cards, then put two cards from your hand both on top of your library or both on the bottom of your library.':(3,2,Placement.EITHER),
}


@dataclass(frozen=True,slots=True)
class FixedDrawHandPutbackTemplate:
    draw_count: int
    putback: FixedHandPutbackSpec

    def compiled(self):
        return ('fixed-draw-hand-putback-'+str(self.draw_count)+'-'+str(self.putback.count)+'-'+self.putback.placement.value+'-v1',
                ({'op':'draw','player':'$controller','count':self.draw_count,'private':True},self.putback.effect()),
                None,(FIXED_HAND_PUTBACK_MECHANIC,))


def fixed_draw_hand_putback_template(text):
    parsed=_CLAUSES.get(' '.join(text.casefold().split()))
    if parsed is None:return None
    draw_count,count,placement=parsed
    return FixedDrawHandPutbackTemplate(draw_count,FixedHandPutbackSpec(count,placement))
