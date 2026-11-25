"""Bind an ordinary X-cost permanent's self-entry instruction to sealed entry X."""
from collections.abc import Mapping
from . import regex_working_set as re
from ..entry_x_amount_model import EntryXAmountSpec,ENTRY_X_AMOUNT_MECHANIC
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND,CastXAmountSpec
from .declared_effect_amounts import declared_effect_amount_template
from .draw_templates import trigger_ability_word_material_line
from .optional_effect_templates import fixed_optional_effect_template


def entry_x_trigger_effect_compiler(record,material_line,*,card_name,ordinary,match_source):
    if '{X}' not in record.mana_cost or record.faces or re.search(r'\bX\b',material_line) is None:return ordinary
    matched=match_source(trigger_ability_word_material_line(material_line),card_name=card_name)
    if (matched is None
            or matched['event'].casefold()!='enters' or not re.search(r'\bX\b',matched['body'])
            or re.search(r'\bwhere X\b',matched['body'],re.I)):return ordinary
    def declared(body, **kwargs):
        def fixed(text):
            from .damage_templates import source_pronoun_damage_effect_template
            text=re.sub(r'^(?:he|she) deals\b','It deals',text,flags=re.I)
            damage=source_pronoun_damage_effect_template(text)
            return damage.compiled() if damage is not None else ordinary(text,**kwargs)
        value=declared_effect_amount_template(body,source_name=card_name,compile_fixed=fixed,cast_x_available=True,forbid_public_x=True)
        if value is None:return (None, (), None, ())
        def bind(child):
            if isinstance(child,Mapping):
                if child.get('kind')==CAST_X_AMOUNT_KIND:return EntryXAmountSpec(CastXAmountSpec.from_dict(child).coefficient).to_dict()
                return {key:bind(item) for key,item in child.items()}
            if isinstance(child,(list,tuple)):return type(child)(bind(item) for item in child)
            return child
        return value[0],tuple(bind(effect) for effect in value[1]),value[2],(*value[3],ENTRY_X_AMOUNT_MECHANIC)

    def compile_body(body, **kwargs):
        if body.casefold().startswith("you may search "):
            optional = fixed_optional_effect_template(body, compile_effect=lambda text: declared(text, **kwargs))
            if optional is not None:
                return optional.compiled()
        result = declared(body, **kwargs)
        return result if result[0] is not None else ordinary(body, **kwargs)

    return compile_body


_GRAVEYARD_X_TARGET = re.compile(
    r"return target artifact or creature card with mana value X or less from your graveyard to the battlefield\.?",
    re.IGNORECASE,
)


def entry_x_target_trigger_effect_compiler(record, material_line, *, card_name, ordinary):
    """Recognize the exact public return bound inside an ordinary X-cost self-entry."""
    if record.faces or record.mana_cost.count("{X}") != 1 or "X" not in material_line:
        return ordinary
    from .fixed_counter_trigger_nodes import fixed_counter_trigger_binding

    binding = fixed_counter_trigger_binding(material_line, card_name=card_name)
    if (binding is None or binding.event.value != "permanent.enter.self" or binding.active_zone != "battlefield"
            or not _GRAVEYARD_X_TARGET.fullmatch(binding.body)):
        return ordinary

    def compile_body(body, **kwargs):
        if not _GRAVEYARD_X_TARGET.fullmatch(body):
            return ordinary(body, **kwargs)
        fixed = re.sub(r"(?<=mana value )X\b", "3", body, count=1, flags=re.IGNORECASE)
        template, effects, schema, mechanics = ordinary(fixed, **kwargs)
        if template is None:
            return template, effects, schema, mechanics
        from ..rules.entry_x_graveyard_target import declare_entry_x_graveyard_target

        return template, effects, declare_entry_x_graveyard_target(schema), mechanics

    return compile_body


def entry_x_typed_event_compiler(record, compile_event):
    if record.faces or record.mana_cost.count("{X}") != 1:
        return compile_event

    def compile_trigger(*, trigger_effect_template, **values):
        effect = entry_x_target_trigger_effect_compiler(
            record, values["material_line"], card_name=values["card_name"], ordinary=trigger_effect_template,
        )
        return compile_event(**values, trigger_effect_template=effect)

    return compile_trigger


def entry_result_trigger_node_compiler(record, *, trigger_node, ordinary_effect, match_source, material_rows):
    def compile_trigger(**values):
        effect=entry_x_trigger_effect_compiler(record,values.get('material_line') or values['line'],
            card_name=values['card_name'],ordinary=ordinary_effect,match_source=match_source)
        return trigger_node(**values,effect_template=effect)

    from .devour_nodes import devour_result_trigger_compiler
    return devour_result_trigger_compiler(record, fallback=compile_trigger, ordinary_effect=ordinary_effect,
                                          material_rows=material_rows)
