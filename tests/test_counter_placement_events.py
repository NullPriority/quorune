from __future__ import annotations

"""Pinned CR122.6/603.2c: committed quantities and counter multiplicity."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class CounterPlacementEventCompilerTests(unittest.TestCase):
    def test_counter_occurrence_producer_reads_actual_counts_and_rejects_stale_identity(self):
        from types import SimpleNamespace
        from quorune.counter_placement_events import capture_counter_placement_occurrences
        card=SimpleNamespace(object_id='body',logical_object_id='body@1',ref='BODY',zone='battlefield',controller='B',owner='C',object_kind='card',zone_change_counter=1)
        host=SimpleNamespace(active_seats=['A','B','C'],state=SimpleNamespace(cards={'body':card}),_effective_card_data=lambda c:{'type_line':'Creature — Human','power':'2','toughness':'3','colors':['G'],'mana_value':2})
        def event(amount,identity='body@1'):
            return SimpleNamespace(kind='counter.place',children=(),affected_object=SimpleNamespace(object_id='body'),
                event_id='placement1',payload={'amount':amount,'requested_amount':1,'counter_name':'+1/+1','placing_player':'A','target_logical_object_id':identity})
        occurrence=capture_counter_placement_occurrences(host,(event(4),),reason='Actual replacement amount')[0]
        self.assertEqual(4,occurrence.context['amount']);self.assertEqual('A',occurrence.context['placing_player'])
        self.assertEqual('B',occurrence.context['controller']);self.assertEqual('C',occurrence.context['owner'])
        self.assertEqual((),capture_counter_placement_occurrences(host,(event(0),),reason='Prevented placement'))
        with self.assertRaises(ValueError):capture_counter_placement_occurrences(host,(event(2,'body@0'),),reason='Stale')

    def test_counter_occurrence_compiler_preserves_multiplicity_actor_and_subject(self):
        for text,event in [
            ('Whenever a +1/+1 counter is put on this creature, draw a card.','counter.single_put'),
            ('Whenever one or more +1/+1 counters are put on this creature, draw a card.','counter.put'),
            ('Whenever you put one or more -1/-1 counters on a creature, create a 1/1 green Snake creature token with deathtouch.','counter.put'),
            ('Whenever you put one or more counters on a creature you don\'t control, draw a card.','counter.put'),
            ('Whenever you put a counter on a creature you control, draw a card.', 'counter.single_put'),
            ('Whenever you put one +1/+1 counter on another creature, draw a card.', 'counter.single_put'),
            ('Whenever you put one or more -1/-1 counters on a creature, create that many 1/1 black Insect creature tokens.', 'counter.put'),
            ('Whenever one or more +1/+1 counters are put on another non-Hydra creature you control, put a +1/+1 counter on this creature.','counter.put')]:
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertEqual('exact',ir.status)
                node=ir.faces[0].nodes[0];self.assertEqual(event,node.event)
                self.assertIn('trigger.event.normalized_counter_placement',node.capability_dependencies)
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        ir=compile_oracle_card(query_record("Whenever you put one or more counters on a creature you don't control, draw a card."),capability_registry=load_default_capability_registry())
        conditions=str(ir.faces[0].nodes[0].event_condition)
        self.assertIn('placing_player',conditions);self.assertIn('controller',conditions)

    def test_counter_occurrence_unsupported_threshold_removal_and_aggregation_stay_residual(self):
        from quorune.compiler.counter_placement_event_bindings import counter_placement_event_binding_spec
        for text in ('When the tenth +1/+1 counter is put on this creature, draw a card.',
            'Whenever a time counter is removed from this card while it\'s exiled, draw a card.',
            'Whenever one or more +1/+1 counters are put on this creature for the first time each turn, draw a card.',
            'Whenever you put a +1/+1 counters on a creature, draw a card.',
            'Whenever you put one or more +1/+1 counters on one or more creatures, draw a card.'):
            self.assertIsNone(counter_placement_event_binding_spec(text,card_name='Generic Counter Observer'))
        text='Whenever one or more +1/+1 counters are put on this creature, you win the game.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertNotEqual('exact',ir.status)
        for incomplete in ('Whenever you put a +1/+1 counter on a creature, draw that many cards.',
                           'Whenever you put one or more counters on a creature, draw that many cards.'):
            ir = compile_oracle_card(query_record(incomplete), capability_registry=load_default_capability_registry())
            self.assertNotEqual('exact', ir.status)


class CounterPlacementEventRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'events.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/counter-replacement-cards.json',ROOT/'tests/fixtures/counter-doubling-cards.json',
            ROOT/'tests/fixtures/counter-placement-event-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Counter occurrence witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    prepare = InheritedNativeFixture.prepare
    cast = InheritedNativeFixture.cast
    assert_closed = InheritedNativeFixture.assert_closed

    def order_triggers(self,session):
        while session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
            principal=session.pending_principals()[0]
            packet=session.packet(principal,full=True)['decision']
            refs=[row['id'] for row in packet['ctx']['triggers']]
            result=session.act(principal,{'action_id':'order','triggers':refs})
            self.assertTrue(result.ok,result.summary)

    def resolve(self,session):
        for _ in range(96):
            decision=session.state.pending_decision
            if decision is not None and decision.kind=='trigger.order':
                self.order_triggers(session);continue
            if decision is not None and decision.kind=='semantic.choice':
                packet=session.packet(session.pending_principals()[0],full=True)['decision']
                operation=packet['ctx'].get('operation')
                if operation=='offer_draw':choice='draw'
                elif operation=='offer_optional_effect':choice='apply'
                else:return decision
                result=session.act(session.pending_principals()[0],{'action_id':'choose','choice':choice})
                self.assertTrue(result.ok,result.summary);continue
            if decision is not None and decision.kind!='priority':
                return decision
            if not session.state.stack:return None
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Counter trigger sequence did not finish')

    def test_actual_fathom_and_herd_distinguish_replaced_quantity_from_one_occurrence(self):
        session=self.session(286001);engine=session.engine
        fathom=self.add(engine,'Fathom Mage')
        self.add(engine,'Branching Evolution')
        spell=self.add(engine,'Generic Counter Event Two',zone='hand')
        herd=self.add(engine,'Herd Baloth');second=self.add(engine,'Generic Counter Event Two',zone='hand',ref='second-counter-event')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[fathom.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        # Stop after the physical spell's resolution, before optional triggers.
        while any(item.card_object_id==spell.object_id for item in engine.state.stack):
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.order_triggers(session)
        self.assertEqual(4,engine.state.cards[fathom.object_id].counters['+1/+1'])
        self.assertEqual(4,sum(item.source_object_id==fathom.object_id for item in engine.state.stack))
        self.resolve(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==second.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[herd.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        while any(item.card_object_id==second.object_id for item in engine.state.stack):
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.order_triggers(session)
        self.assertEqual(4,engine.state.cards[herd.object_id].counters['+1/+1'])
        self.assertEqual(1,sum(item.source_object_id==herd.object_id for item in engine.state.stack))
        self.resolve(session)
        beasts=[card for card in session.state.cards.values() if card.object_kind=='token' and card.zone=='battlefield'
            and 'beast' in engine._type_parts(engine._effective_card_data(card)['type_line'])[1]]
        self.assertEqual(1,len(beasts))
        self.replay(session,load=True)

    def test_actual_placing_player_and_recipient_controller_are_distinct(self):
        session=self.session(286002);engine=session.engine
        patron=self.add(engine,'Generous Patron');body=self.add(engine,'Generic Bound Body',seat='B')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-counter-event-c')
        spell=self.add(engine,'Generic Counter Event Two',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session);before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:C',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertNotIn(private.ref,str(session.packet('pilot:B',full=True)))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(8,len(session.state.players['A'].zones['hand']))
        self.assertEqual(2,session.state.cards[body.object_id].counters['+1/+1'])
        self.replay(session,load=True)

    def test_counter_entry_occurrences_observe_complete_new_permanents_and_replay(self):
        session=self.session(286003);engine=session.engine
        source=self.add(engine,'Generic Counter Put Entry Observer',zone='hand')
        action=self.ready(session,source,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(2,session.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(9,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_counter_occurrences_pending_replacement_save_load_and_stale_subject(self):
        session=self.session(286004);engine=session.engine
        source=self.add(engine,'Fathom Mage');self.add(engine,'Branching Evolution');self.add(engine,'Hardened Scales')
        spell=self.add(engine,'Generic Counter Event Two',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session);self.assertEqual('replacement.order',pending.kind)
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        resumed=self.replay(session,load=True)
        before=authoritative_state_hash(resumed.state)
        packet=resumed.packet('pilot:A',full=True)['decision'];choice=packet['ctx']['options'][0]['id']
        wrong=resumed.act('pilot:B',{'action_id':'choose','replacement':choice})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
        wrong=resumed.act('pilot:A',{'action_id':'choose','replacement':'not-a-replacement'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
        while resumed.state.pending_decision is not None and resumed.state.pending_decision.kind=='replacement.order':
            packet=resumed.packet('pilot:A',full=True)['decision']
            result=resumed.act('pilot:A',{'action_id':'choose','replacement':packet['ctx']['options'][0]['id']})
            self.assertTrue(result.ok,result.summary)
        self.order_triggers(resumed)
        amount=resumed.state.cards[source.object_id].counters['+1/+1'];self.assertIn(amount,(5,6))
        self.assertEqual(amount,sum(item.source_object_id==source.object_id for item in resumed.state.stack))
        self.resolve(resumed);self.replay(resumed,load=True)

    def test_actual_two_creature_batch_has_two_one_or_more_occurrences(self):
        session=self.session(286005);engine=session.engine
        first=self.add(engine,'Herd Baloth',ref='first-herd');second=self.add(engine,'Herd Baloth',ref='second-herd')
        spell=self.add(engine,'Generic Counter Event All',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        while any(item.card_object_id==spell.object_id for item in engine.state.stack):
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.order_triggers(session)
        self.assertEqual(2,sum(item.source_object_id in {first.object_id,second.object_id} for item in engine.state.stack))
        self.resolve(session);self.replay(session,load=True)

    def test_actual_hapatra_committed_negative_counters_create_one_snake(self):
        session=self.session(286006);engine=session.engine
        source=self.add(engine,'Hapatra, Vizier of Poisons');body=self.add(engine,'Generic Bound Body',seat='B')
        spell=self.add(engine,'Generic Counter Event Negative',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        snakes=[card for card in session.state.cards.values() if card.object_kind=='token' and card.zone=='battlefield'
            and 'snake' in engine._type_parts(engine._effective_card_data(card)['type_line'])[1]]
        self.assertEqual(1,len(snakes));self.assertIn('Deathtouch',engine._effective_card_data(snakes[0])['keywords'])
        self.replay(session,load=True)

    def test_actual_infect_counter_damage_triggers_after_committed_result(self):
        session=self.session(286007);engine=session.engine
        self.add(engine,'Hapatra, Vizier of Poisons');source=self.add(engine,'Generic Counter Damage Source')
        body=self.add(engine,'Generic Bound Body',seat='B')
        action=self.ready(session,source,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(2,session.state.cards[body.object_id].counters['-1/-1'])
        self.assertEqual(0,session.state.cards[body.object_id].marked_damage)
        snakes=[c for c in session.state.cards.values() if c.object_kind=='token' and c.zone=='battlefield'
            and 'snake' in engine._type_parts(engine._effective_card_data(c)['type_line'])[1]]
        self.assertEqual(1,len(snakes));self.replay(session,load=True)

    def test_actual_copied_entry_observers_get_own_counters_and_triggers(self):
        session=self.session(286008);engine=session.engine
        body=self.add(engine,'Generic Counter Put Entry Observer');body.counters['+1/+1']=7
        spell=self.add(engine,'Generic Counter Entry Copies',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        copies=[card for card in session.state.cards.values() if card.object_kind=='token' and card.zone=='battlefield']
        self.assertEqual(2,len(copies));self.assertEqual([2,2],sorted(c.counters['+1/+1'] for c in copies))
        self.assertEqual(11,len(session.state.players['A'].zones['hand']))
        self.assertEqual(7,session.state.cards[body.object_id].counters['+1/+1'])
        self.replay(session,load=True)

    def test_resolved_zero_counter_owner_creates_no_occurrence(self):
        from types import SimpleNamespace
        from quorune.counter_placement_events import dispatch_prepared_counter_events
        from unittest.mock import Mock
        event=SimpleNamespace(kind='counter.place',children=(),affected_object=SimpleNamespace(object_id='unneeded'),
            payload={'amount':0},event_id='prevented-counter')
        host=SimpleNamespace(state=SimpleNamespace(cards={}),_dispatch_semantic_event=Mock())
        dispatch_prepared_counter_events(host,SimpleNamespace(events=(event,)),reason='Resolved zero owner diagnostic')
        host._dispatch_semantic_event.assert_not_called()

    def test_counter_event_pending_source_new_incarnation_rejects_unchanged_hash(self):
        session=self.session(286010);engine=session.engine
        source=self.add(engine,'Fathom Mage');self.add(engine,'Branching Evolution');self.add(engine,'Hardened Scales')
        spell=self.add(engine,'Generic Counter Event Two',zone='hand')
        action=self.ready(session,spell,{})
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session);self.assertEqual('replacement.order',pending.kind)
        packet=session.packet('pilot:A',full=True)['decision'];choice=packet['ctx']['options'][0]['id']
        engine.move_card(source.object_id,'exile',log=False)
        engine.move_card(source.object_id,'battlefield',controller='A',log=False)
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'choose','replacement':choice})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_simultaneous_entry_owner_seals_full_counter_set_before_discovery(self):
        session=self.session(286011);engine=session.engine
        first=self.add(engine,'Generic Counter Put Entry Observer',zone='hand',ref='entry-first')
        second=self.add(engine,'Generic Counter Put Entry Observer',zone='hand',ref='entry-second')
        self.ready(session,first,{})
        before=authoritative_state_hash(session.state)
        original=engine._dispatch_semantic_event;seen=[]
        def observe(event,context,**kwargs):
            if event in {'counter.put','counter.single_put'}:
                self.assertEqual('battlefield',session.state.cards[first.object_id].zone)
                self.assertEqual('battlefield',session.state.cards[second.object_id].zone)
                self.assertEqual(2,session.state.cards[first.object_id].counters['+1/+1'])
                self.assertEqual(2,session.state.cards[second.object_id].counters['+1/+1'])
                seen.append((event,context['card']))
            return original(event,context,**kwargs)
        with patch.object(engine,'_dispatch_semantic_event',side_effect=observe):
            engine._move_cards_simultaneously([(first.object_id,'battlefield'),(second.object_id,'battlefield')],
                reason='Simultaneous entry owner diagnostic')
        self.assertEqual(4,sum(event=='counter.single_put' for event,_ in seen))

    def test_actual_queued_counter_trigger_keeps_controller_after_source_control_change(self):
        session=self.session(286012);engine=session.engine
        source=self.add(engine,'Generous Patron');body=self.add(engine,'Generic Bound Body',seat='C')
        counter=self.add(engine,'Generic Counter Event Two',zone='hand')
        control=self.add(engine,'Generic Counter Event Control',seat='B',zone='hand')
        action=self.ready(session,counter,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        while any(i.card_object_id==counter.object_id for i in engine.state.stack):
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.order_triggers(session)
        trigger=next(i for i in engine.state.stack if i.source_object_id==source.object_id)
        self.assertEqual('A',trigger.controller)
        while session.pending_principals()[0]!='pilot:B':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==control.ref)
        accepted=session.act('pilot:B',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('B',session.state.cards[source.object_id].controller)
        self.assertEqual(8,len(session.state.players['A'].zones['hand']))
        self.assertEqual(7,len(session.state.players['B'].zones['hand']))
        self.replay(session,load=True)

    def test_counter_dispatch_without_subscribers_skips_per_counter_iteration(self):
        session=self.session(286013);engine=session.engine
        body=self.add(engine,'Generic Bound Body')
        from quorune.counter_placement_event_model import CounterPlacementOccurrence
        from quorune.replacement.immutable import FrozenMap
        from quorune.counter_placement_events import dispatch_counter_placement_occurrences
        occurrence=CounterPlacementOccurrence(FrozenMap({'card':body.ref,'card_object_identity':body.logical_object_id,
            'event_id':'large-known-placement','placing_player':'A','controller':'A','owner':'A',
            'counter':'+1/+1','amount':1000000000}))
        with patch.object(engine,'_dispatch_semantic_event') as dispatched:
            dispatch_counter_placement_occurrences(engine,(occurrence,))
        dispatched.assert_not_called()

    def test_actual_any_kind_trigger_observes_one_multikind_instruction_once(self):
        session=self.session(286014);engine=session.engine
        source=self.add(engine,'Generous Patron');body=self.add(engine,'Generic Bound Body',seat='B')
        spell=self.add(engine,'Generic Counter Event Mixed Kinds',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(1,session.state.cards[body.object_id].counters['+1/+1'])
        self.assertEqual(1,session.state.cards[body.object_id].counters['vigilance'])
        self.assertEqual(8,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_counter_occurrence_omitted_dispatch_mutant_is_killed(self):
        from quorune import counter_placement_events as owner
        with patch.object(owner,'dispatch_prepared_counter_events',return_value=None):
            with self.assertRaises(AssertionError):self.test_actual_fathom_and_herd_distinguish_replaced_quantity_from_one_occurrence()

    def test_active_singular_placement_uses_the_caster_through_replacement_and_turn_limits(self):
        session = self.session(122824001)
        ant_man = self.add(session.engine, "Ant-Man, Colony Commander")
        knight = self.add(session.engine, "Knight of Wundagore")
        own = self.add(session.engine, "Generic Bound Body", ref="own-counter-recipient")
        foreign = self.add(session.engine, "Generic Bound Body", seat="B", ref="foreign-counter-recipient")
        doubler = self.add(session.engine, "Branching Evolution", seat="B")
        opponent_spell = self.add(session.engine, "Battlegrowth", seat="B", zone="hand", ref="opponent-placement")
        self_spell = self.add(session.engine, "Battlegrowth", zone="hand", ref="self-placement")
        foreign_spell = self.add(session.engine, "Battlegrowth", zone="hand", ref="foreign-placement")
        self.add(session.engine, "Forest", seat="B", ref="opponent-payment")
        for card in (ant_man, knight, doubler, opponent_spell):
            self.assert_closed(session, card)
        self.prepare(session, mana={"G": 3})
        self.checkpoint(session)

        self.cast(session, "B", opponent_spell, [own.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[own.object_id].counters.get("+1/+1"))
        self.assertFalse(session.state.cards[knight.object_id].counters)
        self.assertFalse(any(card.is_token for card in session.state.cards.values()))

        self.cast(session, "A", self_spell, [knight.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[knight.object_id].counters.get("+1/+1"))
        insects = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(insects))
        self.assertEqual("A", insects[0].controller)

        session = self.replay(session, load=True)
        self.cast(session, "A", foreign_spell, [foreign.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.cards[foreign.object_id].counters.get("+1/+1"))
        self.assertEqual(2, session.state.cards[knight.object_id].counters.get("+1/+1"))
        self.assertEqual(1, sum(card.is_token and card.zone == "battlefield" for card in session.state.cards.values()))
        self.replay(session, load=True)

    def test_active_singular_any_counter_preserves_recipient_controller(self):
        session = self.session(122824002)
        watcher = self.add(session.engine, "Mikey & Leo, Chaos & Order")
        own = self.add(session.engine, "Generic Bound Body", ref="own-scar-recipient")
        foreign = self.add(session.engine, "Generic Bound Body", seat="B", ref="foreign-scar-recipient")
        first = self.add(session.engine, "Scar", zone="hand", ref="scar-foreign")
        second = self.add(session.engine, "Scar", zone="hand", ref="scar-own")
        self.assert_closed(session, watcher)
        self.assert_closed(session, first)
        self.prepare(session, mana={"B": 3, "R": 3})
        self.checkpoint(session)
        draws = len(session.state.players["A"].draw_history)
        for spell, target, expected_draws in ((first, foreign, draws), (second, own, draws + 1)):
            actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            offer = next(action for action in actions if action["id"] == f"cast:{spell.ref}")
            black = next(option for option in offer["cost_options"] if option["requirements"]["B"] == 1)
            result = session.act("pilot:A", {
                "action_id": offer["id"], "cost_option": black["id"], "targets": [target.ref], "pay": "auto",
            })
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(1, session.state.cards[target.object_id].counters.get("-1/-1"))
            self.assertEqual(expected_draws, len(session.state.players["A"].draw_history))
        self.replay(session, load=True)

    def test_existing_singular_subscription_derives_placement_runtime_authority(self):
        from quorune.card_programs import bind_semantic_program_runtime

        session = self.session(122824003)
        source = self.add(session.engine, "Fathom Mage")
        witness, = (entry for entry in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if entry.event == "counter.single_put")
        self.assertTrue(bind_semantic_program_runtime(
            witness, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        capability = "trigger.event.normalized_counter_placement"
        dependencies = tuple(value for value in witness.capability_dependencies if value != capability)
        withdrawn = replace(witness, capability_dependencies=dependencies,
                            capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        bound = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", bound["blockers"])

    def test_grouped_counter_quantity_drives_damage_and_repeated_investigation(self):
        session = self.session(122824004)
        casey = self.add(session.engine, "Casey Jones, Back Alley Brute")
        lonis = self.add(session.engine, "Lonis, Genetics Expert")
        self.add(session.engine, "Branching Evolution")
        foreign = self.add(session.engine, "Generic Bound Body", seat="B")
        opponent = self.add(session.engine, "Battlegrowth", seat="B", zone="hand", ref="grouped-opponent")
        outside = self.add(session.engine, "Battlegrowth", zone="hand", ref="grouped-foreign-target")
        actual = self.add(session.engine, "Battlegrowth", zone="hand", ref="grouped-owned-target")
        self.add(session.engine, "Forest", seat="B", ref="grouped-opponent-mana")
        for card in (casey, lonis):
            self.assert_closed(session, card)
        self.prepare(session, mana={"G": 3})
        self.checkpoint(session)
        life = session.state.players["B"].life

        def clues():
            return sum(card.is_token and card.zone == "battlefield" and
                       "clue" in session.engine._effective_type_parts(card)[1]
                       for card in session.state.cards.values())

        self.cast(session, "B", opponent, [lonis.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, clues())
        self.assertEqual(life, session.state.players["B"].life)
        self.cast(session, "A", outside, [foreign.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, clues())
        self.assertEqual(life, session.state.players["B"].life)

        self.cast(session, "A", actual, [lonis.ref])
        self.assertEqual("semantic.target", self.resolve(session).kind)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "targets": ["A"]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        selected = session.act("pilot:A", {"action_id": "choose", "targets": ["B"]})
        self.assertTrue(selected.ok, selected.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(4, session.state.cards[lonis.object_id].counters.get("+1/+1"))
        self.assertEqual(4, clues())
        self.assertEqual(life - 2, session.state.players["B"].life)
        self.replay(session, load=True)

    def test_nest_reads_grouped_wither_counters_before_the_recipient_dies(self):
        from quorune.card_programs import bind_semantic_program_runtime

        session = self.session(122824005)
        nest = self.add(session.engine, "Nest of Scarabs")
        target = self.add(session.engine, "Grizzly Bears", seat="B")
        blast = self.add(session.engine, "Puncture Blast", zone="hand")
        for card in (nest, blast):
            self.assert_closed(session, card)
        witness, = (entry for entry in session.engine.semantics.programs_for_oracle(nest.oracle_id)
                    if entry.event == "counter.put")
        unnamed = {"all": [clause for clause in witness.event_condition["all"] if clause.get("field") != "counter"]}
        for malformed in (replace(witness, event="counter.single_put"), replace(witness, event_condition=unnamed)):
            rejected = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(rejected["strict"])
            self.assertTrue(any("Counter amounts require a grouped placement event" in reason for reason in rejected["blockers"]))

        self.prepare(session, mana={"C": 3, "R": 2})
        self.checkpoint(session)
        self.cast(session, "A", blast, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        insects = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(3, len(insects))
        self.assertTrue(all(card.controller == "A" for card in insects))
        self.replay(session, load=True)


if __name__=='__main__':unittest.main()
