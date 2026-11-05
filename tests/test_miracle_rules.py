from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.deck import DeckDefinition, DeckEntry
from quorune.compiler.ir_model import SourceSpan
from quorune.compiler.miracle_nodes import miracle_keyword_nodes
from quorune.drawing.miracle_model import MIRACLE_CAPABILITY_ID
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.record import authoritative_state_hash
from quorune.choice_forms import build_action_form
from quorune.drawing.miracle_triggers import miracle_reveal_active
from quorune.replacement.immutable import FrozenMap
from quorune.semantic_runtime.intents import CopyStackItemIntent
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions


def record(text):
    return CardRecord(
        oracle_id='00000000-0000-4000-8000-00000000a194', name='Miracle Grammar Witness',
        mana_cost='{2}{R}', mana_value=3.0, type_line='Instant', oracle_text=text,
        power=None, toughness=None, loyalty=None, defense=None, colors=('R',),
        color_identity=('R',), keywords=('Miracle',), produced_mana=(), layout='normal',
        released_at='2026-08-07', legalities={'commander':'legal'}, faces=(), raw={},
    )


class MiracleCompilerTests(unittest.TestCase):
    def test_fixed_miracle_produces_both_linked_nodes_with_complete_lifecycle_closure(self):
        text = "Miracle {R} (You may cast this card for its miracle cost when you draw it if it's the first card you drew this turn.)"
        ir = compile_oracle_card(record(text), capability_registry=load_default_capability_registry(),
                                 capability_profile='commander_review')
        nodes = ir.faces[0].nodes
        self.assertEqual(2, len(nodes))
        reveal, trigger = nodes
        self.assertEqual(('all', 'hand'), (reveal.active_zone, trigger.active_zone))
        self.assertEqual(('draw.miracle_reveal', 'card.draw.miracle_revealed.self'),
                         (reveal.event, trigger.event))
        self.assertEqual('miracle_cast_choice', trigger.effects[0]['op'])
        self.assertEqual({'R':1}, {key:value for key,value in reveal.handlers[0]['cost']['mana_cost'].items() if value})
        for node in nodes:
            self.assertEqual(text[node.span.start:node.span.end], node.text)
            self.assertIn(MIRACLE_CAPABILITY_ID, node.capability_dependencies)
            self.assertTrue(node.handlers[0]['requires_complete_card_program'])
            self.assertTrue(node.exact)
        self.assertEqual('exact', ir.status)

    def test_miracle_dependency_and_compiler_mutations_fail_closed(self):
        source=record('Miracle {R}')
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in (MIRACLE_CAPABILITY_ID,'zone.draw.library_to_hand','zone.draw.reveal_as_drawn','trigger.placement.apnap'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==dependency).update(
                status='blocked',blockers=['focused Miracle dependency mutation'])
            with self.subTest(dependency=dependency):
                self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                                                               capability_profile='commander_review').status)
        with patch('quorune.compiler.multi_keyword_nodes.miracle_keyword_nodes',return_value=None):
            ir=compile_oracle_card(source,capability_registry=load_default_capability_registry(),
                                   capability_profile='commander_review')
        self.assertFalse(any(node.template_id in {'miracle-fixed-mana-reveal-v1','miracle-linked-cast-trigger-v1'}
                             for node in ir.faces[0].nodes))
        self.assertNotEqual('exact',ir.status)

    def test_variable_complex_granted_or_unrepresented_faces_do_not_enter_fixed_miracle(self):
        for text in ('Miracle {X}{R}', 'Miracle {W/U}', 'Miracle {R/P}', 'Miracle {S}',
                     'Miracle {R}. Draw a card.', 'Cards in your hand have miracle {R}.',
                     'Miracle {R} (Reveal this whenever you like.)'):
            with self.subTest(text=text):
                residuals=[]
                self.assertIsNone(miracle_keyword_nodes(
                    node_id='n1',line=text,material_line=text,span=SourceSpan(0,len(text),1),
                    mechanics=('miracle',),record=record(text),face_id='front',
                    capability_registry=load_default_capability_registry(),
                    capability_profile='commander_review',residuals=residuals,
                ))
                self.assertNotEqual('exact',compile_oracle_card(record(text),trusted_mechanics=('miracle',),
                    capability_registry=load_default_capability_registry(),capability_profile='commander_review').status)
        for changed in (replace(record('Miracle {R}'), layout='split'),
                        replace(record('Miracle {R}'), type_line='Land')):
            with self.subTest(changed=changed.layout):
                self.assertIsNone(miracle_keyword_nodes(
                    node_id='n1',line='Miracle {R}',material_line='Miracle {R}',span=SourceSpan(0,11,1),
                    mechanics=('miracle',),record=changed,face_id='front',
                    capability_registry=load_default_capability_registry(),
                    capability_profile='commander_review',residuals=[],
                ))


class MiracleRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.SpreeModeCostRuntimeTests.session
    add=actions.SpreeModeCostRuntimeTests.add
    prepare=actions.SpreeModeCostRuntimeTests.prepare
    checkpoint=actions.SpreeModeCostRuntimeTests.checkpoint
    resolve=actions.SpreeModeCostRuntimeTests.resolve
    replay=actions.SpreeModeCostRuntimeTests.replay
    assert_closed=actions.SpreeModeCostRuntimeTests.assert_closed
    reject=actions.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        path=Path(cls.temp.name)/'miracle.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/miracle-cards.json'],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Miracle witness',[DeckEntry('Generic Bound Commander',1,'commander'),
                                DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def begin_original_draw(self,seed=70209401,*,name='Thunderous Wrath',a_mana=None,extras=()):
        session=self.session(seed);engine=session.engine
        inspiration=self.add(engine,'Inspiration',seat='B',zone='hand',ref='miracle-inspiration')
        second=self.add(engine,'Zephyrim',zone='library',ref='miracle-second')
        first=self.add(engine,name,zone='library',ref='miracle-first')
        private=self.add(engine,'Generic Bound Body',seat='D',zone='hand',ref='miracle-private')
        for extra_name,extra_zone,extra_ref in extras:
            extra=self.add(engine,extra_name,zone=extra_zone,ref=extra_ref)
            self.assert_closed(session,extra)
        self.assert_closed(session,first);self.assert_closed(session,inspiration)
        self.prepare(session,actor='B',active='B',mana={'C':3,'U':1})
        engine.state.players['A'].mana_pool.update({'R':1,'W':1,'B':1})
        if a_mana:engine.state.players['A'].mana_pool.update(a_mana)
        self.checkpoint(session)
        result=session.act('pilot:B',{'action_id':'cast:'+inspiration.ref,'targets':['A'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('draw.miracle_reveal',decision.kind)
        self.assertEqual(['A'],decision.actors)
        return session,first.object_id,second.object_id,inspiration.object_id,private.object_id

    def priority_for(self,session,actor='A'):
        for _ in range(4):
            if session.state.priority_player==actor:return
            self.assertEqual('priority',session.state.pending_decision.kind)
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Miracle witness did not reach the requested priority seat')

    def resolve_top(self,session):
        ref=session.state.stack[-1].ref
        for _ in range(16):
            if not any(item.ref==ref for item in session.state.stack):return
            self.assertEqual('priority',session.state.pending_decision.kind)
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Miracle witness top stack object did not resolve')

    def test_original_miracle_nonland_target_revalidates_after_actual_response(self):
        for respond in (False, True):
            with self.subTest(response=respond):
                session, first_id, second_id, _draw_id, private_id = self.begin_original_draw(
                    70209501 + int(respond), name='Vanishment', a_mana={'U': 2},
                    extras=(
                        ('Grizzly Bears', 'battlefield', 'miracle-target-body'),
                        ('Generic Bound Plains', 'battlefield', 'miracle-illegal-land'),
                        ('Unsummon', 'hand', 'miracle-real-response'),
                    ),
                )
                target = session.state.cards['fixture:miracle-target-body']
                land = session.state.cards['fixture:miracle-illegal-land']
                target_counter = target.zone_change_counter
                for principal in ('pilot:C', 'pilot:D', 'spectator'):
                    self.assertNotIn('Vanishment', json.dumps(session.packet(principal, full=True)))
                self.reject(session, 'reveal', actor='D')
                accepted = session.act('pilot:A', {'action_id': 'reveal'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                context = session.packet('pilot:A', full=True)['decision']['ctx']
                option = next(row for row in context['cast_options'] if row['id'] == 'miracle')
                legal = option['target_schema']['legal_refs']
                self.assertIn(target.ref, legal)
                self.assertNotIn(land.ref, legal)
                self.reject(session, 'choose', choice='cast', targets=[land.ref], pay='auto')
                self.reject(session, 'choose', actor='B', choice='cast', targets=[target.ref], pay='auto')
                session = self.replay(session, load=True)
                accepted = session.act('pilot:A', {
                    'action_id': 'choose', 'choice': 'cast',
                    'targets': [target.ref], 'pay': 'auto',
                })
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual('stack', session.state.cards[first_id].zone)
                self.assertEqual(1, session.state.players['A'].mana_pool['U'])
                self.assertFalse(miracle_reveal_active(session.state, session.state.cards[first_id]))
                if respond:
                    self.priority_for(session)
                    actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    response = next(row for row in actions if row['id'] == 'cast:miracle-real-response')
                    self.assertIn(target.ref, response['target_schema']['legal_refs'])
                    accepted = session.act('pilot:A', {
                        'action_id': response['id'], 'targets': [target.ref], 'pay': 'auto',
                    })
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.resolve_top(session)
                    self.assertEqual('hand', session.state.cards[target.object_id].zone)
                    self.assertEqual(target_counter + 1, session.state.cards[target.object_id].zone_change_counter)
                    self.assertEqual(1, len(session.state.stack))
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard', session.state.cards[first_id].zone)
                self.assertEqual('hand' if respond else 'library', session.state.cards[target.object_id].zone)
                self.assertEqual(target_counter + 1, session.state.cards[target.object_id].zone_change_counter)
                if not respond:
                    self.assertEqual(target.object_id, session.state.players['A'].zones['library'][-1])
                self.assertEqual('battlefield', session.state.cards[land.object_id].zone)
                self.assertEqual('hand', session.state.cards[second_id].zone)
                private = session.state.cards[private_id]
                self.assertNotIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
                self.assertIn(private.ref, json.dumps(session.packet('pilot:D', full=True)))
                self.assertEqual('B', session.state.active_player)
                self.replay(session, load=True)

    def test_original_miracle_waits_for_draw_completion_then_casts_pays_and_replays(self):
        session,first_id,second_id,draw_id,private_id=self.begin_original_draw()
        engine=session.engine;first=engine.state.cards[first_id]
        before_identity=first.logical_object_id
        library_size=len(session.state.players['A'].zones['library'])
        self.assertEqual('library',first.zone)
        self.assertEqual('B',session.state.active_player)
        for principal in ('pilot:C','pilot:D','spectator'):
            self.assertNotIn('Thunderous Wrath',json.dumps(session.packet(principal,full=True)))
        self.reject(session,'reveal',actor='D')
        original=deepcopy(session.state.pending_decision.continuation)
        session.state.pending_decision.continuation['opportunity']['turn_sequence']+=1
        self.reject(session,'reveal')
        session.state.pending_decision.continuation=original
        session=self.replay(session,load=True);engine=session.engine
        accepted=session.act('pilot:A',{'action_id':'reveal'})
        self.assertTrue(accepted.ok,accepted.summary)
        first=engine.state.cards[first_id]
        self.assertEqual('hand',first.zone)
        self.assertNotEqual(before_identity,first.logical_object_id)
        self.assertEqual('hand',engine.state.cards[second_id].zone)
        self.assertEqual(library_size-2,len(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard',engine.state.cards[draw_id].zone)
        self.assertTrue(miracle_reveal_active(session.state,first))
        self.assertEqual(1,len(session.state.stack))
        self.assertEqual('triggered_ability',session.state.stack[0].kind)
        self.assertEqual('priority',session.state.pending_decision.kind)
        spectator=session.packet('spectator',full=True)['state']['players']['A']
        self.assertIn(first.ref,{card['id'] for card in spectator['known_hand']})
        self.assertNotIn(engine.state.cards[second_id].ref,{card['id'] for card in spectator['known_hand']})
        for _ in range(4):
            if session.state.priority_player=='A':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        self.assertEqual('A',session.state.priority_player)
        self.reject(session,'cast:'+first.ref,cost_option='miracle',targets=['B'],pay='auto')
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        packet=session.packet('pilot:A',full=True)
        self.assertEqual('miracle_cast_choice',packet['decision']['ctx']['operation'])
        self.assertEqual('miracle',packet['decision']['ctx']['cast_options'][0]['id'])
        context=packet['decision']['ctx']
        form=build_action_form(context['legal_actions'][0],decision_kind='semantic.choice',context=context)
        self.assertEqual({'field':'choice','value':'cast'},form['variants']['when'])
        self.assertTrue(any(field['name']=='targets' for option in form['variants']['options'] for field in option['fields']))
        self.reject(session,'choose',actor='B',choice='cast',targets=['B'])
        session=self.replay(session,load=True);engine=session.engine
        life=session.state.players['B'].life
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast','targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        first=engine.state.cards[first_id]
        self.assertEqual('stack',first.zone)
        self.assertEqual(0,session.state.players['A'].mana_pool['R'])
        self.assertEqual('spell',session.state.stack[-1].kind)
        self.assertFalse(miracle_reveal_active(session.state,first))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',first.zone)
        self.assertEqual(life-5,session.state.players['B'].life)
        self.assertEqual('B',session.state.active_player)
        self.assertNotIn('miracle-private',json.dumps(session.packet('pilot:C',full=True)))
        self.replay(session)

    def test_copied_miracle_uses_current_controller_and_outlives_original_reveal_scope(self):
        session,first_id,_second_id,_draw_id,_private_id=self.begin_original_draw(70209402)
        self.assertTrue(session.act('pilot:A',{'action_id':'reveal'}).ok)
        engine=session.engine;first=engine.state.cards[first_id]
        original=session.state.stack[0]
        # A canonical copied occurrence is the fixture precondition. This
        # does not claim compiler support for any named copy-producing card.
        copied_ref=engine.copy_stack_item_intent(CopyStackItemIntent(
            actor='B',controller='B',target_stack_ref=original.ref,targets=(),
            target_groups=FrozenMap(),reason='Miracle copied-trigger fixture',
        ))
        stifle=self.add(engine,'Stifle',seat='B',zone='hand',ref='miracle-stifle')
        self.assert_closed(session,stifle)
        engine.state.players['B'].mana_pool.update({'U':1,'R':1})
        self.prepare(session,actor='B',active='B')
        self.checkpoint(session)
        self.reject(session,'cast:'+first.ref,actor='B',cost_option='miracle',targets=['C'],pay='auto')
        accepted=session.act('pilot:B',{'action_id':'cast:'+stifle.ref,'targets':[original.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(8):
            if not any(item.ref==original.ref for item in session.state.stack):break
            self.assertEqual('priority',session.state.pending_decision.kind)
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.assertFalse(any(item.ref==original.ref for item in session.state.stack))
        self.assertTrue(any(item.ref==copied_ref for item in session.state.stack))
        self.assertEqual('hand',first.zone)
        self.assertFalse(miracle_reveal_active(session.state,first))
        self.assertNotIn(first.ref,{card['id'] for card in session.packet('spectator',full=True)['state']['players']['A'].get('known_hand',[])})
        self.assertIn('B',first.known_to)
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['B'],decision.actors)
        self.reject(session,'choose',actor='A',choice='cast',targets=['C'])
        session=self.replay(session,load=True);engine=session.engine
        life=session.state.players['C'].life
        a_red=session.state.players['A'].mana_pool['R']
        accepted=session.act('pilot:B',{'action_id':'choose','choice':'cast','targets':['C'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        spell=session.state.stack[-1]
        self.assertEqual(('spell','B'),(spell.kind,spell.controller))
        self.assertEqual('A',engine.state.cards[first_id].owner)
        self.assertEqual(0,session.state.players['B'].mana_pool['R'])
        self.assertEqual(a_red,session.state.players['A'].mana_pool['R'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life-5,session.state.players['C'].life)
        self.assertEqual('graveyard',engine.state.cards[first_id].zone)
        self.assertIn(first_id,session.state.players['A'].zones['graveyard'])
        self.replay(session)

    def test_original_creature_miracle_pays_squad_additional_cost_on_another_turn(self):
        session,first_id,_second_id,_draw_id,_private_id=self.begin_original_draw(
            70209403,name='Zephyrim',a_mana={'C':3,'R':0,'B':0})
        self.assertTrue(session.act('pilot:A',{'action_id':'reveal'}).ok)
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual('B',session.state.active_player)
        context=session.packet('pilot:A',full=True)['decision']['ctx']
        options=context['cast_options']
        paid=next(option for option in options if '+squad:' in option['id'])
        self.assertEqual((3,1),(paid['requirements']['GENERIC'],paid['requirements']['W']))
        form=build_action_form(context['legal_actions'][0],decision_kind='semantic.choice',context=context)
        variant=next(option for option in form['variants']['options'] if option['value']==paid['id'])
        self.assertTrue(any(field['name']=='squad_count' and field['control']=='integer' for field in variant['fields']))
        self.reject(session,'choose',choice='cast',cost_option=paid['id'],squad_count=0,pay='auto')
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cost_option':paid['id'],'squad_count':1,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual((0,0),(session.state.players['A'].mana_pool['C'],session.state.players['A'].mana_pool['W']))
        self.assertIsNone(self.resolve(session))
        first=session.state.cards[first_id]
        self.assertEqual('battlefield',first.zone)
        copies=[card for card in session.state.cards.values() if card.zone=='battlefield' and card.is_token
                and card.printed_name=='Zephyrim' and card.controller=='A']
        self.assertEqual(1,len(copies))
        self.assertTrue({'flying','vigilance'}.issubset(
            {keyword.casefold() for keyword in session.engine._effective_card_data(copies[0]).get('keywords',())}))
        self.assertEqual('B',session.state.active_player)
        self.replay(session)

    def test_declining_intrinsic_reveal_keeps_the_draw_private_and_continues(self):
        session,first_id,second_id,draw_id,_private_id=self.begin_original_draw(70209404)
        accepted=session.act('pilot:A',{'action_id':'decline'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('hand',session.state.cards[first_id].zone)
        self.assertEqual('hand',session.state.cards[second_id].zone)
        self.assertEqual('graveyard',session.state.cards[draw_id].zone)
        self.assertFalse(session.state.stack)
        self.assertEqual('priority',session.state.pending_decision.kind)
        for principal in ('pilot:B','pilot:C','pilot:D','spectator'):
            self.assertNotIn('Thunderous Wrath',json.dumps(session.packet(principal,full=True)))
        self.assertFalse(miracle_reveal_active(session.state,session.state.cards[first_id]))
        self.replay(session)

    def test_unpayable_miracle_offers_decline_and_keeps_the_card_in_hand(self):
        session,first_id,_second_id,_draw_id,_private_id=self.begin_original_draw(
            70209405,a_mana={'R':0})
        self.assertTrue(session.act('pilot:A',{'action_id':'reveal'}).ok)
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        packet=session.packet('pilot:A',full=True)
        self.assertEqual([],packet['decision']['ctx']['cast_options'])
        self.reject(session,'choose',choice='cast',targets=['B'],pay='auto')
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','choice':'decline'}).ok)
        first=session.state.cards[first_id]
        self.assertEqual('hand',first.zone)
        self.assertIn('B',first.known_to)
        self.assertFalse(miracle_reveal_active(session.state,first))
        self.assertNotIn(first.ref,{card['id'] for card in session.packet('spectator',full=True)['state']['players']['A'].get('known_hand',[])})
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_payable_miracle_can_be_declined_without_targets_or_payment(self):
        session,first_id,_second_id,_draw_id,_private_id=self.begin_original_draw(70209407)
        self.assertTrue(session.act('pilot:A',{'action_id':'reveal'}).ok)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        context=session.packet('pilot:A',full=True)['decision']['ctx']
        self.assertTrue(context['cast_options'])
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','choice':'decline'}).ok)
        self.assertEqual(1,session.state.players['A'].mana_pool['R'])
        first=session.state.cards[first_id]
        self.assertEqual('hand',first.zone)
        self.assertFalse(session.state.stack)
        self.assertFalse(miracle_reveal_active(session.state,first))
        self.replay(session)

    def test_discard_return_and_later_draw_cannot_reuse_the_original_miracle_binding(self):
        session,first_id,_second_id,_draw_id,_private_id=self.begin_original_draw(
            70209406,a_mana={'R':2,'W':0,'B':0,'G':1,'U':1,'C':2},
            extras=(('Noose Constrictor','battlefield','miracle-noose'),
                    ('Reclaim','hand','miracle-reclaim'),('Think Twice','hand','miracle-think-twice')))
        self.assertTrue(session.act('pilot:A',{'action_id':'reveal'}).ok)
        first=session.state.cards[first_id]
        original_counter=first.zone_change_counter
        self.priority_for(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        activation=next(row for row in actions if row['id'].startswith('activate:miracle-noose:'))
        accepted=session.act('pilot:A',{'action_id':activation['id'],'cost_cards':[first.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',first.zone)
        self.resolve_top(session)
        noose=session.state.cards['fixture:miracle-noose']
        self.assertEqual((3,3),(session.engine._numeric_stat(noose.object_id,'power'),
                                session.engine._numeric_stat(noose.object_id,'toughness')))
        self.priority_for(session)
        accepted=session.act('pilot:A',{'action_id':'cast:miracle-reclaim','targets':[first.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve_top(session)
        self.assertEqual('library',first.zone)
        self.assertEqual(first_id,session.state.players['A'].zones['library'][-1])
        self.priority_for(session)
        accepted=session.act('pilot:A',{'action_id':'cast:miracle-think-twice','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve_top(session)
        self.assertEqual('hand',first.zone)
        self.assertEqual(original_counter+3,first.zone_change_counter)
        self.assertFalse(miracle_reveal_active(session.state,first))
        self.assertGreaterEqual(session.state.players['A'].mana_pool['R'],1)
        self.assertEqual(1,len(session.state.stack))
        decision=self.resolve(session)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual([],session.packet('pilot:A',full=True)['decision']['ctx']['cast_options'])
        self.reject(session,'choose',choice='cast',targets=['B'],pay='auto')
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','choice':'decline'}).ok)
        self.assertEqual('hand',first.zone)
        self.assertFalse(session.state.stack)
        self.replay(session)
