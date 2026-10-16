from __future__ import annotations

"""CR 608.2h/120.3f: source identity and characteristics survive departure."""

import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class FixedDamageSourceLkiValueTests(unittest.TestCase):
    def test_fixed_damage_set_intent_thaws_immutable_facts_without_relaxing_wire_schema(self):
        from quorune.damage_source import DamageError, DamageSourceSnapshot
        from quorune.fixed_damage_set_model import FixedDamageSetSpec, PlayerDamageGroup, PlayerDamageRelation
        from quorune.replacement.immutable import FrozenMap, thaw_value
        from quorune.semantic_runtime.intents import DealFixedDamageSetIntent
        snapshot = self.snapshot()
        spec = FixedDamageSetSpec((PlayerDamageGroup(PlayerDamageRelation.ALL),))
        intent = DealFixedDamageSetIntent(actor='A', source_ref=snapshot.ref, amount=1, spec=spec,
            reason='Immutable damage source regression', source_snapshot=FrozenMap(snapshot.to_dict()))
        self.assertEqual(snapshot.to_dict(), thaw_value(intent.source_snapshot))
        self.assertEqual(snapshot, DamageSourceSnapshot.from_dict(thaw_value(intent.source_snapshot)))
        malformed = {**snapshot.to_dict(), 'types': ('creature',)}
        with self.assertRaises(DamageError):
            DamageSourceSnapshot.from_dict(malformed)
        with self.assertRaises(ValueError):
            DealFixedDamageSetIntent(actor='A', source_ref='another-source', amount=1, spec=spec,
                reason='Wrong source', source_snapshot=FrozenMap(snapshot.to_dict()))

    def test_source_damage_capability_is_required_before_program_admission(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from test_granted_skulk import record
        text = 'When this creature enters, it deals 3 damage to any target.'
        ir = compile_oracle_card(record(text,'Creature — Human'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertIn('damage.source.stack_current_or_lki',ir.faces[0].nodes[0].capability_dependencies)
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='damage.source.stack_current_or_lki').update(status='blocked',blockers=['withdrawn'])
        ir = compile_oracle_card(record(text,'Creature — Human'),capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_face_down_source_snapshots_withhold_hidden_oracle_identity(self):
        from dataclasses import replace
        from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT, fixed_damage_source_context
        snapshot = replace(self.snapshot(),oracle_id='private-oracle-identity')
        source = SimpleNamespace(ref=snapshot.ref,controller='D',zone='battlefield',face_down=True)
        with patch('quorune.rules.stack_damage_sources.source_snapshot',return_value=snapshot):
            context = fixed_damage_source_context(None,source,[{'op':'damage','source':'$source','amount':3,'target':'B'}])
        self.assertIsNone(context[SOURCE_DAMAGE_CONTEXT]['oracle_id'])

    def test_prepared_damage_event_keeps_its_sealed_source_facts(self):
        from dataclasses import replace
        from quorune.model import StackItem
        from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT, refresh_stack_damage_source
        old = self.snapshot(); current = replace(old,controller='B',keywords=())
        source = SimpleNamespace(object_id=old.object_id,logical_object_id=old.logical_object_id,ref=old.ref,
            controller='B',owner='A',zone='battlefield',face_down=False)
        item = StackItem(stack_id='source-event-id',ref='source-event-ref',kind='triggered_ability',controller='A',label='Source event',
            source_object_id=old.object_id,context={'currently_resolving':True,'source_logical_object_id':old.logical_object_id,
            SOURCE_DAMAGE_CONTEXT:old.to_dict()})
        host = SimpleNamespace(state=SimpleNamespace(stack=[item],cards={old.object_id:source}))
        with patch('quorune.rules.stack_damage_sources.source_snapshot',return_value=current) as capture:
            self.assertEqual(old,refresh_stack_damage_source(host,old,sealed=True))
            capture.assert_not_called()
            self.assertEqual(current,refresh_stack_damage_source(host,old,sealed=False))
    def snapshot(self):
        from quorune.damage_source import DamageSourceSnapshot
        return DamageSourceSnapshot(ref='same-source-ref', object_id='source-object', logical_object_id='old-incarnation',
            controller='D', owner='A', zone='battlefield', types=('creature',), colors=('R',), keywords=('lifelink',))

    def test_source_binding_never_reads_a_new_incarnation_and_rejects_wrong_identity(self):
        from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT, bound_stack_damage_source
        from quorune.damage_source import DamageError
        snapshot = self.snapshot()
        host = SimpleNamespace(state=SimpleNamespace(cards={'source-object':SimpleNamespace(
            logical_object_id='new-incarnation', zone='battlefield')}),
            _effective_card_data=lambda card: self.fail('New incarnation must not supply old source information'))
        item = SimpleNamespace(source_object_id='source-object', card_object_id=None, controller='A',
            context={'source_logical_object_id':'old-incarnation', SOURCE_DAMAGE_CONTEXT:snapshot.to_dict()})
        effect = {'op':'damage','source':'$source','target':'B','amount':3}
        self.assertEqual(snapshot.to_dict(), bound_stack_damage_source(host,effect,item)['source_snapshot'])
        for altered in ('malformed', {**snapshot.to_dict(),'logical_object_id':'another-incarnation'}, None):
            with self.subTest(altered=type(altered).__name__), self.assertRaises(DamageError):
                item.context[SOURCE_DAMAGE_CONTEXT] = altered
                bound_stack_damage_source(host,effect,item)

    def test_waiting_batches_pin_once_and_failed_snapshot_preparation_publishes_no_updates(self):
        from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT, pin_stack_damage_departures
        from quorune.trigger_batches import create_pending_trigger_batch
        from quorune.model import StackItem
        from quorune.damage_source import DamageError
        source = SimpleNamespace(object_id='source-object',logical_object_id='old-incarnation',ref='same-source-ref',controller='D',face_down=False)
        item = StackItem(stack_id='item-id',ref='pending-ref',kind='triggered_ability',controller='A',label='Pending fixed damage',source_object_id=source.object_id,
            context={'source_logical_object_id':source.logical_object_id,'dynamic_effects':[{'op':'damage','source':'$source','target':'B','amount':3}]})
        batch = create_pending_trigger_batch(batch_id='batch-id',ref='batch-ref',items=[item.to_dict()],apnap_order=['A','B','C','D'],turn_sequence=1,priority_epoch=1)
        host = SimpleNamespace(state=SimpleNamespace(stack=[],pending_trigger_batches=[batch],cards={source.object_id:source}),
            semantics=SimpleNamespace(get=lambda key:None))
        with patch('quorune.rules.stack_damage_sources.source_snapshot',return_value=self.snapshot()):
            pin_stack_damage_departures(host,[source])
        current = host.state.pending_trigger_batches[0]
        self.assertEqual(batch.batch_id,current.batch_id)
        self.assertEqual(self.snapshot().to_dict(),current.to_dict()['groups'][0]['items'][0]['context'][SOURCE_DAMAGE_CONTEXT])
        before = current.to_dict(); before_stack = deepcopy(item.context)
        host.state.stack=[item]
        with patch('quorune.rules.stack_damage_sources.source_snapshot',side_effect=DamageError('Unavailable source facts')):
            with self.assertRaises(DamageError):pin_stack_damage_departures(host,[source])
        self.assertEqual(before,host.state.pending_trigger_batches[0].to_dict())
        self.assertEqual(before_stack,item.context)
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import StateInvariantError
        def mutate_scalar(*args,**kwargs):item.context['scalar_reference_snapshots']={'temporarily':'changed'}
        with patch('quorune.zone_transitions.pin_scalar_characteristic_departures',side_effect=mutate_scalar), \
             patch('quorune.token_copy_references.pin_copy_characteristic_departures'), \
             patch('quorune.zone_transitions.pin_host_relative_power_source_departures'), \
             patch('quorune.zone_transitions.pin_host_station_departures'), \
             patch('quorune.rules.stack_damage_sources.source_snapshot',side_effect=DamageError('Unavailable source facts')):
            with self.assertRaises(StateInvariantError):ZoneTransitionOwner(host).pin_characteristic_departures([source])
        self.assertEqual(before_stack,item.context)
        self.assertEqual(before,host.state.pending_trigger_batches[0].to_dict())

    def test_other_creatures_include_a_new_incarnation_of_the_original_damage_source(self):
        from quorune.fixed_damage_set import snapshot_fixed_damage_set
        from quorune.fixed_damage_set_model import FixedDamageSetSpec, PermanentDamageGroup
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.object_query import ObjectQueryResult
        row = ObjectQueryResult(object_id='source-object',ref='same-source-ref',printed_name='Returned source',owner='A',
            controller='A',zone='battlefield',types=('creature',),logical_object_id='new-incarnation')
        query = SimpleNamespace(fixed_damage_active_seats=lambda:('A','B'),fixed_damage_apnap_order=lambda:('A','B'),
            fixed_damage_object_rows=lambda actor:(row,))
        spec = FixedDamageSetSpec((PermanentDamageGroup(query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)),exclude_source=True),))
        old = snapshot_fixed_damage_set(query,actor='A',spec=spec,source_ref=row.ref,source_logical_object_id='old-incarnation')
        self.assertEqual((row.ref,),tuple(value.ref for value in old.recipients))
        current = snapshot_fixed_damage_set(query,actor='A',spec=spec,source_ref=row.ref,source_logical_object_id='new-incarnation')
        self.assertFalse(current.recipients)


class FixedDamageSourceLkiRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name).resolve() / 'fixed-damage-source-lki.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json',
            ROOT / 'tests/fixtures/fixed-damage-source-lki-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Damage source LKI witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def test_original_crook_damage_uses_current_or_departed_source_controller_and_lifelink(self):
        for depart in (False, True):
            with self.subTest(depart=depart):
                session = self.session(401001 + depart); engine = session.engine
                source = self.add(engine, 'Cornered Crook', zone='hand', ref='original-fixed-source-crook')
                payment = self.add(engine, 'Memnite', ref='crook-actual-sacrifice-cost')
                grant = self.add(engine, 'Generic Damage Source Lifelink', zone='hand', ref='crook-temporary-lifelink')
                control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='crook-foreign-control')
                bounce = self.add(engine, 'Generic Damage Source Bounce', zone='hand', ref='crook-response-bounce')
                private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='crook-private-control')
                self.assert_closed(session, source); self.prepare(session, mana={'C': 10, 'R': 4, 'U': 1})
                engine.state.players['D'].mana_pool['W'] = 1
                self.checkpoint(session); self.cast(session, 'A', source)
                for _ in range(16):
                    if session.state.stack[-1].kind == 'triggered_ability':
                        break
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                else:
                    self.fail('Original Crook entry did not create its trigger')
                self.cast(session, 'A', grant, [source.ref]); self.cast(session, 'D', control, [source.ref])
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                source = session.state.cards[source.object_id]; identity = source.logical_object_id
                self.assertEqual('D', source.controller)
                self.assertIn('Lifelink', engine._effective_card_data(source)['keywords'])
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'cards': [payment.ref]}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                chosen = session.act('pilot:A', {'action_id': 'choose', 'cards': [payment.ref]})
                self.assertTrue(chosen.ok, chosen.summary)
                self.assertEqual('semantic.target', session.state.pending_decision.kind)
                self.assertEqual('A', session.state.stack[-1].controller)
                selected = session.act('pilot:A', {'action_id': 'choose', 'targets': ['B']})
                self.assertTrue(selected.ok, selected.summary)
                if depart:
                    self.cast(session, 'A', bounce, [source.ref])
                self.resolve(session)
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                session = self.replay(session, load=True)
                if depart:
                    current = session.state.cards[source.object_id]
                    self.assertEqual('hand', current.zone)
                    self.assertNotEqual(identity, current.logical_object_id)
                self.assertEqual({'A': 40, 'B': 37, 'C': 40, 'D': 43},
                    {seat: player.life for seat, player in session.state.players.items()})

    def test_original_hellion_fixed_damage_set_retains_departed_source_lifelink(self):
        session = self.session(401010); engine = session.engine
        source = self.add(engine, 'Crater Hellion', zone='hand', ref='original-fixed-set-hellion')
        own = self.add(engine, 'Generic Bound Body', ref='hellion-own-recipient')
        foreign = self.add(engine, 'Generic Bound Body', seat='B', ref='hellion-foreign-recipient')
        grant = self.add(engine, 'Generic Damage Source Lifelink', zone='hand', ref='hellion-lifelink')
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='hellion-control')
        bounce = self.add(engine, 'Generic Damage Source Bounce', zone='hand', ref='hellion-response-bounce')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 8, 'R': 3, 'U': 1})
        engine.state.players['D'].mana_pool['W'] = 1
        self.checkpoint(session); self.cast(session, 'A', source)
        for _ in range(16):
            if session.state.stack[-1].kind == 'triggered_ability':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Original Hellion entry did not create its trigger')
        parent = session.state.stack[-1].ref
        self.cast(session, 'A', grant, [source.ref]); self.cast(session, 'D', control, [source.ref])
        for _ in range(24):
            if session.state.stack[-1].ref == parent:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Control and grant spells did not finish before the Hellion trigger')
        self.cast(session, 'A', bounce, [source.ref]); self.resolve(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[own.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[foreign.object_id].zone)
        self.assertEqual(4, session.state.cards[own.object_id].marked_damage)
        self.assertEqual(4, session.state.cards[foreign.object_id].marked_damage)
        self.assertEqual({'A': 40, 'B': 40, 'C': 40, 'D': 48},
            {seat: player.life for seat, player in session.state.players.items()})
        self.replay(session, load=True)

    def test_original_myr_death_trigger_uses_predeparture_source_facts_and_controller(self):
        session = self.session(401020); engine = session.engine
        source = self.add(engine, 'Perilous Myr', zone='hand', ref='original-death-damage-myr')
        grant = self.add(engine, 'Generic Damage Source Lifelink', zone='hand', ref='arsonist-lifelink')
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='arsonist-control')
        kill = self.add(engine, 'Generic Damage Source Kill', zone='hand', ref='arsonist-departure')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 2})
        engine.state.players['D'].mana_pool['W'] = 1
        self.checkpoint(session); self.cast(session, 'A', source); self.resolve(session)
        self.cast(session, 'A', grant, [source.ref]); self.cast(session, 'D', control, [source.ref]); self.resolve(session)
        self.assertEqual('D', session.state.cards[source.object_id].controller)
        self.cast(session, 'A', kill, [source.ref])
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.assertEqual('D', session.state.stack[-1].controller)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': 'choose', 'targets': ['B']}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        chosen = session.act('pilot:D', {'action_id': 'choose', 'targets': ['B']})
        self.assertTrue(chosen.ok, chosen.summary); self.resolve(session)
        self.assertEqual({'A': 40, 'B': 38, 'C': 40, 'D': 42},
            {seat: player.life for seat, player in session.state.players.items()})
        self.replay(session, load=True)

    def test_original_roastmaster_keeps_ability_opponents_separate_from_damage_source_controller(self):
        session = self.session(401030); engine = session.engine
        source = self.add(engine, 'Witty Roastmaster', zone='hand', ref='original-each-opponent-roastmaster')
        entering = self.add(engine, 'Memnite', zone='hand', ref='roastmaster-entry-event')
        grant = self.add(engine, 'Generic Damage Source Lifelink', zone='hand', ref='roastmaster-lifelink')
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='roastmaster-control')
        bounce = self.add(engine, 'Generic Damage Source Bounce', zone='hand', ref='roastmaster-response-bounce')
        self.add(engine, 'Island', ref='roastmaster-response-mana')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 2, 'R': 1, 'U': 1})
        engine.state.players['D'].mana_pool['W'] = 1
        self.checkpoint(session); self.cast(session, 'A', source); self.resolve(session)
        self.cast(session, 'A', entering)
        for _ in range(16):
            if session.state.stack[-1].kind == 'triggered_ability':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Actual creature cast did not create the Roastmaster trigger')
        parent = session.state.stack[-1].ref
        self.cast(session, 'A', grant, [source.ref]); self.cast(session, 'D', control, [source.ref])
        for _ in range(24):
            if session.state.stack[-1].ref == parent:
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        else:
            self.fail('Control and grant spells did not finish before the original trigger')
        self.cast(session, 'A', bounce, [source.ref]); self.resolve(session)
        self.assertEqual({'A': 40, 'B': 39, 'C': 39, 'D': 42},
            {seat: player.life for seat, player in session.state.players.items()})
        self.replay(session, load=True)

    def test_source_facts_are_read_after_a_paid_prefix_removes_the_lifelink_grant(self):
        session = self.session(401040); engine = session.engine
        source = self.add(engine, 'Generic Damage Prefix Source', zone='hand', ref='paid-prefix-damage-source')
        aura = self.add(engine, 'Generic Damage Flash Aura', zone='hand', ref='paid-prefix-lifelink-grant')
        self.assert_closed(session, source); self.assert_closed(session, aura)
        self.prepare(session); self.checkpoint(session); self.cast(session, 'A', source)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        selected = session.act('pilot:A', {'action_id':'choose','targets':['B']})
        self.assertTrue(selected.ok,selected.summary)
        self.cast(session,'A',aura,[source.ref])
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertIn('Lifelink',session.engine._effective_card_data(session.state.cards[source.object_id])['keywords'])
        session=self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[aura.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        self.assertNotIn('Lifelink',session.engine._effective_card_data(session.state.cards[source.object_id])['keywords'])
        self.replay(session,load=True)
        self.assertEqual({'A':40,'B':39,'C':40,'D':40},
            {seat:player.life for seat,player in session.state.players.items()})
