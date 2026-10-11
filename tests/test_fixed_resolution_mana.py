from __future__ import annotations

from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.rules.fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class FixedResolutionManaCompilerTests(unittest.TestCase):
    def test_fixed_symbol_bundles_are_closed_separate_outputs(self):
        registry=load_default_capability_registry()
        for text in ('Add {B}{B}{B}.','Add {W}{U}{B}{R}{G}.','Add {C}{C}.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry)
            self.assertEqual('exact',ir.status,text)
            node=ir.faces[0].nodes[0];self.assertEqual(('mana.production.fixed_resolution',),fixed_resolution_mana_capabilities(effects=node.effects,target_schema=None,mechanic_ids=node.mechanics))
        ir=compile_oracle_card(replace(query_record('Add {W}{U}{B}{R}{G}.'),type_line='Sorcery'),capability_registry=registry)
        self.assertEqual(5,len(ir.faces[0].nodes[0].effects))

    def test_dynamic_restricted_and_malformed_mana_remain_closed(self):
        registry=load_default_capability_registry()
        for text in ('Add {X}.','Add {R} for each unknown thing.','Add {R}{R}. Spend this mana only to cast imaginary spells.','Add one mana of any color.','Add three mana of any one color.','Add one mana of any color combination.','Add one mana of any color. Spend this mana only to cast creature spells.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry).status,text)
        shape={'op':'mana','player':'$controller','color':'B','amount':3,'source':'$source'}
        for change in ({'amount':True},{'amount':0},{'color':'secret'},{'extra':1}):
            self.assertFalse(fixed_resolution_mana_capabilities(effects=({**shape,**change},),target_schema=None,mechanic_ids=('fixed-resolution-mana',)))
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in value['capabilities'] if c['id']=='mana.production.fixed_resolution').update(status='blocked',blockers=['missing mana effect owner'])
        registry=CapabilityRegistry(value);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Add {B}{B}{B}.'),type_line='Instant'),capability_registry=registry).status)

    def test_restricted_bundle_allows_each_spell_type_but_no_ability_payment(self):
        from quorune.compiler.fixed_resolution_mana import fixed_resolution_mana_effect_template
        from quorune.mana_restrictions import mana_restriction_allows,spell_mana_spend_context,ability_mana_spend_context
        template=fixed_resolution_mana_effect_template('Add {R}{R}. Spend this mana only to cast artifact or creature spells.')
        self.assertIsNotNone(template);restriction=template[1][0]['spend_restriction']
        for type_line in ('Artifact','Creature','Artifact Creature'):
            self.assertTrue(mana_restriction_allows(restriction,spell_mana_spend_context(type_line)))
        self.assertFalse(mana_restriction_allows(restriction,spell_mana_spend_context('Instant')))
        self.assertFalse(mana_restriction_allows(restriction,ability_mana_spend_context('Artifact')))


class FixedResolutionManaRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'fixed-resolution-mana.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/fixed-resolution-mana-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Resolution mana',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def test_original_mana_replacement_carriers_fail_closed_at_whole_runtime_admission(self):
        from high_risk_interaction_support import _observed_piece_ids
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from quorune.semantics import SemanticRegistry
        pieces = {'capability.mana.production.fixed_resolution',
            'residual.replacement.replacement-applicability',
            'residual.replacement.self-replacement-and-prevention-ordering'}
        for oid in ('4b1da9aa-a30c-44b8-a10e-f9dc3fe70b6f','5b5bf1fa-6502-4790-b66b-f0f8504ebc7c',
                '907ae517-22d5-4ac7-bc3a-3f4d5eaeeb57','947b40a2-e272-4ef6-85fb-3d767a21bc7a',
                'bdf476e5-1d57-4b17-b45b-d52fd75aadeb'):
            with self.subTest(oracle_id=oid):
                record = self.db.by_oracle_id(oid)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                row = analyze_card_unlocks(ir, program=program, program_error=None,
                    capabilities=self.registry, profile='commander_review')
                self.assertLessEqual(pieces, _observed_piece_ids(row))
                self.assertEqual('residual', row['card_program_status'])
                self.assertIsNone(row['hard_construction_failure'])
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(binding['strict_capability_ready'])
                self.assertFalse(binding['compatible_ready'])
                self.assertIn('trust_basis:unresolved', binding['blockers'])

    def test_actual_dark_ritual_uses_stack_and_adds_exact_black_bundle_then_replays(self):
        s=self.session(328001);e=s.engine;source=self.add(e,'Dark Ritual',zone='hand')
        action=self.ready(s,source,{'B':1});self.checkpoint(s);before=authoritative_state_hash(e.state)
        result=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,e.state.players['A'].mana_pool['B']);self.assertEqual(1,len(e.state.stack));self.resolve(s)
        self.assertEqual(3,e.state.players['A'].mana_pool['B']);self.assertEqual('graveyard',e.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_channel_suns_adds_all_five_colors_and_replays(self):
        s=self.session(328002);e=s.engine;source=self.add(e,'Channel the Suns',zone='hand')
        action=self.ready(s,source,{'G':1,'C':3});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual({c:1 for c in 'WUBRG'},{c:e.state.players['A'].mana_pool[c] for c in 'WUBRG'});self.replay(s,load=True)

    def test_actual_culling_sacrifice_is_paid_but_countered_spell_produces_no_mana(self):
        s=self.session(328003);e=s.engine;source=self.add(e,'Culling the Weak',zone='hand');body=self.add(e,'Generic Bound Body');counter=self.add(e,'Counterspell',seat='B',zone='hand')
        action=self.ready(s,source,{'B':1});e.state.players['B'].mana_pool['U']=2;self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',e.state.cards[body.object_id].zone);target=e.state.stack[-1].ref
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:B':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==counter.ref)
        result=s.act('pilot:B',{'action_id':action['id'],'targets':[target],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(0,e.state.players['A'].mana_pool['B']);self.assertEqual('graveyard',e.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_geosurge_restricted_mana_rejects_instant_and_pays_creature(self):
        s=self.session(328004);e=s.engine;source=self.add(e,'Geosurge',zone='hand');shock=self.add(e,'Shock',zone='hand');body=self.add(e,'Blitz Automaton',zone='hand')
        action=self.ready(s,source,{'R':4});self.checkpoint(s)
        instant=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==shock.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(7,e.state.players['A'].mana_pool['R']);self.assertIn('spell.types_any=artifact,creature',str(e.state.players['A'].stats))
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a.get('card')==shock.ref for a in actions))
        before=authoritative_state_hash(e.state);result=s.act('pilot:A',{'action_id':instant['id'],'targets':['B'],'pay':'auto'})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        creature=next(a for a in actions if a.get('card')==body.ref and a.get('cast_method')!='prototype')
        result=s.act('pilot:A',{'action_id':creature['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(0,e.state.players['A'].mana_pool['R']);self.assertEqual('battlefield',e.state.cards[body.object_id].zone);self.replay(s,load=True)

    def test_actual_entry_mana_uses_stack_and_survives_source_departure(self):
        s=self.session(328005);e=s.engine;source=self.add(e,'Burning-Tree Emissary',zone='hand');bounce=self.add(e,'Unsummon',zone='hand')
        private=self.add(e,'Generic Bound Growth',seat='C',zone='hand',ref='private-c-growth')
        action=self.ready(s,source,{'R':2,'U':1});self.checkpoint(s)
        self.assertNotIn(private.ref,str(s.packet('pilot:B',full=True)))
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        for _ in range(40):
            if e.state.cards[source.object_id].zone=='battlefield':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('battlefield',e.state.cards[source.object_id].zone);self.assertEqual(1,len(e.state.stack));self.assertEqual(0,e.state.players['A'].mana_pool['R'])
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==bounce.ref)
        before=authoritative_state_hash(e.state);wrong=s.act('pilot:B',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual('hand',e.state.cards[source.object_id].zone);self.assertEqual((1,1),(e.state.players['A'].mana_pool['R'],e.state.players['A'].mana_pool['G']));self.replay(s,load=True)

    def test_actual_revolt_entry_checks_departure_history(self):
        for departed in (False,True):
            s=self.session(328006+departed);e=s.engine;herbalist=self.add(e,'Hidden Herbalists',zone='hand')
            source=self.add(e,'Culling the Weak',zone='hand') if departed else herbalist
            body=self.add(e,'Generic Bound Body') if departed else None
            action=self.ready(s,source,{'G':2,**({'B':1} if departed else {})});self.checkpoint(s)
            if departed:
                result=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
                action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==herbalist.ref)
            result=s.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'G':2}});self.assertTrue(result.ok,result.summary);self.resolve(s)
            self.assertEqual(2 if departed else 0,e.state.players['A'].mana_pool['G']);self.replay(s,load=True)

    def test_actual_loyalty_mana_uses_stack_and_counter_cost_then_replays(self):
        s=self.session(328008);e=s.engine;source=self.add(e,'Chandra, Novice Pyromancer',zone='hand')
        action=self.ready(s,source,{'R':1,'C':3});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];before_loyalty=current.counters['loyalty']
        ability=next(a for a in e._activated_abilities(current) if a.effect_text=='Add {R}{R}.')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+current.ref+':') and a['id'].endswith(':'+ability.ability_id))
        result=s.act('pilot:A',{'action_id':action['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(before_loyalty-1,e.state.cards[source.object_id].counters['loyalty']);self.assertEqual(0,e.state.players['A'].mana_pool['R']);self.assertEqual(1,len(e.state.stack))
        self.resolve(s);self.assertEqual(2,e.state.players['A'].mana_pool['R']);self.replay(s,load=True)

    def test_omitting_mana_result_mutant_is_killed_by_actual_ritual(self):
        from quorune.effect_runtime.state_and_permissions import HANDLERS
        with patch.dict(HANDLERS,{'mana':lambda *args,**kwargs:None}):
            with self.assertRaises(AssertionError):self.test_actual_dark_ritual_uses_stack_and_adds_exact_black_bundle_then_replays()
