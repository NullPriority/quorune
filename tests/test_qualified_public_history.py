"""Origin and arrived-card type history retain exact temporal facts."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_public_history_queries as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import TurnHistory,TurnHistoryEvent
from quorune.public_history_query_model import (PublicHistoryQuerySpec,public_history_query_value,
    SPELL_ORIGIN_HISTORY_CAPABILITY as CAST,GRAVEYARD_TYPE_HISTORY_CAPABILITY as ARRIVAL)
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

PERMANENTS=('artifact','battle','creature','enchantment','land','planeswalker')


class QualifiedPublicHistoryCompilerTests(unittest.TestCase):
    def test_qualified_query_versions_and_history_modes_preserve_legacy_payloads(self):
        from quorune.turn_history import reset_turn_history,roll_turn_history
        old=PublicHistoryQuerySpec('spell_cast');self.assertNotIn('origin',old.to_dict())
        for query in (PublicHistoryQuerySpec('spell_cast',schema_version=2,origin='hand'),
                PublicHistoryQuerySpec('card_arrived_graveyard',types_any=PERMANENTS,schema_version=2)):
            self.assertEqual(query,PublicHistoryQuerySpec.from_dict(query.to_dict()))
            for change in ({'schema_version':True},{'schema_version':1},{'origin':'unknown'},{'extra':1}):
                with self.assertRaises(ValueError):PublicHistoryQuerySpec.from_dict({**query.to_dict(),**change})
        history=TurnHistory(turn_sequence=1,arrival_history_version=1,spell_origin_history_version=1,graveyard_type_history_version=1)
        for changed in (TurnHistory.from_dict(history.to_dict()),reset_turn_history(history,2),
                roll_turn_history(history,next_turn_sequence=2,previous_active_player='A')):
            self.assertEqual((1,1),(changed.spell_origin_history_version,changed.graveyard_type_history_version))
        legacy=TurnHistory(turn_sequence=1,arrival_history_version=1).to_dict()
        self.assertEqual(legacy,TurnHistory.from_dict(legacy).to_dict());self.assertNotIn('spell_origin_history_version',legacy)
        for name in ('spell_origin_history_version','graveyard_type_history_version'):
            for value in (True,False,0,2,'1'):
                with self.assertRaises(ValueError):TurnHistory.from_dict({**history.to_dict(),name:value})
        with self.assertRaises(ValueError):TurnHistory(graveyard_type_history_version=1)

    def test_qualified_history_filters_sealed_origin_owner_and_arriving_card_types(self):
        history=TurnHistory(turn_sequence=1,arrival_history_version=1,spell_origin_history_version=1,graveyard_type_history_version=1,
            events=[TurnHistoryEvent('spell_cast',actor='A',origin='command'),TurnHistoryEvent('spell_cast',actor='B',origin='hand'),
                TurnHistoryEvent('card_arrived_graveyard',actor='A',origin='library',object_incarnation='land@1',types=('land',)),
                TurnHistoryEvent('card_arrived_graveyard',actor='A',origin='stack',object_incarnation='instant@2',types=('instant',)),
                TurnHistoryEvent('card_arrived_graveyard',actor='B',origin='battlefield',object_incarnation='foreign@1',types=('creature',))])
        state=SimpleNamespace(turn_history=history,turn_sequence=1,players={s:SimpleNamespace(in_game=True) for s in 'ABCD'},cards={})
        before=history.to_dict();hand=PublicHistoryQuerySpec('spell_cast',schema_version=2,origin='hand')
        arrived=PublicHistoryQuerySpec('card_arrived_graveyard',types_any=PERMANENTS,schema_version=2)
        self.assertEqual(0,public_history_query_value(state,'A',hand));self.assertEqual(1,public_history_query_value(state,'A',arrived))
        self.assertEqual(before,history.to_dict())
        history.events.append(TurnHistoryEvent('spell_cast',actor='A'))
        self.assertIsNone(public_history_query_value(state,'A',hand),'An unsealed origin is unavailable, never a known nonhand cast')
        history.spell_origin_history_version=None;history.graveyard_type_history_version=None
        self.assertIsNone(public_history_query_value(state,'A',hand));self.assertIsNone(public_history_query_value(state,'A',arrived))

    def test_arrival_producer_uses_owner_new_zone_types_and_retains_legacy_empty_types(self):
        from quorune.replacement.immutable import FrozenMap
        from quorune.zone_trigger_events import ZoneChangeOccurrence
        from quorune.zone_trigger_processing import record_zone_change_history
        occurrence=ZoneChangeOccurrence(object_id='borrowed',card_ref='opaque',owner='A',origin='battlefield',destination='graveyard',
            previous_controller='B',current_controller='A',previous_logical_object_id='borrowed@0',current_logical_object_id='borrowed@1',
            zone_change_counter=1,token=False,card_object=True,previous_characteristics=FrozenMap({'type_line':'Creature — Bear'}),
            current_characteristics=FrozenMap({'type_line':'Instant'}))
        class Host:
            def __init__(self,mode):self.state=SimpleNamespace(turn_history=TurnHistory(arrival_history_version=1,graveyard_type_history_version=mode));self.rows=[]
            def _record_turn_history(self,kind,**facts):self.rows.append((kind,facts))
        current=Host(1);record_zone_change_history(current,occurrence)
        arrival=next(facts for kind,facts in current.rows if kind=='card_arrived_graveyard')
        self.assertEqual('A',arrival['actor']);self.assertEqual({'instant'},arrival['types']);self.assertEqual('borrowed@1',arrival['object_incarnation'])
        old=Host(None);record_zone_change_history(old,occurrence)
        self.assertEqual(set(),next(facts for kind,facts in old.rows if kind=='card_arrived_graveyard')['types'])
        token=Host(1);record_zone_change_history(token,replace(occurrence,token=True,card_object=False))
        self.assertFalse(any(kind=='card_arrived_graveyard' for kind,_ in token.rows))

    def test_qualified_history_grammar_and_runtime_capabilities_are_independent(self):
        from quorune.public_history_query_model import public_history_query_dependencies
        registry=load_default_capability_registry()
        for text,cap in (("you haven't cast a spell from your hand this turn",CAST),('you descended this turn',ARRIVAL)):
            condition=fixed_public_state_condition(text,source_name='Generic Observer');self.assertIsNotNone(condition)
            self.assertIn(cap,public_history_query_dependencies(condition.to_dict()))
            ir=compile_oracle_card(query_record('At the beginning of your end step, if '+text+', draw a card.'),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals);self.assertIn(cap,ir.faces[0].nodes[0].capability_dependencies)
        for text in ('you descended twice this turn','you descended last turn',"you haven't cast a spell this game"):
            self.assertIsNone(fixed_public_state_condition(text,source_name='Generic Observer'))

    def test_ignoring_cast_origin_and_arrived_card_type_mutants_are_killed(self):
        import quorune.public_history_query_model as owner
        state=SimpleNamespace(turn_sequence=1,players={'A':SimpleNamespace(in_game=True)},
            turn_history=TurnHistory(turn_sequence=1,arrival_history_version=1,spell_origin_history_version=1,graveyard_type_history_version=1,
                events=[TurnHistoryEvent('spell_cast',actor='A',origin='command'),
                    TurnHistoryEvent('card_arrived_graveyard',actor='A',origin='stack',object_incarnation='nonpermanent@1',types=('instant',))]))
        queries=(PublicHistoryQuerySpec('spell_cast',schema_version=2,origin='hand'),
            PublicHistoryQuerySpec('card_arrived_graveyard',types_any=PERMANENTS,schema_version=2))
        original=owner.public_history_query_value
        def mutant(s,c,q):return original(s,c,replace(q,origin=None,types_any=(),schema_version=1))
        for query in queries:
            def witness():self.assertEqual(0,owner.public_history_query_value(state,'A',query))
            witness()
            with patch.object(owner,'public_history_query_value',side_effect=mutant),self.assertRaises(AssertionError):witness()

    def test_verified_each_qualified_owner_withdrawal_keeps_unqualified_history(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        current=load_default_capability_registry()
        for cap,text in ((CAST,"you haven't cast a spell from your hand this turn"),(ARRIVAL,'you descended this turn')):
            self.assertEqual('exact',compile_oracle_card(query_record('At the beginning of your end step, if '+text+', draw a card.'),capability_registry=current).status)
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['qualified owner withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record('At the beginning of your end step, if '+text+', draw a card.'),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(query_record('At the beginning of your end step, if a creature died under your control this turn, draw a card.'),capability_registry=registry).status)


class QualifiedPublicHistoryNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.PublicHistoryQueryNativeTests.session
    add=native.PublicHistoryQueryNativeTests.add
    prepare=native.PublicHistoryQueryNativeTests.prepare
    cast=native.PublicHistoryQueryNativeTests.cast
    cast_for=native.PublicHistoryQueryNativeTests.cast_for
    action=native.PublicHistoryQueryNativeTests.action
    priority=native.PublicHistoryQueryNativeTests.priority
    checkpoint=native.PublicHistoryQueryNativeTests.checkpoint
    resolve=native.PublicHistoryQueryNativeTests.resolve
    replay=native.PublicHistoryQueryNativeTests.replay
    assert_closed=native.PublicHistoryQueryNativeTests.assert_closed
    end_step=native.PublicHistoryQueryNativeTests.end_step

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'qualified-history.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-public-history-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified history',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def until(self,session,predicate):
        for _ in range(300):
            session.engine.pump()
            if predicate(session):return
            decision=session.state.pending_decision;self.assertIsNotNone(decision)
            principal=session.pending_principals()[0]
            if decision.kind=='priority':command={'action_id':'pass'}
            elif decision.kind=='combat.blockers':command={'action_id':'block','blocks':{}}
            elif decision.kind=='combat.attackers':command={'action_id':'attack','attackers':{}}
            elif decision.kind=='cleanup.discard':
                ctx=session.packet(principal,full=True)['decision']['ctx']
                command={'action_id':'discard','cards':[row['id'] for row in ctx['hand'][:ctx['count']]]}
            elif decision.kind=='trigger.order':
                group=next(g for g in session.state.pending_trigger_batches[0].groups if g.controller==decision.actors[0])
                command={'action_id':'order','triggers':[i.ref for i in group.items]}
            else:self.fail('Unexpected qualified history decision: '+decision.kind)
            result=session.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.fail('Qualified history sequence did not reach the expected state')

    def finish_twiddle(self,session):
        decision=self.resolve(session);self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        result=session.act(session.pending_principals()[0],{'action_id':'choose','choice':'tap'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))

    def test_new_history_modes_bind_manifest_and_absence_keeps_old_checkpoint(self):
        from quorune.model import GameState
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        session=self.session(603633001);raw=session.state.to_dict();versions=format_state_versions(session.state)
        for name in ('spell_origin_history_version','graveyard_type_history_version'):
            self.assertEqual(1,versions[name]);changed=deepcopy(versions);changed[name]=0
            with self.assertRaises(ValueError):validate_state_versions({'format':changed},session.state)
        validate_state_versions({'format':versions},session.state)
        old=deepcopy(raw)
        for name in ('spell_origin_history_version','graveyard_type_history_version'):old['turn_history'].pop(name)
        restored=GameState.from_dict(old);self.assertEqual(old,restored.to_dict())
        for name in ('spell_origin_history_version','graveyard_type_history_version'):self.assertNotIn(name,format_state_versions(restored))

    def test_runtime_qualified_history_dependencies_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603633002)
        for name,cap in (('Wrangler of the Damned',CAST),('Enterprising Scallywag',ARRIVAL)):
            source=self.add(session.engine,name,ref='runtime-'+str(len(session.state.cards)));checked=0
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                if cap not in program.capability_dependencies:continue
                checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=cap)
                changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])
            self.assertGreater(checked,0)

    def test_actual_legacy_mode_cast_keeps_origin_absent_and_arrival_types_empty(self):
        session=self.session(603633003);body=self.add(session.engine,'Grizzly Bears');spell=self.add(session.engine,'Twiddle',zone='hand')
        session.state.turn_history.spell_origin_history_version=None;session.state.turn_history.graveyard_type_history_version=None
        self.prepare(session,mana={'U':1});self.checkpoint(session)
        self.cast(session,'A',spell,[body.ref]);self.finish_twiddle(session)
        casts=[e for e in session.state.turn_history.events if e.kind=='spell_cast'];self.assertEqual(1,len(casts));self.assertNotIn('origin',casts[0].to_dict())
        arrivals=[e for e in session.state.turn_history.events if e.kind=='card_arrived_graveyard'];self.assertEqual(1,len(arrivals));self.assertEqual((),arrivals[0].types)
        self.replay(session,load=True)

    def wrangler_scene(self,seed,*,responding='A'):
        session=self.session(seed);source=self.add(session.engine,'Wrangler of the Damned',zone='hand')
        spell=self.add(session.engine,'Twiddle',seat=responding,zone='hand');self.add(session.engine,'Island',seat=responding,ref='response-island')
        self.assert_closed(session,source);self.prepare(session,active='D',mana={'C':3,'W':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.until(session,lambda s:s.state.active_player=='A' and s.state.phase=='precombat_main' and s.state.step=='main')
        self.assertEqual([],list(session.state.turn_history.events));self.end_step(session)
        self.assertTrue(any(i.source_object_id==source.object_id for i in session.state.stack))
        return session,source,spell

    def test_actual_wrangler_rechecks_after_own_hand_instant_and_preserves_rollback_replay(self):
        session,source,spell=self.wrangler_scene(603633010)
        self.priority(session,'A');offered=next(r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='cast:'+spell.ref)
        command={'action_id':offered['id'],'targets':[source.ref],'pay':'auto'};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.finish_twiddle(session)
        self.assertFalse(any(c.zone=='battlefield' and c.is_token for c in session.state.cards.values()))
        self.assertTrue(any(e.kind=='spell_cast' and e.actor=='A' and e.origin=='hand' for e in session.state.turn_history.events));self.replay(session,load=True)

    def test_actual_wrangler_foreign_hand_instant_does_not_suppress_queued_token(self):
        session,source,spell=self.wrangler_scene(603633020,responding='B')
        self.cast(session,'B',spell,[source.ref]);session=self.replay(session,load=True);self.finish_twiddle(session)
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens));self.assertIn('flying',session.engine._combat_keywords(tokens[0]));self.replay(session,load=True)

    def test_actual_own_turn_wrangler_cast_prevents_end_step_trigger(self):
        session=self.session(603633030);source=self.add(session.engine,'Wrangler of the Damned',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'W':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.end_step(session)
        self.assertFalse(any(i.source_object_id==source.object_id for i in session.state.stack));self.replay(session,load=True)

    def test_actual_jem_command_cast_qualifies_private_draw_without_a_hand_cast(self):
        session=self.session(603633035);source=self.add(session.engine,'Jem Lightfoote, Sky Explorer')
        commander=next(session.state.cards[oid] for oid in session.state.players['A'].zones['command'])
        self.assert_closed(session,source);self.prepare(session,mana={'W':1});self.checkpoint(session)
        self.cast(session,'A',commander);self.assertIsNone(self.resolve(session))
        self.assertTrue(any(e.kind=='spell_cast' and e.actor=='A' and e.origin=='command' for e in session.state.turn_history.events))
        self.end_step(session);self.assertTrue(any(i.source_object_id==source.object_id for i in session.state.stack))
        before=len(session.state.players['A'].zones['hand']);expected=session.state.cards[session.state.players['A'].zones['library'][-1]].ref
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(before+1,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':self.assertNotIn(expected,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_scallywag_counts_arrival_after_card_returns_and_source_leaves(self):
        session=self.session(603633040);source=self.add(session.engine,'Enterprising Scallywag',zone='hand');body=self.add(session.engine,'Grizzly Bears')
        destroy=self.add(session.engine,'Doom Blade',seat='B',zone='hand');regrow=self.add(session.engine,'Regrowth',zone='hand')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'R':1,'G':1});session.state.players['B'].mana_pool.update({'C':1,'B':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'G':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'B',destroy,[body.ref]);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',regrow,[body.ref]);self.assertIsNone(self.resolve(session));self.assertEqual('hand',session.state.cards[body.object_id].zone)
        self.end_step(session);self.assertTrue(any(i.source_object_id==source.object_id for i in session.state.stack))
        self.cast(session,'B',bounce,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertEqual(1,sum(c.zone=='battlefield' and c.is_token and 'Treasure' in c.printed_name for c in session.state.cards.values()))
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_scallywag_dying_token_and_instant_cards_do_not_qualify(self):
        session=self.session(603633050);source=self.add(session.engine,'Enterprising Scallywag',zone='hand')
        create=self.add(session.engine,'Raise the Alarm',zone='hand');destroy=self.add(session.engine,'Doom Blade',seat='B',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,create)
        self.prepare(session,mana={'C':2,'R':1,'W':1});session.state.players['B'].mana_pool.update({'C':1,'B':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',create);self.assertIsNone(self.resolve(session))
        token=next(c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token)
        self.cast(session,'B',destroy,[token.ref]);self.assertIsNone(self.resolve(session));self.end_step(session)
        self.assertFalse(any(i.source_object_id==source.object_id for i in session.state.stack))
        self.assertFalse(any(e.kind=='card_arrived_graveyard' and 'creature' in e.types and e.actor=='A' for e in session.state.turn_history.events));self.replay(session,load=True)
