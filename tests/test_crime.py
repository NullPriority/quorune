"""Crimes classify completed initial action targets, not later resolutions."""
import unittest
from pathlib import Path
import tempfile
from dataclasses import FrozenInstanceError

from quorune.crime_model import CrimeTarget,classify_crime
from quorune.model import TurnHistory,TurnHistoryEvent


class CrimeClassificationTests(unittest.TestCase):
    def classify(self,targets,kind='spell'):
        return classify_crime(actor='A',stack_identity='stack-1',stack_ref='S1',action_kind=kind,
            targets=tuple(targets),participants=tuple('ABCD'))

    def test_opponent_target_categories_use_current_control_and_graveyard_membership(self):
        cases=(('B','player','B',None,True),('A','player','A',None,False),
            ('P1','permanent','B','battlefield',True),('P2','permanent','A','battlefield',False),
            ('S2','spell','B',None,True),('S3','ability','C',None,True),
            ('G1','card','B','graveyard',True),('G2','card','A','graveyard',False),
            ('H1','card','B','hand',False),('E1','card','B','exile',False))
        for ref,category,principal,zone,expected in cases:
            with self.subTest(category=category,principal=principal,zone=zone):
                self.assertEqual(expected,self.classify((CrimeTarget(ref,category,principal,zone),)) is not None)
        borrowed=CrimeTarget.from_target_snapshot({'ref':'BORROWED','category':'permanent','owner':'A','controller':'B','zone':'battlefield'})
        owned=CrimeTarget.from_target_snapshot({'ref':'OWN_CONTROL','category':'permanent','owner':'B','controller':'A','zone':'battlefield'})
        self.assertIsNotNone(self.classify((borrowed,)));self.assertIsNone(self.classify((owned,)))

    def test_multiple_qualifying_targets_are_one_immutable_occurrence(self):
        targets=(CrimeTarget('B','player','B'),CrimeTarget('P1','permanent','C','battlefield'),
            CrimeTarget('G1','card','D','graveyard'))
        fact=self.classify(targets)
        self.assertEqual(3,fact.to_context()['qualifying_target_count'])
        self.assertEqual('A',fact.to_context()['player'])
        self.assertEqual('stack-1',fact.to_context()['stack_identity'])
        with self.assertRaises(FrozenInstanceError):fact.player='B'
        with self.assertRaises(ValueError):self.classify((targets[0],targets[0]))

    def test_spell_and_activated_copies_differ_from_triggered_ability_stack_placement(self):
        targets=(CrimeTarget('B','player','B'),)
        for kind in ('spell','activated_ability','triggered_ability','triggered_ability_copy'):
            with self.subTest(kind=kind):self.assertIsNotNone(self.classify(targets,kind))
        for kind in ('spell_copy','activated_ability_copy'):
            with self.subTest(kind=kind):self.assertIsNone(self.classify(targets,kind))

    def test_unknown_malformed_or_ambiguous_target_roles_reject(self):
        with self.assertRaises(ValueError):self.classify((CrimeTarget('Z','player','Z'),))
        for values in (('P','permanent','B',None),('P','card','B','battlefield'),('P','unknown','B',None),('P','player',True,None)):
            with self.subTest(values=values),self.assertRaises(ValueError):CrimeTarget(*values)
        with self.assertRaises(ValueError):self.classify((),kind='ability_copy')
        self.assertIsNone(self.classify(()))


