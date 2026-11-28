"""Damage tripling follows the actual source and canonical recipient result."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


CARD = "Fiery Emancipation"
CITY = "City on Fire"
CAPABILITY = "damage.replacement.static_quantity"
PROFILE = "commander_review"


class ControlledSourceDamageTriplingTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-source-damage-tripling.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "announced-target-creature-control-cards.json",
            fixtures / "combat-damage-dealer-recipient-control-cards.json",
            fixtures / "optional-draw-attack-restriction-cards.json",
            fixtures / "announced-x-public-damage-set-cards.json",
            fixtures / "controlled-source-damage-tripling-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled-source damage tripling", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def anchor(self, game, *, name=CARD):
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, name)
        self.assert_closed(game, source)
        return source

    def test_actual_sources_triple_against_all_four_damage_recipient_kinds(self):
        game = self.session(12004031)
        self.anchor(game)
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        creature.counters["+1/+1"] = 10
        walker = self.add(game.engine, "Generic Protected Walker", seat="C")
        walker.counters["loyalty"] = 12
        battle = self.add(game.engine, "Generic Protected Battle", seat="B")
        battle.battle_protector = "C"
        battle.counters["defense"] = 12
        targets = ("D", creature.ref, walker.ref, battle.ref)
        bolts = [self.add(game.engine, "Lightning Bolt", zone="hand", ref=f"triple-bolt-{index}")
                 for index in range(4)]
        foreign = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="foreign-unmodified-bolt")
        self.prepare(game, mana={"R": 4})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        for bolt, target in zip(bolts, targets, strict=True):
            self.cast(game, "A", bolt, [target])
            self.assertIsNone(self.resolve(game))
        self.cast(game, "B", foreign, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((37, 31), tuple(game.state.players[seat].life for seat in ("A", "D")))
        self.assertEqual(9, game.state.cards[creature.object_id].marked_damage)
        self.assertEqual(3, game.state.cards[walker.object_id].counters["loyalty"])
        self.assertEqual(3, game.state.cards[battle.object_id].counters["defense"])
        self.replay(game, load=True)

    def test_live_and_departed_damage_sources_use_their_actual_controller(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(12004032 + departed)
                self.anchor(game)
                creature = self.add(game.engine, "Prodigal Sorcerer", seat="B")
                creature.acquired_control_turn_count = -1
                theft = self.add(game.engine, "Act of Aggression", zone="hand")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.prepare(game, actor="B", active="B", mana={"U": 1})
                game.state.players["A"].mana_pool.update({"C": 3, "R": 2})
                self.checkpoint(game)

                actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                offer, = (action for action in actions if action["id"].startswith(f"activate:{creature.ref}:"))
                result = game.act("pilot:B", {"action_id": offer["id"], "targets": ["D"], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                ability, = game.state.stack
                self.priority(game, "A")
                observer = self.action(game, "A", theft)
                mana_only, = (option for option in observer["cost_options"] if not option.get("additional_life"))
                result = game.act("pilot:A", {"action_id": observer["id"], "cost_option": mana_only["id"],
                                             "targets": [creature.ref], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.resolve_top(game)
                self.assertEqual("A", game.state.cards[creature.object_id].controller)
                self.assertEqual("B", ability.controller)
                if departed:
                    self.cast(game, "B", bounce, [creature.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[creature.object_id].zone)
                    self.assertIn(creature.object_id, game.state.players["B"].zones["hand"])
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(37, game.state.players["D"].life)
                self.replay(game, load=True)

    def test_affected_player_orders_tripling_and_prevention_through_replay(self):
        for multiply_first in (False, True):
            with self.subTest(multiply_first=multiply_first):
                game = self.session(12004034 + multiply_first)
                anchor = self.anchor(game)
                shield = self.add(game.engine, "Mending Hands", seat="B", zone="hand")
                bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
                self.prepare(game, mana={"R": 1})
                game.state.players["B"].mana_pool["W"] = 1
                self.checkpoint(game)
                self.cast(game, "A", bolt, ["B"])
                self.cast(game, "B", shield, ["B"])
                self.resolve_top(game)
                pending = self.resolve(game)
                self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
                self.assertEqual(40, game.state.players["B"].life)
                game = self.replay(game, load=True)
                options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                self.assertEqual(2, len(options))
                chosen, = (row["id"] for row in options
                           if row["label"].startswith(anchor.ref + ":") == multiply_first)
                command = {"action_id": "choose", "replacement": chosen}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:A", command).ok)
                self.assertFalse(game.act("pilot:B", {**command, "replacement": "forged"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                result = game.act("pilot:B", command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(35 if multiply_first else 40, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_city_tripler_preserves_the_announced_unmodified_division(self):
        game = self.session(12004036)
        self.anchor(game, name=CITY)
        spell = self.add(game.engine, "Chandra's Pyrohelix", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        creature.counters["+1/+1"] = 3
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        offer = self.action(game, "A", spell)
        command = {"action_id": offer["id"], "targets": [creature.ref, "D"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        malformed = {**command, "damage_allocation": {creature.ref: 3, "D": 3}}
        self.assertFalse(game.act("pilot:A", malformed).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {**command, "damage_allocation": {creature.ref: 1, "D": 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.cards[creature.object_id].marked_damage)
        self.assertEqual(37, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_legal_shape_binding_and_owner_withdrawal_are_independent(self):
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        for name in (CARD, CITY):
            with self.subTest(card=name):
                record = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(record, **options)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = (node for node in ir.faces[0].nodes if node.event == "damage")
                handler, = node.handlers
                self.assertIn(CAPABILITY, node.capability_dependencies)
                self.assertEqual({"multiplier": 3, "additional": 0}, handler["modification"])
                self.assertEqual("source_controller", handler["condition"]["source_controller_relation"])
                self.assertEqual([], handler["condition"]["target_types_all"])
                self.assertIsNone(handler["condition"]["combat"])
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (p for p in semantics.programs_for_oracle(record.oracle_id) if p.event == "damage")
                deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
                closure = self.registry.closure(deps, profile=PROFILE).to_dict() if deps else None
                absent = replace(program, capability_dependencies=deps, capability_closure=closure)
                result = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"])
        original = self.db.lookup(CARD, fuzzy=False)
        for old, new in (("a source you control", "a source an opponent controls"),
                         ("deal damage", "deal combat damage"), ("a permanent or player", "a player")):
            modified = replace(original, oracle_text=original.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(modified, **options).status)
        with patch("quorune.compiler.runtime_templates.static_damage_handler", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(original, **options).status)
