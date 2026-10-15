from __future__ import annotations

"""CR701.22/701.25: resolve public counts before private library partition."""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as bound_witnesses
from test_bound_effect_programs import record


class VariableLibraryCountCompilerTests(unittest.TestCase):
    def test_library_action_observer_predicates_keep_controller_and_global_scope(self):
        from quorune.compiler.fixed_public_action_event_bindings import fixed_public_action_event_binding_spec
        for action,event in (('scry','player.scried'),('surveil','player.surveilled')):
            value=fixed_public_action_event_binding_spec('Whenever you '+action+', draw a card.')
            self.assertEqual(event,value.event)
            self.assertEqual({'field':'player','op':'eq','value':'$source.controller'},value.condition)
        global_spec=fixed_public_action_event_binding_spec('Whenever a player scries, draw a card.')
        self.assertIsNone(global_spec.condition)
        opponent=fixed_public_action_event_binding_spec('Whenever an opponent surveils, draw a card.')
        self.assertEqual('player.surveilled',opponent.event)
        self.assertIsNotNone(opponent.condition)
        self.assertIsNone(fixed_public_action_event_binding_spec('Whenever you scries, draw a card.'))
        with mock.patch('quorune.compiler.fixed_public_action_event_bindings._public_library_action_spec',return_value=None):
            self.assertIsNone(fixed_public_action_event_binding_spec('Whenever you scry, draw a card.'))
    def test_public_count_scry_and_surveil_share_fixed_partition_and_quantity_closure(self):
        cap=load_default_capability_registry()
        for action in ('Scry','Surveil'):
            ir=compile_oracle_card(record(action+' X, where X is the number of artifacts you control.','Sorcery'),
                capability_registry=cap,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0]
            self.assertEqual('public_query_effect_amount',node.effects[0]['count']['kind'])
            self.assertIn('quantity_expression.public_query_effect_amount',node.capability_dependencies)
            self.assertIn('library.scry.fixed_controller' if action=='Scry' else 'library.surveil.fixed_controller',node.capability_dependencies)
        for action in ('Scry', 'Surveil'):
            ir = compile_oracle_card(record(action + ' X, where X is the number of creatures with flying you control.', 'Sorcery'),
                capability_registry=cap, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            quantity = ir.faces[0].nodes[0].effects[0]['count']['quantity']
            self.assertEqual('live_battlefield_quantity', quantity['kind'])
            self.assertEqual('source_controller', quantity['controller_relation'])
            self.assertEqual(['flying'], quantity['query']['keywords_all'])
        for text in ('Scry X.','Surveil X, where X is the number of creatures with an unrepresented ability you control.',
                     'Scry X, where X is the number of cards in an opponent\'s hand.',
                     'Scry X, where X is the greatest mana value among permanents you control, then draw X cards.',
                     'Scry X, where X is the greatest mana value among permanents you control, then draw three cards if you control an artifact.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=cap).status,text)

    def test_library_amount_result_capabilities_cannot_be_omitted(self):
        from quorune.compiler.program_generation import _is_closed_effect_program,generated_programs
        cap=load_default_capability_registry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'amount-boundary.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json'],path)
            with CardDatabase(path) as db:
                for action in ('Scry','Surveil'):
                    row=record(action+' X, where X is the number of artifacts you control.','Sorcery')
                    program=generated_programs(db,row,capability_registry=cap,capability_profile='commander_review')[0]
                    self.assertTrue(_is_closed_effect_program(program))
                    owner='library.scry.fixed_controller' if action=='Scry' else 'library.surveil.fixed_controller'
                    for capability in ('quantity_expression.public_query_effect_amount',owner):
                        dependencies=[item for item in program.capability_dependencies if item!=capability]
                        closure=cap.closure(dependencies,profile='commander_review').to_dict()
                        self.assertFalse(_is_closed_effect_program(replace(program,
                            capability_dependencies=dependencies,capability_closure=closure)))