class CrimeHistoryTests(unittest.TestCase):
    def test_manifest_mode_pins_complete_history_and_rejects_disagreement(self):
        from types import SimpleNamespace
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        state=SimpleNamespace(commander_damage_identity_version=None,control_history_version=None,
            tap_state_event_version=None,turn_history=TurnHistory(crime_history_version=1))
        markers=format_state_versions(state);self.assertEqual(1,markers['crime_history_version'])
        manifest={'format':markers};validate_state_versions(manifest,state)
        for wrong in (None,True,0,2,'1'):
            with self.subTest(wrong=wrong),self.assertRaises(ValueError):
                validate_state_versions({'format':{**markers,'crime_history_version':wrong}},state)
        state.turn_history=TurnHistory()
        legacy=format_state_versions(state);self.assertNotIn('crime_history_version',legacy)
        validate_state_versions({'format':legacy},state)

    def test_history_absence_stays_absent_and_new_mode_round_trips(self):
        from quorune.turn_history import roll_turn_history,reset_turn_history
        legacy=TurnHistory();self.assertNotIn('crime_history_version',legacy.to_dict())
        self.assertEqual(legacy.to_dict(),TurnHistory.from_dict(legacy.to_dict()).to_dict())
        event=TurnHistoryEvent(kind='crime_committed',actor='A',target='stack-1',target_kind='stack_object',amount=1)
        current=TurnHistory(turn_sequence=2,crime_history_version=1,events=[event])
        self.assertEqual(current,TurnHistory.from_dict(current.to_dict()))
        self.assertEqual(1,reset_turn_history(current,3).crime_history_version)
        rolled=roll_turn_history(current,next_turn_sequence=3,previous_active_player='A')
        self.assertEqual(1,rolled.crime_history_version);self.assertEqual([],rolled.events)
        for version in (True,0,2,'1'):
            with self.subTest(version=version),self.assertRaises(ValueError):TurnHistory(crime_history_version=version)
        with self.assertRaises(ValueError):TurnHistory(events=[event])

    def test_known_empty_and_incomplete_history_remain_distinct(self):
        from types import SimpleNamespace
        from quorune.rules.crime import crime_count_this_turn
        state=SimpleNamespace(turn_sequence=2,turn_history=TurnHistory(turn_sequence=2))
        self.assertIsNone(crime_count_this_turn(state,'A'))
        state.turn_history=TurnHistory(turn_sequence=2,crime_history_version=1)
        self.assertEqual(0,crime_count_this_turn(state,'A'))
        state.turn_history.events.append(TurnHistoryEvent(kind='crime_committed',actor='A',target='S1',target_kind='stack_object',amount=1))
        self.assertEqual(1,crime_count_this_turn(state,'A'));self.assertEqual(0,crime_count_this_turn(state,'B'))
        state.turn_sequence=3;self.assertIsNone(crime_count_this_turn(state,'A'))


