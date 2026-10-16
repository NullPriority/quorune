from __future__ import annotations

"""CR 603.12: action payment precedes a distinct, targeted triggered ability."""

import unittest
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class ReflexivePaymentCompilerTests(unittest.TestCase):
    def test_deferred_program_preserves_child_binding_and_rejects_malformed_payloads(self):
        from copy import deepcopy
        from quorune.oracle_ir import _contextual_effect_templates
        from quorune.reflexive_trigger_model import ReflexiveTriggerProgram
        from quorune.semantic_runtime.values import resolve_semantic_value
        fixed, _triggered = _contextual_effect_templates(True, ('creature',), None)
        compiled = fixed('Target player draws two cards.', card_name='Fixture')
        program = ReflexiveTriggerProgram(effects=compiled[1], target_schema=compiled[2], mechanic_ids=compiled[3])
        raw = program.to_dict()
        parent = SimpleNamespace(controller='A', targets=['WRONG-PARENT'], context={})
        self.assertEqual(raw, resolve_semantic_value(None, raw, parent))
        child = SimpleNamespace(controller='A', targets=['C'], context={})
        self.assertEqual('C', resolve_semantic_value(None, raw['effects'][0]['player'], child))
        self.assertEqual(program, ReflexiveTriggerProgram.from_dict(json.loads(json.dumps(raw))))
        for field, value in (('schema_version', True), ('effects', []), ('target_schema', {}), ('extra', 1)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ReflexiveTriggerProgram.from_dict({**raw, field: value})
        altered = deepcopy(raw); altered['effects'][0]['player'] = '$context.target'
        with self.assertRaises(ValueError):
            ReflexiveTriggerProgram.from_dict(altered)
        from quorune.rules.reflexive_payment_shapes import reflexive_program_capabilities
        altered = deepcopy(raw); altered['effects'][0]['op'] = 'unrepresented_draw_operation'
        self.assertEqual((), reflexive_program_capabilities(ReflexiveTriggerProgram.from_dict(altered)))
        from quorune.reflexive_trigger_model import QueueReflexiveTriggerIntent
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceFrame, SemanticChoiceCompletion
        from quorune.semantic_choices.completed_choice import encode_remaining_completion, decode_remaining_completion
        continuation = SemanticChoiceContinuation(handler_id='choice.payment.optional-fixed-effect.v1', handler_version=1,
            stack_ref='parent-frame', effect=FrozenMap({'op':'offer_optional_mana_payment'}), remaining=(),
            destination=None, note='', semantic_frame=SemanticChoiceFrame(semantic_program_id='fixture:trigger',
                semantic_program_version=1, stack_object='parent-frame', instruction_pointer=0, controller='A'))
        completion = SemanticChoiceCompletion(intents=(QueueReflexiveTriggerIntent(controller='A',
            parent_stack_ref='parent-frame', program=FrozenMap(raw)),))
        encoded = encode_remaining_completion(continuation, 'A', {}, completion, 0)
        restored = decode_remaining_completion(json.loads(json.dumps(encoded)))
        self.assertEqual(completion.intents, restored[3].intents)

    def test_reflexive_payment_defers_the_complete_typed_body_and_its_targets(self):
        registry = load_default_capability_registry()
        for cost in ('pay {1}{R}', 'pay 2 life', 'pay {E}{E}', 'discard a card', 'sacrifice a creature'):
            text = 'When this creature enters, you may ' + cost + ". When you do, target creature can't block this turn."
            with self.subTest(cost=cost):
                ir = compile_oracle_card(record(text, 'Creature — Human'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertIsNone(node.target_schema)
                self.assertEqual(1, len(node.effects))
                effect = node.effects[0]
                self.assertEqual(4, effect['schema_version'])
                self.assertEqual('reflexive_trigger_program', effect['reflexive_program']['kind'])
                self.assertEqual('$target.0', effect['reflexive_program']['effects'][0]['card'])
                self.assertIsNotNone(effect['reflexive_program']['target_schema'])
                self.assertIn('trigger.reflexive.fixed_action_result', node.capability_dependencies)
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='trigger.reflexive.fixed_action_result').update(
            status='blocked', blockers=['withdrawn'])
        ir = compile_oracle_card(record(text, 'Creature — Human'), capability_registry=CapabilityRegistry(raw),
            capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)

    def test_repeat_negative_and_ambiguous_paid_object_forms_stay_residual(self):
        registry = load_default_capability_registry()
        for body in ('you may pay {1} any number of times. When you pay this cost one or more times, draw a card.',
                     'you may pay {1}. When you do not, draw a card.',
                     'you may sacrifice a creature. When you do, draw cards equal to its power.'):
            ir = compile_oracle_card(record('When this creature enters, ' + body, 'Creature — Human'),
                capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)
            self.assertTrue(ir.material_residuals)


class ReflexivePaymentRuntimeTests(unittest.TestCase):
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
        path = Path(cls.temporary.name).resolve() / 'reflexive-payments.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/reflexive-payment-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Reflexive payment witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def advance_to(self, session, kind):
        for _ in range(64):
            session.engine.pump()
            decision = session.state.pending_decision
            if decision.kind == kind:
                return decision
            self.assertEqual('priority', decision.kind)
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.fail('Decision did not arrive: ' + kind)

    def choose(self, session, actor='A', **response):
        accepted = session.act('pilot:' + actor, {'action_id': 'choose', **response})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_labyrinth_creates_targeted_response_window_after_payment_and_can_be_countered(self):
        for counter in (False, True):
            with self.subTest(counter=counter):
                session = self.session(400001 + counter); engine = session.engine
                source = self.add(engine, 'Labyrinth Adversary', zone='hand', ref='actual-reflexive-adversary')
                attacker = self.add(engine, 'Generic Bound Body', ref='reflexive-attacker')
                blocker = self.add(engine, 'Generic Bound Body', seat='B', ref='reflexive-blocker')
                self.add(engine, 'Generic Bound Body', seat='B', ref='other-legal-blocker')
                stifle = self.add(engine, 'Stifle', seat='B', zone='hand', ref='actual-reflexive-counter')
                first_mana = self.add(engine, 'Mountain', ref='reflexive-payment-first-land')
                second_mana = self.add(engine, 'Mountain', ref='reflexive-payment-second-land')
                self.add(engine, 'Island', seat='B', ref='reflexive-counter-land')
                private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='reflexive-private')
                self.assert_closed(session, source); self.assert_closed(session, stifle)
                self.prepare(session, mana={'C': 6, 'R': 4})
                self.checkpoint(session); self.cast(session, 'A', source); self.resolve(session)
                self.advance_to(session, 'combat.attackers')
                accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {attacker.ref: 'B'}})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                parent = session.state.stack[-1]
                self.assertFalse(parent.targets)
                parent_ref = parent.ref
                session = self.replay(session, load=True)
                self.choose(session, pay=True)
                self.assertEqual('semantic.target', session.state.pending_decision.kind)
                self.assertTrue(session.state.cards[first_mana.object_id].tapped)
                self.assertTrue(session.state.cards[second_mana.object_id].tapped)
                child = session.state.stack[-1]
                self.assertEqual(parent_ref, child.context['reflexive_parent'])
                self.assertNotEqual(parent_ref, child.ref)
                self.assertFalse(any(item.ref == parent_ref for item in session.state.stack))
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'targets': [blocker.ref]}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                self.choose(session, targets=[blocker.ref])
                if counter:
                    self.cast(session, 'B', stifle, [child.ref])
                self.resolve(session)
                self.advance_to(session, 'combat.blockers')
                before = authoritative_state_hash(session.state)
                result = session.act('pilot:B', {'action_id': 'block', 'blocks': {blocker.ref: attacker.ref}})
                self.assertEqual(counter, result.ok, result.summary)
                if not counter:
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    self.assertTrue(session.act('pilot:B', {'action_id': 'block', 'blocks': {}}).ok)
                for viewer in 'ABC':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_source_sacrifice_retains_locked_controller_incarnation_and_private_draw_target(self):
        for steal in (False, True):
            with self.subTest(steal=steal):
                session = self.session(400010 + steal); engine = session.engine
                source = self.add(engine, 'Generic Reflexive Sacrifice', zone='hand', ref='self-sacrifice-reflexive')
                payment = self.add(engine, 'Generic Bound Body', ref='other-sacrifice-cost') if steal else source
                control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='reflexive-control-change') if steal else None
                self.assert_closed(session, source); self.prepare(session)
                engine.state.players['D'].mana_pool['W'] = 1
                self.checkpoint(session); self.cast(session, 'A', source)
                if steal:
                    for _ in range(16):
                        if session.state.stack[-1].kind == 'triggered_ability':
                            break
                        self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                    else:
                        self.fail('Original source entry did not create its parent trigger')
                    self.cast(session, 'D', control, [source.ref])
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                source = session.state.cards[source.object_id]; original_identity = source.logical_object_id
                self.assertEqual('D' if steal else 'A', source.controller)
                before_hands = {seat: len(player.zones['hand']) for seat, player in session.state.players.items()}
                session = self.replay(session, load=True)
                self.choose(session, cards=[session.state.cards[payment.object_id].ref])
                self.assertEqual('graveyard', session.state.cards[payment.object_id].zone)
                self.assertEqual('semantic.target', session.state.pending_decision.kind)
                child = session.state.stack[-1]
                self.assertEqual('A', child.controller)
                self.assertEqual(original_identity, child.context['source_logical_object_id'])
                if not steal:
                    self.assertNotEqual(original_identity, session.state.cards[source.object_id].logical_object_id)
                session = self.replay(session, load=True); self.choose(session, targets=['C']); self.resolve(session)
                for seat in 'ABCD':
                    self.assertEqual(before_hands[seat] + (2 if seat == 'C' else 0), len(session.state.players[seat].zones['hand']))
                drawn = [session.state.cards[oid].ref for oid in session.state.players['C'].zones['hand'][-2:]]
                for viewer in 'ABD':
                    packet = json.dumps(session.packet('pilot:' + viewer, full=True))
                    for ref in drawn:
                        self.assertNotIn(ref, packet)
                self.replay(session, load=True)

    def test_spell_source_finishes_before_reflexive_target_selection_and_keeps_its_identity(self):
        session = self.session(400030); engine = session.engine
        source = self.add(engine, 'Generic Reflexive Spell', zone='hand', ref='reflexive-spell-source')
        self.assert_closed(session, source); self.prepare(session); self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        old_identity = session.state.cards[source.object_id].logical_object_id
        session = self.replay(session, load=True); self.choose(session, pay=True)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(38, session.state.players['A'].life)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        child = session.state.stack[-1]
        self.assertEqual(source.object_id, child.source_object_id)
        self.assertEqual(old_identity, child.context['source_logical_object_id'])
        hand = len(session.state.players['D'].zones['hand'])
        session = self.replay(session, load=True); self.choose(session, targets=['D']); self.resolve(session)
        self.assertEqual(hand + 1, len(session.state.players['D'].zones['hand']))
        self.replay(session, load=True)

    def test_no_legal_child_target_does_not_suppress_or_refund_payment_and_decline_creates_no_child(self):
        for pay, mana in ((False, 2), (True, 2), (False, 0)):
            with self.subTest(pay=pay, mana=mana):
                session = self.session(400020 + mana + pay); engine = session.engine
                source = self.add(engine, 'Generic Reflexive Tap', zone='hand', ref='no-child-target')
                self.assert_closed(session, source); self.prepare(session, mana={'C': mana}); self.checkpoint(session)
                self.cast(session, 'A', source)
                self.assertEqual('semantic.choice', self.resolve(session).kind)
                session = self.replay(session, load=True)
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'pay': True}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                if not mana:
                    self.assertFalse(session.act('pilot:A', {'action_id': 'choose', 'pay': True}).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                self.choose(session, pay=pay); self.resolve(session)
                self.assertFalse(session.state.stack)
                self.assertEqual(mana - int(pay), session.state.players['A'].mana_pool['C'])
                self.replay(session, load=True)

    def test_actual_final_saga_chapter_discards_before_selecting_that_card_as_a_new_target(self):
        session = self.session(400040); engine = session.engine
        saga = self.add(engine, 'Awaken the Honored Dead', zone='hand', ref='actual-reflexive-saga')
        destroy = self.add(engine, 'Generic Bound Body', seat='B', ref='first-saga-target')
        discard = self.add(engine, 'Generic Bound Body', zone='hand', ref='third-chapter-discard-then-target')
        private = self.add(engine, 'Generic Bound Growth', zone='hand', ref='saga-private-hand-control')
        first_lore = self.add(engine, 'Generic Reflexive Lore', zone='hand', ref='second-chapter-lore')
        final_lore = self.add(engine, 'Generic Reflexive Lore', zone='hand', ref='final-chapter-lore')
        self.assert_closed(session, saga); self.prepare(session, mana={'B': 1, 'G': 1, 'U': 1}); self.checkpoint(session)
        self.cast(session, 'A', saga)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.choose(session, targets=[destroy.ref]); self.resolve(session)
        library_before = len(session.state.players['A'].zones['library'])
        self.cast(session, 'A', first_lore, [saga.ref]); self.resolve(session)
        self.assertEqual(2, session.state.cards[saga.object_id].counters['lore'])
        self.assertEqual(library_before - 3, len(session.state.players['A'].zones['library']))
        self.cast(session, 'A', final_lore, [saga.ref])
        decision = self.resolve(session)
        self.assertIsNotNone(decision, {'zone':session.state.cards[saga.object_id].zone,
            'counters':dict(session.state.cards[saga.object_id].counters),
            'stack':[(item.kind,item.label,item.semantic_key) for item in session.state.stack],
            'decision':session.state.pending_decision.kind})
        self.assertEqual('semantic.choice', decision.kind)
        self.assertEqual('battlefield', session.state.cards[saga.object_id].zone)
        identity = session.state.cards[saga.object_id].logical_object_id
        for viewer in 'BCD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        session = self.replay(session, load=True)
        self.choose(session, cards=[discard.ref])
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.assertEqual('graveyard', session.state.cards[saga.object_id].zone)
        self.assertEqual(identity, session.state.stack[-1].context['source_logical_object_id'])
        current = session.state.cards[discard.object_id]
        self.assertEqual('graveyard', current.zone)
        session = self.replay(session, load=True)
        self.choose(session, targets=[current.ref]); self.resolve(session)
        self.assertEqual('hand', session.state.cards[discard.object_id].zone)
        self.replay(session, load=True)
