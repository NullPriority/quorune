from __future__ import annotations

"""Untargeted results on one sealed permanent occurrence incarnation."""

import re
from ..damage_recipient_model import DAMAGE_PERMANENT_OBJECT, DAMAGE_PERMANENT_VARIANT
from ..event_counter_reference import BLOCK_COUNTERPART_OBJECT
from ..creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_MECHANIC, CREATURE_POWER_DAMAGE_OPERATION,
    OCCURRENCE_POWER_DAMAGE_LKI, OCCURRENCE_POWER_DAMAGE_SOURCE, OCCURRENCE_POWER_DAMAGE_VERSION,
)
from ..received_damage_model import RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT
from .received_damage_bindings import RECEIVED_PERMANENT_VARIANTS

OCCURRENCE_OBJECT='$event.permanent_object'
_EVENT_NEXT_UNTAP = re.compile(
    r"Tap that creature(?: and it|\. (?:That creature|It)) doesn['’]t untap during "
    r"its controller['’]s next untap step\.", re.I,
)
_OCCURRENCE_POWER_DAMAGE = re.compile(r"(?:It|That creature) deals damage equal to its power to (?P<target>[^.]+)\.", re.I)


def occurrence_power_damage_body(body):
    from .damage_templates import source_pronoun_damage_effect_template

    match = _OCCURRENCE_POWER_DAMAGE.fullmatch(body)
    if match is None:
        return None
    target = source_pronoun_damage_effect_template(f"It deals 1 damage to {match['target']}.")
    if target is None or target.target_schema is None:
        return None
    instruction = {
        "op": CREATURE_POWER_DAMAGE_OPERATION, "schema_version": OCCURRENCE_POWER_DAMAGE_VERSION,
        "kind": "bite", "source": OCCURRENCE_POWER_DAMAGE_SOURCE, "source_lki": OCCURRENCE_POWER_DAMAGE_LKI,
        "target": "$target.0", "target_must_be_creature": target.target_schema.get("types_any") == ["creature"],
    }
    return ("occurrence-creature-power-damage-v1", (instruction,), target.target_schema,
            (*target.mechanics, CREATURE_POWER_DAMAGE_MECHANIC))


def occurrence_power_damage_result(binding, body):
    from ..rules.creature_power_damage_capability_shapes import occurrence_power_damage_event_is_closed

    if occurrence_power_damage_event_is_closed(binding.event.value, binding.active_zone):
        compiled = occurrence_power_damage_body(body)
        if compiled is not None:
            return compiled, True
    return None


def entry_permanent_result(binding, body):
    from ..rules.entry_permanent_results import (
        ENTRY_ANIMATION_OPERATION, ENTRY_PERMANENT_RESULT_MECHANIC,
        entry_permanent_result_binding, entry_permanent_result_effect,
    )
    from ..saddle import SADDLE_OPERATION

    operation = {
        "it becomes an artifact creature until end of turn.": ENTRY_ANIMATION_OPERATION,
        "it becomes saddled until end of turn.": SADDLE_OPERATION,
    }.get(body.casefold())
    if operation is None or not entry_permanent_result_binding(binding.event.value, binding.event_condition, operation):
        return None
    return ('fixed-entry-permanent-result-v1', (entry_permanent_result_effect(operation),), None,
        (ENTRY_PERMANENT_RESULT_MECHANIC,)), True


def entry_equipment_attachment_result(binding, body, *, card_name, effect_template):
    from ..rules.attachment_actions import ENTRY_ATTACHMENT_EVENTS, entry_equipment_attachment_effect
    from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, FixedOptionalEffectTemplate

    match = re.fullmatch(r"(?P<optional>you may )?attach this Equipment to (?:it|that creature)\.", body, re.I)
    if (match is None or binding.event.value not in ENTRY_ATTACHMENT_EVENTS
            or binding.active_zone != "battlefield"):
        return None
    targeted = effect_template("Attach this Equipment to target creature you control.", card_name=card_name)
    if targeted[1] != ({"op": "attach", "attachment_kind": "equipment",
                        "source": "$source.zone_object", "target": "$target.0"},):
        return None
    template = "fixed-entry-equipment-attachment-v1"
    effects = (entry_equipment_attachment_effect(),)
    mechanics = ("cr-701-3-attach",)
    if match["optional"]:
        compiled = FixedOptionalEffectTemplate(template, effects, None, (FIXED_OPTIONAL_EFFECT_MECHANIC, *mechanics))
        return compiled.compiled(), True
    return (template, effects, None, mechanics), True


