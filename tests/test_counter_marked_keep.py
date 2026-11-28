"""Counter-marked retention, ordered sacrifice, and the exact vow duration."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.counter_bound_attack_model import COUNTER_BOUND_ATTACK_CAPABILITY, ResolvedCounterBoundAttackRestriction
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.counter_marked_keep import KEEP_COUNTER_CAPABILITY, KEEP_COUNTER_FIELD
from scripts.build_test_database import build_fixture_database


CARD = "Promise of Loyalty"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class CounterMarkedKeepTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "counter-marked-keep.sqlite3"
        build_fixture_database([FIXTURES / name for name in (
            "counter-marked-keep-cards.json", "counter-prohibition-cards.json",
            "chosen-counter-removal-cards.json", "chosen-counter-movement-cards.json",
            "activation-result-views-cards.json", "bound-effect-program-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Counter-marked keep", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_binding_and_closed_untargeted_boundaries(self):
        card = self.db.lookup(CARD, fuzzy=False)
        synthetic = replace(query_record(card.oracle_text), type_line="Sorcery")
        ir = compile_oracle_card(synthetic, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, synthetic.oracle_text[node.span.start:node.span.end])
        self.assertIsNone(node.target_schema)
        self.assertIn(KEEP_COUNTER_CAPABILITY, node.capability_dependencies)
        self.assertIn(COUNTER_BOUND_ATTACK_CAPABILITY, node.capability_dependencies)
        for original, changed in (
            ("Each player", "Each opponent"), ("a creature", "target creature"),
            ("or planeswalkers you control ", ""), ("vow counter on it", "charge counter on it"),
        ):
            with self.subTest(changed=changed):
                mutated = replace(synthetic, oracle_text=synthetic.oracle_text.replace(original, changed, 1))
                self.assertNotEqual("exact", compile_oracle_card(
                    mutated, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        for capability in (KEEP_COUNTER_CAPABILITY, COUNTER_BOUND_ATTACK_CAPABILITY):
            deps = [key for key in program.capability_dependencies if key != capability]
            changed = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        malformed = replace(program, effects=[{**program.effects[0], KEEP_COUNTER_FIELD: True}])
        result = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)

    def resolve_cast(self, game, source, *, mana=None):
        self.assert_closed(game, source)
        self.prepare(game, mana=mana or {"C": 4, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        return self.resolve(game)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose(self, game, seat, reference):
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [reference]})
        self.assertTrue(result.ok, result.summary)

    def card(self, game, card):
        return game.state.cards[card.object_id]

    def controls(self, game, card):
        return [effect for effect in game.state.continuous_effects
                if isinstance(effect, ResolvedCounterBoundAttackRestriction) and effect.applies(self.card(game, card))]

    def make_creature(self, game, seat, label):
        card = self.add(game.engine, "Grizzly Bears", seat=seat, ref="retention-" + label.replace(" ", "-"))
        card.acquired_control_turn_count = -1
        return card

    def test_public_apnap_and_physical_owner_order_commit_all_creatures(self):
        game = self.session(101404503)
        source = self.add(game.engine, CARD, zone="hand")
        a, b, c, d = [self.make_creature(game, seat, seat.casefold()) for seat in "ABCD"]
        extras = [self.make_creature(game, seat, label) for seat, label in (
            ("B", "first loss"), ("B", "second loss"), ("C", "third loss"),
        )]
        walker = self.add(game.engine, "Jace Beleren")
        walker.counters["loyalty"] = 3
        private = self.add(game.engine, "Solemnity", seat="D", zone="hand", ref="retention-private")
        pending = self.resolve_cast(game, source)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual({b.ref, extras[0].ref, extras[1].ref}, set(decision["options"]))
        self.assertEqual({"A": [a.ref]}, decision["prior_public_choices"])
        self.reject(game, "C", {"action_id": "choose", "cards": [c.ref]})
        self.reject(game, "B", {"action_id": "choose", "cards": []})
        self.choose(game, "B", b.ref)
        game = self.replay(game, load=True)
        decision = game.packet("pilot:C", full=True)["decision"]["ctx"]
        self.assertEqual({"A": [a.ref], "B": [b.ref]}, decision["prior_public_choices"])
        self.assertTrue(all(not self.card(game, card).counters for card in (a, b, c, d)))
        self.choose(game, "C", c.ref)
        self.assertEqual("permanent.composition_order", game.state.pending_decision.kind)
        self.assertEqual(["pilot:B"], game.pending_principals())
        self.assertTrue(all(self.card(game, card).zone == "battlefield" for card in (*extras, a, b, c, d)))
        game = self.replay(game, load=True)
        decision = game.packet("pilot:B", full=True)["decision"]
        self.assertNotIn(private.ref, json.dumps(decision))
        order = {"action_id": "order", "cards": [extras[1].ref, extras[0].ref]}
        self.reject(game, "C", order)
        self.reject(game, "B", {**order, "cards": [extras[1].ref]})
        result = game.act("pilot:B", order)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([extras[1].object_id, extras[0].object_id], game.state.players["B"].zones["graveyard"])
        self.assertTrue(all(self.card(game, card).zone == "graveyard" for card in extras))
        for card in (a, b, c, d):
            self.assertEqual({"vow": 1}, self.card(game, card).counters)
            self.assertEqual(1, len(self.controls(game, card)))
        self.replay(game, load=True)

        # This replay segment starts at B's genuine declaration window.
        self.prepare(game, active="B", actor="B")
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        constraints = game.packet("pilot:B", full=True)["decision"]["ctx"]["declaration_constraints"]
        self.assertEqual({"C", "D"}, set(constraints["domains"][b.ref]))
        command = {"action_id": "attack", "attackers": {b.ref: "A"}}
        self.reject(game, "B", command)
        self.reject(game, "B", {"action_id": "attack", "attackers": {b.ref: walker.ref}})
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {b.ref: "C"}})
        self.assertTrue(result.ok, result.summary)
        self.replay(game, load=True)


    def test_flicker_and_phasing_expire_the_marked_incarnation(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.phasing import phase_out_permanent

        game = self.session(101404505)
        source = self.add(game.engine, CARD, zone="hand")
        flickered = self.make_creature(game, "B", "flickered")
        phased = self.make_creature(game, "C", "phased")
        blink = self.add(game.engine, "Ephemerate", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        for card in (source, blink):
            self.assert_closed(game, card)
        self.assertIsNone(self.resolve_cast(game, source))
        original = self.card(game, flickered).logical_object_id
        self.assertEqual(1, len(self.controls(game, flickered)))
        self.cast(game, "B", blink, [flickered.ref])
        self.assertIsNone(self.resolve(game))
        current = self.card(game, flickered)
        self.assertNotEqual(original, current.logical_object_id)
        self.assertEqual(("battlefield", {}), (current.zone, current.counters))
        self.assertEqual([], self.controls(game, flickered))
        self.assertFalse(any(isinstance(effect, ResolvedCounterBoundAttackRestriction)
                            and effect.subject.logical_object_id == original for effect in game.state.continuous_effects))
        game = self.replay(game, load=True)

        current = self.card(game, phased)
        self.assertEqual(1, len(self.controls(game, phased)))
        original = current.logical_object_id
        identity = ContinuousObjectIdentity(current.object_id, original)
        phase_out_permanent(game.engine, identity, actor="C", reason="Phase the marked permanent.")
        self.assertTrue(current.phased_out)
        self.assertEqual((original, {"vow": 1}), (current.logical_object_id, current.counters))
        self.assertFalse(any(isinstance(effect, ResolvedCounterBoundAttackRestriction)
                            and effect.subject == identity for effect in game.state.continuous_effects))
        self.checkpoint(game)
        self.replay(game, load=True)

    def test_counter_prevention_and_existing_counter_start_then_removal_never_restarts(self):
        game = self.session(101404504)
        source = self.add(game.engine, CARD, zone="hand")
        solemnity = self.add(game.engine, "Solemnity")
        self.assert_closed(game, solemnity)
        plain = self.make_creature(game, "B", "without counter")
        old = self.make_creature(game, "C", "existing counter")
        old.counters["vow"] = 1
        defeated = self.make_creature(game, "B", "still sacrificed")
        hexmage = self.add(game.engine, "Vampire Hexmage")
        release = self.add(game.engine, "Disenchant", zone="hand")
        ground = self.add(game.engine, "Nesting Grounds")
        donor = self.add(game.engine, "Plains", ref="retention-existing-donor")
        donor.counters["vow"] = 1
        self.add(game.engine, "Plains", ref="retention-other-white")
        self.add(game.engine, "Plains", ref="retention-extra-mana")
        for card in (hexmage, release, ground):
            self.assert_closed(game, card)
        pending = self.resolve_cast(game, source, mana={"C": 5, "W": 2})
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        self.choose(game, "B", plain.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.card(game, defeated).zone)
        self.assertEqual({}, self.card(game, plain).counters)
        self.assertEqual([], self.controls(game, plain))
        self.assertEqual({"vow": 1}, self.card(game, old).counters)
        self.assertEqual(1, len(self.controls(game, old)))
        self.cast(game, "A", release, [solemnity.ref])
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation = next(row for row in offers if row["id"].startswith("activate:" + hexmage.ref + ":"))
        result = game.act("pilot:A", {"action_id": activation["id"], "targets": [old.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({}, self.card(game, old).counters)
        self.assertEqual([], self.controls(game, old))
        self.priority(game, "A")
        result = game.act("pilot:A", {"action_id": "activate:" + ground.ref + ":ab2",
                          "targets": [donor.ref, old.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "counter_kind": "vow"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"vow": 1}, self.card(game, old).counters)
        self.assertEqual([], self.controls(game, old))
        self.replay(game, load=True)
