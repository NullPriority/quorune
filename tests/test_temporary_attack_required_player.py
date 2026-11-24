"""Native evidence for a temporary requirement to attack a specific player."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.declaration_fragments import DeclarationRequirementTemplate
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.temporary_attack_requirement_model import TemporaryAttackRequirementSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Alluring Siren"


class TemporaryAttackRequiredPlayerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "temporary-attack-required-player.sqlite3"
        fixture = ROOT / "tests/fixtures/temporary-attack-required-player-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary specific defender", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        ability, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "activate"]
        return card, ability

    def test_complete_original_pins_one_opposing_creature_and_a_specific_defending_player(self):
        card, program = self.program()
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNotNone(program.cost_schema)
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
                          "count": 1, "controller_relation": "opponent"}, program.target_schema)
        effect, = program.effects
        self.assertEqual(("grant_declaration_restriction_until_end_of_turn", "$target.0"),
                         (effect["op"], effect["card"]))
        self.assertEqual(TemporaryAttackRequirementSpec(schema_version=4),
                         TemporaryAttackRequirementSpec.from_dict(effect["restriction"]))
        for text in (
            card.oracle_text.replace("attacks you", "attacks a planeswalker you control"),
            card.oracle_text.replace("an opponent controls", "you control"),
            card.oracle_text.replace("this turn", "next turn"),
            card.oracle_text + " Rewrite history.",
        ):
            ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, text)
        combined = compile_oracle_card(replace(card, oracle_text=card.oracle_text + " Draw a card."),
                                       capability_registry=self.registry)
        if combined.status == "exact":
            self.assertIn("draw", {effect["op"] for face in combined.faces for node in face.nodes
                                   for effect in node.effects})

    def test_directed_requirement_codec_keeps_ordinary_history_and_rejects_unbound_defenders(self):
        original = TemporaryAttackRequirementSpec().to_requirement().to_dict()
        self.assertNotIn("schema_version", original)
        self.assertNotIn("defender", original)
        self.assertEqual(original, DeclarationRequirementTemplate.from_dict(original).to_dict())
        directed = TemporaryAttackRequirementSpec(schema_version=4).to_requirement(defender="A")
        encoded = directed.to_dict()
        self.assertEqual((3, "A"), (encoded["schema_version"], encoded["defender"]))
        self.assertEqual(directed, DeclarationRequirementTemplate.from_dict(encoded))
        for changed in ({**encoded, "defender": None}, {**encoded, "defender": "$controller"},
                        {**encoded, "schema_version": 2}, {**encoded, "scope": "attached"}):
            with self.assertRaises(ValueError):
                DeclarationRequirementTemplate.from_dict(changed)

    def activate(self, game, source, target, *, denied_target=None):
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + source.ref + ":")]
        if denied_target is not None:
            original = authoritative_state_hash(game.state)
            denied = game.act("pilot:A", {"action_id": action, "targets": [denied_target.ref], "pay": "auto"})
            self.assertFalse(denied.ok)
            self.assertEqual(original, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": action, "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)

    def ready_attackers(self, game):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.assertEqual(["B"], game.state.pending_decision.actors)

    def test_actual_directed_requirement_survives_its_source_and_excludes_other_defenders(self):
        game = self.session(50811910)
        siren = self.add(game.engine, ORIGINAL)
        bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="siren-opponent-bear")
        own = self.add(game.engine, "Grizzly Bears", ref="siren-own-bear")
        walker = self.add(game.engine, "Jace Beleren")
        walker.counters["loyalty"] = 3
        murder = self.add(game.engine, "Murder", seat="C", zone="hand")
        for creature in (siren, bear, own):
            creature.acquired_control_turn_count = -1
        for card in (siren, bear, own, walker, murder):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        game.state.players["C"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(game)
        self.activate(game, siren, bear, denied_target=own)
        self.cast_for(game, "C", murder, [siren.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[siren.object_id].zone)
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.ready_attackers(game)
        for attackers in ({}, {bear.ref: "C"}, {bear.ref: walker.ref}):
            before = authoritative_state_hash(game.state)
            rejected = game.act("pilot:B", {"action_id": "attack", "attackers": attackers})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", {"action_id": "attack", "attackers": {bear.ref: "A"}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("A", game.state.combat.attackers[bear.object_id])
        self.replay(game, load=True)

    def test_actual_tax_and_new_target_incarnation_leave_ordinary_declarations_available(self):
        for mode in ("tax", "blink"):
            with self.subTest(mode=mode):
                game = self.session(50811920 + len(mode))
                siren = self.add(game.engine, ORIGINAL)
                bear = self.add(game.engine, "Grizzly Bears", seat="B")
                siren.acquired_control_turn_count = -1
                bear.acquired_control_turn_count = -1
                self.assert_closed(game, siren)
                response = None
                if mode == "tax":
                    tax = self.add(game.engine, "Propaganda")
                    self.assert_closed(game, tax)
                    for index in range(2):
                        self.add(game.engine, "Plains", seat="B", ref=f"siren-tax-funding-{index}")
                else:
                    self.add(game.engine, "Fervor", seat="B")
                    response = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
                    self.add(game.engine, "Plains", seat="B")
                    self.assert_closed(game, response)
                self.prepare(game, actor="B", active="B")
                self.checkpoint(game)
                original = bear.logical_object_id
                self.activate(game, siren, bear)
                self.resolve_top(game)
                game = self.replay(game, load=True)
                if response is not None:
                    self.cast_for(game, "B", response, [bear.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(original, game.state.cards[bear.object_id].logical_object_id)
                self.ready_attackers(game)
                attackers = {} if mode == "tax" else {bear.ref: "C"}
                result = game.act("pilot:B", {"action_id": "attack", "attackers": attackers})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(None if mode == "tax" else "C", game.state.combat.attackers.get(bear.object_id))
                self.replay(game, load=True)