def occurrence_object_result(binding,body,*,effect_template,card_name):
    if body == binding.body and _EVENT_NEXT_UNTAP.fullmatch(body):
        reference = {
            DAMAGE_PERMANENT_VARIANT: DAMAGE_PERMANENT_OBJECT,
            'source_block_pair_counterpart': BLOCK_COUNTERPART_OBJECT,
        }.get(binding.variant)
        if reference is not None:
            return ('tap-event-creature-next-controller-untap-v1', (
                {'op': 'tap', 'card': reference},
                {'op': 'prohibit_next_untap', 'card': reference},
            ), None, ('tap-and-untap',)), False
    if binding.variant == 'source_block_pair_counterpart':
        moved=re.fullmatch(r"(?P<destroy>Destroy that creature\.)(?P<prohibit> It can['’]t be regenerated\.)?|(?P<bounce>Return that creature to its owner's hand\.)",body,re.I)
        if moved is not None:
            op='bounce' if moved['bounce'] else 'destroy'
            instruction={'op':op,'card':BLOCK_COUNTERPART_OBJECT}
            if moved['prohibit']:instruction['regeneration_prohibited']=True
            return ('fixed-block-counterpart-zone-result-v1',(instruction,),None,
                ('return-to-owner-hand',) if op=='bounce' else ('destroy',*(('regeneration-prohibition',) if moved['prohibit'] else ()))),False
        damage = re.fullmatch(r'this creature deals (?P<amount>[1-9][0-9]*) damage to that creature\.', body, re.I)
        if damage is not None:
            from .damage_templates import source_pronoun_damage_effect_template
            leaf = source_pronoun_damage_effect_template('It deals ' + damage['amount'] + ' damage to target creature.')
            if leaf is None:
                return None
            template, effects, schema, mechanics = leaf.compiled()
            return (template, ({**effects[0], 'target': BLOCK_COUNTERPART_OBJECT},), None,
                    tuple(mechanic for mechanic in mechanics if mechanic != 'cr-115-targets')), True
        from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
        from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
        match = re.fullmatch(r'that creature gains (?P<keyword>[a-z ]+) until end of turn\.', body, re.I)
        keyword = match['keyword'].title() if match is not None else None
        if keyword not in FIXED_CHARACTERISTIC_KEYWORDS:
            return None
        template, effects, schema, mechanics = FixedSourceCharacteristicsTemplate(
            source_kind='block-counterpart', keywords=(keyword,)).compiled()
        return (template, ({**effects[0], 'card': BLOCK_COUNTERPART_OBJECT},), None, mechanics), True
    tap = binding.event.value in {'permanent.tap','permanent.untap'} and binding.variant in {'public_tap_state_transition','source_public_tap_state_transition'}
    receiving = binding.variant in RECEIVED_PERMANENT_VARIANTS and binding.event.value in {RECEIVED_DAMAGE_EVENT,RECEIVED_COMBAT_DAMAGE_EVENT,'permanent.tap'}
    if not tap and not receiving:return None
    if tap and re.fullmatch(r'its controller gets a poison counter\.',body,re.I):
        return ('poison-fixed-occurrence-controller-v1',({'op':'place_player_counters','subjects':'target','counter':'poison',
            'amount':1,'source':'$source','target':{'kind':'occurrence_controller_information','schema_version':1}},),
            None,('cr-122-counters',)),True
    match=re.fullmatch(r'Destroy (?:it|that permanent|that creature|that artifact|that land)\.(?P<prohibit> It can[\x27’]t be regenerated\.)?',body,re.I)
    if match is None:return None
    prohibit=bool(match['prohibit'])
    compiled=effect_template('Destroy target permanent.'+(" It can't be regenerated." if prohibit else ''),card_name=card_name)
    expected={'op':'destroy','card':'$target.0',**({'regeneration_prohibited':True} if prohibit else {})}
    if compiled[0] is None or len(compiled[1])!=1 or compiled[1][0]!=expected:return None
    return ('destroy-fixed-occurrence-object-v1',({**expected,'card':OCCURRENCE_OBJECT},),None,tuple(m for m in compiled[3] if m!='cr-115-targets')),True
