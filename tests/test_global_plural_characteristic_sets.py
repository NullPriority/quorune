from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.continuous_effects import CharacteristicState, evaluate_continuous_effects
from quorune.semantic_runtime import ContinuousEffectSourceContext, default_continuous_effect_component_registry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_fixed_evasion_grants as evasion


class GlobalPluralCharacteristicCompilerTests(unittest.TestCase):
    def test_bare_subtype_combined_grant_preserves_undefined_noncreature_stats(self):
        ir = compile_oracle_card(query_record('Other Merfolk get +1/+1 and have islandwalk.'),
            capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status)
        effects = default_continuous_effect_component_registry().lower(ir.faces[0].nodes[0].handlers[0],
            ContinuousEffectSourceContext(source_object_id='source', source_ref='source-ref',
                source_controller='A', source_timestamp=1, component_id='source:a1'))
        state = CharacteristicState(name='Kindred Merfolk', controller='A',
            card_types={'Kindred', 'Enchantment'}, subtypes={'Merfolk'}, power=None, toughness=None)
        result = evaluate_continuous_effects(state, effects, context={'object_id': 'recipient',
            'logical_object_id': 'recipient@0', 'ref': 'recipient-ref', 'zone': 'battlefield',
            'owner': 'A', 'controller': 'A'})
        self.assertIsNone(result.characteristics['power'])
        self.assertIsNone(result.characteristics['toughness'])
        self.assertIn('Islandwalk', result.characteristics['abilities'])

    def test_bare_subtype_union_modifier_keeps_kindred_and_rejects_unknown_subtypes(self):
        from copy import deepcopy
        from quorune.semantic_runtime import SemanticNodeError
        ir = compile_oracle_card(query_record('Faeries or Merfolk you control get +1/+1 and have shroud.'),
            capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status)
        handler = ir.faces[0].nodes[0].handlers[0]
        self.assertEqual([], handler['condition']['predicate']['types_all'])
        self.assertEqual({'faerie', 'merfolk'}, set(handler['condition']['predicate']['subtypes_any']))
        malformed = deepcopy(handler)
        malformed['condition']['predicate']['subtypes_any'] = ['not-a-real-subtype']
        with self.assertRaises(SemanticNodeError):
            default_continuous_effect_component_registry().validate(malformed)

    def test_global_other_plural_uses_canonical_subtype_without_controller_filter(self):
        for subject,subtype in (('Other Merfolk','merfolk'),('Other Goblins','goblin'),('Elves','elf')):
            text=subject+' get +1/+1 and have forestwalk.'
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];condition=node.handlers[0]['condition']
            self.assertEqual('any',condition['target_controller']);self.assertEqual([subtype],condition['predicate']['subtypes_all'])
            self.assertEqual(subject.startswith('Other'),condition['exclude_source'])
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_unknown_nouns_and_class_level_context_remain_residual(self):
        from dataclasses import replace
        for text in ('Other NotARealSubtype get +1/+1 and have flying.','Other elves and artifacts get +1/+1 and have flying.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)
        card=replace(query_record('Other Elves get +1/+1 and have forestwalk.'),type_line='Enchantment — Class')
        self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=load_default_capability_registry()).status)


class GlobalPluralCharacteristicRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'global-plurals.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/global-plural-characteristic-sets.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Global plurals',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    blocks=evasion.FixedEvasionGrantRuntimeTests.blocks

    def test_actual_atlantis_cast_grants_foreign_merfolk_and_source_exclusion_with_replay(self):
        s=self.session(313001);e=s.engine
        own=self.add(e,'Merfolk of the Pearl Trident',ref='own-merfolk');foreign=self.add(e,'Merfolk of the Pearl Trident',seat='B',ref='foreign-merfolk')
        other=self.add(e,'Generic Bound Body',seat='C');source=self.add(e,'Lord of Atlantis',zone='hand')
        action=self.ready(s,source,{'U':2});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        for body in (own,foreign):
            self.assertEqual(2,e._numeric_stat(body.object_id,'power'));self.assertIn('islandwalk',e._combat_keywords(body))
        self.assertEqual(2,e._numeric_stat(source.object_id,'power'));self.assertNotIn('islandwalk',e._combat_keywords(source))
        self.assertEqual(2,e._numeric_stat(other.object_id,'power'));self.replay(s,load=True)

    def test_actual_atlantis_layer_grant_changes_block_offers_and_acceptance(self):
        s=self.session(313002);e=s.engine
        attacker=self.add(e,'Merfolk of the Pearl Trident',seat='B');blocker=self.add(e,'Generic Bound Body',seat='C')
        island=self.add(e,'Island',seat='C');source=self.add(e,'Lord of Atlantis',zone='hand')
        action=self.ready(s,source,{'U':2});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertNotIn(attacker.ref,self.blocks(s,attacker,blocker));self.checkpoint(s)
        before=s.state.to_dict();rejected=s.act('pilot:C',{'a':'block','blk':{blocker.ref:attacker.ref}})
        self.assertFalse(rejected.ok);self.assertEqual(before,s.state.to_dict())
        e.move_card(island.object_id,'graveyard',log=False)
        self.assertIn(attacker.ref,self.blocks(s,attacker,blocker));self.checkpoint(s)
        accepted=s.act('pilot:C',{'a':'block','blk':{blocker.ref:attacker.ref}});self.assertTrue(accepted.ok,accepted.summary);self.replay(s)

    def test_global_plural_selector_omission_mutant_is_killed_by_original_atlantis(self):
        with patch('quorune.compiler.public_state_queries._GLOBAL_PLURAL_SUBJECT') as matcher:
            matcher.fullmatch.return_value=None
            ir=compile_oracle_card(self.db.lookup('Lord of Atlantis'),capability_registry=self.registry)
            with self.assertRaises(AssertionError):self.assertEqual('exact',ir.status)

    def test_actual_atlantis_bounce_removes_current_grants_from_all_controllers(self):
        s=self.session(313003);e=s.engine
        bodies=[self.add(e,'Merfolk of the Pearl Trident',seat=seat,ref='depart-merfolk-'+seat) for seat in 'AB']
        source=self.add(e,'Lord of Atlantis',zone='hand');bounce=self.add(e,'Unsummon',zone='hand')
        action=self.ready(s,source,{'U':3});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==bounce.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        for body in bodies:
            self.assertEqual(1,e._numeric_stat(body.object_id,'power'));self.assertNotIn('islandwalk',e._combat_keywords(body))
        self.assertEqual('hand',s.state.cards[source.object_id].zone);self.replay(s)


if __name__=='__main__':unittest.main()
