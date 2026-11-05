"""Event-time quota, source incarnation and immutable look-back contracts."""
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_bound_effect_programs as witnesses

from quorune.activation_usage import commit_trigger_usage
from quorune.model import CardInstance
from quorune.rules.trigger_limits import (
    TriggerLimitSpec,DEPARTURE_TRIGGER_LIMIT_CAPABILITY,TRIGGER_ONCE_PER_TURN_CAPABILITY,
    EVENT_SOURCE_USAGE_CONTEXT,event_source_usage_context,trigger_usage_plan,validate_program_trigger_limit,
    trigger_limit_capabilities,
)


def source(*,counter=0,controller='A'):
    return CardInstance(object_id='quota-source',ref='QUOTA_SOURCE',oracle_id='fixture:quota',
        printed_name='Generic quota source',owner='A',controller=controller,zone='battlefield',zone_change_counter=counter)


def program(*,version=2,key='fixture:quota:ab1',ability='ab1',event='creature.dies'):
    limit=TriggerLimitSpec(2,source_scope='event_incarnation') if version==2 else TriggerLimitSpec()
    return SimpleNamespace(key=key,ability_id=ability,event=event,event_condition=None,active_zone='battlefield',
        trigger_limit=limit.to_dict(),capability_dependencies=trigger_limit_capabilities(limit.to_dict()))


def pending(card,p,turn=7):
    return SimpleNamespace(source_object_id=card.object_id,semantic_key=p.key,
        context=event_source_usage_context(card,p,turn))


