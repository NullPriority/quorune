"""Bind an ordinary X-cost permanent's self-entry instruction to sealed entry X."""
from collections.abc import Mapping
import re
from ..entry_x_amount_model import EntryXAmountSpec,ENTRY_X_AMOUNT_MECHANIC
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND,CastXAmountSpec
from .declared_effect_amounts import declared_effect_amount_template
from .draw_templates import trigger_ability_word_material_line


def entry_x_trigger_effect_compiler(record,material_line,*,card_name,ordinary,match_source):
    if '{X}' not in record.mana_cost or record.faces or re.search(r'\bX\b',material_line) is None:return ordinary
    matched=match_source(trigger_ability_word_material_line(material_line),card_name=card_name)
    if (matched is None
            or matched['event'].casefold()!='enters' or not re.search(r'\bX\b',matched['body'])
            or re.search(r'\bwhere X\b',matched['body'],re.I)):return ordinary
    def compile_body(body,**kwargs):
        def fixed(text):
            from .damage_templates import source_pronoun_damage_effect_template
            text=re.sub(r'^(?:he|she) deals\b','It deals',text,flags=re.I)
            damage=source_pronoun_damage_effect_template(text)
            return damage.compiled() if damage is not None else ordinary(text,**kwargs)
        value=declared_effect_amount_template(body,source_name=card_name,compile_fixed=fixed,cast_x_available=True,forbid_public_x=True)
        if value is None:return ordinary(body,**kwargs)
        def bind(child):
            if isinstance(child,Mapping):
                if child.get('kind')==CAST_X_AMOUNT_KIND:return EntryXAmountSpec(CastXAmountSpec.from_dict(child).coefficient).to_dict()
                return {key:bind(item) for key,item in child.items()}
            if isinstance(child,(list,tuple)):return type(child)(bind(item) for item in child)
            return child
        return value[0],tuple(bind(effect) for effect in value[1]),value[2],(*value[3],ENTRY_X_AMOUNT_MECHANIC)
    return compile_body


def entry_x_trigger_node_compiler(record,*,trigger_node,ordinary_effect,match_source):
    def compile_trigger(**values):
        effect=entry_x_trigger_effect_compiler(record,values.get('material_line') or values['line'],
            card_name=values['card_name'],ordinary=ordinary_effect,match_source=match_source)
        return trigger_node(**values,effect_template=effect)
    return compile_trigger
