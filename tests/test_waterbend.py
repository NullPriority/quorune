from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json,tempfile,unittest
from common import ROOT
from quorune.waterbend import FixedWaterbendCost,WaterbendObject,WaterbendPayment
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record
import test_fixed_reveal_additional_costs as reveal


class WaterbendModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def test_fixed_waterbend_has_closed_identity_codecs_and_source_spanned_costs(self):
        payment=WaterbendPayment(FixedWaterbendCost(3),(WaterbendObject('id','REF','id@2'),))
        self.assertEqual(payment,WaterbendPayment.from_dict(json.loads(json.dumps(payment.to_dict()))))
        for text,kind in (('Waterbend {2}: This creature gets +1/+1 until end of turn.','Creature'),
                ('As an additional cost to cast this spell, waterbend {5}.\nDraw two cards.','Sorcery'),
                ('As an additional cost to cast this spell, waterbend {5}.\nFlying','Creature')):
            ir=compile_oracle_card(record(text,kind),capability_registry=self.registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            for node in ir.faces[0].nodes:self.assertEqual(node.text,text[node.span.start:node.span.end])
            self.assertTrue(any('cost.waterbend.fixed' in node.capability_dependencies for node in ir.faces[0].nodes))

    def test_unknown_variable_optional_and_malformed_waterbend_remain_closed(self):
        for text in ('Waterbend {X}: Draw a card.','Waterbend {2}{U}: Draw a card.',
                'As an additional cost to cast this spell, you may waterbend {2}.\nDraw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature' if ':' in text else 'Sorcery'),capability_registry=self.registry).status)
        for raw in ({'schema_version':True,'kind':'waterbend','generic':2},
                {'schema_version':1,'kind':'waterbend','generic':True},{'schema_version':1,'kind':'waterbend','generic':-1}):
            with self.assertRaises(ValueError):FixedWaterbendCost.from_dict(raw)
        with self.assertRaises(ValueError):WaterbendPayment(FixedWaterbendCost(1),(WaterbendObject('id','REF','id@1'),)*2)


class WaterbendRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=reveal.FixedRevealCostRuntimeTests.session
    add=reveal.FixedRevealCostRuntimeTests.add
    fund=reveal.FixedRevealCostRuntimeTests.fund
    checkpoint=reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve=reveal.FixedRevealCostRuntimeTests.resolve
    replay=reveal.FixedRevealCostRuntimeTests.replay
    reject=reveal.FixedRevealCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'waterbend.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/waterbend-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Waterbend witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_ruthless_tapped_payment_offered_committed_and_replayed(self):
        session=self.session(701670);source=self.add(session.engine,'Ruthless Waterbender',ref='actual-ruthless')
        body=self.add(session.engine,'Generic Bound Body',ref='waterbend-body')
        private=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='waterbend-private')
        self.fund(session,{})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next((a for a in actions if a['id'].startswith('activate:'+source.ref+':')),None)
        self.assertIsNotNone(action,'Two untapped creatures can pay this Waterbend2 ability')
        self.assertEqual({source.ref,body.ref},set(action['choice_schema']['waterbend_cards']['legal_refs']))
        response={'action_id':action['id'],'waterbend_cards':[source.ref,body.ref],'pay':'auto'}
        self.reject(session,'B',**response)
        result=session.act('pilot:A',response);self.assertTrue(result.ok,result.summary)
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (source,body)))
        self.assertEqual(1,sum(e.code=='cost.waterbend' for e in session.state.events))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        original=self.db.lookup(source.printed_name)
        self.assertEqual(int(original.power)+1,session.engine._numeric_stat(source.object_id,'power'))
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_mandatory_waterbend_spell_keeps_ordinary_mana_and_can_pay_in_mana(self):
        for count in (0,2):
            with self.subTest(tapped=count):
                session=self.session(701671+count);source=self.add(session.engine,'Generic Waterbend Draw',zone='hand',ref='waterbend-draw')
                bodies=[self.add(session.engine,'Generic Bound Body',ref='spell-waterbend-'+str(i)) for i in range(2)]
                self.fund(session,{'U':1,'C':3-count});hand=len(session.state.players['A'].zones['hand'])
                action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)
                option,=action['cost_options'];self.assertEqual(2,option['choice_schema']['waterbend_cards']['maximum'])
                result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[c.ref for c in bodies[:count]],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
                self.assertEqual(1,sum(e.code=='cost.waterbend' for e in session.state.events))
                self.replay(session,load=True)

    def test_paid_waterbend_triggers_once_for_mana_or_taps_and_uses_the_acting_player(self):
        for tapped in (False,True):
            with self.subTest(tapped=tapped):
                session=self.session(701674+tapped);source=self.add(session.engine,'Ruthless Waterbender',ref='event-ruthless')
                body=self.add(session.engine,'Generic Bound Body',ref='event-body')
                self.add(session.engine,'Generic Waterbend Observer',ref='own-waterbend-observer')
                self.add(session.engine,'Generic Waterbend Observer',seat='B',ref='other-waterbend-observer')
                self.fund(session,{} if tapped else {'C':2});hands={s:len(session.state.players[s].zones['hand']) for s in 'AB'}
                action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
                response={'action_id':action['id'],'pay':'auto','waterbend_cards':[source.ref,body.ref] if tapped else []}
                result=session.act('pilot:A',response);self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session));self.assertEqual(hands['A']+1,len(session.state.players['A'].zones['hand']))
                self.assertEqual(hands['B'],len(session.state.players['B'].zones['hand']));self.replay(session,load=True)

    def test_waterbend_rejects_wrong_objects_and_reserves_joint_mana_resources(self):
        session=self.session(701680);source=self.add(session.engine,'Ruthless Waterbender',ref='authority-source')
        body=self.add(session.engine,'Generic Bound Body',ref='authority-body')
        artifact=self.add(session.engine,'Sol Ring',ref='authority-artifact')
        foreign=self.add(session.engine,'Generic Bound Body',seat='B',ref='authority-foreign')
        tapped=self.add(session.engine,'Generic Bound Body',ref='authority-tapped');tapped.tapped=True
        phased=self.add(session.engine,'Generic Bound Body',ref='authority-phased');phased.phased_out=True
        wrong=self.add(session.engine,'Generic Bound Plains',ref='authority-land')
        self.fund(session,{})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        legal=set(action['choice_schema']['waterbend_cards']['legal_refs']);self.assertEqual({source.ref,body.ref,artifact.ref},legal)
        for refs in ([foreign.ref],[tapped.ref],[phased.ref],[wrong.ref],[body.ref,body.ref],['missing'],[source.ref,body.ref,artifact.ref]):
            self.reject(session,action_id=action['id'],waterbend_cards=refs,pay='auto')
        result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[body.ref,artifact.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_waterbend_taps_cannot_pay_the_spells_ordinary_generic_cost(self):
        session=self.session(701681);source=self.add(session.engine,'Generic Waterbend Draw',zone='hand',ref='capped-waterbend')
        bodies=[self.add(session.engine,'Generic Bound Body',ref='capped-body-'+str(i)) for i in range(3)]
        self.fund(session,{'U':1})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a.get('card')==source.ref for a in actions))
        self.reject(session,action_id='cast:'+source.ref,waterbend_cards=[c.ref for c in bodies],pay='auto')

    def test_source_tap_cost_cannot_share_its_object_with_waterbend(self):
        session=self.session(701682);source=self.add(session.engine,'North Pole Patrol',ref='tap-source-patrol')
        source.acquired_control_turn_count=-1
        bodies=[self.add(session.engine,'Generic Bound Body',ref='patrol-helper-'+str(i)) for i in range(3)]
        target=self.add(session.engine,'Generic Bound Body',seat='B',ref='patrol-enemy')
        self.fund(session,{})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':') and 'waterbend_cards' in a.get('choice_schema',{}))
        self.assertNotIn(source.ref,action['choice_schema']['waterbend_cards']['legal_refs'])
        response={'action_id':action['id'],'targets':[target.ref],'pay':'auto'}
        self.reject(session,**response,waterbend_cards=[source.ref,bodies[0].ref,bodies[1].ref])
        result=session.act('pilot:A',{**response,'waterbend_cards':[c.ref for c in bodies]});self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[target.object_id].tapped);self.replay(session,load=True)

    def test_pending_mana_replacement_preserves_waterbend_reservation_and_replays(self):
        self.pending_waterbend_replacement(activation=False)

    def test_activation_mana_replacement_preserves_partial_waterbend_payment_and_replays(self):
        self.pending_waterbend_replacement(activation=True)

    def pending_waterbend_replacement(self,*,activation):
        from quorune.damage_prevention_creation import PreventionShieldCreationRequest,PreventionSubjectAllocation,plan_prevention_shield_creation,commit_prevention_shield_creation
        from quorune.damage_modifier_state import PreventionMode,DamageModifierDuration
        session=self.session(701683+activation);source=self.add(session.engine,'Generic Colored Waterbend Activation' if activation else 'Generic Waterbend Draw',zone='battlefield' if activation else 'hand',ref='pending-waterbend')
        reef=self.add(session.engine,'Shivan Reef',ref='pending-reef')
        bodies=[self.add(session.engine,'Generic Bound Body',ref='pending-helper-'+str(i)) for i in range(2)]
        for label in ('one','two'):
            request=PreventionShieldCreationRequest(source_id='fixture:waterbend-prevent:'+label,controller='A',mode=PreventionMode.AMOUNT,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,subjects=(PreventionSubjectAllocation('A',1),),label='Prevent '+label)
            commit_prevention_shield_creation(session.engine,plan_prevention_shield_creation(session.engine,request))
        self.fund(session,{} if activation else {'C':1});hand=len(session.state.players['A'].zones['hand'])
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':')) if activation else {'id':'cast:'+source.ref}
        result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[c.ref for c in bodies],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertTrue(all(not session.state.cards[c.object_id].tapped for c in (*bodies,reef)))
        self.assertEqual('battlefield' if activation else 'hand',session.state.cards[source.object_id].zone)
        session=self.replay(session,load=True);ctx=session.packet('pilot:A',full=True)['decision']['ctx']
        result=session.act('pilot:A',{'action_id':'choose','replacement':ctx['options'][0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(hand if activation else hand+1,len(session.state.players['A'].zones['hand']))
        self.assertEqual(1,sum(e.code=='cost.waterbend' for e in session.state.events));self.replay(session,load=True)

    def test_all_eight_originals_have_complete_source_bindings(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        for name in ('Ruthless Waterbender','Benevolent River Spirit','Water Whip','Foggy Swamp Vinebender',
                'Flexible Waterbender','North Pole Patrol','Giant Koi','Geyser Leaper'):
            with self.subTest(name=name):
                program=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_vinebender_counter_and_flexible_base_stats_use_ordinary_resolution(self):
        for index,(name,amount) in enumerate((('Foggy Swamp Vinebender',5),('Flexible Waterbender',3))):
            with self.subTest(name=name):
                session=self.session(701690+index);source=self.add(session.engine,name,ref='result-waterbender')
                bodies=[self.add(session.engine,'Generic Bound Body',ref='result-helper-'+str(i)) for i in range(amount-1)]
                self.fund(session,{})
                action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
                result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[source.ref]+[c.ref for c in bodies],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));card=session.state.cards[source.object_id]
                self.assertEqual((5,4) if index==0 else (5,2),tuple(session.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
                if index==0:self.assertEqual(1,card.counters.get('+1/+1',0))
                else:self.assertIn('vigilance',session.engine._combat_keywords(card))
                self.replay(session,load=True)

    def test_river_spirit_keeps_flying_ward_and_private_scry_after_waterbend_cost(self):
        session=self.session(701692);source=self.add(session.engine,'Benevolent River Spirit',zone='hand',ref='river-waterbend')
        helpers=[self.add(session.engine,'Generic Bound Body',ref='river-helper-'+str(i)) for i in range(5)]
        self.fund(session,{'U':2})
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'waterbend_cards':[c.ref for c in helpers],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        card=session.state.cards[source.object_id];self.assertEqual('battlefield',card.zone)
        self.assertIn('flying',session.engine._combat_keywords(card))
        self.assertEqual((4,5),tuple(session.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
        ctx=session.packet('pilot:A',full=True)['decision']['ctx'];refs=ctx['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual(2,len(refs))
        for viewer in 'BCD':
            packet=json.dumps(session.packet('pilot:'+viewer,full=True));self.assertTrue(all(ref not in packet for ref in refs))
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'choose','cards':{'top':refs,'bottom':[]}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_water_whip_returns_both_selected_creatures_then_draws_two(self):
        session=self.session(701693);source=self.add(session.engine,'Water Whip',zone='hand',ref='water-whip')
        helpers=[self.add(session.engine,'Generic Bound Body',ref='whip-helper-'+str(i)) for i in range(5)]
        victims=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='whip-target-'+seat) for seat in 'BC']
        self.fund(session,{'U':2});hand=len(session.state.players['A'].zones['hand'])
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'targets':[c.ref for c in victims],
            'waterbend_cards':[c.ref for c in helpers],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in victims));self.replay(session,load=True)

    def test_geyser_loot_keeps_a_private_discard_choice_after_payment(self):
        session=self.session(701694);source=self.add(session.engine,'Geyser Leaper',ref='geyser-waterbend')
        helpers=[self.add(session.engine,'Generic Bound Body',ref='geyser-helper-'+str(i)) for i in range(3)]
        private=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='geyser-private')
        self.fund(session,{});hand=len(session.state.players['A'].zones['hand'])
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[source.ref]+[c.ref for c in helpers],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual('choice.apnap',self.resolve(session).kind)
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        session=self.replay(session,load=True);self.reject(session,'B',action_id='choose',cards=[private.ref])
        result=session.act('pilot:A',{'action_id':'choose','cards':[private.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(hand,len(session.state.players['A'].zones['hand']));self.replay(session,load=True)

    def test_legal_waterbend_offer_preserves_the_colored_mana_source(self):
        session=self.session(701695);source=self.add(session.engine,'Generic Waterbend Draw',zone='hand',ref='joint-waterbend')
        bird=self.add(session.engine,'Birds of Paradise',ref='aaa-colored-mana');bird.acquired_control_turn_count=-1
        bodies=[self.add(session.engine,'Generic Bound Body',ref='zzz-waterbend-'+str(i)) for i in range(2)]
        self.fund(session,{'C':1})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)
        self.reject(session,action_id=action['id'],waterbend_cards=[bird.ref,bodies[0].ref],pay='auto')
        result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[c.ref for c in bodies],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[bird.object_id].tapped)
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_total_cost_reduction_reduces_waterbend_mana_without_creating_extra_tap_capacity(self):
        session=self.session(701696);source=self.add(session.engine,'Generic Waterbend Draw',zone='hand',ref='reduced-waterbend')
        self.add(session.engine,'Generic Casting Reveal Reducer',ref='waterbend-reducer')
        self.fund(session,{'U':1});hand=len(session.state.players['A'].zones['hand'])
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)
        option,=action['cost_options'];self.assertEqual(0,option['requirements']['GENERIC'])
        self.assertEqual(0,option['choice_schema']['waterbend_cards']['maximum'])
        result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']));self.replay(session,load=True)

    def test_countering_a_waterbend_spell_keeps_paid_taps_without_a_draw(self):
        session=self.session(701697);source=self.add(session.engine,'Generic Waterbend Draw',zone='hand',ref='countered-waterbend')
        helpers=[self.add(session.engine,'Generic Bound Body',ref='countered-helper-'+str(i)) for i in range(2)]
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='waterbend-counterspell')
        session.state.players['B'].mana_pool.update({'U':2});self.fund(session,{'U':1,'C':1})
        hand=len(session.state.players['A'].zones['hand'])
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'waterbend_cards':[c.ref for c in helpers],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);spell=session.state.stack[-1];session=self.replay(session,load=True)
        self.assertTrue(session.act('pilot:A',{'action_id':'pass'}).ok)
        result=session.act('pilot:B',{'action_id':'cast:'+counter.ref,'targets':[spell.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in helpers));self.replay(session,load=True)

    def test_waterbend_dependency_producer_payment_and_occurrence_mutants_are_killed(self):
        from unittest.mock import patch
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        with patch('quorune.compiler.spell_additional_cost_nodes.fixed_waterbend_additional_cost_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record('As an additional cost to cast this spell, waterbend {2}.\nDraw two cards.','Sorcery'),capability_registry=self.registry).status)
        for dependency,text,kind in (('cost.waterbend.fixed','Waterbend {2}: Draw a card.','Creature'),
                ('casting.payment.improvise','Waterbend {2}: Draw a card.','Creature'),
                ('permanent.tap_state.fixed_set','Waterbend {2}: Draw a card.','Creature'),
                ('trigger.placement.apnap','Waterbend {2}: Draw a card.','Creature'),
                ('casting.additional_cost.waterbend_result_composition','As an additional cost to cast this spell, waterbend {2}.\nDraw two cards.','Sorcery')):
            changed=json.loads(json.dumps(raw));next(c for c in changed['capabilities'] if c['id']==dependency).update(status='blocked',blockers=['Waterbend dependency omitted'])
            self.assertNotEqual('exact',compile_oracle_card(record(text,kind),capability_registry=CapabilityRegistry(changed)).status)
        method='test_actual_ruthless_tapped_payment_offered_committed_and_replayed'
        with patch('quorune.rules.waterbend_payments.tap_waterbend_objects',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_paid_waterbend_triggers_once_for_mana_or_taps_and_uses_the_acting_player'
        with patch('quorune.rules.waterbend_payments.dispatch_waterbend_occurrence',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_water_whip_returns_both_selected_creatures_then_draws_two'
        with patch('quorune.compiler.waterbend_costs.closed_waterbend_spell_result',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_payment_rechecks_selected_incarnations_after_mana_activations(self):
        session=self.session(701698);source=self.add(session.engine,'Ruthless Waterbender',ref='post-mana-ruthless')
        body=self.add(session.engine,'Generic Bound Body',ref='post-mana-helper');self.fund(session,{'C':1})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        original=session.engine._pay_for_cost
        def pay_then_substitute(*args,**kwargs):
            result=original(*args,**kwargs);session.state.cards[body.object_id].zone_change_counter+=1;return result
        session.engine._pay_for_cost=pay_then_substitute
        self.reject(session,action_id=action['id'],waterbend_cards=[body.ref],pay='auto')

    def test_internal_waterbend_payment_cannot_be_forged_by_a_pilot(self):
        session=self.session(701699);source=self.add(session.engine,'Ruthless Waterbender',ref='forged-waterbend')
        self.fund(session,{'C':2})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        self.reject(session,action_id=action['id'],pay='auto',_waterbend_payment={'schema_version':1,'objects':[]})

    def test_original_waterbend_block_restrictions_use_actual_combat_legality(self):
        from quorune.model import CombatState
        for index,name in enumerate(('Giant Koi','Foggy Swamp Vinebender')):
            with self.subTest(name=name):
                session=self.session(701700+index);source=self.add(session.engine,name,ref='waterbend-attacker')
                blocked=self.add(session.engine,'Generic Bound Body',seat='B',ref='low-power-blocker')
                if index:self.add(session.engine,'Flexible Waterbender',seat='B',ref='legal-greater-blocker').counters['+1/+1']=1
                self.fund(session,{'C':3 if not index else 5})
                action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
                result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                current=session.state.cards[source.object_id];current.acquired_control_turn_count=-1
                engine=session.engine;engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.started=True;engine.state.active_player='A';engine.state.phase='combat';engine.state.step='declare_attackers'
                engine.state.phase_index=5;engine.state.combat=CombatState();engine._issue_attackers();self.checkpoint(session)
                life=session.state.players['B'].life
                result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
                for _ in range(32):
                    decision=session.state.pending_decision
                    if decision.kind=='combat.blockers':
                        self.reject(session,'B',action_id='block',blocks={blocked.ref:source.ref})
                        result=session.act('pilot:B',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
                    elif session.state.phase!='combat':break
                    else:self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                self.assertEqual(life-5,session.state.players['B'].life)
                self.replay(session,load=True)

    def test_giant_koi_islandcycling_and_patrol_untap_keep_their_independent_costs(self):
        session=self.session(701702);source=self.add(session.engine,'Giant Koi',zone='hand',ref='cycled-koi')
        island=self.add(session.engine,'Island',zone='library',ref='koi-private-island');self.fund(session,{'C':2})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        self.assertNotIn('waterbend_cards',action.get('choice_schema',{}))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertIsNotNone(self.resolve(session))
        result=session.act('pilot:A',{'action_id':'choose','search_card':island.ref});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('hand',session.state.cards[island.object_id].zone)
        self.assertFalse(any(e.code=='cost.waterbend' for e in session.state.events));self.replay(session,load=True)

        session=self.session(701703);patrol=self.add(session.engine,'North Pole Patrol',ref='ordinary-patrol');patrol.acquired_control_turn_count=-1
        body=self.add(session.engine,'Generic Bound Body',ref='patrol-untap-target');body.tapped=True;self.fund(session,{})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+patrol.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertFalse(session.state.cards[body.object_id].tapped)
        self.assertFalse(any(e.code=='cost.waterbend' for e in session.state.events));self.replay(session,load=True)

    def test_free_base_cast_still_requires_the_waterbend_additional_price(self):
        from quorune.rules.casting.costs import build_cast_cost_options
        from quorune.rules.casting.proposal import _spell_semantic_key
        session=self.session(701704);source=self.add(session.engine,'Generic Waterbend Draw',zone='hand',ref='free-waterbend-base')
        self.fund(session,{'C':1})
        program=session.engine.semantics.get(_spell_semantic_key(self.db.lookup(source.printed_name),None))
        options=build_cast_cost_options(session.engine,'A',source,program,hint=True,force_without_mana_cost=True)
        self.assertFalse(options,'One generic mana cannot pay the additional Waterbend2')
        body=self.add(session.engine,'Generic Bound Body',ref='free-price-helper')
        options=build_cast_cost_options(session.engine,'A',source,program,hint=True,force_without_mana_cost=True)
        self.assertEqual(1,len(options));value=options[0].to_dict()
        self.assertEqual(1,value['requirements']['GENERIC']);self.assertEqual(0,value['requirements']['U'])