class DepartureTriggerLimitOwnerTests(unittest.TestCase):
    def test_schema_one_stays_exact_and_new_departure_scope_requires_its_own_authority(self):
        import json
        from common import ROOT
        from jsonschema import Draft202012Validator
        old={'schema_version':1,'kind':'once_per_turn'}
        self.assertEqual(old,TriggerLimitSpec.from_dict(old).to_dict())
        new={'schema_version':2,'kind':'once_per_turn','source_scope':'event_incarnation'}
        self.assertEqual(new,TriggerLimitSpec.from_dict(new).to_dict())
        self.assertEqual((TRIGGER_ONCE_PER_TURN_CAPABILITY,),trigger_limit_capabilities(old))
        self.assertEqual((TRIGGER_ONCE_PER_TURN_CAPABILITY,DEPARTURE_TRIGGER_LIMIT_CAPABILITY),trigger_limit_capabilities(new))
        fragments=[json.loads((ROOT/'schemas/semantic-program.schema.json').read_text(encoding="utf-8"))['properties']['trigger_limit'],
            json.loads((ROOT/'schemas/card-program-v2.schema.json').read_text(encoding="utf-8"))['$defs']['runtime']['properties']['trigger_limit']]
        for fragment in fragments:
            validator=Draft202012Validator(fragment)
            validator.validate(old);validator.validate(new)
            self.assertFalse(validator.is_valid({**new,'source_scope':'current_object'}))
            self.assertFalse(validator.is_valid({**new,'schema_version':True}))
        for event in ('creature.dies','permanent.sacrificed.self','permanent.leave','artifact.graveyard'):
            with self.subTest(event=event):
                validate_program_trigger_limit(program(event=event))
                with self.assertRaisesRegex(ValueError,'nondeparture'):
                    validate_program_trigger_limit(program(event=event,version=1))
        for event in ('life.gained','spell.countered','multi.event','activate'):
            with self.subTest(event=event),self.assertRaises(ValueError):validate_program_trigger_limit(program(event=event))
        p=program();p.capability_dependencies=(TRIGGER_ONCE_PER_TURN_CAPABILITY,)
        with self.assertRaises(ValueError):validate_program_trigger_limit(p)
        for bad in ({**new,'schema_version':True},{**new,'source_scope':True},
                {**new,'source_scope':'current_object'},{**old,'source_scope':None},
                {'schema_version':2,'kind':'once_per_turn'},{**new,'extra':0}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):TriggerLimitSpec.from_dict(bad)

    def test_surviving_source_reads_both_event_time_and_current_usage_without_writes(self):
        live=source();snapshot=deepcopy(live);state=SimpleNamespace(cards={live.object_id:live},turn_sequence=7)
        before=deepcopy(snapshot.annotations)
        claim=trigger_usage_plan(state,snapshot,program(),())
        self.assertIs(live,claim.live_source)
        commit_trigger_usage(live,ability_id='ab1',turn_sequence=7)
        self.assertIsNone(trigger_usage_plan(state,snapshot,program(),()))
        self.assertEqual(before,snapshot.annotations)
        fresh=source();old_used=deepcopy(fresh);commit_trigger_usage(old_used,ability_id='ab1',turn_sequence=7)
        self.assertIsNone(trigger_usage_plan(SimpleNamespace(cards={fresh.object_id:fresh},turn_sequence=7),old_used,program(),()))
        self.assertEqual({},fresh.annotations)

    def test_unused_departed_source_claims_only_once_across_distinct_snapshot_copies(self):
        original=source();after=source(counter=1);after.zone='graveyard'
        state=SimpleNamespace(cards={after.object_id:after},turn_sequence=7);p=program()
        first=trigger_usage_plan(state,deepcopy(original),p,())
        self.assertIsNotNone(first)
        self.assertIsNone(first.live_source)
        held=pending(original,p)
        self.assertIsNone(trigger_usage_plan(state,deepcopy(original),p,(held,)))
        self.assertEqual({},original.annotations)
        self.assertEqual({},after.annotations)
        self.assertIsNotNone(trigger_usage_plan(SimpleNamespace(cards={},turn_sequence=7),deepcopy(original),p,()))

    def test_previously_used_departed_source_never_reads_fresh_incarnation_as_unused(self):
        old=source();commit_trigger_usage(old,ability_id='ab1',turn_sequence=7)
        after=source(counter=1);after.zone='graveyard';state=SimpleNamespace(cards={after.object_id:after},turn_sequence=7)
        before=deepcopy(old.annotations)
        self.assertIsNone(trigger_usage_plan(state,old,program(),()))
        self.assertEqual(before,old.annotations)
        self.assertEqual({},after.annotations)

    def test_control_preserves_usage_and_new_incarnation_and_new_turn_refresh_it(self):
        old=source();p=program();held=pending(old,p)
        live=source(counter=2,controller='B');state=SimpleNamespace(cards={live.object_id:live},turn_sequence=7)
        self.assertIs(live,trigger_usage_plan(state,deepcopy(live),p,(held,)).live_source)
        commit_trigger_usage(live,ability_id='ab1',turn_sequence=7)
        live.controller='C'
        self.assertIsNone(trigger_usage_plan(state,deepcopy(live),p,()))
        state.turn_sequence=8
        self.assertIs(live,trigger_usage_plan(state,deepcopy(live),p,(held,)).live_source)

    def test_pending_claims_separate_sources_and_abilities_and_reject_malformed_frames(self):
        card=source();p=program();state=SimpleNamespace(cards={},turn_sequence=7)
        other=program(key='fixture:quota:ab2',ability='ab2')
        self.assertIsNotNone(trigger_usage_plan(state,card,p,(pending(card,other),)))
        for change in ({'schema_version':True},{'object_id':'other'},{'program_key':'other'},
                {'turn_sequence':True},{'turn_sequence':8},{'ability_id':''},{'ability_id':'forged'},{'extra':0}):
            held=pending(card,p);held.context[EVENT_SOURCE_USAGE_CONTEXT].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):trigger_usage_plan(state,card,p,(held,))
        held=pending(card,p);held.context={}
        with self.assertRaises(ValueError):trigger_usage_plan(state,card,p,(held,))

    def test_historical_live_only_path_and_empty_context_remain_unchanged(self):
        p=program(version=1,event='life.gained');card=source();state=SimpleNamespace(cards={card.object_id:card},turn_sequence=7)
        self.assertEqual({},event_source_usage_context(card,p,7))
        self.assertIs(card,trigger_usage_plan(state,card,p,()).live_source)
        state.cards={}
        self.assertIsNone(trigger_usage_plan(state,card,p,()))

    def test_new_authority_is_inferred_and_verified_withdrawal_leaves_old_limits_exact(self):
        import json
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        registry=load_default_capability_registry()
        death=query_record('Whenever one or more other creatures die, draw a card. This ability triggers only once each turn.')
        counter=query_record('Whenever one or more +1/+1 counters are put on this creature, draw a card. This ability triggers only once each turn.')
        ir=compile_oracle_card(death,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual(2,ir.faces[0].nodes[0].trigger_limit['schema_version'])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(row for row in raw['capabilities'] if row['id']==DEPARTURE_TRIGGER_LIMIT_CAPABILITY).update(
            status='blocked',blockers=['Departure incarnation authority withdrawn'])
        blocked=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        blocked.mark_evidence_verified(validate_capability_evidence_index(index,registry=blocked))
        self.assertNotEqual('exact',compile_oracle_card(death,capability_registry=blocked,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(counter,capability_registry=blocked,capability_profile='commander_review').status)
        from quorune.semantics import SemanticProgram
        from dataclasses import fields
        actual=SemanticProgram(key='fixture:infer-quota',label='Quota',active_zone='battlefield',event='creature.dies',
            effects=list(ir.faces[0].nodes[0].effects))
        forged=SimpleNamespace(**{field.name:getattr(actual,field.name) for field in fields(actual)})
        forged.trigger_limit=TriggerLimitSpec(2,source_scope='event_incarnation').to_dict()
        forged.capability_dependencies=[]
        bound=bind_semantic_program_runtime(forged,capability_registry=registry,profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertTrue(any(DEPARTURE_TRIGGER_LIMIT_CAPABILITY in row for row in bound['blockers']))


class DepartureTriggerLimitNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'departure-limits.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-state-trigger-cards.json',ROOT/'tests/fixtures/departure-trigger-limit-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Departure quotas',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def closed(self,session,card):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        p=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        bound=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
        self.assertEqual('capability_closed',bound['trust_basis'])

    def start(self,session):
        from quorune.turn_step_owner import TURN_STEPS
        e=session.engine;e.permissions.invalidate_current();e.state.pending_decision=None
        e.state.active_player='A';e.state.started=True;e.state.phase='precombat_main';e.state.step='main'
        e.state.phase_index=TURN_STEPS.index(('precombat_main','main'))
        for seat in 'ABCD':e.state.players[seat].mana_pool.update(W=8,U=8,B=10,R=8,C=20)
        e._grant_priority('A');e.pump();self.checkpoint(session)

    def priority(self,session,seat):
        for _ in range(12):
            self.assertEqual('priority',session.state.pending_decision.kind)
            if session.pending_principals()==['pilot:'+seat] or tuple(session.pending_principals())==('pilot:'+seat,):return
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Requested principal did not receive priority')

    def cast(self,session,spell,*,seat='A',targets=()):
        from quorune.record import authoritative_state_hash
        self.closed(session,spell);self.priority(session,seat)
        actions=session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        offer=next(row for row in actions if row.get('card')==spell.ref)
        payload={'action_id':offer['id'],'targets':list(targets),'pay':'auto'}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',payload).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:'+seat,payload);self.assertTrue(result.ok,result.summary)

    def advance(self,session,predicate):
        for _ in range(64):
            if predicate():return
            self.assertEqual('priority',session.state.pending_decision.kind)
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Expected gameplay boundary was not reached')

    def test_actual_used_then_departed_source_keeps_spent_quota_after_its_trigger_is_countered(self):
        s=self.session(649001);e=s.engine
        observer=self.add(e,'Morbid Opportunist',ref='USED_MORBID');self.closed(s,observer)
        victim=self.add(e,'Generic Bound Body',seat='C',ref='FIRST_VICTIM')
        self.add(e,'Generic Bound Body',seat='B',ref='SECOND_VICTIM')
        murder=self.add(e,'Murder',zone='hand',ref='FIRST_MURDER')
        wipe=self.add(e,'Day of Judgment',zone='hand',ref='USED_QUOTA_WIPE')
        stifle=self.add(e,'Stifle',seat='B',zone='hand',ref='QUOTA_STIFLE')
        self.start(s);initial=len(s.state.players['A'].zones['hand'])
        self.cast(s,murder,targets=[victim.ref])
        self.advance(s,lambda:any(item.kind=='triggered_ability' for item in s.state.stack))
        first=next(item for item in s.state.stack if item.kind=='triggered_ability')
        ability=first.context[EVENT_SOURCE_USAGE_CONTEXT]['ability_id']
        self.assertEqual(s.state.turn_sequence,s.state.cards[observer.object_id].annotations['once_per_turn_triggers'][ability])
        s=self.replay(s,load=True)
        trigger=next(item for item in s.state.stack if item.kind=='triggered_ability')
        self.cast(s,stifle,seat='B',targets=[trigger.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(initial-1,len(s.state.players['A'].zones['hand']))
        self.cast(s,wipe);self.assertIsNone(self.resolve(s))
        self.assertEqual(initial-2,len(s.state.players['A'].zones['hand']))
        self.assertEqual('graveyard',s.state.cards[observer.object_id].zone)
        self.assertNotIn('once_per_turn_triggers',s.state.cards[observer.object_id].annotations)
        self.replay(s,load=True)

    def test_actual_unused_simultaneously_departed_copies_trigger_once_each_with_atomic_rollback(self):
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        from quorune.rules.trigger_limits import trigger_usage_plan as real_plan
        s=self.session(649002);e=s.engine
        a=self.add(e,'Morbid Opportunist',ref='FIRST_UNUSED_MORBID')
        b=self.add(e,'Morbid Opportunist',seat='B',ref='SECOND_UNUSED_MORBID')
        for card in (a,b):self.closed(s,card)
        others=[self.add(e,'Generic Bound Body',seat=seat,ref='BATCH_VICTIM_'+seat) for seat in 'CD']
        wipe=self.add(e,'Day of Judgment',zone='hand',ref='UNUSED_QUOTA_WIPE')
        self.start(s);hands={seat:len(s.state.players[seat].zones['hand']) for seat in 'AB'}
        identities={card.object_id:card.logical_object_id for card in (a,b)}
        self.cast(s,wipe)
        while s.pending_principals()[0]!='pilot:D':
            self.assertTrue(s.act(s.pending_principals()[0],{'action_id':'pass'}).ok)
        before=authoritative_state_hash(s.state);calls=[]
        def fail_second(*args,**kwargs):
            plan=real_plan(*args,**kwargs);calls.append(plan)
            if len(calls)==2:raise GameRuleError('Injected quota failure after one collected trigger')
            return plan
        with patch('quorune.rules.trigger_limits.trigger_usage_plan',side_effect=fail_second):
            failed=s.act('pilot:D',{'action_id':'pass'})
        self.assertFalse(failed.ok);self.assertEqual(2,len(calls))
        self.assertEqual(before,authoritative_state_hash(s.state))
        for card in (a,b,*others):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        self.assertTrue(s.act('pilot:D',{'action_id':'pass'}).ok)
        triggers=[item for item in s.state.stack if item.kind=='triggered_ability']
        self.assertEqual(['A','B'],[item.controller for item in triggers])
        self.assertEqual(2,len(triggers))
        for item in triggers:
            claim=item.context[EVENT_SOURCE_USAGE_CONTEXT]
            self.assertEqual(identities[item.source_object_id],claim['logical_object_id'])
        for card in (a,b,*others):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(hands['A'],len(s.state.players['A'].zones['hand']))
        self.assertEqual(hands['B']+1,len(s.state.players['B'].zones['hand']))
        for card in (a,b):self.assertNotIn('once_per_turn_triggers',s.state.cards[card.object_id].annotations)
        self.replay(s,load=True)

    def test_actual_control_change_preserves_usage_and_blink_grants_fresh_incarnation(self):
        s=self.session(649003);e=s.engine
        observer=self.add(e,'Morbid Opportunist',ref='BORROWED_MORBID');self.closed(s,observer)
        orrery=self.add(e,'Vedalken Orrery',seat='B',ref='BORROWER_ORRERY');self.closed(s,orrery)
        victims=[self.add(e,'Generic Bound Body',seat='C',ref='CONTROL_VICTIM_'+str(i)) for i in range(3)]
        murders=[self.add(e,'Murder',zone='hand',ref='CONTROL_MURDER_'+str(i)) for i in range(3)]
        borrow=self.add(e,'Act of Treason',seat='B',zone='hand',ref='BORROW_MORBID')
        blink=self.add(e,'Cloudshift',seat='B',zone='hand',ref='BLINK_MORBID')
        self.start(s);a_hand=len(s.state.players['A'].zones['hand']);b_hand=len(s.state.players['B'].zones['hand'])
        old_identity=observer.logical_object_id
        self.cast(s,murders[0],targets=[victims[0].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(a_hand,len(s.state.players['A'].zones['hand']))
        self.cast(s,borrow,seat='B',targets=[observer.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('B',s.state.cards[observer.object_id].controller)
        self.assertEqual(old_identity,s.state.cards[observer.object_id].logical_object_id)
        self.cast(s,murders[1],targets=[victims[1].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(b_hand-1,len(s.state.players['B'].zones['hand']))
        self.cast(s,blink,seat='B',targets=[observer.ref]);self.assertIsNone(self.resolve(s))
        current=s.state.cards[observer.object_id]
        self.assertNotEqual(old_identity,current.logical_object_id)
        self.assertEqual(('A','B'),(current.owner,current.controller))
        self.cast(s,murders[2],targets=[victims[2].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(b_hand-1,len(s.state.players['B'].zones['hand']))
        self.assertEqual(a_hand-2,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)

    def test_actual_departed_borrowed_vraan_uses_event_controller_and_self_alone_does_not_trigger(self):
        s=self.session(649004);e=s.engine
        observer=self.add(e,'Vraan, Executioner Thane',ref='BORROWED_VRAAN');self.closed(s,observer)
        orrery=self.add(e,'Vedalken Orrery',seat='B',ref='VRAAN_BORROWER_ORRERY');self.closed(s,orrery)
        borrow=self.add(e,'Act of Treason',seat='B',zone='hand',ref='VRAAN_BORROW_SPELL')
        self.add(e,'Generic Bound Body',seat='B',ref='VRAAN_OTHER_CREATURE')
        wipe=self.add(e,'Day of Judgment',zone='hand',ref='VRAAN_QUOTA_WIPE')
        self.start(s);self.cast(s,borrow,seat='B',targets=[observer.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(('A','B'),(s.state.cards[observer.object_id].owner,s.state.cards[observer.object_id].controller))
        self.cast(s,wipe);self.assertIsNone(self.resolve(s))
        self.assertEqual({'A':38,'B':42,'C':38,'D':38},{seat:s.state.players[seat].life for seat in 'ABCD'})
        self.assertEqual('graveyard',s.state.cards[observer.object_id].zone)
        self.assertEqual('A',s.state.cards[observer.object_id].controller)
        self.assertNotIn('once_per_turn_triggers',s.state.cards[observer.object_id].annotations)
        self.replay(s,load=True)
        alone=self.session(649005);e=alone.engine
        morbid=self.add(e,'Morbid Opportunist',ref='LONE_MORBID');self.closed(alone,morbid)
        kill=self.add(e,'Murder',zone='hand',ref='LONE_MORBID_MURDER')
        self.start(alone);hand=len(alone.state.players['A'].zones['hand'])
        self.cast(alone,kill,targets=[morbid.ref]);self.assertIsNone(self.resolve(alone))
        self.assertEqual(hand-1,len(alone.state.players['A'].zones['hand']))
        self.replay(alone,load=True)
