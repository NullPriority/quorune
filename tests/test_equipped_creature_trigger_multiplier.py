from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


CARD = "Wizard's Staff"
RULE = "equipped_creature_source"


class EquippedCreatureTriggerMultiplierTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "equipped-creature-trigger-multiplier.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "equipped-creature-trigger-multiplier-cards",
            "attached-cast-restriction-cards", "activation-restriction-material-cards",
            "attached-destruction-set-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped creature trigger multiplier", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def equip(self, game, staff, card, cost, forbidden=None):
        self.priority(game, "A")
        source = game.state.cards[staff.object_id]
        ability, = [ability for ability in game.engine._activated_abilities(source) if ability.mana.get("GENERIC") == cost]
        action = f"activate:{source.ref}:{ability.ability_id}"
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action, {row["id"] for row in legal})
        if forbidden is not None:
            before = authoritative_state_hash(game.state)
            result = game.act("pilot:A", {"action_id": action, "targets": [forbidden.ref], "pay": "auto"})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": action, "targets": [card.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(card.object_id, game.state.cards[staff.object_id].attached_to)

    def ordered_triggers(self, game, card):
        pending = game.state.pending_decision
        if pending is not None and pending.kind == "trigger.order":
            rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
            result = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in rows]})
            self.assertTrue(result.ok, result.summary)
        return [item for item in game.state.stack if item.source_object_id == card.object_id]

    def test_actual_restricted_equip_grants_prowess_and_keeps_two_original_occurrences_after_departure(self):
        game = self.session(60320361)
        staff = self.add(game.engine, CARD, zone="hand")
        wizard = self.add(game.engine, "Fugitive Wizard")
        other = self.add(game.engine, "Doomed Traveler")
        shock = self.add(game.engine, "Shock", zone="hand")
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        hidden = self.add(game.engine, "Island", seat="D", zone="hand", ref="staff-private")
        for number in range(2):
            self.add(game.engine, "Plains", seat="B", ref=f"staff-removal-mana-{number}")
        self.assert_closed(game, staff)
        self.prepare(game, mana={"C": 5, "U": 1, "R": 1})
        self.checkpoint(game)
        self.cast(game, "A", staff)
        self.assertIsNone(self.resolve(game))
        self.equip(game, staff, wizard, 1, forbidden=other)
        self.cast(game, "A", shock, ["C"])
        triggers = self.ordered_triggers(game, wizard)
        self.assertEqual(2, len({item.stack_id for item in triggers}))
        self.assertEqual({"triggered_ability"}, {item.kind for item in triggers})
        self.assertEqual(0, self.trigger_count(game, other))

        game = self.replay(game, load=True)
        self.cast(game, "B", destroy, [staff.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[staff.object_id].zone)
        self.assertEqual({item.ref for item in triggers}, {item.ref for item in self.ordered_triggers(game, wizard)})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.engine._numeric_stat(wizard.object_id, "power"))
        self.assertEqual(38, game.state.players["C"].life)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_ordinary_equip_repeats_departure_trigger_from_last_known_attachment(self):
        game = self.session(60320362)
        staff = self.add(game.engine, CARD)
        traveler = self.add(game.engine, "Doomed Traveler")
        murderer = self.add(game.engine, "Murder", seat="B", zone="hand")
        for number in range(3):
            self.add(game.engine, "Swamp", seat="B", ref=f"staff-black-mana-{number}")
        self.assert_closed(game, staff)
        self.assert_closed(game, traveler)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        self.equip(game, staff, traveler, 3)
        self.cast(game, "B", murderer, [traveler.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[traveler.object_id].zone)
        self.assertIsNone(game.state.cards[staff.object_id].attached_to)
        triggers = self.ordered_triggers(game, traveler)
        self.assertEqual(2, len({item.ref for item in triggers}))
        self.assertEqual({"A"}, {item.controller for item in triggers})
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, sum(card.is_token and card.zone == "battlefield" and card.controller == "A"
                                for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_current_and_historical_wording_use_the_equipped_creature_not_another_ability_source(self):
        original = self.db.lookup(CARD, fuzzy=False)
        for text in (original.oracle_text, original.oracle_text.replace("a triggered ability", "an ability")):
            result = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry,
                                         capability_profile="commander_review")
            self.assertEqual("exact", result.status, result.material_residuals)
            rules = [handler["fragment"]["value"].get("rule") for face in result.faces for node in face.nodes
                     for handler in node.handlers if handler.get("fragment", {}).get("kind") == "trigger_multiplier"]
            self.assertEqual([RULE], rules)
        changed = replace(original, oracle_text=original.oracle_text.replace("ability of equipped creature",
                                                                            "ability of equipped enchantment"))
        self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                        capability_profile="commander_review").status)

    def test_omitting_the_equipped_participation_binding_is_killed(self):
        with patch("quorune.compiler.runtime_templates.static_trigger_multiplier_handler", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_current_and_historical_wording_use_the_equipped_creature_not_another_ability_source()
