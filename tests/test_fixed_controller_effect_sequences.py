from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler.affected_player_discard_templates import (
    fixed_controller_discard_effect_template,
)
from quorune.compiler.fixed_controller_effect_sequences import (
    fixed_controller_effect_sequence_template,
)
from quorune.compiler.life_templates import fixed_life_effect_template
from quorune.compiler.program_generation import register_generated_programs
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.affected_player_discard_capability_shapes import (
    fixed_affected_player_discard_node_capabilities,
)
from quorune.rules.fixed_controller_effect_shapes import (
    fixed_controller_effect_sequence_node_capabilities,
    fixed_life_node_capabilities,
)
from quorune.semantics import SemanticProgram


SEQUENCE_CAPABILITY = "resolution.effect_sequence.fixed_controller"


class FixedConniveRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory()
        root=Path(__file__).parents[1]
        path=Path(cls.temporary.name)/'connive.sqlite3'
        build_fixture_database([root/'tests/fixtures/bound-effect-program-cards.json',
                                root/'tests/fixtures/fixed-connive-cards.json'],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Generic Connive Deck',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        from quorune.semantics import SemanticRegistry
        session=BoundEffectProgramRuntimeTests.session(self,seed)
        engine=session.engine
        engine.semantics=SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(self.db,engine.semantics,tuple(self.db.iter_cards()),trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        return session

    def add(self,*args,**kwargs):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        return BoundEffectProgramRuntimeTests.add(self,*args,**kwargs)

    def ready(self,*args,**kwargs):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        session,source=args[:2]
        program=compile_best_available_card_program(self.db,self.db.lookup(source.printed_name),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        return BoundEffectProgramRuntimeTests.ready(self,*args,**kwargs)

    def checkpoint(self,session):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        BoundEffectProgramRuntimeTests.checkpoint(self,session)

    def resolve(self,session):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        return BoundEffectProgramRuntimeTests.resolve(self,session)

    def replay(self,session,**kwargs):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        return BoundEffectProgramRuntimeTests.replay(self,session,**kwargs)

    def choose_replacements(self,session,*,load=False):
        for _ in range(8):
            decision=session.state.pending_decision
            if decision is None or decision.kind!='replacement.order':return session
            if load:session=self.replay(session,load=True);decision=session.state.pending_decision
            actor=decision.actors[0]
            selected=session.packet('pilot:'+actor,full=True)['decision']['ctx']['options'][0]['id']
            result=session.act('pilot:'+actor,{'action_id':'choose','choices':{'replacement':selected}})
            self.assertTrue(result.ok,result.summary)
        self.fail('Connive replacement choice failed to finish')

    def test_original_connive_redirected_discard_and_counter_replacements_resume_once(self):
        session=self.session(386004);engine=session.engine
        source=self.add(engine,'Hypnotic Grifter')
        selected=self.add(engine,'Disperse',zone='hand',ref='connive-redirected-discard')
        selected_identity=selected.logical_object_id
        for i in range(2):
            self.add(engine,'Dauthi Voidwalker',seat='B',ref=f'connive-voidwalker-{i}')
            self.add(engine,'Hardened Scales',ref=f'connive-scales-{i}')
        action=self.ready(session,source,{'C':3})
        hand_before=len(engine.state.players['A'].zones['hand'])
        draw_before=sum(event.code=='card.draw.private' for event in engine.state.events)
        self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        self.assertEqual(hand_before+1,len(engine.state.players['A'].zones['hand']))
        result=session.act('pilot:A',{'action_id':'choose','cards':[selected.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual('semantic_intent_completion',session.state.pending_decision.continuation['replacement_resume_kind'])
        self.assertEqual('hand',engine.state.cards[selected.object_id].zone)
        self.assertEqual(0,engine.state.cards[source.object_id].counters.get('+1/+1',0))
        session=self.choose_replacements(session,load=True);engine=session.engine
        self.assertEqual('exile',engine.state.cards[selected.object_id].zone)
        self.assertEqual(1,engine.state.cards[selected.object_id].counters.get('void',0))
        self.assertEqual(3,engine.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(hand_before,len(engine.state.players['A'].zones['hand']))
        self.assertEqual(draw_before+1,sum(event.code=='card.draw.private' for event in engine.state.events))
        self.assertEqual(1,sum(event.kind=='card_discarded' and event.object_incarnation==selected_identity
                               for event in engine._current_turn_history('card_discarded')))
        self.assertEqual(1,sum(event.code=='connive.complete' for event in engine.state.events))
        self.replay(session)

    def test_original_connive_uses_private_discard_and_replays_from_cast(self):
        for seed,name,mana,land in (
            (386001,"Raffine's Informant",{'W':1,'C':1},False),
            (386002,'Echo Inspector',{'U':1,'C':3},True),
        ):
            with self.subTest(name=name):
                session=self.session(seed);engine=session.engine
                source=self.add(engine,name,zone='hand')
                selected=self.add(engine,'Generic Bound Plains' if land else 'Disperse',zone='hand',ref='private-selected')
                selected_identity=selected.logical_object_id
                action=self.ready(session,source,mana)
                self.checkpoint(session)
                before_hand=len(engine.state.players['A'].zones['hand'])
                result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(result.ok,result.summary)
                decision=self.resolve(session)
                self.assertEqual('semantic.choice',decision.kind)
                self.assertEqual(['A'],list(decision.actors))
                self.assertIn(selected.ref,session.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']['legal_refs'])
                self.assertEqual(before_hand,len(engine.state.players['A'].zones['hand']))
                self.assertFalse(any(event.code=='connive.complete' for event in engine.state.events))
                for seat in 'BCD':
                    packet=json.dumps(session.packet('pilot:'+seat,full=True))
                    self.assertNotIn(selected.ref,packet)
                before=authoritative_state_hash(session.state)
                result=session.act('pilot:B',{'action_id':'choose','cards':[selected.ref]})
                self.assertFalse(result.ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                result=session.act('pilot:A',{'action_id':'choose','cards':[]})
                self.assertFalse(result.ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                resumed=self.replay(session,load=True)
                result=resumed.act('pilot:A',{'action_id':'choose','cards':[selected.ref]})
                self.assertTrue(result.ok,result.summary)
                engine=resumed.engine
                current=engine.state.cards[source.object_id]
                self.assertEqual('graveyard',engine.state.cards[selected.object_id].zone)
                self.assertEqual(0 if land else 1,current.counters.get('+1/+1',0))
                complete=[event for event in engine.state.events if event.code=='connive.complete']
                self.assertEqual(1,len(complete))
                self.assertEqual(current.logical_object_id,complete[0].details['conniver_logical_object_id'])
                self.assertEqual(0 if land else 1,complete[0].details['nonlands_discarded'])
                self.assertTrue(any(event.kind=='card_discarded' and event.object_incarnation==selected_identity
                                    for event in engine._current_turn_history('card_discarded')))
                self.assertEqual(before_hand-1,len(engine.state.players['A'].zones['hand']))
                self.replay(resumed)

    def test_original_connive_departure_uses_last_controller_and_never_counters_returned_object(self):
        session=self.session(386003);engine=session.engine
        conniver=self.add(engine,'Hypnotic Grifter',ref='grifter-source')
        selected=self.add(engine,'Disperse',seat='B',zone='hand',ref='b-private-discard')
        # The activation was controlled by A; the responding player can change
        # its source's control before resolution without changing stack control.
        action=self.ready(session,conniver,{'C':3,'U':1})
        self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        result=session.act('pilot:A',{'action_id':'pass'})
        self.assertTrue(result.ok,result.summary)
        # Current controller at departure is established in a checkpoint that
        # retains the real activation and its immutable A controller.
        engine.change_control(conniver.object_id,'B',reason='fixture later control')
        engine.state.players['B'].mana_pool.update({'U':1,'C':1})
        bounce_b=self.add(engine,'Disperse',seat='B',zone='hand',ref='b-bounce')
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine._grant_priority('B');engine.pump()
        self.checkpoint(session)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce_b.ref)
        identity=conniver.logical_object_id
        result=session.act('pilot:B',{'action_id':cast['id'],'targets':[conniver.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['B'],list(decision.actors))
        self.assertEqual('hand',engine.state.cards[conniver.object_id].zone)
        self.assertEqual(0,engine.state.cards[conniver.object_id].counters.get('+1/+1',0))
        departed_context=deepcopy(engine.state.pending_decision.continuation['effect'])
        self.assertEqual(identity,departed_context['_conniver']['logical_object_id'])
        engine.move_card(conniver.object_id,'battlefield',controller='A',reason='fixture new incarnation')
        returned=engine.state.cards[conniver.object_id]
        self.assertNotEqual(identity,returned.logical_object_id)
        # The new incarnation is independently staged; replay begins before
        # the real private command against the retained departed instruction.
        self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':'choose','cards':[selected.ref]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        resumed=self.replay(session,load=True)
        result=resumed.act('pilot:B',{'action_id':'choose','cards':[selected.ref]})
        self.assertTrue(result.ok,result.summary)
        complete=next(event for event in resumed.state.events if event.code=='connive.complete')
        self.assertEqual(('B',identity),(complete.details['player'],complete.details['conniver_logical_object_id']))
        self.assertEqual(0,resumed.state.cards[conniver.object_id].counters.get('+1/+1',0))
        self.replay(resumed)

    def test_target_connive_uses_target_controller_and_fixed_n_keeps_land_distinction(self):
        session=self.session(386005);engine=session.engine
        source=self.add(engine,'Generic Target Connive',zone='hand')
        target=self.add(engine,'Generic Bound Body',seat='B')
        selected=self.add(engine,'Disperse',seat='B',zone='hand',ref='target-private-nonland')
        action=self.ready(session,source,{'U':1});self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);decision=self.resolve(session)
        self.assertEqual(['B'],list(decision.actors))
        self.assertIsNone(session.packet('pilot:A',full=True).get('decision'))
        result=session.act('pilot:B',{'action_id':'choose','cards':[selected.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,engine.state.cards[target.object_id].counters.get('+1/+1',0))
        self.replay(session)

        session=self.session(386006);engine=session.engine
        source=self.add(engine,'Generic Connive Three')
        first=self.add(engine,'Disperse',zone='hand',ref='first-nonland')
        second=self.add(engine,'Disperse',zone='hand',ref='second-nonland')
        land=self.add(engine,'Generic Bound Plains',zone='hand',ref='selected-land')
        action=self.ready(session,source,{'C':1});before_hand=len(engine.state.players['A'].zones['hand'])
        self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        schema=session.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']
        self.assertEqual((3,3),(schema['minimum'],schema['maximum']))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':'choose','cards':[first.ref,first.ref,land.ref]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','cards':[first.ref,second.ref,land.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,engine.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(before_hand,len(engine.state.players['A'].zones['hand']))
        self.replay(session)

    def test_connive_zero_has_no_draw_discard_counter_or_completion(self):
        session=self.session(386007);engine=session.engine
        source=self.add(engine,'Generic Connive Zero')
        action=self.ready(session,source,{'C':1})
        before_hand=tuple(engine.state.players['A'].zones['hand'])
        before_library=tuple(engine.state.players['A'].zones['library'])
        self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        self.assertEqual(before_hand,tuple(engine.state.players['A'].zones['hand']))
        self.assertEqual(before_library,tuple(engine.state.players['A'].zones['library']))
        self.assertEqual({},engine.state.cards[source.object_id].counters)
        self.assertFalse(any(event.code=='connive.complete' for event in engine.state.events))
        self.replay(session)

    def test_connive_counter_omission_mutant_is_killed_by_original_card_action(self):
        from quorune.semantic_choices.connive import _counter_preparation
        def omit(*args,**kwargs):
            return replace(_counter_preparation(*args,**kwargs),preparation_intents=())
        with patch('quorune.semantic_choices.connive._counter_preparation',side_effect=omit):
            session=self.session(386009);engine=session.engine
            source=self.add(engine,"Raffine's Informant",zone='hand')
            selected=self.add(engine,'Disperse',zone='hand',ref='mutation-nonland')
            action=self.ready(session,source,{'W':1,'C':1})
            result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.resolve(session)
            result=session.act('pilot:A',{'action_id':'choose','cards':[selected.ref]})
            self.assertTrue(result.ok,result.summary)
            with self.assertRaisesRegex(AssertionError,'Connive nonland counter omitted'):
                self.assertEqual(1,engine.state.cards[source.object_id].counters.get('+1/+1',0),
                                 'Connive nonland counter omitted')

    def test_original_connive_draw_replacement_preserves_mandatory_discard_and_replay(self):
        session=self.session(386008);engine=session.engine
        source=self.add(engine,'Hypnotic Grifter')
        dredge=self.add(engine,'Golgari Brownscale',zone='graveyard',ref='connive-dredger')
        action=self.ready(session,source,{'C':3})
        hand_before=len(engine.state.players['A'].zones['hand'])
        library_before=len(engine.state.players['A'].zones['library'])
        draws_before=sum(event.code=='card.draw.private' for event in engine.state.events)
        self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);decision=self.resolve(session)
        self.assertEqual('draw.replacement',decision.kind)
        self.assertEqual(library_before,len(engine.state.players['A'].zones['library']))
        resumed=self.replay(session,load=True)
        packet=resumed.packet('pilot:A',full=True)['decision']
        option=next(row for row in packet['ctx']['options'] if 'Dredge' in row['label'])
        result=resumed.act('pilot:A',{'action_id':'choose','choice':option['id']})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',resumed.state.pending_decision.kind)
        self.assertIn(dredge.ref,resumed.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']['legal_refs'])
        result=resumed.act('pilot:A',{'action_id':'choose','cards':[dredge.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,resumed.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(library_before-2,len(resumed.state.players['A'].zones['library']))
        self.assertEqual(hand_before,len(resumed.state.players['A'].zones['hand']))
        self.assertEqual(draws_before,sum(event.code=='card.draw.private' for event in resumed.state.events))
        self.assertEqual(1,sum(event.code=='connive.complete' for event in resumed.state.events))
        self.replay(resumed)

    def test_connive_departure_trigger_captures_original_controller_before_new_stack_item(self):
        session=self.session(386010);engine=session.engine
        source=self.add(engine,'Generic Departing Conniver')
        selected=self.add(engine,'Disperse',zone='hand',ref='departure-trigger-discard')
        original=source.logical_object_id
        action=self.ready(session,source,{})
        before_life=engine.state.players['A'].life
        self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['A'],list(decision.actors))
        result=session.act('pilot:A',{'action_id':'choose','cards':[selected.ref]})
        self.assertTrue(result.ok,result.summary)
        self.resolve(session)
        self.assertEqual(before_life+1,engine.state.players['A'].life)
        self.assertEqual({},engine.state.cards[source.object_id].counters)
        complete=next(event for event in engine.state.events if event.code=='connive.complete')
        self.assertEqual(original,complete.details['conniver_logical_object_id'])
        self.assertEqual(1,complete.details['nonlands_discarded'])
        self.replay(session)


class FixedControllerEffectSequenceCompilerTests(unittest.TestCase):
    def test_connive_empty_hand_stale_choice_and_hidden_discard_fail_closed(self):
        from types import SimpleNamespace
        from quorune.connive import FixedConniveSpec,ConnivingObject,connive_effect,connive_discard_characteristic_ids
        from quorune.object_query import ObjectQueryResult
        from quorune.semantic_choices.connive import FixedConniveChoiceHandler
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery,SemanticChoiceContext
        from quorune.semantic_choices.model import SemanticChoiceError,SemanticChoiceContinuation,SemanticChoiceFrame
        retained=ConnivingObject('source','source@1','source','A')
        effect={**connive_effect(FixedConniveSpec(1),card='source',player='A'),
                '_connive_stage':'discard','_conniver':retained.to_dict()}
        query=SnapshotSemanticChoiceQuery(tuple('ABCD'),tuple('ABCD'))
        context=SemanticChoiceContext('A','stack','B','Connive','source',None,'generic',1,query)
        handler=FixedConniveChoiceHandler()
        empty=handler.prepare(effect,context)
        self.assertEqual('event',empty.auto_continue.prepend_effects[0]['_connive_stage'])
        self.assertEqual(0,empty.auto_continue.prepend_effects[0]['_nonlands'])
        row=ObjectQueryResult('hand','private','Nonland','A','A','hand',types=('instant',),logical_object_id='hand@1')
        offered=handler.prepare(effect,replace(context,query=replace(query,object_rows=(row,))))
        continuation=SemanticChoiceContinuation(handler.handler_id,1,'stack',offered.continuation_effect,(),None,'',
            SemanticChoiceFrame('generic',1,'stack',0,'B'))
        for response,rows in (({'cards':['private']},()),({'cards':['private']},(replace(row,logical_object_id='hand@3'),)),
                              ({'cards':['private','private']},(row,)),({'cards':['private'],'extra':True},(row,))):
            with self.assertRaises(SemanticChoiceError):handler.complete(continuation,response,replace(query,object_rows=rows))
        card=SimpleNamespace(object_id='hand',zone_change_counter=2,zone='library',face_down=False,revealed_to=[])
        event=SimpleNamespace(actor='A',object_incarnation='hand@1')
        host=SimpleNamespace(state=SimpleNamespace(cards={'hand':card}),active_seats=tuple('ABCD'),
            _current_turn_history=lambda kind:(event,))
        self.assertEqual((),connive_discard_characteristic_ids(host,'A',{'_discards':{'hand@1':True}}))
        card.zone='exile'
        self.assertEqual(('hand@1',),connive_discard_characteristic_ids(host,'A',{'_discards':{'hand@1':True}}))
        card.face_down=True
        self.assertEqual((),connive_discard_characteristic_ids(host,'A',{'_discards':{'hand@1':True}}))

    def test_fixed_connive_admission_rejects_open_shapes_and_missing_dependencies(self):
        from quorune.compiler.connive_templates import fixed_connive_effect_template
        from quorune.rules.connive_capability_shapes import fixed_connive_node_capabilities
        from quorune.connive import CONNIVE_CAPABILITY
        card=self.fixture('When this creature enters, it connives.',type_line='Creature — Human',name='Generic Conniver')
        ir=compile_oracle_card(card,capability_registry=self.capabilities)
        self.assertEqual('exact',ir.status,ir.to_dict())
        node=next(n for face in ir.faces for n in face.nodes if n.kind=='triggered_ability')
        self.assertIn(CONNIVE_CAPABILITY,node.capability_dependencies)
        activation=compile_oracle_card(self.fixture('{3}: This creature connives.',type_line='Creature — Human',
            name='Generic Conniver'),capability_registry=self.capabilities)
        self.assertEqual('exact',activation.status,activation.to_dict())
        death=compile_oracle_card(self.fixture('When this creature dies, it connives.',type_line='Creature — Human',
            name='Generic Conniver'),capability_registry=self.capabilities)
        self.assertEqual('exact',death.status,death.to_dict())
        ambiguous=compile_oracle_card(self.fixture('Whenever a creature you control attacks, it connives.',
            type_line='Creature — Human',name='Generic Conniver'),capability_registry=self.capabilities)
        self.assertNotEqual('exact',ambiguous.status)
        effect=dict(node.effects[0])
        for bad in ({**effect,'extra':True},{**effect,'player':'$controller'},
                    {**effect,'spec':{'schema_version':1,'count':True}},
                    {**effect,'_connive_stage':'draw'}):
            self.assertEqual((),fixed_connive_node_capabilities(effects=(bad,),target_schema=None,mechanic_ids=node.mechanics))
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((Path(__file__).parents[1]/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        blocked=deepcopy(raw)
        for row in blocked['capabilities']:
            if row['id']==CONNIVE_CAPABILITY:row['status']='blocked';row['blockers']=['test dependency withdrawal']
        denied=compile_oracle_card(card,capability_registry=CapabilityRegistry(blocked))
        self.assertNotEqual('exact',denied.status)
        compiled=fixed_connive_effect_template('Target creature connives.',source_name='Generic Spell')
        self.assertEqual((CONNIVE_CAPABILITY,'target.revalidate_resolution'),
            fixed_connive_node_capabilities(effects=compiled[1],target_schema=compiled[2],mechanic_ids=compiled[3]))

    def test_fixed_connive_producer_omission_mutant_is_killed(self):
        card=self.fixture('When this creature enters, it connives.',type_line='Creature — Human',name='Generic Conniver')
        baseline=compile_oracle_card(card,capability_registry=self.capabilities)
        self.assertEqual('exact',baseline.status)
        with patch('quorune.compiler.resolution_effect_templates.fixed_connive_effect_template',return_value=None), \
             patch('quorune.compiler.connive_templates.fixed_connive_effect_template',return_value=None), \
             patch('quorune.compiler.source_self_effect_templates.fixed_connive_effect_template',return_value=None):
            mutated=compile_oracle_card(card,capability_registry=self.capabilities)
        self.assertNotEqual('exact',mutated.status)

    def test_connive_phases_keep_identity_and_committed_discard_results(self):
        from quorune.connive import FixedConniveSpec, ConnivingObject, connive_effect
        from quorune.object_query import ObjectQueryResult
        from quorune.semantic_choices.connive import FixedConniveChoiceHandler
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery, SemanticChoiceContext
        from quorune.semantic_runtime.intents import DrawCardsIntent, PlaceCountersIntent
        from quorune.semantic_runtime.connive_intent import ConniveCompletedIntent
        handler=FixedConniveChoiceHandler()
        retained=ConnivingObject('source','source@1','conniver','B')
        instruction={**connive_effect(FixedConniveSpec(3),card='conniver',player='B'),
                     '_connive_stage':'draw','_conniver':retained.to_dict()}
        row=ObjectQueryResult('source','conniver','Conniver','A','B','battlefield',logical_object_id='source@1')
        query=SnapshotSemanticChoiceQuery(tuple('ABCD'),tuple('ABCD'),object_rows=(row,),
            committed_discard_ids=('land@0','nonland@0','hidden@0'),
            defined_discard_characteristic_ids=('land@0','nonland@0'))
        context=SemanticChoiceContext('B','stack','A','Generic Connive','conniver',None,'generic',1,query)
        drawn=handler.prepare(instruction,context)
        self.assertIsInstance(drawn.preparation_intents[0],DrawCardsIntent)
        self.assertEqual(('B',3),(drawn.preparation_intents[0].player,drawn.preparation_intents[0].count))
        counter={**instruction,'_connive_stage':'counter',
                 '_discards':{'land@0':False,'nonland@0':True,'hidden@0':True}}
        placed=handler.prepare(counter,context)
        self.assertIsInstance(placed.preparation_intents[0],PlaceCountersIntent)
        self.assertEqual(('B',1),(placed.preparation_intents[0].actor,placed.preparation_intents[0].amount))
        event=handler.prepare(placed.auto_continue.prepend_effects[0],context)
        self.assertIsInstance(event.preparation_intents[0],ConniveCompletedIntent)
        self.assertEqual(1,event.preparation_intents[0].nonlands_discarded)
        returned=replace(row,logical_object_id='source@3')
        no_counter=handler.prepare(counter,replace(context,query=replace(query,object_rows=(returned,))))
        self.assertEqual((),no_counter.preparation_intents)
        zero={**instruction,'spec':FixedConniveSpec(0).to_dict()}
        self.assertEqual((),handler.prepare(zero,context).preparation_intents)

    def test_fixed_connive_codec_and_recipient_grammar_are_closed(self):
        from quorune.connive import FixedConniveSpec
        from quorune.compiler.connive_templates import fixed_connive_effect_template
        for count in (0,1,4,20):
            spec=FixedConniveSpec(count)
            self.assertEqual(spec,FixedConniveSpec.from_dict(spec.to_dict()))
        for count in (True,-1,21,'1',1.5):
            with self.assertRaises(ValueError):FixedConniveSpec(count)
        for text,card,player in (
            ('This creature connives.','$source','$source.connive_controller'),
            ('This creature connives 3.','$source','$source.connive_controller'),
            ('Target creature connives.','$target.0','$target.current_controller.0'),
        ):
            compiled=fixed_connive_effect_template(text,source_name='Generic Conniver',source_is_permanent=True)
            self.assertIsNotNone(compiled)
            self.assertEqual((card,player),(compiled[1][0]['card'],compiled[1][0]['player']))
        for text in ('This creature connives X.','Each creature connives.','Target creature connives, then draw a card.'):
            self.assertIsNone(fixed_connive_effect_template(text,source_name='Generic Conniver'))
        self.assertIsNone(fixed_connive_effect_template('It connives.',source_name='Generic Conniver'))
        self.assertIsNone(fixed_connive_effect_template('It connives.',source_name='Generic Conniver',source_is_permanent=True))

    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()
        cls.base = CardRecord(
            oracle_id="fixture:fixed-controller-sequence",
            name="Fixed Controller Sequence",
            mana_cost="{1}{U}",
            mana_value=2.0,
            type_line="Sorcery",
            oracle_text="Scry 2, then draw a card.",
            power=None,
            toughness=None,
            loyalty=None,
            defense=None,
            colors=("U",),
            color_identity=("U",),
            keywords=(),
            produced_mana=(),
            layout="normal",
            released_at="2026-01-01",
            legalities={"commander": "legal"},
            faces=(),
            raw={},
        )

    def fixture(
        self,
        text: str,
        *,
        type_line: str = "Sorcery",
        name: str = "Fixed Controller Sequence",
    ) -> CardRecord:
        return replace(
            self.base,
            oracle_id=f"fixture:{abs(hash((text, type_line)))}",
            name=name,
            oracle_text=text,
            type_line=type_line,
        )

    def test_fixed_life_templates_and_shapes_are_closed(self):
        cases = (
            (
                "You gain 3 life.",
                {"op": "life", "player": "$controller", "delta": 3},
                ("cr-119-life",),
            ),
            (
                "Lose two life.",
                {
                    "op": "lose_life",
                    "player": "$controller",
                    "amount": 2,
                },
                ("cr-119-life",),
            ),
            (
                "Each opponent loses 1 life.",
                {"op": "lose_life_each_opponent", "amount": 1},
                (
                    "cr-119-life",
                    "cr-101-the-magic-golden-rules",
                ),
            ),
        )
        for text, effect, mechanics in cases:
            with self.subTest(text=text):
                template = fixed_life_effect_template(text)
                self.assertIsNotNone(template)
                self.assertEqual((effect,), template.compiled()[1])
                self.assertEqual(
                    ("life.change.effect",),
                    fixed_life_node_capabilities(
                        effects=(effect,),
                        target_schema=None,
                        mechanic_ids=mechanics,
                    ),
                )

        valid = {"op": "life", "player": "$controller", "delta": 1}
        for effect, target, mechanics in (
            ({**valid, "delta": True}, None, ("cr-119-life",)),
            ({**valid, "delta": 0}, None, ("cr-119-life",)),
            ({**valid, "delta": -1}, None, ("cr-119-life",)),
            ({**valid, "player": "$target.0"}, None, ("cr-119-life",)),
            ({**valid, "extra": 1}, None, ("cr-119-life",)),
            (valid, {"count": 1}, ("cr-119-life",)),
            (valid, None, ()),
            (
                {"op": "set_life", "player": "$controller", "amount": 20},
                None,
                ("cr-119-life",),
            ),
            (
                {"op": "lose_life_each_opponent", "amount": 1},
                None,
                ("cr-119-life",),
            ),
        ):
            with self.subTest(effect=effect, target=target, mechanics=mechanics):
                self.assertEqual(
                    (),
                    fixed_life_node_capabilities(
                        effects=(effect,),
                        target_schema=target,
                        mechanic_ids=mechanics,
                    ),
                )
        for text in (
            "You gain 0 life.",
            "You gain X life.",
            "You gain life equal to your devotion.",
            "Your life total becomes 20.",
            "Exchange life totals with target opponent.",
            "You may pay 2 life.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_life_effect_template(text))

    def test_cr119_capability_requires_the_exact_fixed_life_shape(self):
        self.assertEqual(
            (),
            capability_dependencies_for_node(
                effects=(
                    {
                        "op": "set_life",
                        "player": "$controller",
                        "amount": 20,
                    },
                ),
                target_schema=None,
                mechanic_ids=("cr-119-life",),
            ),
        )

    def test_spell_trigger_and_activated_contexts_share_sequence_lowering(self):
        fixtures = (
            (
                self.fixture("Scry 2, then draw a card."),
                "spell_ability",
                ("scry", "draw"),
            ),
            (
                self.fixture(
                    "{T}: Draw a card and you lose 1 life.",
                    type_line="Artifact",
                ),
                "activated_ability",
                ("draw", "lose_life"),
            ),
            (
                self.fixture(
                    "When Fixed Controller Sequence enters, draw a card. Scry 1.",
                    type_line="Creature — Wizard",
                ),
                "triggered_ability",
                ("draw", "scry"),
            ),
            (
                self.fixture("Draw two cards, then discard two cards."),
                "spell_ability",
                ("draw", "choose_cards_apnap"),
            ),
            (
                self.fixture(
                    "{T}: Draw three cards, then discard four cards.",
                    type_line="Artifact",
                ),
                "activated_ability",
                ("draw", "choose_cards_apnap"),
            ),
            (
                self.fixture(
                    "When Fixed Controller Sequence enters, discard a card, "
                    "then draw a card.",
                    type_line="Creature — Wizard",
                ),
                "triggered_ability",
                ("choose_cards_apnap", "draw"),
            ),
        )
        for record, kind, operations in fixtures:
            with self.subTest(kind=kind):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(1, node.span.line)
                self.assertEqual(
                    "fixed-controller-draw-effect-sequence-v1",
                    node.template_id,
                )
                self.assertEqual(
                    operations,
                    tuple(effect["op"] for effect in node.effects),
                )
                self.assertIn(SEQUENCE_CAPABILITY, node.capability_dependencies)
                self.assertIn(
                    "zone.draw.library_to_hand",
                    node.capability_dependencies,
                )
                if "choose_cards_apnap" in operations:
                    self.assertIn(
                        "choice.affected_player.fixed_discard",
                        node.capability_dependencies,
                    )
                    self.assertIn(
                        "zone.change.destination_replacement",
                        node.capability_dependencies,
                    )

    def test_controller_discard_leaf_and_sequence_shape_are_closed(self):
        template = fixed_controller_discard_effect_template(
            "You discard four cards."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(4, template.count)
        discard = template.effects[0]
        draw = {
            "op": "draw",
            "player": "$controller",
            "count": 3,
            "private": True,
        }
        self.assertEqual(["$controller"], discard["players"])
        self.assertEqual(
            (),
            fixed_affected_player_discard_node_capabilities(
                effects=(discard,),
                target_schema=None,
                mechanic_ids=(
                    "fixed-affected-player-discard",
                    "cr-402-hand",
                ),
            ),
        )
        self.assertEqual(
            (
                "choice.affected_player.fixed_discard",
                SEQUENCE_CAPABILITY,
                "zone.change.destination_replacement",
                "zone.draw.library_to_hand",
            ),
            fixed_controller_effect_sequence_node_capabilities(
                effects=(draw, discard),
                target_schema=None,
                mechanic_ids=(
                    "fixed-controller-effect-sequence",
                    "fixed-affected-player-discard",
                    "cr-121-drawing-a-card",
                    "cr-402-hand",
                ),
            ),
        )

        mutations = []
        for field, value in (
            ("players", "all"),
            ("players", "opponents"),
            ("players", ["$target.0"]),
            ("count", 5),
            ("hidden", False),
            ("then", "sacrifice"),
        ):
            mutant = deepcopy(discard)
            mutant[field] = value
            if field == "players" and value == ["$target.0"]:
                mutant["target"] = "$target.0"
            mutations.append(mutant)
        for mutant in mutations:
            with self.subTest(mutant=mutant):
                self.assertEqual(
                    (),
                    fixed_controller_effect_sequence_node_capabilities(
                        effects=(draw, mutant),
                        target_schema=None,
                        mechanic_ids=(
                            "fixed-controller-effect-sequence",
                            "fixed-affected-player-discard",
                            "cr-121-drawing-a-card",
                            "cr-402-hand",
                        ),
                    ),
                )
        for text in (
            "Draw a card, then target player discards a card.",
            "Draw a card, then each player discards a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_controller_effect_sequence_template(text)
                )
                ir = compile_oracle_card(
                    self.fixture(text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertTrue(
                    all(
                        SEQUENCE_CAPABILITY not in node.capability_dependencies
                        for face in ir.faces
                        for node in face.nodes
                    )
                )

    def test_sequence_shape_rejects_open_and_malformed_variants(self):
        valid = (
            {"op": "scry", "player": "$controller", "count": 2},
            {
                "op": "draw",
                "player": "$controller",
                "count": 1,
                "private": True,
            },
        )
        self.assertEqual(
            (
                "library.scry.fixed_controller",
                SEQUENCE_CAPABILITY,
                "zone.draw.library_to_hand",
            ),
            fixed_controller_effect_sequence_node_capabilities(
                effects=valid,
                target_schema=None,
                mechanic_ids=(
                    "fixed-controller-effect-sequence",
                    "scry",
                    "cr-121-drawing-a-card",
                ),
            ),
        )
        mutants = (
            (({**valid[0], "count": True}, valid[1]), None),
            ((valid[0], {**valid[1], "private": False}), None),
            ((valid[0], {**valid[1], "player": "$target.0"}), None),
            ((valid[0], valid[1], valid[0]), None),
            ((valid[0], valid[0]), None),
            ((valid[1], valid[1]), None),
            ((valid[0], valid[1]), {"count": 1}),
        )
        for effects, target in mutants:
            with self.subTest(effects=effects, target=target):
                self.assertEqual(
                    (),
                    fixed_controller_effect_sequence_node_capabilities(
                        effects=effects,
                        target_schema=target,
                        mechanic_ids=("fixed-controller-effect-sequence",),
                    ),
                )

    def test_whole_hand_sequence_promotes_composition_but_keeps_leaf_boundary(self):
        text = "Draw two cards, then discard your hand."
        self.assertIsNone(fixed_controller_effect_sequence_template(text))
        ir = compile_oracle_card(
            self.fixture(text),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effects = ir.faces[0].nodes[0].effects
        self.assertEqual(["draw", "discard_whole_hands"], [effect["op"] for effect in effects])
        self.assertEqual(2, effects[0]["count"])
        self.assertEqual("$controller", effects[0]["player"])
        self.assertEqual(["$controller"], effects[1]["players"])

    def test_public_quantity_sequence_promotes_composition_but_keeps_leaf_boundary(self):
        text='Draw cards equal to the number of creatures you control. Scry 1.'
        self.assertIsNone(fixed_controller_effect_sequence_template(text))
        ir=compile_oracle_card(self.fixture(text),capability_registry=self.capabilities,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        effects=ir.faces[0].nodes[0].effects
        self.assertEqual(['draw','scry'],[e['op'] for e in effects])
        self.assertEqual('public_query_effect_amount',effects[0]['count']['kind'])
        self.assertEqual(1,effects[1]['count'])

    def test_unsupported_sequence_wording_remains_a_precise_residual(self):
        supported="Draw two cards, then discard a card at random."
        template=fixed_controller_effect_sequence_template(supported)
        self.assertIsNotNone(template)
        ir=compile_oracle_card(self.fixture(supported),capability_registry=self.capabilities,
            capability_profile="commander_review")
        self.assertEqual("exact",ir.status,ir.material_residuals)
        self.assertEqual(("draw","discard_random_cards"),tuple(effect['op'] for effect in ir.faces[0].nodes[0].effects))
        composed = "Draw a card. Scry 1. You gain 1 life."
        self.assertIsNone(fixed_controller_effect_sequence_template(composed))
        for text in (
            "Draw cards equal to the number of different powers among creatures you control. Scry 1.",
            "Scry 1, then you may draw a card.",
            "Draw a card. Each player loses X life.",
            "Draw a card unless an opponent pays {1}.",
            "Draw two cards, then discard a creature card at random.",
            "Draw two cards, then discard each opponent's hand.",
            "Draw two cards, then discard two cards if you control a Wizard.",
            "Draw a card, then discard five cards.",
            "Discard a card, then draw a card, then lose 1 life.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_controller_effect_sequence_template(text))
                ir = compile_oracle_card(
                    self.fixture(text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_draw_then_all_player_loss_keeps_controller_and_global_recipients(self):
        text="Draw a card. Each player loses 1 life."
        self.assertIsNone(fixed_controller_effect_sequence_template(text))
        ir=compile_oracle_card(self.fixture(text),capability_registry=self.capabilities,
                               capability_profile="commander_review")
        self.assertEqual("exact",ir.status,ir.material_residuals)
        effects=ir.faces[0].nodes[0].effects
        self.assertEqual("$controller",effects[0]["player"])
        self.assertEqual("all_active_players",effects[1]["scope"])
        self.assertEqual(1,effects[1]["amount"])

    def test_sequence_compiler_and_shape_mutants_are_killed(self):
        records = (
            self.fixture("Scry 2, then draw a card."),
            self.fixture("Draw two cards, then discard a card."),
        )

        def assert_exact() -> None:
            for record in records:
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(
                    "fixed-controller-draw-effect-sequence-v1",
                    ir.faces[0].nodes[0].template_id,
                )

        assert_exact()
        with patch(
            "quorune.oracle_ir.fixed_controller_effect_sequence_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()
        with patch(
            "quorune.compiler.fixed_controller_effect_sequences."
            "fixed_controller_discard_effect_template",
            return_value=None,
        ):
            ir = compile_oracle_card(
                records[1],
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)


class FixedControllerEffectSequenceRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def session(
        self,
        seed: int,
        *,
        players: int = 2,
        auto_pass_empty: bool = True,
    ):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=auto_pass_empty,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.priority_player = None
        return session

    def begin_sequence(self, session, effects, *, seat: str = "A"):
        engine = session.engine
        card = next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.is_card_object
            and card.zone not in {"command", "outside", "library"}
        )
        starting_zone = card.zone
        engine._remove_from_zone(card)
        engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        card.controller = seat
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        key = f"test:fixed-controller-sequence:{session.state.config.seed}"
        program = SemanticProgram(
            key=key,
            label="Fixed controller sequence",
            effects=list(effects),
            destination="graveyard",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id=uuid.uuid4().hex,
            ref=f"S-sequence-{session.state.config.seed}",
            kind="spell",
            controller=seat,
            label=program.label,
            card_object_id=card.object_id,
            semantic_key=key,
            default_destination="graveyard",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._begin_resolve_item(
            item,
            program.effects,
            program.destination,
            note="fixed controller sequence regression",
        )
        return card, starting_zone

    @staticmethod
    def choose(session, seat: str, refs: list[str]):
        return session.act(
            f"pilot:{seat}",
            {"action_id": "choose", "cards": refs},
        )

    def assert_replays(self, session, label: str) -> None:
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def install_opponent_voidwalker(self, engine) -> CardInstance:
        record = self.db.lookup("Dauthi Voidwalker")
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
            promote_exact_effect_programs=True,
        )
        card = CardInstance(
            object_id="fixture:sequence-voidwalker",
            ref="sequence-voidwalker",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="B",
            controller="B",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["B"].zones["battlefield"].append(card.object_id)
        return card

    def test_draw_then_life_commits_in_printed_order(self):
        session = self.session(123001)
        engine = session.engine
        hand_before = len(engine.state.players["A"].zones["hand"])
        life_before = engine.state.players["A"].life
        event_start = len(engine.state.events)
        _source, starting_zone = self.begin_sequence(
            session,
            (
                {
                    "op": "draw",
                    "player": "A",
                    "count": 2,
                    "private": True,
                },
                {"op": "lose_life", "player": "A", "amount": 2},
            ),
        )
        self.assertEqual(
            hand_before + 2 - int(starting_zone == "hand"),
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(life_before - 2, engine.state.players["A"].life)
        relevant = [
            event.code
            for event in engine.state.events[event_start:]
            if event.code in {"card.draw.private", "effect.life"}
        ]
        self.assertEqual(
            ["card.draw.private", "card.draw.private", "effect.life"],
            relevant,
        )

    def test_scry_then_draw_resumes_privately_and_replays_exactly(self):
        session = self.session(123002, players=4)
        engine = session.engine
        library = engine.state.players["A"].zones["library"]
        expected = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(library[-2:])
        )
        hand_before = len(engine.state.players["A"].zones["hand"])
        _source, starting_zone = self.begin_sequence(
            session,
            (
                {"op": "scry", "player": "A", "count": 2},
                {
                    "op": "draw",
                    "player": "A",
                    "count": 1,
                    "private": True,
                },
            ),
        )
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        continuation = json.dumps(
            engine.state.pending_decision.continuation,
            sort_keys=True,
            default=str,
        )
        self.assertIn('"op": "draw"', continuation)
        for seat in "BCD":
            packet = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertTrue(all(ref not in packet for ref in expected))
        session.commands.clear()
        session.decisions.clear()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": [expected[1]], "bottom": [expected[0]]},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            hand_before + 1 - int(starting_zone == "hand"),
            len(engine.state.players["A"].zones["hand"]),
        )
        drawn = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.draw.private"
        )
        self.assertEqual(expected[1], drawn.details["objects"][0])
        for seat in "BCD":
            packet = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertNotIn(expected[1], packet)
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-controller-sequence"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_invalid_remaining_effect_rolls_back_the_choice_command(self):
        session = self.session(123003)
        engine = session.engine
        library_before = tuple(engine.state.players["A"].zones["library"])
        life_before = engine.state.players["A"].life
        expected = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(library_before[-2:])
        )
        self.begin_sequence(
            session,
            (
                {"op": "scry", "player": "A", "count": 2},
                {
                    "op": "lose_life",
                    "player": "missing-seat",
                    "amount": 1,
                },
            ),
        )
        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": [expected[1]], "bottom": [expected[0]]},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual(library_before, tuple(engine.state.players["A"].zones["library"]))
        self.assertEqual(life_before, engine.state.players["A"].life)

    def test_draw_then_controller_discard_is_private_and_replays_exactly(self):
        session = self.session(123004, players=4, auto_pass_empty=False)
        engine = session.engine
        self.install_opponent_voidwalker(engine)
        library = engine.state.players["A"].zones["library"]
        drawn_refs = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(library[-2:])
        )
        event_start = len(engine.state.events)
        template = fixed_controller_effect_sequence_template(
            "Draw two cards, then discard a card."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.begin_sequence(session, template.effects)

        self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
        self.assertEqual(["pilot:A"], session.pending_principals())
        decision = StateProjector(self.db, engine.state)._decision("pilot:A")
        self.assertIsNotNone(decision)
        assert decision is not None
        serialized = json.dumps(decision, sort_keys=True)
        for ref in drawn_refs:
            self.assertIn(ref, serialized)
        for seat in "BCD":
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(f"pilot:{seat}")
            )
            packet = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertTrue(all(ref not in packet for ref in drawn_refs))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = self.choose(session, "A", [drawn_refs[0]])
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            "exile",
            engine._resolve_object("A", drawn_refs[0]).zone,
        )
        self.assertEqual(
            1,
            engine._resolve_object("A", drawn_refs[0]).counters["void"],
        )
        self.assertEqual("hand", engine._resolve_object("A", drawn_refs[1]).zone)
        relevant = [
            event.code
            for event in engine.state.events[event_start:]
            if event.code in {"card.draw.private", "choice.discard"}
        ]
        self.assertEqual(
            ["card.draw.private", "card.draw.private", "choice.discard"],
            relevant,
        )
        self.assert_replays(session, "draw-then-controller-discard")

    def test_controller_discard_then_draw_commits_in_printed_order(self):
        session = self.session(123005, players=4, auto_pass_empty=False)
        engine = session.engine
        event_start = len(engine.state.events)
        template = fixed_controller_effect_sequence_template(
            "Discard a card, then draw a card."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.begin_sequence(session, template.effects)
        discarded = engine.state.cards[
            engine.state.players["A"].zones["hand"][0]
        ]

        self.assertFalse(
            any(
                event.code == "card.draw.private"
                for event in engine.state.events[event_start:]
            )
        )
        accepted = self.choose(session, "A", [discarded.ref])
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", discarded.zone)
        relevant = [
            event.code
            for event in engine.state.events[event_start:]
            if event.code in {"choice.discard", "card.draw.private"}
        ]
        self.assertEqual(["choice.discard", "card.draw.private"], relevant)

    def test_stale_controller_discard_choice_rolls_back_without_resuming(self):
        session = self.session(123006, players=4, auto_pass_empty=False)
        engine = session.engine
        template = fixed_controller_effect_sequence_template(
            "Discard a card, then draw a card."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.begin_sequence(session, template.effects)
        stale = engine.state.cards[
            engine.state.players["A"].zones["hand"][0]
        ]
        engine.move_card(
            stale.object_id,
            "graveyard",
            reason="focused stale controller discard choice",
        )
        before = authoritative_state_hash(engine.state)
        event_count = len(engine.state.events)

        rejected = self.choose(session, "A", [stale.ref])

        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual(event_count, len(engine.state.events))


if __name__ == "__main__":
    unittest.main()
