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
