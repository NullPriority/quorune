"""Typed current-turn history conditions keep occurrence custody and time."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_qualified_attack_subjects as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import TurnHistory,TurnHistoryEvent
from quorune.continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind
from quorune.public_history_query_model import PublicHistoryQuerySpec,public_history_query_value,PUBLIC_HISTORY_QUERY_CAPABILITY as CAPABILITY
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class PublicHistoryQueryCompilerTests(unittest.TestCase):
    def test_new_history_schema_round_trips_without_changing_legacy_payloads(self):
        spec=PublicHistoryQuerySpec('creature_died','controller')
        condition=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.HISTORY_COUNT_AT_LEAST,amount=1,schema_version=8,history_query=spec)
        self.assertEqual(condition,FixedPublicStateConditionSpec.from_dict(condition.to_dict()))
        old=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST,amount=20)
        self.assertEqual({'schema_version':1,'kind':'controller_life_at_least','amount':20,'counter_name':None},old.to_dict())
        for change in ({'schema_version':True},{'schema_version':7},{'amount':True},{'kind':'controller_life_at_least'},
                {'extra':None},{'counter_name':'charge'}):
            with self.assertRaises(ValueError):FixedPublicStateConditionSpec.from_dict({**condition.to_dict(),**change})
        for change in ({'event':'card_drawn_secretly'},{'relation':'owner'},{'schema_version':True},
                {'reduction':'sum_amount'},{'types_all':['unknown']},{'extra':None}):
            with self.assertRaises(ValueError):PublicHistoryQuerySpec.from_dict({**spec.to_dict(),**change})

    def test_history_queries_use_sealed_roles_and_per_player_amounts_without_current_objects(self):
        history=TurnHistory(turn_sequence=4,events=[
            TurnHistoryEvent('creature_died',actor='A',object_incarnation='borrowed@1',types=('artifact','creature')),
            TurnHistoryEvent('creature_died',actor='B',object_incarnation='other@1',types=('creature',)),
            TurnHistoryEvent('permanent_entered',actor='A',object_incarnation='one@1',types=('artifact',)),
            TurnHistoryEvent('permanent_entered',actor='A',object_incarnation='one@3',types=('artifact','creature')),
            TurnHistoryEvent('player_lost_life',target='B',amount=2),TurnHistoryEvent('player_lost_life',target='C',amount=3),
            TurnHistoryEvent('player_gained_life',target='A',amount=2),TurnHistoryEvent('player_lost_life',target='A',amount=2)])
        state=SimpleNamespace(turn_history=history,turn_sequence=4,players={s:SimpleNamespace(in_game=True) for s in 'ABCD'},cards={})
        before=history.to_dict()
        self.assertEqual(1,public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died')))
        self.assertEqual(2,public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died','any_player')))
        self.assertEqual(2,public_history_query_value(state,'A',PublicHistoryQuerySpec('permanent_entered',types_all=('artifact',))))
        self.assertEqual(3,public_history_query_value(state,'A',PublicHistoryQuerySpec('player_lost_life','opponents',reduction='max_player_amount')))
        self.assertEqual(5,public_history_query_value(state,'A',PublicHistoryQuerySpec('player_lost_life','opponents',reduction='sum_amount')))
        self.assertEqual(before,history.to_dict())
        state.players['B'].in_game=False
        self.assertEqual(2,public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died','any_player')))
        self.assertEqual(0,public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died','opponents')))
        for missing in (None,TurnHistory(turn_sequence=3)):
            state.turn_history=missing;self.assertIsNone(public_history_query_value(state,'A',PublicHistoryQuerySpec('creature_died')))
        state.turn_history=history
        self.assertIsNone(public_history_query_value(state,'A',PublicHistoryQuerySpec('permanent_left')))

    def test_current_turn_history_grammar_composes_with_independent_trigger_results(self):
        registry=load_default_capability_registry()
        for condition in ('a creature died under your control this turn','three or more artifacts entered the battlefield under your control this turn',
                'two or more creatures entered the battlefield under your control this turn','you gained or lost life this turn',
                'a player lost 4 or more life this turn','you sacrificed a creature this turn'):
            parsed=fixed_public_state_condition(condition,source_name='Generic Observer');self.assertIsNotNone(parsed,condition)
            text='At the beginning of your end step, if '+condition+', draw a card.'
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(text,ir.material_residuals));self.assertIn(CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
        combined=fixed_public_state_condition('a creature died under your control this turn and you gained life this turn',source_name='Generic Observer')
        self.assertIsNotNone(combined);self.assertEqual(2,len(combined.terms))
        for condition in ('a creature died under your control last turn','you will gain life this turn','a creature of the chosen type died this turn'):
            self.assertIsNone(fixed_public_state_condition(condition,source_name='Generic Observer'))

    def test_ignoring_sealed_event_actor_mutant_is_killed(self):
        import quorune.public_history_query_model as owner
        state=SimpleNamespace(turn_sequence=1,players={s:SimpleNamespace(in_game=True) for s in 'AB'},
            turn_history=TurnHistory(turn_sequence=1,events=[TurnHistoryEvent('creature_died',actor='B',object_incarnation='foreign@1',types=('creature',))]))
        spec=PublicHistoryQuerySpec('creature_died','controller')
        def witness():self.assertEqual(0,owner.public_history_query_value(state,'A',spec))
        witness()
        with patch.object(owner,'public_history_query_value',return_value=1),self.assertRaises(AssertionError):witness()

    def test_verified_history_query_withdrawal_preserves_unrelated_flying(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in value['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['history query owner withdrawn'])
        registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        text='At the beginning of your end step, if a creature died under your control this turn, draw a card.'
        self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record('Flying'),capability_registry=registry).status)


class PublicHistoryQueryNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.QualifiedAttackNativeTests.session
    add=native.QualifiedAttackNativeTests.add
    prepare=native.QualifiedAttackNativeTests.prepare
    cast=native.QualifiedAttackNativeTests.cast
    cast_for=native.QualifiedAttackNativeTests.cast_for
    priority=native.QualifiedAttackNativeTests.priority
    action=native.QualifiedAttackNativeTests.action
    checkpoint=native.QualifiedAttackNativeTests.checkpoint
    resolve=native.QualifiedAttackNativeTests.resolve
    replay=native.QualifiedAttackNativeTests.replay
    assert_closed=native.QualifiedAttackNativeTests.assert_closed
    until=native.QualifiedAttackNativeTests.until
    activate=native.QualifiedAttackNativeTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'public-history.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/public-history-query-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public history',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_runtime_history_query_dependency_is_inferred_from_intervening_condition(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603632001);source=self.add(session.engine,'Denethor, Ruling Steward');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def end_step(self,session):
        self.until(session,lambda s:s.state.phase=='ending' and s.state.step=='end_step')
        while session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
            decision=session.state.pending_decision
            group=next(g for g in session.state.pending_trigger_batches[0].groups if g.controller==decision.actors[0])
            result=session.act(session.pending_principals()[0],{'action_id':'order','triggers':[i.ref for i in group.items]});self.assertTrue(result.ok,result.summary)

    def test_actual_denethor_controlled_sacrifice_counts_death_and_queued_token_survives_source_bounce(self):
        session=self.session(603632010);source=self.add(session.engine,'Denethor, Ruling Steward',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');removal=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'W':1,'B':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':2});self.assertIsNone(self.resolve(session))
        self.priority(session,'A');offered=next(r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'cost_cards':[body.ref],'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.end_step(session);self.assertTrue(session.state.stack)
        self.cast(session,'B',removal,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens));self.replay(session,load=True)

    def test_actual_denethor_exiled_creature_does_not_satisfy_death_condition(self):
        session=self.session(603632020);source=self.add(session.engine,'Denethor, Ruling Steward',zone='hand')
        body=self.add(session.engine,'Grizzly Bears');exile=self.add(session.engine,'Final Reward',seat='B',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,exile)
        self.prepare(session,mana={'C':1,'W':1,'B':1});session.state.players['B'].mana_pool.update({'C':4,'B':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.cast(session,'B',exile,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[body.object_id].zone);self.end_step(session)
        self.assertFalse(any(i.source_object_id==source.object_id for i in session.state.stack));self.replay(session,load=True)

    def test_missing_history_is_unavailable_in_zero_count_and_boolean_conditions(self):
        from quorune.card_programs.runtime import fixed_public_state_condition_matches
        from quorune.continuous_conditions import FixedPublicStateConditionError
        session=self.session(603632025);source=self.add(session.engine,'Denethor, Ruling Steward')
        session.state.turn_history=None
        zero=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.HISTORY_COUNT_AT_MOST,amount=0,schema_version=8,
            history_query=PublicHistoryQuerySpec('creature_died'))
        positive=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST,amount=0)
        combined=FixedPublicStateConditionSpec(FixedPublicStateConditionKind.ANY,schema_version=6,terms=(positive,zero))
        for condition in (zero,combined):
            with self.assertRaises(FixedPublicStateConditionError):fixed_public_state_condition_matches(session.state,source,condition,
                public_object_resolver=None,quantity_resolver=None,require_available=True)
        self.assertFalse(fixed_public_state_condition_matches(session.state,source,zero,
            public_object_resolver=None,quantity_resolver=None))

    def test_actual_malcator_counts_repeat_artifact_entries_after_the_object_leaves(self):
        session=self.session(603632030);source=self.add(session.engine,'Malcator, Purity Overseer',zone='hand')
        artifact=self.add(session.engine,'Memnite',zone='hand');blink=self.add(session.engine,'Flicker of Fate',zone='hand')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'W':2,'U':1});session.state.players['B'].mana_pool['U']=1;self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',artifact,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',blink,[artifact.ref]);self.assertIsNone(self.resolve(session));self.cast(session,'B',bounce,[artifact.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[artifact.object_id].zone)
        self.end_step(session);self.assertTrue(any(i.source_object_id==source.object_id for i in session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(2,sum(c.zone=='battlefield' and c.is_token for c in session.state.cards.values()));self.replay(session,load=True)

    def test_actual_soothsayer_life_gain_and_loss_do_not_cancel_history(self):
        session=self.session(603632040);source=self.add(session.engine,'Starlit Soothsayer',zone='hand')
        gain=self.add(session.engine,"Chaplain's Blessing",zone='hand');loss=self.add(session.engine,'Sign in Blood',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'B':3,'W':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'B':2,'W':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',gain,reserve={'B':2});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',loss,['A']);self.assertIsNone(self.resolve(session));self.end_step(session)
        self.assertTrue(any(i.source_object_id==source.object_id for i in session.state.stack))
        session=self.replay(session,load=True);decision=self.resolve(session);self.assertIsNotNone(decision)
        expected=session.state.cards[session.state.players['A'].zones['library'][-1]].ref
        for seat in 'BCD':self.assertNotIn(expected,json.dumps(session.packet('pilot:'+seat,full=True)))
        result=session.act('pilot:A',{'action_id':'choose','cards':{'top':[expected],'graveyard':[]}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_actual_soothsayer_without_life_event_does_not_create_a_trigger(self):
        session=self.session(603632050);source=self.add(session.engine,'Starlit Soothsayer',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'B':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.end_step(session)
        self.assertFalse(any(i.source_object_id==source.object_id for i in session.state.stack));self.replay(session,load=True)
