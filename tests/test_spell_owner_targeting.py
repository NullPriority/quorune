"""CR112.2/702.140a: spell ownership is independent of its controller."""
from types import SimpleNamespace
import json
from common import ROOT
import unittest

from quorune.card_programs.runtime_requirements import _event_query_runtime_requirements
from quorune.compiler.dependency_gate import explicit_capability_gate
from quorune.model import GameConfig,GameState
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.spell_owner_targets import (
    TARGET_SPELL_OWNER_CAPABILITY,spell_owner_target_dependencies,target_has_spell_owner,
)
from quorune.selection.targeting import TargetSelectionOwnerMixin
from quorune.semantics import SemanticProgram
from quorune.target_characteristics import TargetCharacteristicSnapshot
from quorune.target_source import (
    TargetingSource,TargetingSourceBinding,spell_targeting_source,stack_targeting_source,
)
from quorune.targets import target_plan

SCHEMA={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],
    'owner_relation':'spell_owner','count':1}


class SpellOwnerTargetingTests(unittest.TestCase):
    def test_spell_facts_keep_foreign_owner_and_copy_owner_after_control_changes(self):
        original=SimpleNamespace(owner='B',controller='A',zone='exile')
        copy=SimpleNamespace(owner='C',controller='D',zone='stack')
        host=SimpleNamespace(state=SimpleNamespace(cards={'copy':copy}),
            _effective_card_data=lambda _card:{'type_line':'Creature — Beast','colors':['G']})
        facts=spell_targeting_source(host,original)
        self.assertEqual(('A','B'),(facts.source_controller,facts.spell_owner))
        item=SimpleNamespace(kind='spell_copy',card_object_id='copy',source_object_id=None,controller='D',context={})
        facts=stack_targeting_source(host,item)
        self.assertEqual(('D','C'),(facts.source_controller,facts.spell_owner))
        copy.controller='A';facts=stack_targeting_source(host,item)
        self.assertEqual(('A','C'),(facts.source_controller,facts.spell_owner))
        host.state.cards.clear();item.context={'copy_permanent_characteristics':{'type_line':'Creature — Beast','colors':['G']}}
        self.assertIsNone(stack_targeting_source(host,item).spell_owner,'Unavailable ownership never becomes the current controller')

    def test_shared_matcher_uses_spell_owner_across_target_controllers(self):
        state=GameState(game_id='spell-owner-query',config=GameConfig(),players={},cards={},deck_names={},
            commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)
        host=SimpleNamespace(state=state,
            _relation_matches=lambda seat,actor,relation:relation=='any' or relation=='you' and seat==actor or relation=='opponent' and seat!=actor)
        group=target_plan(SCHEMA).groups[0]
        source=TargetingSource('spell',source_controller='A',spell_owner='B')
        values=TargetCharacteristicSnapshot(types=frozenset({'creature'})).row_values()
        for owner in 'ABC':
            for controller in 'ABC':
                row={'ref':owner+controller,'zone':'battlefield','category':'permanent','owner':owner,
                    'controller':controller,'card':None,**values}
                with self.subTest(owner=owner,controller=controller):
                    self.assertEqual(owner=='B',TargetSelectionOwnerMixin._target_row_matches(
                        host,'A',group,row,source_ref=None,acting_source=source,as_target=False))
        for missing in (None,TargetingSource('spell',source_controller='B'),TargetingSource('ability',source_controller='B')):
            self.assertFalse(target_has_spell_owner(missing,'B','permanent'))
        for category in ('player','ability'):
            self.assertFalse(target_has_spell_owner(source,'B',category))

    def test_new_relation_is_owner_only_and_requires_its_independent_capability(self):
        self.assertEqual((TARGET_SPELL_OWNER_CAPABILITY,),spell_owner_target_dependencies(SCHEMA))
        program=SemanticProgram(key='fixture:owner-dependency',label='Owner dependency',event='spell.resolve',
            effects=[],target_schema=SCHEMA,capability_dependencies=[])
        required,blockers=_event_query_runtime_requirements(program)
        self.assertIn(TARGET_SPELL_OWNER_CAPABILITY,required);self.assertFalse(blockers)
        old={**SCHEMA,'owner_relation':'you'}
        self.assertEqual((),spell_owner_target_dependencies(old))
        for key in ('controller_relation','player_relation'):
            with self.subTest(key=key),self.assertRaises(ValueError):
                target_plan({**old,key:'spell_owner'})
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(row for row in raw['capabilities'] if row['id']==TARGET_SPELL_OWNER_CAPABILITY).update(
            status='blocked',blockers=['Spell ownership authority withdrawn'])
        gate=explicit_capability_gate(TARGET_SPELL_OWNER_CAPABILITY,
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertTrue(any(TARGET_SPELL_OWNER_CAPABILITY in blocker for blocker in gate.blockers))

    def test_owner_facts_are_closed_and_existing_source_binding_wire_is_unchanged(self):
        for owner in (True,False,0,'',' B','B ',[]):
            with self.subTest(owner=owner),self.assertRaises(ValueError):TargetingSource('spell',spell_owner=owner)
        with self.assertRaises(ValueError):TargetingSource('ability',spell_owner='A')
        binding=TargetingSourceBinding('object','REF','object@0','battlefield','A','Creature — Beast',('G',))
        expected={'schema_version':1,'object_id':'object','object_ref':'REF','logical_object_id':'object@0',
            'zone':'battlefield','controller':'A','type_line':'Creature — Beast','colors':['G']}
        self.assertEqual(expected,binding.to_dict());self.assertEqual(binding,TargetingSourceBinding.from_dict(expected))
        self.assertIsNone(binding.facts().spell_owner)
        with self.assertRaises(ValueError):TargetingSourceBinding.from_dict({**expected,'spell_owner':'A'})


if __name__=='__main__':unittest.main()
