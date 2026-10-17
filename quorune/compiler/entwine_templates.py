from __future__ import annotations

"""Full-consumption fixed-mana Entwine over independently closed modes."""

from dataclasses import dataclass
import hashlib
import re

from ..entwine_model import ENTWINE_FIXED_MECHANIC,EntwineModeCost
from ..util import mana_cost_to_vector,stable_json
from .modal_templates import fixed_nonrepeating_modal_template


@dataclass(frozen=True,slots=True)
class FixedEntwineModalTemplate:
    body: object
    price: EntwineModeCost

    @property
    def cost_schema(self):return {'entwine':self.price.to_dict()}

    def compiled(self):
        template,effects,schema,mechanics=self.body.compiled()
        digest=hashlib.sha256(stable_json({'body':template,'price':self.price.to_dict()}).encode()).hexdigest()[:16]
        return 'fixed-entwine-modal-'+digest+'-v1',effects,schema,(*mechanics,ENTWINE_FIXED_MECHANIC)


def fixed_entwine_modal_template(material_rows,*,compile_effect):
    if len(material_rows) not in {4,5,6,7} or material_rows[0][1]!='Choose one —':return None
    match=re.fullmatch(r'Entwine (?P<cost>(?:\{(?:[0-9]+|[WUBRGC])\})+)\.?',material_rows[-1][1])
    if match is None:return None
    vector,complex_symbols=mana_cost_to_vector(match['cost'])
    if complex_symbols:return None
    rows=[(material_rows[0][0],'Choose one or more —',material_rows[0][2]),*material_rows[1:-1]]
    body=fixed_nonrepeating_modal_template(rows,compile_effect=compile_effect)
    if body is None or body.context_prefix:return None
    price=EntwineModeCost(tuple(body.target_schema['modes']),vector)
    price.validate_schema(body.target_schema)
    return FixedEntwineModalTemplate(body,price)