class VariableLibraryCountActionTests(unittest.TestCase):
    add=bound_witnesses.BoundEffectProgramRuntimeTests.add
    ready=bound_witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=bound_witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=bound_witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=bound_witnesses.BoundEffectProgramRuntimeTests.replay
    session=bound_witnesses.BoundEffectProgramRuntimeTests.session

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'library-count.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/variable-library-count-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Generic variable library deck',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_real_variable_scry_and_surveil_private_partition_pending_reload_and_replay(self):
        for index,action in enumerate(('Scry','Surveil')):
            with self.subTest(action=action):
                session=self.session(701220+index);engine=session.engine
                spell=self.add(engine,'Generic Variable '+action,zone='hand',ref='variable-library-spell')
                self.add(engine,'Generic Bound Mentor',ref='quantity-artifact-one')
                self.add(engine,'Generic Bound Mentor',ref='quantity-artifact-two')
                self.add(engine,'Generic Bound Mentor',seat='B',ref='other-controller-artifact')
                offered=self.ready(session,spell,{'U':1});self.checkpoint(session)
                self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                ctx=session.packet('pilot:A',full=True)['decision']['ctx'];refs=[row['id'] for row in ctx['objects']]
                self.assertEqual(2,len(refs))
                for seat in 'BCD':
                    packet=json.dumps(session.packet('pilot:'+seat,full=True))
                    for ref in refs:self.assertNotIn(ref,packet)
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:D',{'action_id':'choose','cards':{'top':refs,'bottom':[]}}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                resumed=self.replay(session,load=True)
                response={'top':[refs[1]],'bottom' if action=='Scry' else 'graveyard':[refs[0]]}
                accepted=resumed.act('pilot:A',{'action_id':'choose','cards':response})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(resumed)
                self.assertEqual(1,sum(event.code==('library.scry' if action=='Scry' else 'library.surveil') for event in resumed.state.events))
                if action=='Surveil':
                    self.assertTrue(any(resumed.state.cards[key].ref==refs[0] for key in resumed.state.players['A'].zones['graveyard']))
                self.replay(resumed)

    def test_zero_public_count_creates_no_choice_or_scry_surveil_event(self):
        for index,action in enumerate(('Scry','Surveil')):
            session=self.session(701225+index);engine=session.engine
            spell=self.add(engine,'Generic Variable '+action,zone='hand',ref='zero-library-spell')
            offered=self.ready(session,spell,{'U':1});self.checkpoint(session)
            self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
            self.assertIsNone(self.resolve(session))
            self.assertFalse(any(event.code in {'library.scry','library.surveil','player.surveilled'} for event in session.state.events))
            self.replay(session)

    def test_positive_scry_on_empty_library_completes_an_event_without_private_choice(self):
        # CR701.22d: positive Scry completes even if looking is impossible.
        session=self.session(701228);engine=session.engine
        spell=self.add(engine,'Generic Variable Scry',zone='hand',ref='empty-positive-scry')
        self.add(engine,'Generic Bound Mentor',ref='empty-scry-quantity')
        for key in list(engine.state.players['A'].zones['library']):
            engine.move_card(key,'outside',reason='empty-library contract fixture',log=False)
        offered=self.ready(session,spell,{'U':1});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1,sum(event.code=='library.scry' for event in session.state.events))
        self.replay(session)

    def test_actual_library_actions_trigger_only_current_controller_observers_after_completion(self):
        for index,(action,count,empty) in enumerate((('Scry',2,False),('Surveil',2,False),('Scry',0,False),
                                                   ('Surveil',0,False),('Scry',1,True),('Surveil',1,True))):
            with self.subTest(action=action,count=count,empty=empty):
                session=self.session(701240+index);engine=session.engine
                source=self.add(engine,'Generic '+action+' Observer',ref='observer-own')
                other=self.add(engine,'Generic '+action+' Observer',seat='B',ref='observer-other')
                for number in range(count):self.add(engine,'Generic Bound Mentor',ref='observer-quantity-'+str(number))
                if empty:
                    for key in list(engine.state.players['A'].zones['library']):engine.move_card(key,'outside',log=False,reason='empty library fixture')
                spell=self.add(engine,'Generic Variable '+action,zone='hand',ref='observer-spell')
                offered=self.ready(session,spell,{'U':1});self.checkpoint(session)
                self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
                decision=self.resolve(session)
                if count and not empty:
                    self.assertEqual('semantic.choice',decision.kind)
                    self.assertEqual({},session.state.cards[source.object_id].counters)
                    refs=[r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['objects']]
                    resumed=self.replay(session,load=True)
                    accepted=resumed.act('pilot:A',{'action_id':'choose','cards':{'top':refs,'bottom' if action=='Scry' else 'graveyard':[]}})
                    self.assertTrue(accepted.ok,accepted.summary);self.resolve(resumed);session=resumed
                self.assertEqual(int(count>0),session.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual({},session.state.cards[other.object_id].counters)
                self.replay(session)

    def test_original_cerebral_download_surveys_current_artifact_count_then_draws_and_replays(self):
        session=self.session(701230);engine=session.engine
        spell=self.add(engine,'Cerebral Download',zone='hand',ref='original-download')
        self.add(engine,'Generic Bound Mentor',ref='download-artifact-one')
        self.add(engine,'Generic Bound Mentor',ref='download-artifact-two')
        offered=self.ready(session,spell,{'U':1,'C':4});self.checkpoint(session)
        before_hand=len(session.state.players['A'].zones['hand'])
        self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        refs=[r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['objects']]
        self.assertEqual(2,len(refs))
        resumed=self.replay(session,load=True)
        accepted=resumed.act('pilot:A',{'action_id':'choose','cards':{'top':refs,'graveyard':[]}})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(resumed)
        self.assertEqual(before_hand+2,len(resumed.state.players['A'].zones['hand']))
        self.assertEqual(1,sum(event.code=='library.surveil' for event in resumed.state.events))
        self.replay(resumed)

    def test_original_ugins_insight_uses_greatest_mana_value_then_draws_and_replays(self):
        session = self.session(701231)
        engine = session.engine
        spell = self.add(engine, "Ugin's Insight", zone="hand", ref="original-insight")
        for number in range(3):
            self.add(engine, 'Generic Bound Mentor', ref='insight-artifact-' + str(number))
        offered = self.ready(session, spell, {'U': 2, 'C': 3})
        self.checkpoint(session)
        before_hand = len(session.state.players['A'].zones['hand'])
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        refs = [row['id'] for row in session.packet('pilot:A', full=True)['decision']['ctx']['objects']]
        # The three artifact creatures have mana value 2; their count is not X.
        self.assertEqual(2, len(refs))
        resumed = self.replay(session, load=True)
        accepted = resumed.act('pilot:A', {'action_id': 'choose', 'cards': {'top': refs, 'bottom': []}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(resumed)
        self.assertEqual(before_hand + 2, len(resumed.state.players['A'].zones['hand']))
        self.assertEqual(1, sum(event.code == 'library.scry' for event in resumed.state.events))
        self.replay(resumed)

    def test_original_library_observers_complete_counter_and_temporary_keyword_results(self):
        for index,(action,name) in enumerate((('Scry','Flamespeaker Adept'),('Surveil','Dimir Spybug'))):
            session=self.session(701250+index);engine=session.engine
            source=self.add(engine,name,ref='original-library-observer')
            self.add(engine,'Generic Bound Mentor',ref='original-observer-quantity')
            spell=self.add(engine,'Generic Variable '+action,zone='hand',ref='original-observer-spell')
            offered=self.ready(session,spell,{'U':1});self.checkpoint(session)
            self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
            self.assertEqual('semantic.choice',self.resolve(session).kind)
            refs=[r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['objects']]
            self.assertTrue(session.act('pilot:A',{'action_id':'choose','cards':{'top':refs,'bottom' if action=='Scry' else 'graveyard':[]}}).ok)
            self.resolve(session)
            if action=='Surveil':self.assertEqual(1,session.state.cards[source.object_id].counters['+1/+1'])
            else:
                self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'))
                self.assertIn('First Strike',session.engine._effective_card_data(session.state.cards[source.object_id])['keywords'])
            self.replay(session,load=True)
