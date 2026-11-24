from __future__ import annotations

"""Immutable acting-object facts for targeting and immediate source LKI."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite
from .characteristic_evaluation import type_parts
from .creature_subtypes import canonical_creature_subtype
from .errors import GameRuleError
from .protection import ProtectionSource

TARGETING_SOURCE_CONTEXT='targeting_source_characteristics'
BOUND_TARGETING_SOURCE_VERSION = 2
TURN_SPELL_SOURCE_COLOR_PROTECTION_OPERATION = "grant_turn_spell_source_color_protection"
TURN_SPELL_SOURCE_COLOR_PROTECTION_MECHANIC = "fixed-turn-spell-source-color-protection"
TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY = "resolution.turn_spell_source_color_protection"
TURN_SPELL_SOURCE_COLOR_PROTECTION_WINDOW = "turn_spell_source_color_protection"


def _turn_spell_source_colors(raw):
    if (not isinstance(raw, (list, tuple)) or not raw
            or any(type(color) is not str or color not in set("WUBRG") for color in raw)
            or tuple(raw) != tuple(color for color in "WUBRG" if color in raw)):
        raise ValueError("Turn-long spell-source color protection requires distinct canonical colors")
    return tuple(raw)


def turn_spell_source_color_grant(effect, *, player="$controller"):
    fields = {"op", "schema_version", "player", "colors"}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != TURN_SPELL_SOURCE_COLOR_PROTECTION_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or effect["player"] != player):
        raise ValueError("Turn-long spell-source protection requires a closed controller result")
    colors = _turn_spell_source_colors(effect["colors"])
    if len(colors) != 2:
        raise ValueError("Turn-long spell-source protection requires two fixed colors")
    return colors


def current_turn_spell_source_protection_colors(stats, turn_sequence):
    raw = stats.get(TURN_SPELL_SOURCE_COLOR_PROTECTION_WINDOW)
    if raw is None:
        return ()
    if (not isinstance(raw, Mapping) or set(raw) != {"schema_version", "turn_sequence", "colors"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or type(raw["turn_sequence"]) is not int or not 0 <= raw["turn_sequence"] <= turn_sequence):
        raise ValueError("Turn-long spell-source protection has an invalid duration")
    colors = _turn_spell_source_colors(raw["colors"])
    return colors if raw["turn_sequence"] == turn_sequence else ()


def granted_turn_spell_source_colors(stats, turn_sequence, colors):
    previous = current_turn_spell_source_protection_colors(stats, turn_sequence)
    return {"schema_version": 1, "turn_sequence": turn_sequence,
            "colors": [color for color in "WUBRG" if color in previous or color in colors]}


def spell_source_is_prohibited_by_color(source, colors):
    if not colors:
        return False
    return source is None or (source.kind == "spell"
                              and (source.colors is None or not source.colors.isdisjoint(colors)))


@dataclass(frozen=True,slots=True)
class TargetingSource:
    kind: str
    colors: frozenset[str] | None=None
    card_types: frozenset[str] | None=None
    subtypes: frozenset[str] | None=None
    source_controller: str | None=None
    spell_owner: str | None=None
    protection_source: ProtectionSource | None = None
    logical_object_id: str | None = None
    chosen_creature_type: str | None = None
    chosen_color: str | None = None

    def __post_init__(self):
        if self.kind not in {'spell','ability'}:raise ValueError('Targeting source kind must be explicit')
        if self.logical_object_id is not None and (
            type(self.logical_object_id) is not str or not self.logical_object_id
        ):
            raise ValueError("Targeting source incarnation must be explicit")
        if self.chosen_creature_type is not None and (
            self.kind != "ability" or canonical_creature_subtype(self.chosen_creature_type) != self.chosen_creature_type
        ):
            raise ValueError("A targeting designation requires a canonical source creature type")
        if self.chosen_color is not None and (
            self.kind != "ability" or type(self.chosen_color) is not str
            or self.chosen_color not in {"W", "U", "B", "R", "G"}
        ):
            raise ValueError("A targeting designation requires a canonical source color")
        if self.source_controller is not None and (type(self.source_controller) is not str or not self.source_controller):raise ValueError('Targeting source controller must be a seat or unavailable')
        if self.spell_owner is not None and (self.kind!='spell' or type(self.spell_owner) is not str
                or not self.spell_owner or self.spell_owner.strip()!=self.spell_owner):
            raise ValueError('Spell ownership requires an explicit canonical spell owner')
        for name in ('colors','card_types','subtypes'):
            value=getattr(self,name)
            if value is not None and (type(value) is not frozenset or any(type(v) is not str or not v for v in value)):
                raise ValueError('Targeting source characteristics must be immutable canonical terms')
        if self.colors is not None and not self.colors.issubset(set('WUBRG')):raise ValueError('Targeting colors must be canonical')
        if any(v!=v.casefold() for values in (self.card_types,self.subtypes) if values is not None for v in values):
            raise ValueError('Targeting types must be canonical')
        if self.protection_source is not None and (
            not isinstance(self.protection_source, ProtectionSource)
            or self.protection_source.colors != self.colors
        ):
            raise ValueError("Targeting protection requires the acting source's typed characteristics")

    @classmethod
    def from_characteristics(cls,kind,value,*,controller=None,spell_owner=None,protection_source=None,logical_object_id=None):
        types,subtypes,_=type_parts(str(value.get('type_line') or ''))
        return cls(kind, frozenset(str(v).upper() for v in value.get('colors',())), frozenset(types),
                   frozenset(subtypes), controller, spell_owner, protection_source, logical_object_id)


@dataclass(frozen=True,slots=True)
class TargetingSourceBinding:
    object_id: str
    object_ref: str
    logical_object_id: str
    zone: str
    controller: str
    type_line: str
    colors: tuple[str,...]
    mana_value: float | None = None
    schema_version: int = 1

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.object_id,self.object_ref,self.logical_object_id,self.zone,self.controller)) or type(self.type_line) is not str:
            raise ValueError('Targeting source binding requires its complete incarnation')
        if self.zone not in {'battlefield','stack','hand','graveyard','exile','command','library'}:raise ValueError('Unknown targeting source zone')
        if type(self.colors) is not tuple or len(set(self.colors))!=len(self.colors) or any(v not in tuple('WUBRG') for v in self.colors):
            raise ValueError('Targeting source binding has malformed colors')
        if (type(self.schema_version) is not int or self.schema_version not in {1, BOUND_TARGETING_SOURCE_VERSION}
                or self.mana_value is not None and (self.schema_version != BOUND_TARGETING_SOURCE_VERSION
                    or type(self.mana_value) not in {int, float} or self.mana_value < 0 or not isfinite(self.mana_value))):
            raise ValueError('Targeting source binding has malformed protection facts')

    def to_dict(self):
        data = {'schema_version':self.schema_version,'object_id':self.object_id,'object_ref':self.object_ref,'logical_object_id':self.logical_object_id,
                'zone':self.zone,'controller':self.controller,'type_line':self.type_line,'colors':list(self.colors)}
        if self.schema_version == BOUND_TARGETING_SOURCE_VERSION:
            data['mana_value'] = self.mana_value
        return data

    @classmethod
    def from_dict(cls,value):
        fields = {'schema_version','object_id','object_ref','logical_object_id','zone','controller','type_line','colors'}
        if isinstance(value, Mapping) and value.get('schema_version') == BOUND_TARGETING_SOURCE_VERSION:
            fields.add('mana_value')
        if not isinstance(value,Mapping) or set(value)!=fields or type(value['colors']) not in (list,tuple):
            raise ValueError('Targeting source binding fields are closed')
        return cls(value['object_id'],value['object_ref'],value['logical_object_id'],value['zone'],
                   value['controller'],value['type_line'],tuple(value['colors']),
                   mana_value=value.get('mana_value'), schema_version=value['schema_version'])

    def facts(self):
        characteristics = {'type_line': self.type_line, 'colors': self.colors, 'mana_value': self.mana_value}
        protection = ProtectionSource.from_characteristics(characteristics) if self.schema_version == BOUND_TARGETING_SOURCE_VERSION else None
        return TargetingSource.from_characteristics('ability', characteristics, controller=self.controller,
                                                    protection_source=protection,logical_object_id=self.logical_object_id)


def capture_targeting_source(host,source,*,characteristics=None,zone=None,schema_version=1):
    data=host._effective_card_data(source) if characteristics is None else characteristics
    if not isinstance(data,Mapping) or type(data.get('type_line')) is not str:
        raise ValueError('Targeting source characteristics require an explicit type line')
    return TargetingSourceBinding(source.object_id,source.ref,source.logical_object_id,zone or source.zone,source.controller if (zone or source.zone) in {'battlefield','stack'} else source.owner,
        data['type_line'],tuple(sorted(set(data.get('colors',())))),
        mana_value=ProtectionSource.from_characteristics(data).mana_value if schema_version == BOUND_TARGETING_SOURCE_VERSION else None,
        schema_version=schema_version)


def spell_targeting_source(host,card):
    return TargetingSource.from_characteristics('spell',host._effective_card_data(card),
        controller=card.controller,spell_owner=card.owner)


def ability_targeting_source(host,card):
    facts = capture_targeting_source(host, card).facts()
    from .rules.chosen_color_target_shapes import current_linked_target_color

    annotations = getattr(card, "annotations", {})
    subtype = annotations.get("chosen_creature_type")
    color = current_linked_target_color(card)
    choices = {}
    if subtype is not None and canonical_creature_subtype(subtype) == subtype:
        choices["chosen_creature_type"] = subtype
    if type(color) is str and color in {"W", "U", "B", "R", "G"}:
        choices["chosen_color"] = color
    return replace(facts, **choices) if choices else facts


def _with_retained_designations(item, facts):
    from .designation_prevention import DESIGNATION_INFORMATION_FIELD, source_entry_designation_value
    from .entry_designations import EntryDesignationKind

    info = item.context.get(DESIGNATION_INFORMATION_FIELD)
    values = info.get("values") if isinstance(info, Mapping) else None
    if not isinstance(values, Mapping):
        return facts
    try:
        choices = {
            kind.annotation: source_entry_designation_value(item, kind)
            for kind in (EntryDesignationKind.CREATURE_TYPE, EntryDesignationKind.COLOR)
            if kind.value in values
        }
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return replace(facts, **choices) if choices else facts


def _requires_shared_draw_source(program):
    from .rules.optional_draw_discard import is_source_shared_type_draw_discard

    return program is not None and any(is_source_shared_type_draw_discard(effect) for effect in program.effects)


def stack_targeting_source(host,item):
    if item.kind in {'spell','spell_copy'}:
        card=host.state.cards.get(item.card_object_id or item.source_object_id)
        if card is not None and card.zone=='stack':return spell_targeting_source(host,card)
        data=item.context.get('copy_permanent_characteristics')
        return TargetingSource.from_characteristics('spell',data) if isinstance(data,Mapping) else TargetingSource('spell')
    if item.kind not in {'activated_ability','triggered_ability'}:raise GameRuleError('Unsupported targeting stack object kind')
    source_id=item.source_object_id
    source=host.state.cards.get(source_id)
    raw=item.context.get(TARGETING_SOURCE_CONTEXT)
    try:binding=TargetingSourceBinding.from_dict(raw) if raw is not None else None
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    expected=item.context.get('source_logical_object_id')
    if binding is not None:
        if binding.object_id!=source_id or expected not in {None,binding.logical_object_id}:
            raise GameRuleError('Targeting source binding refers to another incarnation')
        if (source is not None and source.ref==binding.object_ref and source.logical_object_id==binding.logical_object_id
                and source.zone==binding.zone
                and not (getattr(source, "phased_out", False)
                         and _requires_shared_draw_source(host.semantics.get(item.semantic_key)))):
            facts = capture_targeting_source(host, source, schema_version=binding.schema_version).facts()
        else:
            facts = binding.facts()
    elif source is not None and expected == source.logical_object_id:
        facts = capture_targeting_source(host, source).facts()
    else:
        facts = TargetingSource("ability")
    return _with_retained_designations(item, facts)


def pin_targeting_source_departures(host,cards,*,error_type=GameRuleError,phase_out=False):
    """Capture source information before a departure or a supported phase-out batch."""
    if not cards:
        return
    from .trigger_batches import PendingTriggerBatch
    from .trigger_processing import replace_pending_trigger_batch
    try:
        departures={(c.object_id,c.logical_object_id):c for c in cards}
        updates={};pending=[]
        frames=[item.to_dict() for item in host.state.stack]
        for batch in host.state.pending_trigger_batches:
            frames.extend(item for group in batch.to_dict()['groups'] for item in group['items'])
        for item in frames:
            raw=item.get('context',{}).get(TARGETING_SOURCE_CONTEXT)
            if raw is None:continue
            binding=TargetingSourceBinding.from_dict(raw)
            if binding.object_id!=item.get('source_object_id'):raise ValueError('Targeting source frame identity changed')
            source=departures.get((binding.object_id,binding.logical_object_id))
            if source is None or source.zone != binding.zone:
                continue
            if phase_out or getattr(source, "phased_out", False):
                shared_draw = _requires_shared_draw_source(host.semantics.get(item.get("semantic_key")))
                if phase_out != shared_draw:
                    continue
            updates[item['ref']]=capture_targeting_source(host,source,schema_version=binding.schema_version).to_dict()
        for index,batch in enumerate(host.state.pending_trigger_batches):
            data=batch.to_dict();changed=False
            for group in data['groups']:
                for item in group['items']:
                    if item['ref'] in updates:item['context'][TARGETING_SOURCE_CONTEXT]=updates[item['ref']];changed=True
            if changed:pending.append((index,PendingTriggerBatch.from_dict(data)))
        for item in host.state.stack:
            if item.ref in updates:item.context[TARGETING_SOURCE_CONTEXT]=updates[item.ref]
        for index,batch in pending:replace_pending_trigger_batch(host,index,batch)
    except (ValueError,TypeError,KeyError) as exc:raise error_type(str(exc)) from exc


def ensure_trigger_targeting_source(host,item):
    if item.kind not in {'triggered_ability','activated_ability'} or TARGETING_SOURCE_CONTEXT in item.context:return
    program=host.semantics.get(item.semantic_key)
    from .ability_source_information import complete_ability_source_information
    if not (item.targets or item.context.get('target_schema_override') or getattr(program,'target_schema',None)
            or _requires_shared_draw_source(program) or complete_ability_source_information(host.state)):return
    source=host.state.cards.get(item.source_object_id)
    if source is not None and item.context.get('source_logical_object_id',source.logical_object_id)==source.logical_object_id:
        item.context[TARGETING_SOURCE_CONTEXT]=capture_targeting_source(host,source).to_dict()


def spell_copy_targeting_source(host,template):
    from .rules.casting.spell_text import CAST_TEXT_COPY_CHARACTERISTICS
    if CAST_TEXT_COPY_CHARACTERISTICS in template:
        actor = template.get("controller")
        return TargetingSource.from_characteristics("spell", template[CAST_TEXT_COPY_CHARACTERISTICS],
            controller=actor, spell_owner=actor)
    card=host.state.cards.get(str(template.get('card_object_id') or ''))
    if card is not None:
        from .continuous_effect_state import fixed_target_color_effects
        if fixed_target_color_effects(host.state, card):
            from .selection.exile_cast import mana_value_of_cost
            actor = template.get("controller")
            characteristics = host._spell_copy_characteristics(card)
            mana_value = mana_value_of_cost(str(characteristics.get("mana_cost") or ""),
                                          x_value=int(template.get("x_value") or 0))
            return TargetingSource.from_characteristics("spell", characteristics,
                controller=actor, spell_owner=actor,
                protection_source=ProtectionSource.from_characteristics({**characteristics, "mana_value": mana_value}))
        return spell_targeting_source(host,card)
    return TargetingSource('spell')


def stack_copy_targeting_source(host, item, *, controller):
    card = host.state.cards.get(item.card_object_id or "")
    if item.kind in {"spell", "spell_copy"} and card is not None and card.zone == "stack":
        return spell_copy_targeting_source(host, {"card_object_id": card.object_id,
            "controller": controller, "x_value": item.x_value})
    return stack_targeting_source(host, item)
