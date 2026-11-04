"""Creation and entry differ; cardinality and first-event time stay explicit."""
import unittest
from pathlib import Path
import tempfile
from dataclasses import FrozenInstanceError

from quorune.token_creation_batch_model import TokenCreationBatchFact
from quorune.compiler.token_creation_event_bindings import token_creation_event_binding_spec


class TokenCreationFactTests(unittest.TestCase):
    def test_erasing_first_event_history_is_killed_by_the_independent_quantity_table(self):
        from unittest.mock import patch,PropertyMock
        result=unittest.TestResult()
        with patch.object(TokenCreationBatchFact,'first_creation_this_turn',new_callable=PropertyMock,return_value=False):
            TokenCreationFactTests('test_complete_instruction_quantities_preserve_first_event_and_known_zero').run(result)
        self.assertGreater(len(result.failures),0);self.assertEqual([],result.errors)

    def test_complete_instruction_quantities_preserve_first_event_and_known_zero(self):
        for quantity,creatures,previous,first in ((2,2,0,True),(3,1,4,False),(0,0,0,False),(1,0,0,True)):
            with self.subTest(quantity=quantity,creatures=creatures,previous=previous):
                fact=TokenCreationBatchFact('B',quantity,creatures,previous)
                self.assertEqual(first,fact.first_creation_this_turn)
                self.assertEqual({'creator':'B','quantity':quantity,'creature_quantity':creatures,
                    'previous_quantity':previous,'first_creation_this_turn':first},fact.to_context())
                with self.assertRaises(FrozenInstanceError):fact.quantity=4

    def test_unknown_boolean_negative_or_inconsistent_creation_facts_reject(self):
        for values in (('',1,1,0),('A',True,1,0),('A',2,3,0),('A',1,0,-1),('A',1,False,0)):
            with self.subTest(values=values),self.assertRaises(ValueError):TokenCreationBatchFact(*values)


