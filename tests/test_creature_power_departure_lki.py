from __future__ import annotations

"""CR 608.2h/120.3f: immediate source power, controller and keyword information."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, StackItem
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_paid_entry_copy as actions


class CreaturePowerDepartureRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.PaidEntryCopyRuntimeTests.session
    add = actions.PaidEntryCopyRuntimeTests.add
    checkpoint = actions.PaidEntryCopyRuntimeTests.checkpoint
    resolve = actions.PaidEntryCopyRuntimeTests.resolve
    replay = actions.PaidEntryCopyRuntimeTests.replay
    prepare = actions.PaidEntryCopyRuntimeTests.prepare
    cast = actions.PaidEntryCopyRuntimeTests.cast
    assert_closed = actions.PaidEntryCopyRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'power-departure.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json',
            ROOT / 'tests/fixtures/fixed-damage-source-lki-cards.json',
            ROOT / 'tests/fixtures/creature-power-departure-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Power departure witness', [DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()

    def resolve_response(self, session, card):
        for _ in range(48):
            if session.state.cards[card.object_id].zone == 'graveyard':
                return
            self.assertEqual('priority', session.state.pending_decision.kind)
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id':'pass'}).ok)
        self.fail('Response did not resolve before the power ability')

    def exercise_source(self, name, *, departure, seed):
        session = self.session(seed)
        source = self.add(session.engine,name,ref='power-source')
        target = self.add(session.engine,'Colossal Dreadmaw',seat='B',ref='power-target')
        growth = self.add(session.engine,'Battlegrowth',zone='hand',ref='power-growth')
        grant = self.add(session.engine,'Generic Damage Source Lifelink',zone='hand',ref='power-lifelink')
        control = self.add(session.engine,'Generic Event Subject Control',seat='B',zone='hand',ref='power-control')
        bounce = self.add(session.engine,'Unsummon',zone='hand',ref='power-bounce')
        blink = self.add(session.engine,'Cloudshift',seat='B',zone='hand',ref='power-blink')
        private = self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='power-private')
        for card in (source,target,growth,grant,control,bounce,blink):self.assert_closed(session,card)
        self.prepare(session,mana={'C':1,'R':2,'G':2,'W':3,'U':3})
        session.state.players['B'].mana_pool['W']=2
        session.state.players['A'].turns_begun=1
        session.state.cards[source.object_id].acquired_control_turn_count=0
        session.engine.permissions.invalidate_current();session.state.pending_decision=None
        session.engine._grant_priority('A');session.engine.pump()
        self.checkpoint(session)
        row=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if a.get('source')==source.ref and a['id'].startswith('activate:'))
        command={'action_id':row['id'],'targets':[target.ref],'pay':'auto'}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('A',session.state.stack[-1].controller)
        self.cast(session,'A',growth,[source.ref]);self.resolve_response(session,growth)
        self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',grant,[source.ref]);self.resolve_response(session,grant)
        self.cast(session,'B',control,[source.ref]);self.resolve_response(session,control)
        self.assertEqual('B',session.state.cards[source.object_id].controller)
        self.assertIn('lifelink',session.engine._combat_keywords(session.state.cards[source.object_id]))
        session=self.replay(session,load=True)
        if departure=='bounce':
            self.cast(session,'A',bounce,[source.ref]);self.resolve_response(session,bounce)
            self.assertEqual(('hand','A'),(session.state.cards[source.object_id].zone,session.state.cards[source.object_id].owner))
        elif departure=='blink':
            self.cast(session,'B',blink,[source.ref]);self.resolve_response(session,blink)
            self.assertEqual(1,session.engine._numeric_stat(source.object_id,'power'))
        session=self.replay(session,load=True)
        self.resolve(session)
        self.assertEqual(2,session.state.cards[target.object_id].marked_damage)
        self.assertEqual({'A':40,'B':42,'C':40,'D':40},{seat:p.life for seat,p in session.state.players.items()})
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_actual_goblin_departure_uses_latest_power_controller_and_lifelink(self):
        self.exercise_source('Spikeshot Goblin',departure='bounce',seed=425000)

    def test_actual_elder_returned_incarnation_does_not_replace_original_source(self):
        self.exercise_source('Spikeshot Elder',departure='blink',seed=425001)

    def test_actual_live_source_reads_current_information_before_damage(self):
        self.exercise_source('Spikeshot Goblin',departure=None,seed=425002)

    def test_departure_pin_omission_is_killed_by_actual_supported_source(self):
        with mock.patch('quorune.rules.stack_damage_sources.pin_creature_power_damage_departures'):
            with self.assertRaises(AssertionError):
                self.exercise_source('Spikeshot Goblin',departure='bounce',seed=425003)


class CreaturePowerDepartureValueTests(unittest.TestCase):
    def test_current_noncreature_power_is_zero_and_returned_incarnation_keeps_old_lki(self):
        import quorune.creature_power_damage as power_damage
        from quorune.damage_source import DamageSourceSnapshot
        card = CardInstance(object_id='power-object', ref='power-ref', oracle_id='fixture:power',
            printed_name='Power source', owner='A', controller='B', zone='battlefield')
        old = DamageSourceSnapshot(ref=card.ref, object_id=card.object_id,
            logical_object_id=card.logical_object_id, owner='A', controller='A', zone='battlefield', types=('creature',))
        lki = {'schema_version': 1, 'power': 4, 'source': old.to_dict()}
        host = SimpleNamespace(state=SimpleNamespace(cards={card.object_id:card}),
            _effective_card_data=lambda _: {'type_line':'Artifact', 'power':'99'},
            _type_parts=lambda _: ({'artifact'},set(),set()))
        with mock.patch.object(power_damage, 'source_snapshot', return_value=replace(old, types=('artifact',), controller='B')):
            amount, snapshot = power_damage._source_value(host, ref=card.ref, lki=lki)
        self.assertEqual((0, 'B'), (amount, snapshot.controller))
        host._effective_card_data = lambda _: {'type_line':'Creature', 'power':'99'}
        host._type_parts = lambda _: ({'creature'},set(),set())
        prior = {**lki, 'source': replace(old, logical_object_id='older-incarnation').to_dict()}
        self.assertEqual(4, power_damage._source_value(host, ref=card.ref, lki=prior)[0])
        for malformed in ({**lki, 'schema_version':True}, {**lki, 'unknown':1}, {'power':4}):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                power_damage._source_value(host, ref=card.ref, lki=malformed)


    def test_departure_power_refreshes_stack_and_waiting_frames_once_without_rebinding(self):
        import quorune.creature_power_damage as power_damage
        from quorune.damage_source import DamageSourceSnapshot
        from quorune.rules.stack_damage_sources import pin_creature_power_damage_departures
        from quorune.trigger_batches import PendingTriggerBatch
        key = power_damage.CREATURE_POWER_DAMAGE_LKI_CONTEXT
        card = CardInstance(object_id='power-object', ref='power-ref', oracle_id='fixture:power',
            printed_name='Power source', owner='A', controller='B', zone='battlefield')
        source = DamageSourceSnapshot(ref=card.ref, object_id=card.object_id, logical_object_id=card.logical_object_id,
            controller='A', owner='A', zone='battlefield', types=('creature',))
        before = {'schema_version': 1, 'power': 4, 'source': source.to_dict()}
        after = {'schema_version': 1, 'power': 5, 'source': replace(source, controller='B', keywords=('lifelink',)).to_dict()}
        frame = StackItem(stack_id='stack1', ref='frame1', kind='triggered_ability', controller='A', label='Power',
            source_object_id=card.object_id, context={key:deepcopy(before), 'source_logical_object_id':card.logical_object_id})
        waiting = replace(frame, stack_id='stack2', ref='frame2')
        old_source = replace(source, logical_object_id='old-incarnation')
        old_frame = replace(frame, stack_id='stack3', ref='frame3', context={key:{**before, 'source':old_source.to_dict()},
            'source_logical_object_id':'old-incarnation'})
        pending = PendingTriggerBatch.from_dict({'batch_id':'batch', 'ref':'batch-ref', 'apnap_order':['A','B'],
            'groups':[{'controller':'A','items':[waiting.to_dict()]}], 'turn_sequence':0,'priority_epoch':0,'placement_started':False})
        host = SimpleNamespace(state=SimpleNamespace(stack=[frame,old_frame], pending_trigger_batches=[pending]))
        with mock.patch.object(power_damage, 'capture_creature_power_damage_source_lki', return_value=after) as capture:
            pin_creature_power_damage_departures(host, (card,))
        self.assertEqual(1, capture.call_count)
        self.assertEqual(after, frame.context[key])
        self.assertEqual(after, host.state.pending_trigger_batches[0].to_dict()['groups'][0]['items'][0]['context'][key])
        self.assertEqual(4, old_frame.context[key]['power'])
        self.assertEqual('A', frame.controller)
        # Validate every relevant frame before publishing any refresh.
        for bad_snapshot in ({**before,'power':True},
                {**before,'source':replace(source,ref='foreign-public-ref').to_dict()}):
            with self.subTest(bad_snapshot=bad_snapshot):
                bad = replace(frame, stack_id='stack4', ref='frame4', context={key:bad_snapshot})
                frame.context[key] = deepcopy(before)
                host.state.stack = [frame,bad]
                prior = deepcopy(frame.context)
                with mock.patch.object(power_damage, 'capture_creature_power_damage_source_lki', return_value=after):
                    with self.assertRaises(ValueError):
                        pin_creature_power_damage_departures(host, (card,))
                self.assertEqual(prior, frame.context)
