from __future__ import annotations

"""CR 702.191/603.4: actual paid mana, current size, independent instances."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.increment_model import CAPABILITY,increment_condition_holds
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.spell_cast_events import SpellCastEvent,SpellCastEventError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_compound_damage_recipients as controls


def cast_base():
    return dict(card_ref='CARD',object_id='OBJECT',logical_object_id='LOGICAL',controller='A',origin='hand',stack_ref='STACK',
        types=('instant',),mana_value=1,owner='A',active_player='A',caster_spell_number=1,kicked=False,has_x_cost=False,
        has_adventure=False,keywords=(),phase='precombat_main',targets=(),creature_target_controllers=())


class IncrementCompilerTests(unittest.TestCase):
    def test_paid_cast_event_is_versioned_closed_and_does_not_reinterpret_legacy_events(self):
        base=cast_base();old=SpellCastEvent(schema_version=6,**base)
        self.assertNotIn('mana_spent_total',old.to_context());self.assertEqual(old,SpellCastEvent.from_context(old.to_context()))
        for amount in (0,1,7):
            event=SpellCastEvent(schema_version=7,mana_spent_total=amount,**base)
            self.assertEqual(event,SpellCastEvent.from_context(event.to_context()))
            self.assertEqual(amount,event.to_context()['mana_spent_total'])
        for value in (None,True,-1,1.5,'4'):
            with self.assertRaises(SpellCastEventError):SpellCastEvent(schema_version=7,mana_spent_total=value,**base)
        with self.assertRaises(SpellCastEventError):SpellCastEvent(schema_version=6,mana_spent_total=4,**base)
        event=SpellCastEvent(schema_version=7,mana_spent_total=4,**base).to_context()
        with self.assertRaises(SpellCastEventError):SpellCastEvent.from_context({**event,'paid':'forged'})

    def test_runtime_paid_fact_is_required_even_when_zero_would_exceed_negative_power(self):
        from types import SimpleNamespace
        from quorune.rules.increment_condition import current_increment_condition
        source=SimpleNamespace(zone='battlefield',phased_out=False,controller='A',logical_object_id='same',object_id='object')
        host=SimpleNamespace(_effective_card_data=lambda card:{'type_line':'Creature'},
            _type_parts=lambda line:({'creature'},set()),_numeric_stat=lambda oid,stat:-1 if stat=='power' else 2)
        self.assertTrue(current_increment_condition(host,source,{'controller':'A','mana_spent_total':0}))
        for malformed in ({'controller':'A'},{'controller':'A','mana_spent_total':True}):
            with self.assertRaises(ValueError):current_increment_condition(host,source,malformed)

    def test_increment_compares_greater_than_either_current_stat_and_requires_creature_and_paid_fact(self):
        for power,toughness,spent,expected in ((2,5,3,True),(5,2,3,True),(3,3,3,False),(4,4,3,False),(-1,2,0,True)):
            self.assertEqual(expected,increment_condition_holds(is_creature=True,power=power,toughness=toughness,mana_spent=spent))
        self.assertFalse(increment_condition_holds(is_creature=False,power=0,toughness=0,mana_spent=4))
        for value in (None,True,-1,1.5):
            with self.assertRaises(ValueError):increment_condition_holds(is_creature=True,power=1,toughness=1,mana_spent=value)

    def test_printed_instances_have_independent_nodes_and_withdrawal_keeps_them_residual(self):
        registry=load_default_capability_registry();source=replace(record('Increment, increment','Creature — Wizard'),keywords=('Increment',))
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=ir.faces[0].nodes;self.assertEqual(2,len(nodes));self.assertNotEqual(nodes[0].node_id,nodes[1].node_id)
        for node in nodes:
            self.assertEqual('spell.cast',node.event);self.assertIn(CAPABILITY,node.capability_dependencies)
            self.assertIn('intervening_condition',node.runtime_coverage)
            self.assertIn('current_ability_fragment_required',node.runtime_coverage)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(row for row in raw['capabilities'] if row['id']==CAPABILITY).update(status='blocked',blockers=['withdrawn'])
        self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)

    def test_keyword_preflight_coverage_requires_current_trusted_same_line_owner(self):
        from types import SimpleNamespace
        from quorune.compiler.preflight_keyword_coverage import static_lines_without_keyword_owners
        source=replace(record('Increment\nUnowned mystery ability.','Creature — Wizard'),keywords=('Increment',))
        owner=SimpleNamespace(trust_level='trusted',event='spell.cast',coverage=('increment',),
            capability_dependencies=(CAPABILITY,),provenance={'source_span':{'line':1}})
        lines=['Increment','Unowned mystery ability.']
        self.assertEqual(lines[1:],static_lines_without_keyword_owners(lines,record=source,trusted_programs=[owner]))
        for changed in ({'trust_level':'provisional'},{'capability_dependencies':()},
                {'provenance':{'source_span':{'line':2}}},{'event':'permanent.enter'}):
            bad=SimpleNamespace(**{**vars(owner),**changed})
            self.assertEqual(lines,static_lines_without_keyword_owners(lines,record=source,trusted_programs=[bad]))


class IncrementRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundDamageRuntimeTests.session
    add=controls.CompoundDamageRuntimeTests.add
    prepare=controls.CompoundDamageRuntimeTests.prepare
    checkpoint=controls.CompoundDamageRuntimeTests.checkpoint
    cast=controls.CompoundDamageRuntimeTests.cast
    resolve=controls.CompoundDamageRuntimeTests.resolve
    replay=controls.CompoundDamageRuntimeTests.replay
    binding=controls.CompoundDamageRuntimeTests.binding
    actions=controls.CompoundDamageRuntimeTests.actions
    action=controls.CompoundDamageRuntimeTests.action
    private_replay=controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'increment.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/increment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Increment witnesses',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_professor_receives_one_counter_then_one_private_sibling_draw(self):
        session=self.session(730301);source=self.add(session.engine,'Pensive Professor')
        spell=self.add(session.engine,'Inspiration',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        before=len(session.state.players['A'].zones['hand'])
        snapshot=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':self.action(session,spell)['id'],'targets':['A'],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(snapshot,authoritative_state_hash(session.state))
        self.cast(session,'A',spell,['A'])
        self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
        cast_item=next(item for item in session.state.stack if item.kind=='spell')
        self.assertEqual(4,cast_item.context['mana_spent_total'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(before-1+2+1,len(session.state.players['A'].zones['hand']))
        for viewer in 'BCD':
            packet=json.dumps(session.packet('pilot:'+viewer,full=True))
            self.assertTrue(all(session.state.cards[oid].ref not in packet for oid in session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_original_professor_rechecks_current_size_after_actual_growth_response(self):
        session=self.session(730302);source=self.add(session.engine,'Pensive Professor')
        spell=self.add(session.engine,'Inspiration',zone='hand');growth=self.add(session.engine,'Giant Growth',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':1})
        self.prepare(session,mana={'U':1,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',spell,['A']);self.cast(session,'B',growth,[source.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        # Professor is0/2;Growth makes3/5.4still exceeds3, so Increment
        # applies. This contrasts with raising BOTH stats above the payment.
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.private_replay(session,private)

    def test_fresh_original_professor_kills_increment_condition_omission(self):
        method='test_original_professor_receives_one_counter_then_one_private_sibling_draw'
        with patch('quorune.trigger_discovery.current_increment_condition',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_two_growth_responses_make_intervening_condition_false_at_resolution(self):
        session=self.session(730303);source=self.add(session.engine,'Pensive Professor')
        spell=self.add(session.engine,'Inspiration',zone='hand')
        growths=[self.add(session.engine,'Giant Growth',seat='B',zone='hand',ref='increment-growth-'+str(i)) for i in range(2)]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':2})
        self.prepare(session,mana={'U':1,'C':3});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,'A',spell,['A'])
        self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
        for growth in growths:self.cast(session,'B',growth,[source.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(before-1+2,len(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_actual_reduction_uses_three_paid_mana_not_four_mana_value(self):
        session=self.session(730304);source=self.add(session.engine,'Hungry Graffalon')
        reducer=self.add(session.engine,'Goblin Electromancer')
        spell=self.add(session.engine,'Inspiration',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        for card in (source,reducer,spell):self.assertTrue(self.binding(session,card)['strict_capability_ready'],card.printed_name)
        self.cast(session,'A',spell,['A']);item=next(item for item in session.state.stack if item.kind=='spell')
        self.assertEqual(3,item.context['mana_spent_total'])
        self.assertFalse(any(item.kind=='triggered_ability' for item in session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.private_replay(session,private)

    def test_actual_kicker_payment_uses_six_paid_mana_instead_of_three_mana_value(self):
        session=self.session(730305);source=self.add(session.engine,'Hungry Graffalon')
        spell=self.add(session.engine,'Field Research',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':2,'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,spell)['id'],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        item=next(item for item in session.state.stack if item.kind=='spell')
        self.assertEqual(6,item.context['mana_spent_total'])
        self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.private_replay(session,private)

    def test_actual_opponent_cast_and_actual_copy_do_not_become_controller_casts(self):
        session=self.session(730306);source=self.add(session.engine,'Pensive Professor')
        enemy=self.add(session.engine,'Pensive Professor',seat='B',ref='increment-opponent-professor')
        spell=self.add(session.engine,'Inspiration',zone='hand')
        copy=self.add(session.engine,'Reverberate',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'R':2})
        self.prepare(session,mana={'U':1,'C':3});self.checkpoint(session)
        self.cast(session,'A',spell,['A'])
        original=next(item for item in session.state.stack if item.kind=='spell')
        self.cast(session,'B',copy,[original.ref]);pending=self.resolve(session)
        if pending is not None:
            self.assertEqual('semantic.choice',pending.kind)
            accepted=session.act('pilot:B',{'action_id':'choose','targets':['B']})
            self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        # B's own2mana Reverberate qualifies against0/2 once. Its copied
        # Inspiration is not another cast and must not add a second counter.
        self.assertEqual(1,session.state.cards[enemy.object_id].counters.get('+1/+1',0))
        self.assertEqual(2,len(session.engine._current_turn_history('spell_cast')))
        self.private_replay(session,private)

    def test_source_departure_in_response_does_not_transfer_increment_to_returned_card(self):
        session=self.session(730307);source=self.add(session.engine,'Pensive Professor')
        spell=self.add(session.engine,'Inspiration',zone='hand')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1})
        self.prepare(session,mana={'U':1,'C':3});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand']);identity=source.logical_object_id
        self.cast(session,'A',spell,['A']);self.cast(session,'B',bounce,[source.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        current=session.state.cards[source.object_id]
        self.assertEqual('hand',current.zone);self.assertNotEqual(identity,current.logical_object_id)
        self.assertEqual(0,current.counters.get('+1/+1',0))
        self.assertEqual(before-1+1+2,len(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_all_five_originals_have_complete_preflight_and_runtime_increment_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.preflight import card_semantic_status
        session=self.session(730308)
        for name in ('Cuboid Colony','Pensive Professor','Topiary Lecturer','Hungry Graffalon','Textbook Tabulator'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],name)
            status=card_semantic_status(self.db.lookup(name),session.engine.semantics,db=self.db,
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],(name,status['unresolved']))
        source=self.db.lookup('Pensive Professor')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
        for cap in (CAPABILITY,'counter.producer.fixed_effect','trigger.event.normalized_spell_cast'):
            dependencies=tuple(value for value in program.capability_dependencies if value!=cap)
            missing=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        from quorune.rules.increment_condition import increment_runtime_dependencies
        with self.assertRaises(ValueError):
            increment_runtime_dependencies(replace(program,coverage=tuple(x for x in program.coverage if x!='current_ability_fragment_required')))
        with self.assertRaises(ValueError):
            increment_runtime_dependencies(replace(program,event_condition={'field':'increment_paid_mana_exceeds_source_size','op':'falsy'}))

    def test_independent_printed_instances_recheck_after_the_first_counter(self):
        session=self.session(730309);source=self.add(session.engine,'Generic Double Increment')
        spell=self.add(session.engine,'Reverberate',zone='hand')
        target=self.add(session.engine,'Giant Growth',seat='B',zone='hand')
        body=self.add(session.engine,'Generic Bound Body',seat='B',ref='increment-double-body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':1})
        self.prepare(session,mana={'R':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        # B supplies an actual stack spell; A's2mana Reverberate then queues
        # both1/1 Increment instances. The first counter makes the second false.
        self.cast(session,'B',target,[body.ref]);original=next(item for item in session.state.stack if item.kind=='spell')
        self.cast(session,'A',spell,[original.ref])
        self.assertEqual('trigger.order',session.state.pending_decision.kind)
        session=self.replay(session,load=True)
        refs=[item.ref for group in session.state.pending_trigger_batches[0].groups for item in group.items]
        accepted=session.act('pilot:A',{'action_id':'order','triggers':refs})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(2,sum(item.kind=='triggered_ability' for item in session.state.stack))
        pending=self.resolve(session)
        if pending is not None:
            session=self.replay(session,load=True)
            accepted=session.act('pilot:A',{'action_id':'choose','targets':[body.ref]})
            self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.private_replay(session,private)

    def test_original_topiary_mana_uses_current_power_after_increment(self):
        session=self.session(730310);source=self.add(session.engine,'Topiary Lecturer')
        source.acquired_control_turn_count=-1
        spell=self.add(session.engine,'Inspiration',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',spell,['A']);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        for _ in range(16):
            if session.state.priority_player=='A':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':'))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(2,session.state.players['A'].mana_pool['G'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.private_replay(session,private)

    def test_original_tabulator_surveil_entry_stays_private_before_later_increment(self):
        session=self.session(730311);source=self.add(session.engine,'Textbook Tabulator',zone='hand')
        spell=self.add(session.engine,'Inspiration',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':2,'C':5});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);pending=self.resolve(session)
        self.assertEqual('semantic.choice',pending.kind)
        packet=session.packet('pilot:A',full=True)
        self.assertIsNotNone(packet['decision'])
        for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
        # Surveil sees the current top2; choose the same top order and no grave.
        refs=[session.state.cards[oid].ref for oid in reversed(session.state.players['A'].zones['library'][-2:])]
        for seat in 'BCD':
            view=json.dumps(session.packet('pilot:'+seat,full=True))
            for ref in refs:self.assertNotIn(ref,view)
        session=self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':'choose','cards':{'top':refs,'graveyard':[]}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',spell,['A']);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.private_replay(session,private)

    def test_typed_free_cast_permission_seam_emits_zero_paid_mana_and_no_increment(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant,ImpulseAccessDuration
        session=self.session(730312);source=self.add(session.engine,'Pensive Professor')
        spell=self.add(session.engine,'Inspiration',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        grant_temporary_cast_permission(session.engine,card=spell,
            grant=TemporaryCastPermissionGrant(player='A',duration=ImpulseAccessDuration.END_OF_TURN,
                not_before_turn_sequence=None,without_mana_cost=True,source='existing-typed-free-payment-seam'),
            reason='Independent actual-payment contrast, not a new producer-card claim')
        self.prepare(session);self.checkpoint(session)
        action=self.action(session,spell)
        option=next(row for row in action['cost_options'] if not any(row['requirements'].values()))
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        item=next(item for item in session.state.stack if item.kind=='spell')
        self.assertEqual(0,item.context['mana_spent_total'])
        self.assertFalse(any(item.kind=='triggered_ability' for item in session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.private_replay(session,private)

    def test_actual_announced_x_and_counter_replacement_keep_paid_amount_and_counter_owner(self):
        session=self.session(730313);source=self.add(session.engine,'Hungry Graffalon')
        replacement=self.add(session.engine,'Hardened Scales')
        spell=self.add(session.engine,'Earthquake',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session)
        for card in (source,replacement,spell):self.assertTrue(self.binding(session,card)['strict_capability_ready'],card.printed_name)
        accepted=session.act('pilot:A',{'action_id':self.action(session,spell)['id'],'x':3,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        item=next(item for item in session.state.stack if item.kind=='spell')
        self.assertEqual(4,item.context['mana_spent_total'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(2,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(3,session.state.cards[source.object_id].marked_damage)
        self.assertEqual([37]*4,[session.state.players[s].life for s in 'ABCD'])
        self.private_replay(session,private)

    def test_actual_humble_removes_future_increment_without_erasing_pending_trigger(self):
        for pending in (False,True):
            session=self.session(730314+int(pending));source=self.add(session.engine,'Pensive Professor')
            humility=self.add(session.engine,'Humble',seat='B',zone='hand')
            spell=self.add(session.engine,'Inspiration',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            session.state.players['B'].mana_pool.update({'W':2,'C':2})
            self.prepare(session,mana={'W':2,'U':1,'C':5});self.checkpoint(session)
            self.assertTrue(self.binding(session,humility)['strict_capability_ready'])
            if pending:self.cast(session,'A',spell,['A'])
            self.cast(session,'B',humility,[source.ref])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual([],session.engine._effective_card_data(session.state.cards[source.object_id])['ability_fragments'])
            if not pending:
                self.cast(session,'A',spell,['A'])
                self.assertFalse(any(item.kind=='triggered_ability' for item in session.state.stack))
                self.assertIsNone(self.resolve(session))
            self.assertEqual(int(pending),session.state.cards[source.object_id].counters.get('+1/+1',0))
            self.private_replay(session,private)

    def test_fresh_original_humble_kills_current_ability_omission(self):
        method='test_actual_humble_removes_future_increment_without_erasing_pending_trigger'
        with patch('quorune.trigger_discovery.program_has_current_ability_fragments',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