class TokenCreationBindingTests(unittest.TestCase):
    def test_creation_capability_withdrawal_and_removed_binding_producer_leave_residuals(self):
        import json
        from common import ROOT
        from unittest.mock import patch
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.compiler import token_creation_event_bindings
        from test_qualified_zone_event_queries import query_record
        record=query_record('Whenever you create one or more tokens, draw a card.')
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review').status)
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(row for row in value['capabilities'] if row['id']=='trigger.event.token_creation')
        row.update(status='blocked',blockers=['Token creation event owner deliberately withdrawn'])
        missing=CapabilityRegistry(value);missing.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=missing,capability_profile='commander_review').status)
        with patch.object(token_creation_event_bindings,'token_creation_event_binding_spec',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review').status)

    def test_counter_target_self_exclusion_uses_the_source_reference_and_rejects_another_name(self):
        from quorune.compiler.counter_placement_templates import fixed_counter_placement_effect_template
        from quorune.rules.source_references import SourceReferenceSpec
        name='Ava of Meadow';self.assertTrue(SourceReferenceSpec(name).matches('Ava'))
        for reference in ('Ava','Ava of Meadow'):
            result=fixed_counter_placement_effect_template('Put a +1/+1 counter on target creature you control other than '+reference+'.',card_name=name)
            self.assertIsNotNone(result)
            self.assertTrue(result.target_spec.source_exclusion)
        self.assertIsNone(fixed_counter_placement_effect_template('Put a +1/+1 counter on target creature you control other than Lena.',card_name=name))

    def test_individual_batch_creature_and_first_event_heads_keep_distinct_scopes(self):
        for head,event,variant in (
                ('Whenever you create a token','token.created','controller_creates_token'),
                ('Whenever you create a Blood token','token.created','controller_creates_blood_token'),
                ('Whenever you create one or more tokens','tokens.created','controller_creates_one_or_more_tokens'),
                ('Whenever you create one or more creature tokens','tokens.created','controller_creates_one_or_more_creature_tokens'),
                ('Whenever you create one or more tokens for the first time each turn','tokens.created','controller_creates_first_one_or_more_tokens')):
            with self.subTest(head=head):
                binding=token_creation_event_binding_spec(head+', draw a card.')
                self.assertEqual((event,variant,'draw a card.'),(binding.event,binding.variant,binding.body))
                self.assertIn('trigger.event.token_creation',binding.capabilities)
        first=token_creation_event_binding_spec('Whenever you create one or more tokens for the first time each turn, draw a card.')
        self.assertIn({'field':'first_creation_this_turn','op':'eq','value':True},first.condition['all'])
        self.assertNotIn('trigger_limit',first.condition)

    def test_create_or_sacrifice_is_one_ability_with_independent_creator_and_controller_predicates(self):
        from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
        binding=token_creation_event_binding_spec('Whenever you create or sacrifice a token, each opponent loses 1 life.')
        subscriptions=FixedEventSubscriptionSet.from_condition(binding.condition)
        self.assertEqual(('token.created','permanent.sacrificed'),tuple(row.event for row in subscriptions.subscriptions))
        created=subscriptions.subscription_for('token.created')
        self.assertEqual({'field':'owner','op':'eq','value':'$source.controller'},dict(created.condition))
        sacrificed=subscriptions.subscription_for('permanent.sacrificed')
        self.assertEqual('previous_controller',sacrificed.condition['all'][0]['field'])
        self.assertIn('trigger.subscription.fixed_multi_event',binding.capabilities)

    def test_entry_copy_and_unknown_creation_predicates_do_not_alias_to_supported_heads(self):
        for text in ('Whenever a token enters under your control, draw a card.',
                'Whenever you copy a permanent spell, draw a card.',
                'Whenever you create two or more tokens, draw a card.',
                'Whenever you create a token for the first time each turn, draw a card.',
                'Whenever an opponent creates a token, draw a card.',
                'Whenever you create one or more creature tokens for the first time each turn, draw a card.'):
            with self.subTest(text=text):self.assertIsNone(token_creation_event_binding_spec(text))


class TokenCreationTriggerNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'creation-trigger.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-view-equivalence-cards.json',
            ROOT/'tests/fixtures/additional-token-definition-cards.json',
            ROOT/'tests/fixtures/double-faced-spell-copy-cards.json',
            ROOT/'tests/fixtures/state-based-destination-cards.json',
            ROOT/'tests/fixtures/token-creation-trigger-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Creation triggers',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def session(self,seed,actor='A'):
        from copy import deepcopy
        from common import keep_all
        from quorune.model import GameConfig
        from quorune.session import CommanderSession
        session=CommanderSession.create(self.db,{seat:deepcopy(self.deck) for seat in 'ABCD'},first_player=actor,
            seed=seed,config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        keep_all(session);return session

    def assert_closed(self,session,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])

    def start(self,session,actor='A'):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.turn_step_owner import TURN_STEPS
        engine=session.engine;engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player=actor;engine.state.started=True
        engine.state.phase='precombat_main';engine.state.step='main';engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'))
        engine.state.players[actor].mana_pool.update({'C':30,'W':10,'U':4,'R':4,'B':4})
        H.add(self,engine,'Generic Bound Growth',seat='D',zone='hand',ref='CREATION_PRIVATE_D_HAND')
        engine._grant_priority(actor);engine.pump();H.checkpoint(self,session)

    def cast(self,session,card,targets=(),actor='A'):
        from quorune.record import authoritative_state_hash
        offered=next(row for row in session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions']
            if row.get('card')==card.ref)
        command={'action_id':offered['id'],'targets':list(targets),'pay':'auto'}
        before=authoritative_state_hash(session.engine.state)
        self.assertFalse(session.act('pilot:'+('B' if actor=='A' else 'A'),command).ok);self.assertEqual(before,authoritative_state_hash(session.engine.state))
        accepted=session.act('pilot:'+actor,command);self.assertTrue(accepted.ok,accepted.summary)

    def tokens(self,session):
        return tuple(card for card in session.engine.state.cards.values() if card.is_token and card.zone=='battlefield')

    def settle(self,session,*,rosie=None):
        from quorune.record import authoritative_state_hash
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        chosen=[];loaded=False
        for _ in range(180):
            engine=session.engine;engine.pump();decision=engine.state.pending_decision
            if decision is None:
                if engine.state.game_over:return session,chosen
                self.fail('Creation journey lost its pending decision')
            principal=session.pending_principals()[0];ctx=session.packet(principal,full=True)['decision']['ctx']
            if decision.kind=='priority':
                if not engine.state.stack:return session,chosen
                command={'action_id':'pass'}
            elif decision.kind=='trigger.order':command={'action_id':'order','triggers':[row['id'] for row in ctx['triggers']]}
            elif decision.kind=='semantic.target':
                self.assertIsNotNone(rosie)
                legal=ctx['target_schema']['legal_refs'];self.assertNotIn(rosie.ref,legal)
                candidates=[card.ref for card in self.tokens(session) if card.ref in legal and card.ref not in chosen]
                self.assertTrue(candidates,'Every creation target must see the completed new token group')
                before=authoritative_state_hash(engine.state)
                self.assertFalse(session.act('pilot:B',{'action_id':'choose','targets':[candidates[0]]}).ok)
                self.assertFalse(session.act(principal,{'action_id':'choose','targets':[rosie.ref]}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                if not loaded:
                    session=H.replay(self,session,load=True);loaded=True;continue
                command={'action_id':'choose','targets':[candidates[0]]};chosen.append(candidates[0])
            elif decision.kind=='replacement.order':command={'action_id':'choose','replacement':ctx['options'][0]['id']}
            else:self.fail('Unexpected creation journey decision '+decision.kind)
            accepted=session.act(principal,command);self.assertTrue(accepted.ok,accepted.summary)
        self.fail('Creation journey did not settle')

    def finish(self,session):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for seat in 'ABC':self.assertNotIn('CREATION_PRIVATE_D_HAND',str(session.packet('pilot:'+seat,full=True)))
        H.replay(self,session,load=True)

    def test_actual_multiplied_creation_targets_all_new_tokens_and_counts_per_token_vs_batch(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(964100);engine=session.engine
        bats=H.add(self,engine,'Mirkwood Bats',ref='CREATION_BATS')
        staff=H.add(self,engine,'Staff of the Storyteller',ref='CREATION_STAFF')
        rosie=H.add(self,engine,'Rosie Cotton of South Lane',ref='CREATION_ROSIE')
        doubler=H.add(self,engine,'Anointed Procession',ref='CREATION_DOUBLER')
        spell=H.add(self,engine,'Raise the Alarm',zone='hand',ref='CREATION_RAISE')
        for card in (bats,staff,rosie,doubler,spell):self.assert_closed(session,card)
        self.start(session);self.cast(session,spell)
        from unittest.mock import patch
        from quorune.game_object_store import GameObjectStore
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        for _ in range(3):
            passed=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(passed.ok,passed.summary)
        self.assertEqual(['pilot:D'],list(session.pending_principals()))
        before=authoritative_state_hash(engine.state);inserted=[];original=GameObjectStore.commit
        def fail_after_registration(store,*args,**kwargs):
            result=original(store,*args,**kwargs)
            if any(card.is_token and card.zone=='battlefield' for card in kwargs.get('upserts',())):
                inserted.append(True);raise GameRuleError('Injected creation trigger registration fault')
            return result
        with patch.object(GameObjectStore,'commit',fail_after_registration):
            rejected=session.act('pilot:D',{'action_id':'pass'})
        self.assertFalse(rejected.ok);self.assertEqual([True],inserted)
        self.assertIn('Injected creation trigger registration fault',rejected.summary)
        self.assertEqual(before,authoritative_state_hash(engine.state));self.assertEqual(0,len(self.tokens(session)))
        accepted=session.act('pilot:D',{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        session,chosen=self.settle(session,rosie=rosie);engine=session.engine
        tokens=self.tokens(session);self.assertEqual(4,len(tokens));self.assertEqual(4,len(chosen))
        self.assertTrue(all(card.counters.get('+1/+1',0)==1 for card in tokens))
        self.assertEqual(1,engine.state.cards[staff.object_id].counters.get('story',0))
        self.assertEqual((40,36,36,36),tuple(engine.state.players[p].life for p in 'ABCD'))
        self.finish(session)

    def test_actual_first_creation_triggers_once_and_its_bird_does_not_retrigger(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(964101);engine=session.engine
        akim=H.add(self,engine,'Akim, the Soaring Wind',ref='FIRST_CREATION_AKIM')
        staff=H.add(self,engine,'Staff of the Storyteller',ref='FIRST_CREATION_STAFF')
        spells=[H.add(self,engine,'Raise the Alarm',zone='hand',ref='FIRST_CREATION_RAISE_'+str(i)) for i in range(2)]
        for card in (akim,staff,*spells):self.assert_closed(session,card)
        self.start(session)
        for spell,expected,story in zip(spells,(3,5),(2,3)):
            self.cast(session,spell);session,_=self.settle(session);engine=session.engine
            self.assertEqual(expected,len(self.tokens(session)))
            birds=[card for card in self.tokens(session) if 'bird' in engine._type_parts(str(engine._effective_card_data(card).get('type_line') or ''))[1]]
            self.assertEqual(1,len(birds))
            self.assertEqual(story,engine.state.cards[staff.object_id].counters.get('story',0))
        self.finish(session)

    def test_actual_source_arriving_after_an_earlier_creation_does_not_reset_first_event(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(964102);engine=session.engine
        akim=H.add(self,engine,'Akim, the Soaring Wind',zone='hand',ref='LATE_CREATION_AKIM')
        spells=[H.add(self,engine,'Raise the Alarm',zone='hand',ref='LATE_CREATION_RAISE_'+str(i)) for i in range(2)]
        for card in (akim,*spells):self.assert_closed(session,card)
        self.start(session);self.cast(session,spells[0]);session,_=self.settle(session)
        self.cast(session,akim);session,_=self.settle(session)
        self.cast(session,spells[1]);session,_=self.settle(session)
        self.assertEqual(4,len(self.tokens(session)))
        self.finish(session)

    def test_actual_copied_permanent_spell_becomes_a_token_without_a_creation_trigger(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(964103);engine=session.engine
        bats=H.add(self,engine,'Mirkwood Bats',ref='COPY_NOT_CREATED_BATS')
        staff=H.add(self,engine,'Staff of the Storyteller',ref='COPY_NOT_CREATED_STAFF')
        observer=H.add(self,engine,'Generic Complete Creature Copy Observer',ref='COPY_NOT_CREATED_OBSERVER')
        spell=H.add(self,engine,'Ornithopter',zone='hand',ref='COPY_NOT_CREATED_SPELL')
        for card in (bats,staff,observer,spell):self.assert_closed(session,card)
        self.start(session);self.cast(session,spell);session,_=self.settle(session);engine=session.engine
        self.assertEqual(1,len(self.tokens(session)));self.assertEqual('battlefield',engine.state.cards[spell.object_id].zone)
        self.assertEqual(0,engine.state.cards[staff.object_id].counters.get('story',0))
        self.assertEqual((40,40,40,40),tuple(engine.state.players[p].life for p in 'ABCD'))
        self.assertEqual(0,engine.state.players['A'].stats.get('tokens_created_by_turn',{}).get(str(engine.state.turn_sequence),0))
        self.finish(session)

    def test_actual_token_sacrifice_cost_triggers_bats_and_token_death_does_not(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(964104);engine=session.engine
        bats=H.add(self,engine,'Mirkwood Bats',ref='SACRIFICE_CREATION_BATS')
        inspector=H.add(self,engine,'Thraben Inspector',zone='hand',ref='SACRIFICE_CLUE_INSPECTOR')
        raise_spell=H.add(self,engine,'Raise the Alarm',zone='hand',ref='SACRIFICE_CREATION_RAISE')
        murder=H.add(self,engine,'Murder',zone='hand',ref='SACRIFICE_CREATION_MURDER')
        for card in (bats,inspector,raise_spell,murder):self.assert_closed(session,card)
        self.start(session);self.cast(session,inspector);session,_=self.settle(session);engine=session.engine
        self.assertEqual((40,39,39,39),tuple(engine.state.players[p].life for p in 'ABCD'))
        clue=next(card for card in self.tokens(session) if 'clue' in engine._type_parts(str(engine._effective_card_data(card).get('type_line') or ''))[1])
        ability=next(a for a in engine._activated_abilities(clue) if a.sacrifice_source)
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('source')==clue.ref and row.get('ability')==ability.ability_id)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('outside',engine.state.cards[clue.object_id].zone)
        session,_=self.settle(session);engine=session.engine
        self.assertEqual((40,38,38,38),tuple(engine.state.players[p].life for p in 'ABCD'))
        self.cast(session,raise_spell);session,_=self.settle(session);engine=session.engine
        self.assertEqual((40,36,36,36),tuple(engine.state.players[p].life for p in 'ABCD'))
        token=self.tokens(session)[0];self.cast(session,murder,(token.ref,));session,_=self.settle(session);engine=session.engine
        self.assertEqual('outside',engine.state.cards[token.object_id].zone)
        self.assertEqual((40,36,36,36),tuple(engine.state.players[p].life for p in 'ABCD'))
        self.finish(session)

    def test_actual_additional_mixed_token_result_filters_after_the_complete_creation(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(964105);engine=session.engine
        staff=H.add(self,engine,'Staff of the Storyteller',ref='MIXED_CREATION_STAFF')
        replacer=H.add(self,engine,'Peregrin Took',ref='MIXED_CREATION_REPLACER')
        spell=H.add(self,engine,'Raise the Alarm',zone='hand',ref='MIXED_CREATION_RAISE')
        rich=H.add(self,engine,'Strike It Rich',zone='hand',ref='MIXED_CREATION_RICH')
        for card in (staff,replacer,spell,rich):self.assert_closed(session,card)
        self.start(session);self.cast(session,spell);session,_=self.settle(session);engine=session.engine
        tokens=self.tokens(session);self.assertEqual(3,len(tokens))
        types=[engine._type_parts(str(engine._effective_card_data(card).get('type_line') or ''))[0] for card in tokens]
        self.assertEqual(2,sum('creature' in value for value in types))
        self.assertEqual(1,engine.state.cards[staff.object_id].counters.get('story',0))
        self.cast(session,rich);session,_=self.settle(session);engine=session.engine
        self.assertEqual(5,len(self.tokens(session)))
        self.assertEqual(1,engine.state.cards[staff.object_id].counters.get('story',0),
            'A later Treasure plus Food event has no created creature')
        self.finish(session)

    def test_actual_changed_control_uses_creator_and_current_controller_instead_of_card_owner(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(964106,actor='B');engine=session.engine
        bats=H.add(self,engine,'Mirkwood Bats',ref='CONTROLLED_CREATION_BATS')
        control=H.add(self,engine,'Control Magic',seat='B',zone='hand',ref='CONTROLLED_CREATION_AURA')
        own=H.add(self,engine,'Raise the Alarm',seat='A',zone='hand',ref='OWNER_CREATION_RAISE')
        foreign=H.add(self,engine,'Raise the Alarm',seat='B',zone='hand',ref='CONTROLLER_CREATION_RAISE')
        for card in (bats,control,own,foreign):self.assert_closed(session,card)
        engine.state.players['A'].mana_pool.update({'C':2,'W':2});self.start(session,actor='B')
        self.cast(session,control,(bats.ref,),actor='B');session,_=self.settle(session);engine=session.engine
        self.assertEqual(('A','B'),(engine.state.cards[bats.object_id].owner,engine.state.cards[bats.object_id].controller))
        self.cast(session,foreign,actor='B');session,_=self.settle(session);engine=session.engine
        self.assertEqual((38,40,38,38),tuple(engine.state.players[p].life for p in 'ABCD'))
        for _ in range(4):
            if session.pending_principals()==['pilot:A']:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        self.cast(session,own);session,_=self.settle(session);engine=session.engine
        self.assertEqual((38,40,38,38),tuple(engine.state.players[p].life for p in 'ABCD'))
        self.finish(session)