class CrimeBindingTests(unittest.TestCase):
    def test_actor_turn_and_graveyard_scope_remain_independent_from_result_bodies(self):
        from quorune.compiler.crime_bindings import crime_event_binding_spec
        own=crime_event_binding_spec('Whenever you commit a crime, draw a card.')
        self.assertEqual(('player.committed_crime','battlefield'),(own.event,own.active_zone))
        self.assertEqual({'field':'player','op':'eq','value':'$source.controller'},own.condition)
        other=crime_event_binding_spec('Whenever an opponent commits a crime, proliferate.')
        self.assertEqual('ne',other.condition['op'])
        turn=crime_event_binding_spec('Whenever you commit a crime during your turn, untap this creature.')
        self.assertIn({'field':'active_player','op':'eq','value':'$source.controller'},turn.condition['all'])
        graveyard=crime_event_binding_spec('Whenever you commit a crime, you may pay {B}. If you do, return this card from your graveyard to the battlefield.')
        self.assertEqual('graveyard',graveyard.active_zone);self.assertEqual('$source.owner',graveyard.condition['value'])
        for text in ('Whenever you commits a crime, draw a card.',
                'Whenever an opponent commit a crime, draw a card.',
                'Whenever you change a target, draw a card.',
                'Whenever you commit your second crime, draw a card.'):
            with self.subTest(text=text):self.assertIsNone(crime_event_binding_spec(text))

    def test_public_condition_infers_crime_owner_and_rejects_nonboolean_forgery(self):
        from quorune.compiler.public_state_fact_queries import fixed_public_fact_condition
        from quorune.crime_model import crime_fact_dependencies
        from quorune.continuous_conditions import FixedPublicStateConditionSpec
        condition=fixed_public_fact_condition("you've committed a crime this turn",source_name='Ava')
        self.assertEqual(('player.action.crime',),crime_fact_dependencies({'source_condition':condition.to_dict()}))
        for change in ({'amount':2},{'fact_parameter':'A'},{'schema_version':3}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                FixedPublicStateConditionSpec.from_dict({**condition.to_dict(),**change})


class CrimeOwnerTests(unittest.TestCase):
    def host(self,mode=1):
        from types import SimpleNamespace
        state=SimpleNamespace(turn_sequence=3,active_player='C',players={p:object() for p in 'ABCD'},
            turn_history=TurnHistory(turn_sequence=3,crime_history_version=mode))
        events=[]
        def record(kind,**fields):state.turn_history.events.append(TurnHistoryEvent(kind=kind,**fields))
        def dispatch(event,context,**kwargs):events.append((event,dict(context)))
        return SimpleNamespace(state=state,_record_turn_history=record,_dispatch_semantic_event=dispatch,events=events)

    def item(self,identity='id1',kind='spell'):
        from types import SimpleNamespace
        targets=['B','P1','G1']
        return SimpleNamespace(stack_id=identity,ref='S'+identity,controller='A',kind=kind,targets=targets,
            context={'target_snapshots':{
                'B':{'ref':'B','category':'player','controller':'B','owner':'B'},
                'P1':{'ref':'P1','category':'permanent','controller':'C','owner':'A','zone':'battlefield'},
                'G1':{'ref':'G1','category':'card','controller':'D','owner':'D','zone':'graveyard'}}})

    def test_completed_action_records_once_and_publishes_after_history_before_resolution(self):
        from quorune.rules.crime import record_crime_for_stack_placement,crime_count_this_turn
        host=self.host();item=self.item()
        self.assertIsNotNone(record_crime_for_stack_placement(host,item))
        self.assertEqual(1,crime_count_this_turn(host.state,'A'));self.assertEqual(1,len(host.events))
        event,context=host.events[0];self.assertEqual('player.committed_crime',event)
        self.assertEqual((1,3,'C'),(context['crime_count_this_turn'],context['qualifying_target_count'],context['active_player']))
        self.assertIsNone(record_crime_for_stack_placement(host,item));self.assertEqual(1,len(host.events))
        self.assertIsNotNone(record_crime_for_stack_placement(host,self.item('id2')))
        self.assertEqual(2,crime_count_this_turn(host.state,'A'))

    def test_legacy_absence_copies_and_no_target_actions_do_not_add_history(self):
        from quorune.rules.crime import record_crime_for_stack_placement
        legacy=self.host(None);before=legacy.state.turn_history.to_dict()
        self.assertIsNone(record_crime_for_stack_placement(legacy,self.item()))
        self.assertEqual(before,legacy.state.turn_history.to_dict());self.assertEqual([],legacy.events)
        host=self.host();self.assertIsNone(record_crime_for_stack_placement(host,self.item(kind='spell_copy')))
        item=self.item();item.targets=[]
        self.assertIsNone(record_crime_for_stack_placement(host,item));self.assertEqual([],host.events)

    def test_missing_initial_target_facts_reject_without_partial_recording(self):
        from quorune.rules.crime import record_crime_for_stack_placement
        from quorune.errors import GameRuleError
        host=self.host();item=self.item();item.context['target_snapshots'].pop('G1')
        with self.assertRaises(GameRuleError):record_crime_for_stack_placement(host,item)
        self.assertEqual([],host.state.turn_history.events);self.assertEqual([],host.events)

    def test_retarget_owner_does_not_add_or_erase_a_completed_initial_crime(self):
        from quorune.rules.crime import record_crime_for_stack_placement,crime_count_this_turn
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        from quorune.semantic_runtime.intents import RetargetStackItemIntent
        from quorune.replacement.immutable import FrozenMap
        for initial,changed,expected in (('A','B',0),('B','A',1)):
            with self.subTest(initial=initial,changed=changed):
                host=self.host();item=self.item();item.targets=[initial]
                item.context['target_snapshots']={initial:{'ref':initial,'category':'player','controller':initial,'owner':initial}}
                host.state.stack=[item];host.state.cards={}
                host._target_snapshot=lambda ref:{'ref':ref,'category':'player','controller':ref,'owner':ref}
                host._log=lambda *args,**kwargs:None
                record_crime_for_stack_placement(host,item)
                self.assertEqual(expected,crime_count_this_turn(host.state,'A'))
                intent=RetargetStackItemIntent(actor='A',target_stack_ref=item.ref,targets=(changed,),
                    target_groups=FrozenMap(),source_stack_ref=item.ref)
                SemanticChoiceIntentHostMixin.retarget_stack_item_intent(host,intent)
                self.assertEqual([changed],item.targets)
                self.assertEqual(expected,crime_count_this_turn(host.state,'A'))


class CrimeMutationTests(unittest.TestCase):
    def test_treating_all_target_principals_as_opponents_is_rejected_by_role_table(self):
        from unittest.mock import patch
        result=unittest.TestResult()
        with patch.object(CrimeTarget,'qualifies',return_value=True):
            CrimeClassificationTests('test_opponent_target_categories_use_current_control_and_graveyard_membership').run(result)
        self.assertGreater(len(result.failures),0);self.assertEqual([],result.errors)

    def test_publishing_without_committed_history_is_rejected_by_completion_evidence(self):
        from unittest.mock import patch
        host=CrimeOwnerTests().host();host._record_turn_history=lambda *args,**kwargs:None
        result=unittest.TestResult()
        with patch.object(CrimeOwnerTests,'host',return_value=host):
            CrimeOwnerTests('test_completed_action_records_once_and_publishes_after_history_before_resolution').run(result)
        self.assertGreater(len(result.failures),0);self.assertEqual([],result.errors)


class CrimeNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'crime.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-view-equivalence-cards.json',
            ROOT/'tests/fixtures/activation-target-view-cards.json',
            ROOT/'tests/fixtures/crime-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Crime events',[DeckEntry('Generic Bound Commander',1,'commander'),
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
        engine.state.players[actor].mana_pool.update({'C':40,'W':8,'U':8,'R':8,'B':8,'G':8})
        H.add(self,engine,'Generic Bound Growth',seat='D',zone='hand',ref='CRIME_PRIVATE_D_HAND')
        engine._grant_priority(actor);engine.pump();H.checkpoint(self,session)

    def cast(self,session,spell,targets=(),actor='A'):
        from quorune.record import authoritative_state_hash
        offered=next(row for row in session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==spell.ref)
        command={'action_id':offered['id'],'targets':list(targets),'pay':'auto'}
        before=authoritative_state_hash(session.engine.state)
        self.assertFalse(session.act('pilot:'+('B' if actor=='A' else 'A'),command).ok)
        self.assertEqual(before,authoritative_state_hash(session.engine.state))
        accepted=session.act('pilot:'+actor,command);self.assertTrue(accepted.ok,accepted.summary)

    def count(self,session,player='A'):
        from quorune.rules.crime import crime_count_this_turn
        return crime_count_this_turn(session.engine.state,player)

    def settle(self,session,*,target=None,tap_choice='tap',proliferate_refs=()):
        for _ in range(180):
            engine=session.engine;engine.pump();decision=engine.state.pending_decision
            if decision is None:
                if engine.state.game_over:return None
                self.fail('Crime journey lost its pending decision')
            principal=session.pending_principals()[0];ctx=session.packet(principal,full=True)['decision']['ctx']
            if decision.kind=='priority':
                if not engine.state.stack:return None
                command={'action_id':'pass'}
            elif decision.kind=='trigger.order':command={'action_id':'order','triggers':[row['id'] for row in ctx['triggers']]}
            elif decision.kind=='semantic.target':
                if target is None:return decision
                command={'action_id':'choose','targets':[target]}
            elif decision.kind=='semantic.choice':
                if ctx.get('operation')=='proliferate':
                    self.proliferate_actors=getattr(self,'proliferate_actors',[])+[principal]
                command=({'action_id':'choose','objects':list(proliferate_refs)} if ctx.get('operation')=='proliferate'
                    else {'action_id':'choose','choice':tap_choice})
            elif decision.kind=='proliferate':
                self.proliferate_actors=getattr(self,'proliferate_actors',[])+[principal]
                command={'action_id':'choose','objects':list(proliferate_refs)}
            elif decision.kind=='replacement.order':command={'action_id':'choose','replacement':ctx['options'][0]['id']}
            else:self.fail('Unexpected crime journey decision '+decision.kind)
            accepted=session.act(principal,command);self.assertTrue(accepted.ok,accepted.summary)
        self.fail('Crime journey did not settle')

    def finish(self,session):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for seat in 'ABC':self.assertNotIn('CRIME_PRIVATE_D_HAND',str(session.packet('pilot:'+seat,full=True)))
        H.replay(self,session,load=True)

    def priority_to(self,session,actor):
        for _ in range(8):
            if list(session.pending_principals())==['pilot:'+actor]:return
            self.assertEqual('priority',session.engine.state.pending_decision.kind)
            passed=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(passed.ok,passed.summary)
        self.fail('Crime journey did not reach requested priority')

    def test_actual_spell_crimes_change_live_characteristics_and_respect_source_once_limits(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(965100);engine=session.engine
        bandit=H.add(self,engine,'Hardbristle Bandit',ref='CRIME_BANDIT');bandit.tapped=True
        power=H.add(self,engine,'Slickshot Vault-Buster',ref='CRIME_POWER')
        double=H.add(self,engine,'Omenport Vigilante',ref='CRIME_DOUBLE')
        muscle=H.add(self,engine,'Overzealous Muscle',ref='CRIME_MUSCLE')
        spells=[H.add(self,engine,'Shock',zone='hand',ref='CRIME_SHOCK_'+str(i)) for i in range(3)]
        own=H.add(self,engine,'Twiddle',zone='hand',ref='CRIME_OWN_TAP')
        land=H.add(self,engine,'Mountain',ref='OFFTURN_CRIME_MOUNTAIN')
        engine.move_card(engine.state.players['A'].zones['hand'][0],'library',log=False)
        for card in (bandit,power,double,muscle,*spells,own,land):self.assert_closed(session,card)
        self.start(session);self.assertEqual(1,engine._numeric_stat(power.object_id,'power'));self.assertEqual(0,self.count(session))
        self.cast(session,spells[0],('B',))
        self.assertEqual(1,self.count(session));self.assertEqual(40,engine.state.players['B'].life)
        self.assertEqual(3,engine._numeric_stat(power.object_id,'power'))
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(self.settle(session));self.assertFalse(engine.state.cards[bandit.object_id].tapped)
        self.assertEqual(38,engine.state.players['B'].life)
        keywords={str(value).casefold() for value in engine._effective_card_data(engine.state.cards[double.object_id]).get('keywords',())}
        self.assertIn('double strike',keywords)
        self.assertIn('indestructible',{str(value).casefold() for value in engine._effective_card_data(engine.state.cards[muscle.object_id]).get('keywords',())})
        self.cast(session,own,(bandit.ref,));self.assertIsNone(self.settle(session))
        self.assertTrue(engine.state.cards[bandit.object_id].tapped);self.assertEqual(1,self.count(session))
        self.cast(session,spells[1],('B',));self.assertIsNone(self.settle(session))
        self.assertEqual(2,self.count(session));self.assertTrue(engine.state.cards[bandit.object_id].tapped)
        self.assertEqual(36,engine.state.players['B'].life)
        from test_ability_label_composition import AbilityLabelCompositionTests as Advance
        Advance.advance(self,session,lambda s:s.state.active_player=='B' and s.state.step=='upkeep' and not s.state.stack)
        self.assertEqual(0,self.count(session));self.assertEqual(1,engine._numeric_stat(power.object_id,'power'))
        self.assertNotIn('indestructible',{str(value).casefold() for value in engine._effective_card_data(engine.state.cards[muscle.object_id]).get('keywords',())})
        self.priority_to(session,'A')
        ability=next(a for a in engine._activated_abilities(land) if a.mana_ability)
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('source')==land.ref and row.get('ability')==ability.ability_id)
        activated=session.act('pilot:A',{'action_id':offered['id']});self.assertTrue(activated.ok,activated.summary)
        self.cast(session,spells[2],('B',));self.assertIsNone(self.settle(session))
        self.assertEqual(1,self.count(session));self.assertFalse(engine.state.cards[bandit.object_id].tapped)
        self.assertEqual(3,engine._numeric_stat(power.object_id,'power'))
        self.assertNotIn('indestructible',{str(value).casefold() for value in engine._effective_card_data(engine.state.cards[muscle.object_id]).get('keywords',())})
        self.assertEqual(34,engine.state.players['B'].life);self.finish(session)

    def test_actual_sacrificed_activation_and_initial_graveyard_trigger_targets_commit_crimes(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        from quorune.engine import CommanderEngine
        from quorune.errors import GameRuleError
        from unittest.mock import patch
        from quorune.crime_model import CRIME_EVENT
        session=self.session(965101);engine=session.engine
        bandit=H.add(self,engine,'Hardbristle Bandit',ref='ACTIVATION_CRIME_BANDIT');bandit.tapped=True
        seal=H.add(self,engine,'Seal of Fire',ref='ACTIVATION_CRIME_SEAL')
        lanterns=[H.add(self,engine,'Soul-Guide Lantern',zone='hand',ref='CRIME_LANTERN_'+str(i)) for i in range(2)]
        own=H.add(self,engine,'Ornithopter',zone='graveyard',ref='CRIME_OWN_GRAVEYARD')
        other=H.add(self,engine,'Ornithopter',seat='B',zone='graveyard',ref='CRIME_OTHER_GRAVEYARD')
        for card in (bandit,seal,*lanterns,own,other):self.assert_closed(session,card)
        self.start(session)
        ability=next(a for a in engine._activated_abilities(seal) if a.sacrifice_source)
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('source')==seal.ref and row.get('ability')==ability.ability_id)
        command={'action_id':offered['id'],'targets':['B']};before=authoritative_state_hash(engine.state)
        original=CommanderEngine._dispatch_semantic_event
        def fail_after_crime(owner,event,context,**kwargs):
            result=original(owner,event,context,**kwargs)
            if event==CRIME_EVENT:raise GameRuleError('Injected crime after committed source payment')
            return result
        with patch.object(CommanderEngine,'_dispatch_semantic_event',fail_after_crime):rejected=session.act('pilot:A',command)
        self.assertFalse(rejected.ok);self.assertIn('Injected crime',rejected.summary)
        self.assertEqual(before,authoritative_state_hash(engine.state));self.assertEqual(0,self.count(session))
        accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',engine.state.cards[seal.object_id].zone);self.assertEqual(1,self.count(session))
        self.assertIsNone(self.settle(session));self.assertEqual(38,engine.state.players['B'].life)
        for lantern,target,expected in zip(lanterns,(own,other),(1,2)):
            self.cast(session,lantern);decision=self.settle(session)
            self.assertEqual('semantic.target',decision.kind);self.assertEqual(expected-1 if target is other else 1,self.count(session))
            session=H.replay(self,session,load=True);engine=session.engine
            before=authoritative_state_hash(engine.state)
            self.assertFalse(session.act('pilot:B',{'action_id':'choose','targets':[target.ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
            accepted=session.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual(expected,self.count(session))
            self.assertIsNone(self.settle(session));self.assertEqual('exile',engine.state.cards[target.object_id].zone)
        self.finish(session)

    def test_actual_multitarget_spell_commits_one_crime_for_several_opponent_targets(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(965102);engine=session.engine
        watcher=H.add(self,engine,'Blood Hustler',ref='MULTITARGET_CRIME_WATCHER')
        targets=[H.add(self,engine,'Ornithopter',seat=seat,ref='CRIME_HEX_TARGET_'+str(i))
            for i,seat in enumerate(('A','B','B','C','C','D'))]
        spell=H.add(self,engine,'Hex',zone='hand',ref='MULTITARGET_CRIME_HEX')
        for card in (watcher,spell,*targets):self.assert_closed(session,card)
        self.start(session);self.cast(session,spell,tuple(card.ref for card in targets))
        self.assertEqual(1,self.count(session));self.assertTrue(all(engine.state.cards[card.object_id].zone=='battlefield' for card in targets))
        self.assertIsNone(self.settle(session))
        self.assertEqual(1,engine.state.cards[watcher.object_id].counters.get('+1/+1',0))
        self.assertTrue(all(engine.state.cards[card.object_id].zone=='graveyard' for card in targets))
        self.finish(session)

    def test_actual_uncast_spell_copy_with_new_opponent_target_is_not_a_new_crime(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=self.session(965103);engine=session.engine
        power=H.add(self,engine,'Slickshot Vault-Buster',ref='COPY_CRIME_POWER')
        shocks=[H.add(self,engine,'Shock',zone='hand',ref='COPY_CRIME_SHOCK_'+str(i)) for i in range(2)]
        copies=[H.add(self,engine,'Reverberate',zone='hand',ref='COPY_CRIME_REVERBERATE_'+str(i)) for i in range(2)]
        for card in (power,*shocks,*copies):self.assert_closed(session,card)
        self.start(session)
        for spell,copy,initial,new,count in zip(shocks,copies,('A','B'),('B','A'),(0,1)):
            self.cast(session,spell,(initial,));original=next(item for item in engine.state.stack if item.card_object_id==spell.object_id)
            self.assertEqual(count,self.count(session));self.cast(session,copy,(original.ref,))
            decision=H.resolve(self,session);self.assertEqual('semantic.choice',decision.kind)
            session=H.replay(self,session,load=True);engine=session.engine
            before=authoritative_state_hash(engine.state)
            self.assertFalse(session.act('pilot:B',{'action_id':'choose','targets':[new]}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
            accepted=session.act('pilot:A',{'action_id':'choose','targets':[new]});self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual(count,self.count(session))
            copied=next(item for item in engine.state.stack if item.kind=='spell_copy')
            self.assertEqual([new],copied.targets)
            self.assertEqual([initial],next(item for item in engine.state.stack if item.ref==original.ref).targets)
            self.assertIsNone(self.settle(session));self.assertEqual(count,self.count(session))
        self.assertEqual((36,36),(engine.state.players['A'].life,engine.state.players['B'].life))
        self.finish(session)

    def test_actual_opponent_spell_and_ability_targets_count_without_requiring_resolution(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=self.session(965104,actor='B');engine=session.engine
        haul=H.add(self,engine,"Bandit's Haul",ref='STACK_CRIME_HAUL');haul.counters['loot']=1
        observer=H.add(self,engine,'Patrolling Peacemaker',ref='STACK_CRIME_OPPONENT_OBSERVER')
        observer.counters['+1/+1']=2  # Its pinned entry result; the fixture starts after entry.
        original=H.add(self,engine,'Ornithopter',seat='B',zone='hand',ref='STACK_CRIME_ORIGINAL')
        own_counter=H.add(self,engine,'Counterspell',zone='hand',ref='STACK_CRIME_OWN_COUNTER')
        other_counter=H.add(self,engine,'Counterspell',seat='B',zone='hand',ref='STACK_CRIME_OTHER_COUNTER')
        seal=H.add(self,engine,'Seal of Fire',seat='B',ref='STACK_CRIME_SEAL')
        stifle=H.add(self,engine,'Stifle',zone='hand',ref='STACK_CRIME_STIFLE')
        for card in (haul,observer,original,own_counter,other_counter,seal,stifle):self.assert_closed(session,card)
        engine.state.players['A'].mana_pool.update({'U':4});self.start(session,actor='B')
        self.cast(session,original,actor='B');self.priority_to(session,'A')
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==original.object_id)
        self.cast(session,own_counter,(reference,));self.assertEqual(1,self.count(session,'A'))
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==own_counter.object_id)
        self.priority_to(session,'B');self.cast(session,other_counter,(reference,),actor='B')
        self.assertEqual(1,self.count(session,'B'));self.assertEqual(1,self.count(session,'A'))
        self.assertIsNone(self.settle(session,proliferate_refs=(haul.ref,)))
        self.assertEqual(['pilot:A'],self.proliferate_actors)
        self.assertEqual('battlefield',engine.state.cards[original.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[own_counter.object_id].zone)
        self.assertEqual(3,engine.state.cards[haul.object_id].counters.get('loot',0))
        self.priority_to(session,'B')
        ability=next(a for a in engine._activated_abilities(seal) if a.sacrifice_source)
        offered=next(row for row in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            if row.get('source')==seal.ref and row.get('ability')==ability.ability_id)
        accepted=session.act('pilot:B',{'action_id':offered['id'],'targets':['B']});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,self.count(session,'B'),'Targeting yourself is not a new crime')
        reference=next(item.ref for item in engine.state.stack if item.kind=='activated_ability' and item.source_object_id==seal.object_id)
        self.priority_to(session,'A');self.cast(session,stifle,(reference,))
        self.assertEqual(2,self.count(session,'A'));self.assertIsNone(self.settle(session))
        self.assertEqual(40,engine.state.players['B'].life,'The targeted opposing ability was countered')
        self.assertEqual(3,engine.state.cards[haul.object_id].counters.get('loot',0),'This source trigger was already used this turn')
        self.finish(session)
