from __future__ import annotations

"""CR 603.12: action payment precedes a distinct, targeted triggered ability."""

import unittest
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
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
    def test_paired_entry_and_step_allow_a_local_linked_target(self):
        from quorune.rules.event_subscriptions import FixedEventSubscriptionSet

        head = "When this enchantment enters and at the beginning of your end step, "
        body = ("you may pay {1}{W}. When you do, exile target creature you control, "
                "then return that card to the battlefield under its owner's control.")
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record(head + body, "Enchantment"), capability_registry=registry,
                                 capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        subscriptions = FixedEventSubscriptionSet.from_condition(node.event_condition)
        self.assertEqual(("permanent.enter", "step.begin"), tuple(child.event for child in subscriptions.subscriptions))
        self.assertIsNone(node.target_schema)
        self.assertIsNotNone(node.effects[0]["reflexive_program"]["target_schema"])
        unsupported = compile_oracle_card(record(head + "put a +1/+1 counter on that creature.", "Enchantment"),
                                          capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", unsupported.status)

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

    def test_occurrence_child_has_its_own_schema_and_rejects_extra_event_references(self):
        from copy import deepcopy
        from quorune.reflexive_trigger_model import ReflexiveTriggerProgram
        from quorune.replacement.immutable import thaw_value

        text = "Whenever another creature you control enters, you may pay {2}. When you do, that creature deals damage equal to its power to target creature."
        ir = compile_oracle_card(record(text, "Creature — Human"), capability_registry=load_default_capability_registry(),
                                 capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertIsNone(node.target_schema)
        payload = thaw_value(node.effects[0]["reflexive_program"])
        self.assertEqual(2, payload["schema_version"])
        self.assertEqual(["creature"], payload["target_schema"]["types_any"])
        self.assertEqual("$target.0", payload["effects"][0]["target"])
        with self.assertRaises(ValueError):
            ReflexiveTriggerProgram.from_dict({**payload, "schema_version": 1})
        forged = deepcopy(payload)
        forged["effects"][0]["target"] = "$context.card"
        with self.assertRaises(ValueError):
            ReflexiveTriggerProgram.from_dict(forged)


    def test_selected_reflexive_sacrifice_rejects_a_forged_paid_power_binding(self):
        from quorune.compiler.fixed_effect_payment_templates import fixed_effect_payment_spec
        from quorune.reflexive_trigger_model import (
            QueueReflexiveTriggerIntent, ReflexivePaidCreatureSnapshot, ReflexiveTriggerProgram,
            SELECTED_REFLEXIVE_SACRIFICE_CAPABILITY,
        )
        from quorune.replacement.immutable import FrozenMap, thaw_value
        from quorune.rules.reflexive_payment_shapes import (
            reflexive_payment_parts, reflexive_program_capabilities, validate_reflexive_paid_power_scope,
        )
        from quorune.scalar_effect_amount_model import PaidCreatureStatSnapshot
        from quorune.semantic_choices.completed_choice import decode_remaining_completion, encode_remaining_completion
        from quorune.semantic_choices.model import (
            SemanticChoiceCompletion, SemanticChoiceContinuation, SemanticChoiceFrame,
        )

        registry = load_default_capability_registry()
        text = ("When this creature enters, you may sacrifice another creature. "
                "When you do, this creature deals damage equal to that creature's power to any target.")
        ir = compile_oracle_card(record(text, 'Creature \u2014 Human'), capability_registry=registry,
                                 capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        parent = thaw_value(ir.faces[0].nodes[0].effects[0])
        payment, child = reflexive_payment_parts(parent)
        self.assertEqual('$source.zone_object', payment.predicate.exclude_ref)
        self.assertTrue(reflexive_program_capabilities(child))
        detached = child.to_dict()['effects']
        with self.assertRaisesRegex(ValueError, 'requires its selected-sacrifice child'):
            validate_reflexive_paid_power_scope(detached, detached, (SELECTED_REFLEXIVE_SACRIFICE_CAPABILITY,))
        malformed = child.to_dict()
        malformed['effects'][0]['target'] = '$target.1'
        self.assertEqual((), reflexive_program_capabilities(ReflexiveTriggerProgram.from_dict(malformed)))
        with self.assertRaises(ValueError):
            ReflexiveTriggerProgram.from_dict({**child.to_dict(), 'schema_version': 2})
        token_price = fixed_effect_payment_spec('sacrifice a token').to_dict()
        with self.assertRaisesRegex(ValueError, 'creature sacrifice predicate'):
            reflexive_payment_parts({**parent, 'payment': token_price})
        unsupported = compile_oracle_card(record(text.replace('another creature', 'a token'), 'Creature \u2014 Human'),
                                          capability_registry=registry, capability_profile='commander_review')
        self.assertNotEqual('exact', unsupported.status)

        continuation = SemanticChoiceContinuation(handler_id='choice.payment.optional-fixed-effect.v1',
            handler_version=1, stack_ref='parent', effect=FrozenMap(parent), remaining=(),
            destination=None, note='', semantic_frame=SemanticChoiceFrame(semantic_program_id='fixture:trigger',
                semantic_program_version=1, stack_object='parent', instruction_pointer=0, controller='A'))
        receipt = ReflexivePaidCreatureSnapshot(PaidCreatureStatSnapshot('paid-incarnation', -2, 4), ('giant',))
        intent = QueueReflexiveTriggerIntent('A', 'parent', FrozenMap(child.to_dict()), receipt)
        completed = SemanticChoiceCompletion(intents=(intent,))
        frozen = encode_remaining_completion(continuation, 'A', {'cards': ['paid-ref']}, completed, 0)
        restored = decode_remaining_completion(json.loads(json.dumps(frozen)))
        self.assertEqual(completed.intents, restored[3].intents)


class ReflexivePaymentRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    resolve_top = InheritedNativeFixture.resolve_top
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

    def pay_for_targeted_child(self, session):
        parent = session.state.stack[-1]
        self.assertFalse(parent.targets)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.choose(session, pay=True)
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        child = session.state.stack[-1]
        self.assertEqual(parent.ref, child.context["reflexive_parent"])
        self.assertNotIn(parent.ref, [item.ref for item in session.state.stack])
        return child

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

    def test_halana_retains_the_entrant_across_payment_and_departure(self):
        from quorune.creature_power_damage_model import OCCURRENCE_POWER_DAMAGE_CONTEXT

        for depart_before_pay in (False, True):
            with self.subTest(depart_before_pay=depart_before_pay):
                session = self.session(603823001 + depart_before_pay)
                watcher = self.add(session.engine, "Halana, Kessig Ranger")
                entrant = self.add(session.engine, "Child of Night", zone="hand")
                boost = self.add(session.engine, "Giant Growth", zone="hand")
                bounce = self.add(session.engine, "Unsummon", zone="hand")
                target = self.add(session.engine, "Wall of Stone", seat="B")
                self.assert_closed(session, watcher)
                self.prepare(session, mana={"C": 7, "B": 1, "G": 3, "U": 3})
                self.checkpoint(session)
                life = session.state.players["A"].life
                self.cast(session, "A", entrant)
                self.resolve_top(session)
                original = session.state.cards[entrant.object_id].logical_object_id
                if not depart_before_pay:
                    child = self.pay_for_targeted_child(session)
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act("pilot:B", {"action_id": "choose", "targets": [target.ref]}).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    self.choose(session, targets=[target.ref])
                self.cast(session, "A", boost, [entrant.ref])
                self.resolve_top(session)
                self.cast(session, "A", bounce, [entrant.ref])
                self.resolve_top(session)
                if depart_before_pay:
                    session = self.replay(session, load=True)
                    child = self.pay_for_targeted_child(session)
                    self.choose(session, targets=[target.ref])
                pending = next(item for item in session.state.stack if item.ref == child.ref)
                snapshot = pending.context[OCCURRENCE_POWER_DAMAGE_CONTEXT]
                self.assertEqual((5, original), (snapshot["power"], snapshot["source"]["logical_object_id"]))
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(5, session.state.cards[target.object_id].marked_damage)
                self.assertEqual(life + 5, session.state.players["A"].life)
                self.replay(session, load=True)

    def test_itzquinth_uses_two_distinct_targets_and_a_live_dinosaur_source(self):
        for source_case in ("live", "noncreature", "departed"):
            with self.subTest(source_case=source_case):
                session = self.session(603823004 + ("live", "noncreature", "departed").index(source_case))
                watcher = self.add(session.engine, "Itzquinth, Firstborn of Gishath", zone="hand")
                kindred = self.add(session.engine, "Generic Reflexive Kindred Dinosaur")
                target = self.add(session.engine, "Wall of Stone", seat="B")
                bounce = self.add(session.engine, "Unsummon", zone="hand")
                self.assert_closed(session, watcher)
                self.prepare(session, mana={"C": 5, "R": 1, "G": 1, "U": 1})
                self.checkpoint(session)
                self.cast(session, "A", watcher)
                self.resolve_top(session)
                self.pay_for_targeted_child(session)
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:A", {"action_id": "choose", "targets": [watcher.ref, watcher.ref]}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                source = kindred if source_case == "noncreature" else watcher
                self.choose(session, targets=[source.ref, target.ref])
                if source_case == "departed":
                    self.cast(session, "A", bounce, [source.ref])
                    self.resolve_top(session)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(2 if source_case == "live" else 0, session.state.cards[target.object_id].marked_damage)
                self.replay(session, load=True)

    def test_cyclops_can_pay_energy_and_deal_its_last_battlefield_power(self):
        session = self.session(603823007)
        cyclops = self.add(session.engine, "Cyclops Superconductor", zone="hand")
        boost = self.add(session.engine, "Giant Growth", zone="hand")
        wrath = self.add(session.engine, "Wrath of God", zone="hand")
        self.assert_closed(session, cyclops)
        self.prepare(session, mana={"C": 8, "R": 4, "G": 4, "W": 5, "U": 2})
        self.checkpoint(session)
        self.cast(session, "A", cyclops)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(3, session.state.players["A"].energy)
        self.cast(session, "A", boost, [cyclops.ref])
        self.assertIsNone(self.resolve(session))
        power = session.engine._numeric_stat(cyclops.object_id, "power")
        life = session.state.players["B"].life
        self.cast(session, "A", wrath)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.pay_for_targeted_child(session)
        self.assertEqual(0, session.state.players["A"].energy)
        self.assertEqual("graveyard", session.state.cards[cyclops.object_id].zone)
        self.choose(session, targets=["B"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life - (power + 1), session.state.players["B"].life)
        self.replay(session, load=True)

    def test_escape_protocol_resolves_the_blink_before_the_cycling_draw(self):
        session = self.session(603823008)
        watcher = self.add(session.engine, "Escape Protocol")
        cycler = self.add(session.engine, "Lonely Sandbar", zone="hand")
        target = self.add(session.engine, "Generic Bound Body")
        self.assert_closed(session, watcher)
        self.assert_closed(session, cycler)
        self.prepare(session, mana={"U": 1, "C": 2})
        self.checkpoint(session)
        original = target.logical_object_id
        draws = len(session.state.players["A"].draw_history)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(action for action in actions if action["id"].startswith("activate:" + cycler.ref + ":"))
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, len(session.state.stack))
        self.pay_for_targeted_child(session)
        self.choose(session, targets=[target.ref])
        self.resolve_top(session)
        current = session.state.cards[target.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(original, current.logical_object_id)
        self.assertEqual(draws, len(session.state.players["A"].draw_history))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(draws + 1, len(session.state.players["A"].draw_history))
        self.replay(session, load=True)

    def test_sparktongue_targets_using_its_ability_sources_final_battlefield_color(self):
        from quorune.target_source import TARGETING_SOURCE_CONTEXT

        session = self.session(603823009)
        source = self.add(session.engine, "Sparktongue Dragon", zone="hand")
        colorless = self.add(session.engine, "Moonlace", zone="hand")
        bounce = self.add(session.engine, "Unsummon", zone="hand")
        knight = self.add(session.engine, "Silver Knight", seat="B")
        skulk = self.add(session.engine, "Mistmeadow Skulk", seat="B")
        for card in (source, colorless, knight, skulk):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 9, "R": 4, "U": 4})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.resolve_top(session)
        self.cast(session, "A", colorless, [source.ref])
        self.resolve_top(session)
        self.cast(session, "A", bounce, [source.ref])
        self.resolve_top(session)
        child = self.pay_for_targeted_child(session)
        snapshot = child.context[TARGETING_SOURCE_CONTEXT]
        self.assertEqual(([], 5.0), (snapshot["colors"], snapshot["mana_value"]))
        action, = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        self.assertIn(knight.ref, action["target_schema"]["legal_refs"])
        self.assertNotIn(skulk.ref, action["target_schema"]["legal_refs"])
        self.choose(session, targets=[knight.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[knight.object_id].zone)
        self.replay(session, load=True)

    def test_merry_bards_keeps_payment_if_no_child_target_remains(self):
        for remove_target in (False, True):
            with self.subTest(remove_target=remove_target):
                session = self.session(603823010 + remove_target)
                bards = self.add(session.engine, "Merry Bards", zone="hand")
                bounce = self.add(session.engine, "Unsummon", zone="hand")
                self.assert_closed(session, bards)
                self.prepare(session, mana={"C": 5, "R": 2, "U": 2})
                self.checkpoint(session)
                self.cast(session, "A", bards)
                self.resolve_top(session)
                if remove_target:
                    self.cast(session, "A", bounce, [bards.ref])
                    self.resolve_top(session)
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                mana = sum(session.state.players["A"].mana_pool.values())
                self.choose(session, pay=True)
                if not remove_target:
                    self.assertEqual("semantic.target", session.state.pending_decision.kind)
                    self.choose(session, targets=[bards.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(mana - 1, sum(session.state.players["A"].mana_pool.values()))
                roles = [card for card in session.state.cards.values() if card.zone == "battlefield" and card.is_token]
                self.assertEqual(0 if remove_target else 1, len(roles))
                if roles:
                    self.assertEqual([roles[0].object_id], session.state.cards[bards.object_id].attachments)
                self.replay(session, load=True)

    def test_go_shintai_counts_shrines_at_child_resolution_after_a_response(self):
        from quorune.turn_step_owner import TURN_STEPS

        session = self.session(603823012)
        watcher = self.add(session.engine, "Go-Shintai of Ancient Wars")
        other = self.add(session.engine, "Honden of Infinite Rage")
        response = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        self.add(session.engine, "Generic Bound Plains", ref="shrine-pay-land")
        for index in range(2):
            self.add(session.engine, "Generic Bound Plains", seat="B", ref=f"disenchant-land-{index}")
        self.assert_closed(session, watcher)
        self.prepare(session)
        session.state.phase, session.state.step = "postcombat_main", "main"
        session.state.phase_index = TURN_STEPS.index(("postcombat_main", "main"))
        self.checkpoint(session)
        life = session.state.players["B"].life
        self.advance_to(session, "semantic.choice")
        self.pay_for_targeted_child(session)
        self.choose(session, targets=["B"])
        self.cast(session, "B", response, [other.ref])
        self.resolve_top(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[other.object_id].zone)
        self.assertEqual(life - 1, session.state.players["B"].life)
        self.replay(session, load=True)

    def test_existing_reflexive_program_derives_its_required_runtime_authority(self):
        from dataclasses import replace
        from quorune.card_programs import bind_semantic_program_runtime

        session = self.session(603823013)
        source = self.add(session.engine, "Labyrinth Adversary")
        self.assert_closed(session, source)
        witness, = (program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if any("reflexive_program" in effect for effect in program.effects))
        capability = "trigger.reflexive.fixed_action_result"
        dependencies = tuple(value for value in witness.capability_dependencies if value != capability)
        mutated = replace(witness, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        bound = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", bound["blockers"])

    def test_quantum_entanglement_pays_and_targets_after_each_independent_occurrence(self):
        from quorune.turn_step_owner import TURN_STEPS

        session = self.session(603824001)
        source = self.add(session.engine, "Quantum Entanglement", zone="hand")
        other_entry = self.add(session.engine, "Honden of Infinite Rage", zone="hand")
        target = self.add(session.engine, "Generic Bound Body", ref="quantum-owned-target")
        foreign = self.add(session.engine, "Generic Bound Body", seat="B", ref="quantum-foreign-target")
        for index in range(4):
            self.add(session.engine, "Generic Bound Plains", ref=f"quantum-plains-{index}")
        self.assert_closed(session, source)
        self.assert_closed(session, other_entry)
        self.prepare(session, mana={"C": 7, "W": 4, "R": 2})
        session.state.phase, session.state.step = "postcombat_main", "main"
        session.state.phase_index = TURN_STEPS.index(("postcombat_main", "main"))
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.resolve_top(session)
        self.assertEqual("permanent.enter", session.state.stack[-1].context["event"])
        self.pay_for_targeted_child(session)
        before = authoritative_state_hash(session.state)
        wrong = session.act("pilot:A", {"action_id": "choose", "targets": [foreign.ref]})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        original = session.state.cards[target.object_id].logical_object_id
        self.choose(session, targets=[target.ref])
        self.assertIsNone(self.resolve(session))
        first_return = session.state.cards[target.object_id].logical_object_id
        self.assertNotEqual(original, first_return)

        self.cast(session, "A", other_entry)
        self.assertIsNone(self.resolve(session))
        self.advance_to(session, "semantic.choice")
        self.assertEqual(("ending", "end_step"), (session.state.phase, session.state.step))
        self.assertEqual("step.begin", session.state.stack[-1].context["event"])
        session = self.replay(session, load=True)
        self.pay_for_targeted_child(session)
        self.choose(session, targets=[target.ref])
        self.assertIsNone(self.resolve(session))
        second_return = session.state.cards[target.object_id].logical_object_id
        self.assertNotIn(second_return, {original, first_return})
        self.replay(session, load=True)

        opponent_turn = self.session(603824002)
        self.add(opponent_turn.engine, "Quantum Entanglement")
        self.add(opponent_turn.engine, "Generic Bound Body", ref="opposing-turn-eligible-target")
        self.prepare(opponent_turn, actor="B", active="B")
        opponent_turn.state.phase, opponent_turn.state.step = "postcombat_main", "main"
        opponent_turn.state.phase_index = TURN_STEPS.index(("postcombat_main", "main"))
        self.checkpoint(opponent_turn)
        for _ in range(8):
            if opponent_turn.state.step == "end_step":
                break
            result = opponent_turn.act(opponent_turn.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("end_step", opponent_turn.state.step)
        self.assertFalse(opponent_turn.state.stack)
        self.assertEqual("priority", opponent_turn.state.pending_decision.kind)
        self.replay(opponent_turn, load=True)

    def _selected_price(self, session, source, price, *, illegal=None):
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice', decision.kind)
        parent = session.state.stack[-1]
        self.assertEqual(source.object_id, parent.source_object_id)
        self.assertFalse(parent.targets)
        command = {'action_id': 'choose', 'cards': [price.ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        if illegal is not None:
            self.assertFalse(session.act('pilot:A', {**command, 'cards': [illegal.ref]}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.choose(session, cards=[price.ref])
        self.assertNotEqual('battlefield', getattr(session.state.cards.get(price.object_id), 'zone', None))
        self.assertFalse(any(item.ref == parent.ref for item in session.state.stack))
        return parent

    def test_original_shirriff_pays_before_its_linked_target(self):
        for eligible_target in (False, True):
            with self.subTest(eligible_target=eligible_target):
                session = self.session(704_856_100 + eligible_target)
                source = self.add(session.engine, 'Shire Shirriff', zone='hand')
                private = self.add(session.engine, 'Generic Bound Growth', seat='D', zone='hand')
                if eligible_target:
                    target = self.add(session.engine, 'Generic Bound Body', seat='C')
                    bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
                    self.add(session.engine, 'Island', seat='B')
                session.engine.create_token('A', name='Food', characteristics={
                    'type_line': 'Token Artifact \u2014 Food',
                    'ability_profile': 'two_tap_sac_gain_three_life_v1',
                })
                price = next(card for card in session.state.cards.values()
                             if card.is_token and card.printed_name == 'Food')
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 2, 'W': 1})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.resolve_top(session)
                parent = self._selected_price(session, source, price, illegal=source)
                if eligible_target:
                    self.assertEqual('semantic.target', session.state.pending_decision.kind)
                    self.assertEqual(parent.ref, session.state.stack[-1].context['reflexive_parent'])
                    session = self.replay(session, load=True)
                    self.choose(session, targets=[target.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual('exile', session.state.cards[target.object_id].zone)
                    self.cast(session, 'B', bounce, [source.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
                else:
                    self.assertFalse(session.state.stack)
                    self.assertEqual('priority', session.state.pending_decision.kind)
                for seat in 'ABC':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + seat, full=True)))
                self.replay(session, load=True)

    def _assert_surtland_paid_damage(self, name, damage, seed):
        session = self.session(seed)
        source = self.add(session.engine, 'Surtland Flinger')
        price = self.add(session.engine, name)
        growth = self.add(session.engine, 'Giant Growth', zone='hand')
        foreign = self.add(session.engine, 'Generic Bound Body', seat='B')
        for card in (source, price, growth):
            self.assert_closed(session, card)
        self.prepare(session, mana={'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [price.ref])
        self.assertIsNone(self.resolve(session))
        incarnation = session.state.cards[price.object_id].logical_object_id
        self.advance_to(session, 'combat.attackers')
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {source.ref: 'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        parent = self._selected_price(session, source, price, illegal=foreign)
        child = session.state.stack[-1]
        self.assertEqual(parent.ref, child.context['reflexive_parent'])
        paid = child.context['reflexive_paid_creature']
        self.assertEqual((4, incarnation), (paid['stats']['power'], paid['stats']['logical_object_id']))
        self.assertEqual(name == 'Universal Automaton', 'giant' in paid['subtypes'])
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        session = self.replay(session, load=True)
        self.choose(session, targets=['C'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(40 - damage, session.state.players['C'].life)
        self.replay(session, load=True)

    def test_original_surtland_flinger_reads_effective_paid_power_and_subtype(self):
        self._assert_surtland_paid_damage('Universal Automaton', 8, 704_856_102)
        self._assert_surtland_paid_damage('Merfolk of the Pearl Trident', 4, 704_856_103)

    def test_reflexive_paid_power_mutant_loses_the_paid_giant_multiplier(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.scalar_effect_amounts import _reflexive_paid_creature_power

        def without_multiplier(item, spec):
            return _reflexive_paid_creature_power(item, replace(spec, double_for_subtype=None))

        with patch('quorune.scalar_effect_amounts._reflexive_paid_creature_power', side_effect=without_multiplier):
            with self.assertRaisesRegex(AssertionError, '32 != 36'):
                self._assert_surtland_paid_damage('Universal Automaton', 8, 704_856_104)

    def test_original_ziatora_loses_all_results_when_its_target_leaves(self):
        from quorune.turn_step_owner import TURN_STEPS

        for index, mode in enumerate(('legal', 'leaves', 'negative')):
            with self.subTest(mode=mode):
                session = self.session(704_856_110 + index)
                source = self.add(session.engine, 'Ziatora, the Incinerator')
                price = self.add(session.engine, 'Grizzly Bears')
                target = self.add(session.engine, 'Generic Bound Body', seat='C')
                if mode == 'leaves':
                    bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
                if mode == 'negative':
                    shrink = self.add(session.engine, 'Hydrosurge', seat='B', zone='hand')
                self.add(session.engine, 'Island', seat='B')
                self.assert_closed(session, source)
                self.prepare(session)
                session.state.phase, session.state.step = 'postcombat_main', 'main'
                session.state.phase_index = TURN_STEPS.index(('postcombat_main', 'main'))
                self.checkpoint(session)
                if mode == 'negative':
                    self.cast(session, 'B', shrink, [price.ref])
                    self.assertIsNone(self.resolve(session))
                self.advance_to(session, 'semantic.choice')
                self.assertEqual(('ending', 'end_step'), (session.state.phase, session.state.step))
                parent = self._selected_price(session, source, price, illegal=source)
                self.assertEqual('semantic.target', session.state.pending_decision.kind)
                self.assertEqual(parent.ref, session.state.stack[-1].context['reflexive_parent'])
                paid_power = session.state.stack[-1].context['reflexive_paid_creature']['stats']['power']
                self.assertEqual(-3 if mode == 'negative' else 2, paid_power)
                self.choose(session, targets=[target.ref] if mode == 'leaves' else ['C'])
                if mode == 'leaves':
                    self.cast(session, 'B', bounce, [target.ref])
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(38 if mode == 'legal' else 40, session.state.players['C'].life)
                treasures = [card for card in session.state.cards.values() if card.zone == 'battlefield'
                             and card.controller == 'A' and card.is_token and card.printed_name == 'Treasure Token']
                self.assertEqual(0 if mode == 'leaves' else 3, len(treasures))
                self.assertEqual('graveyard', session.state.cards[price.object_id].zone)
                self.replay(session, load=True)

    def test_original_manticore_uses_paid_creature_power_after_source_leaves(self):
        session = self.session(704_856_107)
        source = self.add(session.engine, 'Heart-Piercer Manticore', zone='hand')
        price = self.add(session.engine, 'Grizzly Bears')
        growth = self.add(session.engine, 'Giant Growth', zone='hand')
        bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        self.add(session.engine, 'Island', seat='B')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'R': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', growth, [price.ref])
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', source)
        self.resolve_top(session)
        self.cast(session, 'B', bounce, [source.ref])
        self._selected_price(session, source, price)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.choose(session, targets=['D'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(35, session.state.players['D'].life)
        self.replay(session, load=True)
