from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.scalar_effect_amounts import scalar_effect_amount_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import _effect_template
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_compiled_reviewed_trigger_consumers as native


class PublicCountResultCompilerTests(unittest.TestCase):
    def compile(self, text, *, source_event=True, compile_fixed=None):
        return scalar_effect_amount_template(
            text, source_name="Fixture", source_event=source_event,
            compile_fixed=compile_fixed or (lambda body: _effect_template(body, card_name="Fixture")),
        )

    def test_both_damage_word_orders_share_the_existing_public_count_owner(self):
        from quorune.compiler.public_query_effect_amounts import public_query_effect_amount_template

        for text in (
            "It deals damage equal to the number of creatures you control to any target.",
            "It deals damage to any target equal to the number of creatures you control.",
        ):
            with self.subTest(text=text):
                compiled = self.compile(text)
                self.assertIsNotNone(compiled)
                self.assertIn("public-query-effect-amount", compiled[3])
                self.assertEqual("damage", compiled[1][0]["op"])
                self.assertEqual("$source", compiled[1][0]["source"])
                self.assertEqual(1, compiled[2]["count"])
                direct = public_query_effect_amount_template(
                    text.replace("It", "Fixture", 1), source_name="Fixture",
                    compile_fixed=lambda body: _effect_template(body, card_name="Fixture"),
                )
                self.assertIsNotNone(direct)
                self.assertEqual(compiled[1:], direct[1:])
        for text in (
            "Fixture deals damage any target equal to the number of creatures you control.",
            "Fixture deals damage equal to the number of creatures you control to to any target.",
        ):
            self.assertIsNone(public_query_effect_amount_template(
                text, source_name="Fixture", compile_fixed=lambda body: _effect_template(body, card_name="Fixture"),
            ))
        source = self.compile("It deals damage equal to its power to any target.")
        self.assertNotIn("public-query-effect-amount", source[3])

    def test_unknown_counts_unbound_pronouns_and_changed_target_shapes_stay_residual(self):
        self.assertIsNone(self.compile(
            "It deals damage equal to the number of creatures you control to any target.", source_event=False,
        ))
        for text in (
            "It deals damage equal to the number of secret cards named Fixture to any target.",
            "It deals damage equal to twice the number of secret cards named Fixture to any target.",
            "It deals damage equal to the number of creatures you control to any target. Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.compile(text))

        def changed_target_shape(text):
            amount = 2 if "2 damage" in text else 3
            return ("fixture", ({"op": "damage", "source": "$source", "target": "$target.0", "amount": amount},),
                    {"zones": ["player"], "count": amount}, ("cr-120-damage", "cr-115-targets"))
        self.assertIsNone(self.compile(
            "Fixture deals damage equal to the number of creatures you control to target player.",
            compile_fixed=changed_target_shape,
        ))


class PublicCountResultRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.CompiledReviewedTriggerConsumerTests.session
    add = native.CompiledReviewedTriggerConsumerTests.add
    ready = native.CompiledReviewedTriggerConsumerTests.ready
    prepare = native.CompiledReviewedTriggerConsumerTests.prepare
    checkpoint = native.CompiledReviewedTriggerConsumerTests.checkpoint
    priority = native.CompiledReviewedTriggerConsumerTests.priority
    actions = native.CompiledReviewedTriggerConsumerTests.actions
    binding = native.CompiledReviewedTriggerConsumerTests.binding
    reject = native.CompiledReviewedTriggerConsumerTests.reject
    settle = native.CompiledReviewedTriggerConsumerTests.settle
    replay = native.CompiledReviewedTriggerConsumerTests.replay
    cast = native.CompiledReviewedTriggerConsumerTests.cast
    choose = native.CompiledReviewedTriggerConsumerTests.choose
    advance = native.CompiledReviewedTriggerConsumerTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "public-count-results.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/public-count-result-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public count witnesses", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_retained_optional_counts_keep_complete_normal_bindings(self):
        session = self.session(967_030)
        for name in ("Murasa Pyromancer", "Hagra Diabolist", "Tajuru Archer", "Gempalm Polluter", "Ondu Cleric"):
            with self.subTest(card=name):
                source = self.add(session.engine, name, zone="hand")
                bound = self.binding(session, source)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])

    def test_source_departure_changes_the_live_count_without_erasing_its_trigger(self):
        for leave in (False, True):
            with self.subTest(source_leaves=leave):
                session = self.session(967_000 + int(leave))
                source = self.add(session.engine, "Foundry Champion", zone="hand")
                self.add(session.engine, "Grizzly Bears")
                for index in range(2):
                    self.add(session.engine, "Grizzly Bears", seat="B", ref=f"opponent-body-{index}")
                bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
                session.state.players["B"].mana_pool["U"] = 1
                self.ready(session, {"C": 20, "R": 20, "W": 20})
                self.cast(session, source)
                self.assertEqual("semantic.target", self.settle(session).kind)
                session = self.replay(session, load=True)
                self.choose(session, targets=["B"])
                if leave:
                    self.cast(session, bounce, [source.ref], seat="B")
                self.assertIsNone(self.settle(session))
                self.assertEqual(39 if leave else 38, session.state.players["B"].life)
                self.assertEqual("hand" if leave else "battlefield", session.state.cards[source.object_id].zone)
                self.replay(session, load=True)

    def test_basic_land_count_keeps_target_announcement_even_when_zero(self):
        for mountains in (0, 2):
            with self.subTest(mountains=mountains):
                session = self.session(967_010 + mountains)
                source = self.add(session.engine, "Fire Dragon", zone="hand")
                body = self.add(session.engine, "Generic Bound Body", seat="B")
                for index in range(mountains):
                    self.add(session.engine, "Mountain", ref=f"own-mountain-{index}")
                self.add(session.engine, "Mountain", seat="B", ref="foreign-mountain")
                self.add(session.engine, "Forest")
                self.ready(session, {"C": 20, "R": 20})
                self.cast(session, source)
                self.assertEqual("semantic.target", self.settle(session).kind)
                self.reject(session, {"action_id": "choose", "targets": []})
                self.choose(session, targets=[body.ref])
                self.assertIsNone(self.settle(session))
                self.assertEqual(mountains, session.state.cards[body.object_id].marked_damage)
                self.replay(session, load=True)

    def test_attack_count_reads_its_controllers_equipment_at_resolution_for_all_opponents(self):
        session = self.session(967_020)
        source = self.add(session.engine, "Eivor, Battle-Ready", zone="hand")
        removed = self.add(session.engine, "Skullclamp")
        self.add(session.engine, "Lightning Greaves")
        self.add(session.engine, "Skullclamp", seat="B", ref="foreign-equipment")
        removal = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        lands = [self.add(session.engine, "Generic Bound Plains", seat="B", ref=f"response-land-{index}")
                 for index in range(2)]
        self.ready(session, {"C": 20, "R": 20, "W": 20})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.advance(session, lambda s: getattr(s.state.pending_decision, "kind", None) == "combat.attackers")
        command = {"action_id": "attack", "atk": {source.ref: "B"}}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.priority(session, "B")
        for land in lands:
            row = next(row for row in self.actions(session, "B") if row["id"].startswith("activate:" + land.ref + ":"))
            result = session.act("pilot:B", {"action_id": row["id"], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
        self.cast(session, removal, [removed.ref], seat="B")
        self.settle(session)
        self.assertEqual([40, 39, 39, 39], [session.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("graveyard", session.state.cards[removed.object_id].zone)
        self.replay(session, load=True)
