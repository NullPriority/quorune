"""CR700.14: actual paid spell mana crosses a per-player current-turn threshold."""
from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_source_attack_alone as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.expend_model import EXPEND_CAPABILITY as CAPABILITY,SpellManaPaymentEvent,SPELL_MANA_PAYMENT_EVENT
from quorune.model import TurnHistory,TurnHistoryEvent
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.spell_mana_payments import current_spell_mana_paid,capture_spell_mana_payment
from quorune.turn_history import reset_turn_history,roll_turn_history
from scripts.build_test_database import build_fixture_database

TEXT='Whenever you expend 4, draw a card.'


class ExpendModelCompilerTests(unittest.TestCase):
    def test_payment_event_codec_and_crossing_truth_table_are_closed(self):
        for before in range(10):
            for paid in range(1,7):
                event=SpellManaPaymentEvent('B',before,before+paid)
                self.assertEqual(event,SpellManaPaymentEvent.from_context(event.to_context()))
                for threshold in (1,4,6,8,12):self.assertEqual(before<threshold<=before+paid,event.crosses(threshold))
        raw=SpellManaPaymentEvent('A',0,4).to_context()
        for change in ({'player':None},{'player':''},{'mana_before':True},{'mana_after':4.0},{'mana_before':4},{'mana_before':-1},{'schema_version':True},{'schema_version':2},{'extra':0}):
            with self.assertRaises((TypeError,ValueError)):SpellManaPaymentEvent.from_context({**raw,**change})

    def test_history_mode_preserves_old_absence_actor_scope_and_each_turn_reset(self):
        old=TurnHistory(turn_sequence=3);self.assertNotIn('spell_mana_history_version',old.to_dict());self.assertEqual(old.to_dict(),TurnHistory.from_dict(old.to_dict()).to_dict())
        self.assertIsNone(current_spell_mana_paid(old,turn_sequence=3,player='A'))
        history=TurnHistory(turn_sequence=3,spell_mana_history_version=1,events=[TurnHistoryEvent('spell_mana_spent',actor='A',amount=3),TurnHistoryEvent('spell_mana_spent',actor='B',amount=7),TurnHistoryEvent('spell_mana_spent',actor='A',amount=2)])
        self.assertEqual(5,current_spell_mana_paid(history,turn_sequence=3,player='A'));self.assertEqual(7,current_spell_mana_paid(history,turn_sequence=3,player='B'));self.assertEqual(0,current_spell_mana_paid(history,turn_sequence=3,player='C'))
        self.assertEqual(history,TurnHistory.from_dict(history.to_dict()))
        for reset in (reset_turn_history(history,4),roll_turn_history(history,next_turn_sequence=4,previous_active_player='A')):
            self.assertEqual(1,reset.spell_mana_history_version);self.assertEqual(0,current_spell_mana_paid(reset,turn_sequence=4,player='A'))
        for version in (True,0,2):
            with self.assertRaises(ValueError):TurnHistory(spell_mana_history_version=version)
        with self.assertRaises(ValueError):TurnHistory(events=list(history.events))
        for amount in (0,-1,True,1.0):
            with self.assertRaises(ValueError):TurnHistoryEvent('spell_mana_spent',actor='A',amount=amount)

    def test_zero_or_legacy_payment_does_not_fabricate_an_occurrence(self):
        state=SimpleNamespace(turn_sequence=3,turn_history=TurnHistory(turn_sequence=3,spell_mana_history_version=1))
        host=SimpleNamespace(state=state,_record_turn_history=lambda *a,**k:self.fail('No positive payment'),_dispatch_semantic_event=lambda *a,**k:self.fail('No crossing'))
        self.assertEqual([],capture_spell_mana_payment(host,player='A',spent={'C':0}));state.turn_history=TurnHistory(turn_sequence=3)
        self.assertEqual([],capture_spell_mana_payment(host,player='A',spent={'G':9}));self.assertFalse(state.turn_history.events)

    def test_expend_grammar_keeps_player_and_before_after_predicates_and_complete_bodies(self):
        registry=load_default_capability_registry()
        from quorune.compiler.fixed_public_action_event_bindings import fixed_public_action_event_binding_spec
        spec=fixed_public_action_event_binding_spec(TEXT)
        self.assertEqual(SPELL_MANA_PAYMENT_EVENT,spec.event);self.assertEqual({'all':[{'field':'player','op':'eq','value':'$source.controller'},{'field':'mana_before','op':'lt','value':4},{'field':'mana_after','op':'gte','value':4}]},spec.condition)
        for text in (TEXT,'Whenever you expend 8, return up to one target permanent card from your graveyard to your hand.',
                'Whenever you expend 4, put a +1/+1 counter on this creature.',
                'When this enchantment enters and whenever you expend 4, put a stash counter on it.'):
            ir=compile_oracle_card(query_record(text),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals)
            node,=ir.faces[0].nodes;self.assertIn(CAPABILITY,node.capability_dependencies);self.assertEqual((text,0,len(text)),(node.text,node.span.start,node.span.end))
        for text in ('Whenever you expend X, draw a card.','Whenever you expend 0, draw a card.','Whenever an opponent expends 4, draw a card.',TEXT+' Rewrite history.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_verified_expend_dependency_withdrawal_keeps_single_draw_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['paid threshold owner withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text in (TEXT,'When this enchantment enters and whenever you expend 4, draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Sorcery'),capability_registry=registry).status)


class ExpendNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.SourceAttackAloneNativeTests.session
    add=native.SourceAttackAloneNativeTests.add
    prepare=native.SourceAttackAloneNativeTests.prepare
    cast=native.SourceAttackAloneNativeTests.cast
    cast_for=native.SourceAttackAloneNativeTests.cast_for
    action=native.SourceAttackAloneNativeTests.action
    priority=native.SourceAttackAloneNativeTests.priority
    checkpoint=native.SourceAttackAloneNativeTests.checkpoint
    resolve=native.SourceAttackAloneNativeTests.resolve
    resolve_top=native.SourceAttackAloneNativeTests.resolve_top
    replay=native.SourceAttackAloneNativeTests.replay
    assert_closed=native.SourceAttackAloneNativeTests.assert_closed
    until=native.SourceAttackAloneNativeTests.until
    trigger_count=native.SourceAttackAloneNativeTests.trigger_count

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'expend.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/expend-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Expend',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def paid(self,session,seat):return current_spell_mana_paid(session.state.turn_history,turn_sequence=session.state.turn_sequence,player=seat)

    def test_actual_boxer_crosses_after_earlier_cast_and_not_again_or_from_an_opponents_spell(self):
        session=self.session(700645001);source=self.add(session.engine,'Bark-Knuckle Boxer',zone='hand');spell=self.add(session.engine,'Divination',zone='hand');later=self.add(session.engine,'Hill Giant',zone='hand')
        foreign=self.add(session.engine,'Revitalize',seat='B',zone='hand',ref='expend-foreign-spell');session.state.players['B'].mana_pool.update({'C':1,'W':1})
        private=self.add(session.engine,'Shock',seat='D',zone='hand',ref='expend-private');self.assert_closed(session,source);self.prepare(session,mana={'C':6,'G':1,'U':1,'R':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':5,'U':1,'R':1});self.assertIsNone(self.resolve(session));self.assertEqual(2,self.paid(session,'A'));self.assertNotIn('indestructible',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.cast(session,'B',foreign);self.assertIsNone(self.resolve(session));self.assertEqual(2,self.paid(session,'A'));self.assertEqual(2,self.paid(session,'B'))
        self.cast_for(session,'A',spell,reserve={'C':3,'R':1});self.assertEqual(1,self.trigger_count(session,source));trigger=next(i for i in session.state.stack if i.source_object_id==source.object_id)
        self.assertEqual((2,5),(trigger.context['mana_before'],trigger.context['mana_after']));self.assertIsNone(self.resolve(session));self.assertIn('indestructible',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.cast(session,'A',later);self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.assertEqual(9,self.paid(session,'A'))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_bruiser_cannot_trigger_for_its_own_five_mana_payment(self):
        session=self.session(700645010);source=self.add(session.engine,'Junkblade Bruiser',zone='hand');later=self.add(session.engine,'Shock',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'R':6});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'R':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(5,self.paid(session,'A'));self.assertEqual((4,5),tuple(session.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        self.cast(session,'A',later,['B']);self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.assertEqual(6,self.paid(session,'A'));self.replay(session,load=True)

    def test_actual_slinger_keeps_payment_time_trigger_when_sacrificed_for_same_cast(self):
        session=self.session(700645020);source=self.add(session.engine,'Teapot Slinger');spell=self.add(session.engine,'Bone Splinters',zone='hand')
        prior=self.add(session.engine,'Divination',zone='hand');target=self.add(session.engine,'Grizzly Bears',seat='B',ref='slinger-spell-target');self.assert_closed(session,source);self.prepare(session,mana={'C':2,'U':1,'B':1});self.checkpoint(session)
        self.cast_for(session,'A',prior,reserve={'B':1});self.assertIsNone(self.resolve(session));self.priority(session,'A')
        offered=self.action(session,'A',spell);before=authoritative_state_hash(session.state)
        command={'action_id':offered['id'],'targets':[target.ref],'pay':'auto','sacrifice_cards':[source.ref]}
        self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual(1,self.trigger_count(session,source))
        life={s:session.state.players[s].life for s in 'ABCD'};session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for seat in 'BCD':self.assertEqual(life[seat]-2,session.state.players[seat].life)
        self.assertEqual(life['A'],session.state.players['A'].life);self.replay(session,load=True)

    def test_runtime_expend_requirement_is_inferred_inside_single_and_paired_subscriptions(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(700645030);checked=0
        for name in ('Bark-Knuckle Boxer',"Hoarder's Overflow"):
            source=self.add(session.engine,name)
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                if CAPABILITY not in program.capability_dependencies:continue
                checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
                changed=replace(program,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertEqual(2,checked)

    def test_actual_affinity_and_activation_payments_do_not_replace_actual_spell_spending(self):
        session=self.session(700645040);source=self.add(session.engine,'Teapot Slinger');mage=self.add(session.engine,'Azure Mage')
        for name in ('Sol Ring','Memnite','Ornithopter'):self.add(session.engine,name)
        first=self.add(session.engine,'Frogmite',zone='hand',ref='expend-discounted');free=self.add(session.engine,'Frogmite',zone='hand',ref='expend-free');spell=self.add(session.engine,'Inspiration',zone='hand')
        for card in (source,mage,first,spell):self.assert_closed(session,card)
        self.prepare(session,mana={'C':8,'U':2});self.checkpoint(session);self.cast_for(session,'A',first,reserve={'C':7,'U':2});self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.assertEqual(1,self.paid(session,'A'))
        self.priority(session,'A');offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+mage.ref+':'))
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto','reserve':{'C':3,'U':1}});self.assertTrue(result.ok,result.summary);self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.assertEqual(1,self.paid(session,'A'))
        self.cast_for(session,'A',free,reserve={'C':3,'U':1});self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.assertEqual(1,self.paid(session,'A'))
        self.cast(session,'A',spell,['A']);self.assertEqual(1,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.assertEqual(5,self.paid(session,'A'));self.replay(session,load=True)

    def test_failed_cast_after_real_payment_rolls_back_journal_and_collected_occurrence(self):
        session=self.session(700645050);source=self.add(session.engine,'Teapot Slinger');spell=self.add(session.engine,'Hill Giant',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'R':1});self.checkpoint(session);offered=self.action(session,'A',spell);before=authoritative_state_hash(session.state)
        command={'action_id':offered['id'],'pay':'auto'}
        with patch('quorune.rules.casting.emerge_costs.validate_emerge_after_mana_payment',side_effect=ValueError('Prepared payment-time invalidation')):
            result=session.act('pilot:A',command);self.assertFalse(result.ok)
        self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual(0,self.paid(session,'A'));self.assertFalse(session.state.stack)
        self.cast(session,'A',session.state.cards[spell.object_id]);self.assertEqual(1,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_actual_scout_threshold_selects_only_its_controllers_permanent_graveyard_card(self):
        session=self.session(700645060);source=self.add(session.engine,'Trailtracker Scout');first=self.add(session.engine,'Hill Giant',zone='hand',ref='scout-first');second=self.add(session.engine,'Hill Giant',zone='hand',ref='scout-second')
        own=self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='scout-own');foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='scout-foreign');nonpermanent=self.add(session.engine,'Shock',zone='graveyard',ref='scout-wrong-type')
        self.assert_closed(session,source);self.prepare(session,mana={'C':6,'R':2});self.checkpoint(session);self.cast_for(session,'A',first,reserve={'C':3,'R':1});self.assertIsNone(self.resolve(session));self.assertEqual(0,self.trigger_count(session,source))
        self.cast(session,'A',second);self.assertEqual('semantic.target',session.state.pending_decision.kind)
        for principal,target in (('pilot:C',own),('pilot:A',foreign),('pilot:A',nonpermanent)):
            before=authoritative_state_hash(session.state);result=session.act(principal,{'action_id':'choose','targets':[target.ref]});self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','targets':[own.ref]});self.assertTrue(result.ok,result.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[own.object_id].zone);self.assertEqual('graveyard',session.state.cards[foreign.object_id].zone);self.assertEqual(8,self.paid(session,'A'));self.replay(session,load=True)

    def test_actual_overflow_pairs_entry_and_payment_counters_then_uses_sacrificed_source_count(self):
        session=self.session(700645070);source=self.add(session.engine,"Hoarder's Overflow",zone='hand');spell=self.add(session.engine,'Divination',zone='hand')
        private=self.add(session.engine,'Shock',seat='D',zone='hand',ref='overflow-private');self.assert_closed(session,source)
        self.prepare(session,mana={'C':4,'R':2,'U':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':3,'R':1,'U':1});self.assertIsNone(self.resolve(session));self.assertEqual(1,session.state.cards[source.object_id].counters.get('stash',0))
        self.cast_for(session,'A',spell,reserve={'C':1,'R':1});self.assertIsNone(self.resolve(session));self.assertEqual(2,session.state.cards[source.object_id].counters.get('stash',0));self.assertEqual(5,self.paid(session,'A'))
        self.priority(session,'A');offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+session.state.cards[source.object_id].ref+':'))
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual(2,len(session.state.players['A'].zones['hand']));self.assertEqual(5,self.paid(session,'A'))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_next_turn_resets_paid_history_and_allows_controller_to_cross_off_turn(self):
        from quorune.engine import TURN_STEPS
        session=self.session(700645080);source=self.add(session.engine,'Bark-Knuckle Boxer');first=self.add(session.engine,'Inspiration',zone='hand',ref='reset-first');second=self.add(session.engine,'Inspiration',zone='hand',ref='reset-second')
        for i in range(4):self.add(session.engine,'Island',ref='expend-next-turn-land-'+str(i))
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session);self.cast(session,'A',first,['A']);self.assertIsNone(self.resolve(session));self.assertEqual(4,self.paid(session,'A'))
        engine=session.engine;engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.phase='ending';session.state.step='end_step';session.state.phase_index=TURN_STEPS.index(('ending','end_step'));engine._grant_priority('A');engine.pump();self.checkpoint(session)
        for _ in range(40):
            if session.state.active_player=='B' and session.state.pending_decision and session.state.pending_decision.kind=='priority':break
            decision=session.state.pending_decision;principal=session.pending_principals()[0]
            if decision.kind=='cleanup.discard':
                ctx=session.packet(principal,full=True)['decision']['ctx'];cards=[r['id'] for r in ctx['hand'] if r['id']!=second.ref][:ctx['count']];command={'action_id':'discard','cards':cards}
            else:self.assertEqual('priority',decision.kind);command={'action_id':'pass'}
            result=session.act(principal,command);self.assertTrue(result.ok,result.summary)
        else:self.fail('The prepared end step did not enter the next turn')
        self.assertEqual(0,self.paid(session,'A'));self.assertNotIn('indestructible',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.cast(session,'A',session.state.cards[second.object_id],['A']);self.assertEqual(1,self.trigger_count(session,source));self.assertIsNone(self.resolve(session));self.assertEqual(4,self.paid(session,'A'));self.replay(session,load=True)

    def test_legacy_history_absence_and_manifest_provenance_remain_explicit(self):
        from quorune.model import GameState
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        session=self.session(700645090);spell=self.add(session.engine,'Shock',zone='hand');self.prepare(session,mana={'R':1})
        versions=format_state_versions(session.state);self.assertEqual(1,versions['spell_mana_history_version']);validate_state_versions({'format':versions},session.state)
        for version in (0,True,2):
            with self.assertRaises(ValueError):validate_state_versions({'format':{**versions,'spell_mana_history_version':version}},session.state)
        payload=session.state.to_dict();payload['turn_history'].pop('spell_mana_history_version');legacy=GameState.from_dict(payload)
        self.assertIsNone(legacy.turn_history.spell_mana_history_version);self.assertNotIn('spell_mana_history_version',legacy.to_dict()['turn_history'])
        session.state.turn_history.spell_mana_history_version=None;self.checkpoint(session);self.cast(session,'A',spell,['B']);self.assertIsNone(self.resolve(session))
        self.assertIsNone(self.paid(session,'A'));self.assertFalse(any(e.kind=='spell_mana_spent' for e in session.state.turn_history.events));self.replay(session,load=True)

    def test_after_only_threshold_mutant_is_killed_by_a_later_paid_spell(self):
        import quorune.compiler.fixed_public_action_event_bindings as owner
        original=owner._public_expend_spec
        def mutated(text):
            value=original(text)
            return replace(value,condition={'all':[c for c in value.condition['all'] if c.get('field')!='mana_before']}) if value else None
        self.test_actual_boxer_crosses_after_earlier_cast_and_not_again_or_from_an_opponents_spell()
        with patch.object(owner,'_public_expend_spec',side_effect=mutated),self.assertRaises(AssertionError):self.test_actual_boxer_crosses_after_earlier_cast_and_not_again_or_from_an_opponents_spell()

    def test_after_additional_cost_capture_mutant_is_killed(self):
        import quorune.rules.casting.spell_mana_payments as payments
        import quorune.rules.casting.commit as casting
        capture=payments.capture_spell_mana_payment;additional=casting._commit_additional_costs;pending={}
        def delayed(host,**kwargs):
            result=[];pending[id(host)]=(host,kwargs,result);return result
        def after_additional(*args,**kwargs):
            result=additional(*args,**kwargs);host=args[0]
            if id(host) in pending:
                saved,values,batch=pending.pop(id(host));batch.extend(capture(saved,**values))
            return result
        with patch.object(payments,'capture_spell_mana_payment',side_effect=delayed),patch.object(casting,'_commit_additional_costs',side_effect=after_additional),self.assertRaises(AssertionError):
            self.test_actual_slinger_keeps_payment_time_trigger_when_sacrificed_for_same_cast()
