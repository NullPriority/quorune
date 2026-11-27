"""Actual attached planeswalker, loyalty, combat, and replay witnesses."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.equipped_planeswalker import EQUIPPED_DAMAGE_COVERAGE, equipped_damage_condition
from quorune.rules.loyalty_attachment import LOYALTY_ATTACHMENT_MECHANIC
from scripts.build_test_database import build_fixture_database


NAME = "The Aetherspark"


class EquippedPlaneswalkerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-planeswalker.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/equipped-color-upkeep-counter-cards.json",
            ROOT / "tests/fixtures/equipped-planeswalker-cards.json",
            ROOT / "tests/fixtures/resolution-mana-choice-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Equipped planeswalker",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def activate(self, game, source, delta, targets=()):
        self.priority(game, "A")
        source = game.state.cards[source.object_id]
        ability, = [ability for ability in game.engine._activated_abilities(source) if ability.loyalty_delta == delta]
        self.assertFalse(ability.mana_ability)
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.assertIn(action_id, [offer["id"] for offer in offers])
        result = game.act("pilot:A", {"action_id": action_id, "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_strict_and_fail_closed_shapes(self):
        record = self.db.lookup(NAME)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        child, = [node for node in ir.faces[0].nodes if EQUIPPED_DAMAGE_COVERAGE in node.runtime_coverage]
        self.assertEqual(equipped_damage_condition(), child.event_condition)
        increase, = [node for node in ir.faces[0].nodes if LOYALTY_ATTACHMENT_MECHANIC in node.mechanics]
        self.assertEqual(1, increase.target_schema["up_to"])
        for before, after in (("during your turn", "during any turn"),
                              ("up to one target", "up to two target")):
            changed = replace(record, oracle_text=record.oracle_text.replace(before, after))
            candidate = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
            self.assertTrue(candidate.material_residuals)
        from quorune.compiler import equipped_planeswalker_nodes as compiler

        for producer in ("equipped_planeswalker_nodes", "loyalty_attachment_template"):
            with patch.object(compiler, producer, return_value=None):
                candidate = compile_oracle_card(
                    record, capability_registry=self.registry, capability_profile="commander_review",
                )
                self.assertTrue(candidate.material_residuals, producer)
        game = self.session(2511700)
        self.assert_closed(game, self.add(game.engine, NAME))

    def test_actual_loyalty_target_zero_and_source_departure(self):
        for mode in ("attach", "zero", "source_departed", "target_departed"):
            with self.subTest(mode=mode):
                game = self.session(2511710 + len(mode))
                source = self.add(game.engine, NAME, zone="hand")
                target = self.add(game.engine, "Grizzly Bears")
                removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.add(game.engine, "Plains", seat="B")
                self.add(game.engine, "Island", seat="B")
                self.add(game.engine, "Wastes", seat="B")
                self.prepare(game, mana={"C": 4})
                self.checkpoint(game)
                self.cast_for(game, "A", source)
                self.resolve_top(game)
                self.assertEqual(4, game.state.cards[source.object_id].counters["loyalty"])
                self.activate(game, source, 1, () if mode == "zero" else (target.ref,))
                self.assertEqual(5, game.state.cards[source.object_id].counters["loyalty"])
                self.assertFalse(game.state.cards[source.object_id].attached_to)
                if mode == "source_departed":
                    source = game.state.cards[source.object_id]
                    self.cast_for(game, "B", removal, [source.ref])
                elif mode == "target_departed":
                    self.cast_for(game, "B", bounce, [target.ref])
                self.assertIsNone(self.resolve(game))
                current_source = game.state.cards[source.object_id]
                current_target = game.state.cards[target.object_id]
                self.assertEqual(1 if mode in ("attach", "source_departed") else 0,
                                 current_target.counters.get("+1/+1", 0))
                self.assertEqual(target.object_id if mode == "attach" else None, current_source.attached_to)
                self.replay(game, load=True)

    def test_actual_combat_adds_loyalty_and_excludes_new_attacks(self):
        game = self.session(2511720)
        source = self.add(game.engine, NAME)
        body = self.add(game.engine, "Grizzly Bears")
        blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="second-creature")
        body.acquired_control_turn_count = -1
        blocker.acquired_control_turn_count = -1
        source.counters["loyalty"] = 4
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, source, 1, (body.ref,))
        self.resolve_top(game)
        self.attack(game, [(body, "C")])
        self.until(game, lambda session: self.trigger_count(session, source) == 1)
        self.resolve_top(game)
        self.assertEqual(8, game.state.cards[source.object_id].counters["loyalty"])
        self.until(game, lambda session: (
            session.state.active_player == "B" and session.state.pending_decision.kind == "combat.attackers"
        ))
        ctx = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertNotIn(source.ref, ctx["defenders"])
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {blocker.ref: source.ref}})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.replay(game, load=True)

    def test_attachment_during_combat_preserves_the_declaration(self):
        game = self.session(2511730)
        source = self.add(game.engine, NAME, seat="B")
        attacker = self.add(game.engine, "Grizzly Bears")
        recipient = self.add(game.engine, "Grizzly Bears", seat="B", ref="equipment-creature")
        spell = self.add(game.engine, "Magnetic Theft", zone="hand")
        self.add(game.engine, "Mountain")
        attacker.acquired_control_turn_count = -1
        source.counters["loyalty"] = 4
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(attacker, source.ref)])
        self.cast_for(game, "A", spell, [source.ref, recipient.ref])
        self.resolve_top(game)
        self.assertEqual(recipient.object_id, game.state.cards[source.object_id].attached_to)
        self.assertEqual(source.ref, game.state.combat.attackers[attacker.object_id])
        self.replay(game, load=True)

    def test_attached_creature_deals_no_loyalty_damage_on_its_controllers_turn(self):
        game = self.session(2511735)
        source = self.add(game.engine, NAME)
        attacker = self.add(game.engine, "Grizzly Bears", seat="B")
        attacker.acquired_control_turn_count = -1
        source.counters["loyalty"] = 4
        attach_objects(game.state.cards, source, attacker,
                       source_timestamp=source.zone_timestamp, players=game.state.players)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda session: (
            session.state.active_player == "B" and session.state.pending_decision.kind == "combat.attackers"
        ))
        before = game.state.players["C"].life
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker.ref: "C"}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda session: session.state.players["C"].life < before)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual(4, game.state.cards[source.object_id].counters["loyalty"])
        self.replay(game, load=True)

    def test_actual_ultimate_is_stack_mana_and_replays_pending_color(self):
        game = self.session(2511740)
        source = self.add(game.engine, NAME)
        source.counters["loyalty"] = 10
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, source, -10)
        self.assertTrue(game.state.stack)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.until(game, lambda session: session.state.pending_decision.kind == "semantic.choice")
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(("choose_mana", "one_color", 10), (
            context["operation"], context["selection"], context["amount"],
        ))
        for actor, choice in (("D", "U"), ("A", "C")):
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act(f"pilot:{actor}", {"action_id": "choose", "choice": choice}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "U"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(10, game.state.players["A"].mana_pool["U"])
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)
