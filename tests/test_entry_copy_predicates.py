from __future__ import annotations

import json,unittest
from types import SimpleNamespace
from common import ROOT
from quorune.entry_copy_predicates import EntryCopyPredicate,entry_copy_predicate_matches,actual_entry_mana_spent
from quorune.model import CardInstance,StackItem,TurnHistory,TurnHistoryEvent
from quorune.turn_history import reset_turn_history,roll_turn_history
from quorune.compiler.entry_copy_templates import entry_copy_spec
from quorune.entry_copy import EntryCopySpec


class EntryCopyPredicateModelTests(unittest.TestCase):
    def host(self):
        return SimpleNamespace(state=SimpleNamespace(stack=[],turn_sequence=3,turn_history=TurnHistory(turn_sequence=3,arrival_history_version=1)),
            _effective_card_data=lambda card:{'mana_value':2})

    def test_arrival_history_keeps_old_bytes_and_current_incarnation_origin(self):
        old=TurnHistoryEvent('creature_died',object_incarnation='card@0')
        self.assertNotIn('origin',old.to_dict());self.assertEqual(old,TurnHistoryEvent.from_dict(old.to_dict()))
        arrived=TurnHistoryEvent('card_arrived_graveyard',object_incarnation='card@1',origin='battlefield')
        self.assertEqual(arrived,TurnHistoryEvent.from_dict(json.loads(json.dumps(arrived.to_dict()))))
        history=TurnHistory(turn_sequence=3,arrival_history_version=1,events=[arrived])
        self.assertEqual(1,reset_turn_history(history,4).arrival_history_version)
        self.assertEqual(1,roll_turn_history(history,next_turn_sequence=4,previous_active_player='A').arrival_history_version)
        self.assertNotIn('arrival_history_version',TurnHistory().to_dict())
        for value in (True,2):
            with self.assertRaises(ValueError):TurnHistory(arrival_history_version=value)
        for changes in ({'origin':True},{'origin':'arbitrary'},{'origin':None},{'object_incarnation':[]}):
            with self.assertRaises(ValueError):TurnHistoryEvent.from_dict({**arrived.to_dict(),**changes})

    def test_graveyard_predicate_is_arrived_card_not_died_or_same_physical_return(self):
        h=self.host();target=CardInstance('card','CARD','oracle','Source','B','B','graveyard',zone_change_counter=1)
        p=EntryCopyPredicate('graveyard_from_battlefield_this_turn')
        h.state.turn_history.events=[TurnHistoryEvent('creature_died',actor='B',object_incarnation='card@0')]
        self.assertFalse(entry_copy_predicate_matches(h,p,entering=None,target=target,controller='A'))
        h.state.turn_history.events.append(TurnHistoryEvent('card_arrived_graveyard',object_incarnation='card@1',origin='battlefield'))
        self.assertTrue(entry_copy_predicate_matches(h,p,entering=None,target=target,controller='A'))
        target.zone_change_counter=3
        self.assertFalse(entry_copy_predicate_matches(h,p,entering=None,target=target,controller='A'))
        target.zone_change_counter=1;h.state.turn_history.arrival_history_version=None
        self.assertFalse(entry_copy_predicate_matches(h,p,entering=None,target=target,controller='A'))

    def test_raid_uses_entering_controller_current_turn_declared_attacks(self):
        h=self.host();p=EntryCopyPredicate('controller_attacked_this_turn')
        h.state.turn_history.events=[TurnHistoryEvent('creature_attacked',actor='B',object_incarnation='gone@0')]
        self.assertFalse(entry_copy_predicate_matches(h,p,entering=None,target=None,controller='A'))
        self.assertTrue(entry_copy_predicate_matches(h,p,entering=None,target=None,controller='B'))
        h.state.turn_sequence=4
        self.assertFalse(entry_copy_predicate_matches(h,p,entering=None,target=None,controller='B'))

    def test_actual_mana_spent_uses_accepted_cast_and_copies_or_noncast_have_zero(self):
        h=self.host();card=CardInstance('enter','ENTER','oracle','Source','A','A','stack')
        item=StackItem('stack','S','spell','A','Source',card_object_id=card.object_id,context={'mana_spent_total':3})
        h.state.stack=[item];self.assertEqual(3,actual_entry_mana_spent(h,card))
        p=EntryCopyPredicate('mana_value_at_most_mana_spent')
        self.assertTrue(entry_copy_predicate_matches(h,p,entering=card,target=card,controller='A'))
        item.kind='spell_copy';self.assertEqual(0,actual_entry_mana_spent(h,card))
        self.assertFalse(entry_copy_predicate_matches(h,p,entering=card,target=card,controller='A'))
        item.kind='spell';item.context['mana_spent_total']=True
        with self.assertRaises(ValueError):actual_entry_mana_spent(h,card)
        h.state.stack=[];card.zone='hand';self.assertEqual(0,actual_entry_mana_spent(h,card))

    def test_predicate_and_entry_codec_are_closed_without_changing_old_shapes(self):
        for text in ['Raid — If you attacked this turn, you may have this creature enter as a copy of any creature on the battlefield.',
          "You may have this creature enter as a copy of any creature card in a graveyard that was put there from the battlefield this turn, except it's a Phyrexian in addition to its other types.",
          "You may have this creature enter as a copy of any creature on the battlefield with mana value less than or equal to the amount of mana spent to cast this creature, except it's a Bird in addition to its other types and it has flying."]:
            spec=entry_copy_spec(text,source_name='Generic Copier');self.assertIsNotNone(spec,text)
            self.assertEqual(4,spec.schema_version);self.assertEqual(spec,EntryCopySpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        for value in ({'schema_version':True,'kind':'controller_attacked_this_turn'},{'schema_version':1,'kind':'printed_cost'},{'schema_version':1,'kind':'controller_attacked_this_turn','extra':1}):
            with self.assertRaises(ValueError):EntryCopyPredicate.from_dict(value)
        old=entry_copy_spec('You may have this creature enter as a copy of any creature on the battlefield.',source_name='Generic Copier')
        self.assertNotIn('predicate',old.to_dict())


    def test_omitting_history_predicate_is_killed_by_independent_incarnation_contract(self):
        from unittest.mock import patch
        import sys
        method='test_graveyard_predicate_is_arrived_card_not_died_or_same_physical_return'
        with patch.object(sys.modules[__name__],'entry_copy_predicate_matches',return_value=True),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()


import test_entry_copy_values as values


class EntryCopyPredicateCompilerTests(unittest.TestCase):
    def test_dependency_and_producer_omissions_keep_copy_predicates_residual(self):
        from copy import deepcopy
        from unittest.mock import patch
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        text='Raid — If you attacked this turn, you may have this creature enter as a copy of any creature on the battlefield.'
        record=query_record(text);caps=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(record,capability_registry=caps).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in ('zone.entry.fixed_copy','zone.entry.copy_source_predicates'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==capability).update(status='blocked',blockers=['omitted source predicate authority'])
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=CapabilityRegistry(changed)).status)
        with patch('quorune.compiler.entry_copy_templates.entry_copy_spec',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=caps).status)

    def test_arrival_record_provenance_is_explicit_and_mismatches_reject(self):
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        # Use the repository's normal state through a runtime fixture in the
        # native class; this pure check protects closed history mode values.
        old=TurnHistory();self.assertNotIn('arrival_history_version',old.to_dict())
        current=TurnHistory(arrival_history_version=1)
        self.assertEqual(current,TurnHistory.from_dict(json.loads(json.dumps(current.to_dict()))))
        with self.assertRaises(ValueError):TurnHistory.from_dict({'schema_version':1,'arrival_history_version':True})


class EntryCopyPredicateRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=values.EntryCopyValueRuntimeTests.session
    add=values.EntryCopyValueRuntimeTests.add
    prepare=values.EntryCopyValueRuntimeTests.prepare
    checkpoint=values.EntryCopyValueRuntimeTests.checkpoint
    cast=values.EntryCopyValueRuntimeTests.cast
    resolve=values.EntryCopyValueRuntimeTests.resolve
    replay=values.EntryCopyValueRuntimeTests.replay
    choose_copy=values.EntryCopyValueRuntimeTests.choose_copy
    assert_closed=values.EntryCopyValueRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'copy-predicates.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/entry-copy-predicate-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Copy predicates',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_mockingbird_filters_by_actual_total_mana_paid_and_copies_entry_draw(self):
        s=self.session(707230);e=s.engine
        low=self.add(e,'Elvish Visionary',ref='paid-low')
        high=self.add(e,'Air Elemental',ref='paid-high')
        source=self.add(e,'Mockingbird',zone='hand');self.assert_closed(s,source)
        self.prepare(s,mana={'U':3,'C':4});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':'cast:'+source.ref,'x':1,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,s.state.stack[-1].context['mana_spent_total'])
        decision=self.resolve(s);self.assertIsNotNone(decision)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertTrue(any(low.ref in row['id'] for row in options))
        self.assertFalse(any(high.ref in row['id'] for row in options))
        s=self.choose_copy(s,low)
        data=s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertIn('bird',s.engine._type_parts(data['type_line'])[1]);self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.replay(s,load=True)

    def test_actual_sleeper_copies_current_arrival_but_not_discarded_creature(self):
        s=self.session(707231);e=s.engine
        body=self.add(e,'Elvish Visionary',seat='B',ref='arrival-body')
        old=self.add(e,'Generic Bound Body',seat='B',zone='graveyard',ref='old-grave-body')
        murder=self.add(e,'Murder',zone='hand');source=self.add(e,'Activated Sleeper',zone='hand')
        self.assert_closed(s,source);self.prepare(s,mana={'B':6,'C':5});self.checkpoint(s)
        self.cast(s,'A',murder,[body.ref]);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',source);decision=self.resolve(s);self.assertIsNotNone(decision)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertTrue(any(body.ref in row['id'] for row in options));self.assertFalse(any(old.ref in row['id'] for row in options))
        s=self.choose_copy(s,body)
        data=s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertIn('phyrexian',s.engine._type_parts(data['type_line'])[1]);self.assertEqual('graveyard',s.state.cards[body.object_id].zone)
        self.replay(s,load=True)

    def test_raid_decline_without_attack_keeps_original_body_and_no_copy_choice(self):
        s=self.session(707232);e=s.engine
        self.add(e,'Elvish Visionary');source=self.add(e,'Protean Raider',zone='hand');self.assert_closed(s,source)
        self.prepare(s,mana={'R':3,'U':3,'C':5});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual('Protean Raider',s.engine.display_name(source.object_id))
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        self.replay(s,load=True)


    def test_raid_qualifies_after_actual_attack_even_when_attacker_leaves(self):
        from quorune.model import CombatState
        s=self.session(707233);e=s.engine
        attacker=self.add(e,'Generic Bound Body',ref='raid-attacker');attacker.acquired_control_turn_count=-1
        target=self.add(e,'Elvish Visionary',seat='B',ref='raid-copy-source')
        murder=self.add(e,'Murder',zone='hand');source=self.add(e,'Protean Raider',zone='hand')
        self.prepare(s,mana={'B':5,'R':3,'U':3,'C':7})
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.phase='combat';e.state.step='declare_attackers';e.state.phase_index=5;e.state.combat=CombatState();e._issue_attackers();self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':'attack','attacks':{attacker.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.cast(s,'A',murder,[attacker.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[attacker.object_id].zone)
        # Walk only the remaining empty combat priority windows into the same
        # turn's postcombat main, retaining the actual declared attack journal.
        for _ in range(24):
            s.engine.pump()
            if s.state.phase=='postcombat_main':break
            decision=s.state.pending_decision
            if decision.kind=='declare_blockers':response={'action_id':'block','blocks':{}}
            else:self.assertEqual('priority',decision.kind);response={'action_id':'pass'}
            result=s.act(s.pending_principals()[0],response);self.assertTrue(result.ok,result.summary)
        self.assertEqual('postcombat_main',s.state.phase)
        s.engine.state.players['A'].mana_pool.update({'R':1,'U':1,'C':1})
        # Mana setup starts a new replay boundary with the real current-turn
        # attack history and departed attacker still present in state.
        s.engine.permissions.invalidate_current();s.engine.state.pending_decision=None;s.engine._grant_priority('A');s.engine.pump();self.checkpoint(s)
        self.cast(s,'A',source);s=self.choose_copy(s,target)
        self.assertEqual('Elvish Visionary',s.engine.display_name(source.object_id));self.replay(s,load=True)


    def test_arrival_not_death_qualifies_printed_creature_that_was_noncreature_artifact(self):
        s=self.session(707234);e=s.engine
        body=self.add(e,'Ornithopter',ref='noncreature-arrival-card')
        # Existing copied-value representation supplies a noncreature permanent
        # for this explicit state-boundary setup. Native Naturalize performs its
        # actual battlefield-to-graveyard transition and seals the arrival fact.
        body.annotations['copy_overrides']={'name':'Noncreature artifact','mana_cost':'{2}','mana_value':2,'type_line':'Artifact','colors':[],
            'power':None,'toughness':None,'keywords':[],'ability_fragments':[]}
        destroy=self.add(e,'Naturalize',zone='hand');source=self.add(e,'Activated Sleeper',zone='hand')
        self.prepare(s,mana={'G':3,'B':3,'C':5});self.checkpoint(s)
        self.cast(s,'A',destroy,[body.ref]);self.assertIsNone(self.resolve(s))
        arrivals=[event for event in s.state.turn_history.events if event.kind=='card_arrived_graveyard' and event.object_incarnation==s.state.cards[body.object_id].logical_object_id]
        self.assertEqual(1,len(arrivals));self.assertEqual('battlefield',arrivals[0].origin)
        self.assertFalse(any(event.kind=='creature_died' and event.object_incarnation.startswith(body.object_id+'@') for event in s.state.turn_history.events))
        self.cast(s,'A',source);decision=self.resolve(s);self.assertIsNotNone(decision)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertTrue(any(body.ref in row['id'] for row in options))
        self.replay(s,load=True)

    def test_sleeper_does_not_reuse_arrival_after_card_returns_to_hand_and_is_discarded(self):
        s=self.session(707235);e=s.engine
        body=self.add(e,'Elvish Visionary',ref='stale-arrival-body')
        murder=self.add(e,'Murder',zone='hand');raise_dead=self.add(e,'Raise Dead',zone='hand')
        source=self.add(e,'Activated Sleeper',zone='hand')
        self.prepare(s,mana={'B':7,'C':6});self.checkpoint(s)
        self.cast(s,'A',murder,[body.ref]);self.assertIsNone(self.resolve(s))
        arrived=s.state.cards[body.object_id].logical_object_id
        self.cast(s,'A',raise_dead,[body.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[body.object_id].zone)
        # Canonical discard transition is the explicit setup boundary; it must
        # not reuse the prior battlefield-arrival incarnation in its journal.
        from quorune.zone_trigger_events import ZoneTransitionKind
        s.engine.move_card(body.object_id,'graveyard',reason='copy predicate discard boundary',semantic_events=True,transition_kind=ZoneTransitionKind.DISCARD)
        self.assertNotEqual(arrived,s.state.cards[body.object_id].logical_object_id)
        self.prepare(s,mana={'B':1,'C':2});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)


    def test_noncast_mockingbird_has_zero_budget_and_can_copy_only_zero_mana_value(self):
        s=self.session(707236);e=s.engine
        zero=self.add(e,'Ornithopter',ref='zero-copy-source');self.add(e,'Elvish Visionary',ref='nonzero-copy-source')
        bird=self.add(e,'Mockingbird',zone='graveyard');reanimate=self.add(e,'Reanimate',zone='hand')
        self.prepare(s,mana={'B':3});self.checkpoint(s)
        self.cast(s,'A',reanimate,[bird.ref]);decision=self.resolve(s);self.assertIsNotNone(decision)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertTrue(any(zero.ref in r['id'] for r in options));self.assertFalse(any('nonzero-copy-source' in r['id'] for r in options))
        s=self.choose_copy(s,zero)
        self.assertEqual('Ornithopter',s.engine.display_name(bird.object_id));self.replay(s,load=True)


    def test_mockingbird_native_mana_payment_pending_choice_privacy_and_replay(self):
        from quorune.record import authoritative_state_hash
        s=self.session(707237);e=s.engine
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        versions=format_state_versions(s.state);self.assertEqual(1,versions['arrival_history_version'])
        validate_state_versions({'format':versions},s.state)
        with self.assertRaises(ValueError):validate_state_versions({'format':{**versions,'arrival_history_version':0}},s.state)
        target=self.add(e,'Elvish Visionary',ref='mana-copy-source')
        elf=self.add(e,'Llanowar Elves',ref='copy-mana-elf');elf.acquired_control_turn_count=-1
        source=self.add(e,'Mockingbird',zone='hand');private=self.add(e,'Generic Bound Body',seat='C',zone='hand',ref='private-predicate-hand')
        self.prepare(s,mana={'U':1});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':'cast:'+source.ref,'x':1,'pay':'manual',
            'mana':[{'source':elf.ref,'ability':'ab1','bundle':{'G':1}}],'payment':{'U':1,'G':1}})
        self.assertTrue(result.ok,result.summary);self.assertEqual(2,s.state.stack[-1].context['mana_spent_total'])
        decision=self.resolve(s);self.assertEqual('replacement.order',decision.kind)
        s=self.replay(s,load=True);options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        option=next(r for r in options if target.ref in r['id'])
        before=authoritative_state_hash(s.state)
        denied=s.act('pilot:D',{'action_id':'choose','replacement':option['id']})
        self.assertFalse(denied.ok,denied.summary);self.assertEqual(before,authoritative_state_hash(s.state))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        result=s.act('pilot:A',{'action_id':'choose','replacement':option['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('Elvish Visionary',s.engine.display_name(source.object_id))
        self.replay(s,load=True)

    def test_native_predicate_and_arrival_omissions_fail_actual_witnesses(self):
        from unittest.mock import patch
        import quorune.semantic_runtime.entry_copy as owner
        method='test_raid_decline_without_attack_keeps_original_body_and_no_copy_choice'
        with patch.object(owner,'entry_copy_predicate_matches',return_value=True),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()
        method='test_actual_sleeper_copies_current_arrival_but_not_discarded_creature'
        with patch.object(owner,'entry_copy_predicate_matches',return_value=False),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()

    def test_all_three_originals_have_complete_predicate_admission(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        for name in ('Protean Raider','Activated Sleeper','Mockingbird'):
            c=self.db.lookup(name);p=compile_best_available_card_program(self.db,c,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
            with self.subTest(card=name):self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
