from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import ProtectionSpec, ability_fragment_from_dict
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.attached_commander_protection import (
    COMMANDER_COMPLEMENT_PROTECTION_CAPABILITY, AttachedCommanderProtectionHandler,
    commander_complement_protection_descriptor,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


class CommanderComplementProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "commander-complement-protection.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "commander-complement-protection-cards",
            "granted-static-characteristic-cards", "activation-public-state-cards", "attached-cast-restriction-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        partners = ["Rograkh, Son of Rohgahh", "Prava of the Steel Legion"]
        cls.partner_deck = DeckDefinition("Two colors", [
            *(DeckEntry(name, 1, "commander") for name in partners), DeckEntry("Plains", 30),
        ], partners)
        cls.blue_deck = DeckDefinition("Blue color", [
            DeckEntry("Kitsa, Otterball Elite", 1, "commander"), DeckEntry("Island", 30),
        ], ["Kitsa, Otterball Elite"])

    def protections(self, game, card):
        values = game.engine._effective_card_data(game.state.cards[card.object_id])["ability_fragments"]
        return {spec.quality for row in values
                if isinstance(spec := ability_fragment_from_dict(row), ProtectionSpec)}

    def action_named(self, game, seat, action_id):
        self.priority(game, seat)
        view = game.packet(f"pilot:{seat}", full=True)
        return next(row for row in view["decision"]["ctx"]["legal"]["actions"] if row["id"] == action_id)

    def equip(self, game, plate, target, *, ability, generic, rejected=()):
        offer = self.action_named(game, "A", f"activate:{plate.ref}:ab{ability}")
        self.assertIn(target.ref, offer["target_schema"]["legal_refs"])
        command = {"action_id": offer["id"], "targets": [target.ref], "pay": "manual", "payment": {"C": generic}}
        before = authoritative_state_hash(game.state)
        for card in rejected:
            self.assertNotIn(card.ref, offer["target_schema"]["legal_refs"])
            failure = game.act("pilot:A", {**command, "targets": [card.ref]})
            self.assertFalse(failure.ok, failure.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)

    def make_scene(self, seed):
        game = make_session(self.db, self.partner_deck, self.blue_deck, seed=seed)
        keep_all(game)
        plate = self.add(game.engine, "Commander's Plate")
        bearer = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Fugitive Wizard", seat="B")
        steal = self.add(game.engine, "Steal Artifact", seat="B", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        private = self.add(game.engine, "Disenchant", seat="D", zone="hand", ref="concealed-plate-response")
        for index in range(5):
            self.add(game.engine, "Island", seat="B", ref=f"plate-blue-land-{index}")
        self.add(game.engine, "Mountain", seat="B", ref="plate-red-land")
        for card in (plate, bearer, steal, bounce, shock):
            self.assert_closed(game, card)
        rograkh = next(card for card in game.state.cards.values()
                       if card.is_commander and card.owner == "A" and card.printed_name.startswith("Rograkh"))
        self.prepare(game, mana={"C": 8})
        game.state.players["B"].mana_pool.update({"U": 5, "R": 1})
        self.prepare(game, mana={"C": 8})
        self.checkpoint(game)
        return game, plate, bearer, rograkh, foreign, steal, bounce, shock, private

    def test_actual_equip_prices_partner_union_and_changing_source_control_replay(self):
        game, plate, bearer, rograkh, foreign, steal, bounce, shock, private = self.make_scene(61170216)
        self.cast(game, "A", rograkh)
        self.resolve_top(game)
        rograkh = game.state.cards[rograkh.object_id]
        self.assertEqual([], list(self.protections(game, bearer)))
        self.equip(game, plate, rograkh, ability=2, generic=3, rejected=(bearer, foreign))
        self.assertEqual({"U", "B", "G"}, self.protections(game, rograkh))
        self.assertEqual(3, game.engine._numeric_stat(rograkh.object_id, "power"))
        self.equip(game, plate, bearer, ability=3, generic=5, rejected=(foreign,))
        self.assertEqual(set(), self.protections(game, rograkh))
        self.assertEqual(5, game.engine._numeric_stat(bearer.object_id, "power"))
        self.assertEqual({"U", "B", "G"}, self.protections(game, bearer))
        blue = self.action_named(game, "B", f"cast:{bounce.ref}")
        self.assertNotIn(bearer.ref, blue["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        failure = game.act("pilot:B", {"action_id": blue["id"], "targets": [bearer.ref], "pay": "auto"})
        self.assertFalse(failure.ok, failure.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        red = self.action_named(game, "B", f"cast:{shock.ref}")
        self.assertIn(bearer.ref, red["target_schema"]["legal_refs"])
        self.until(game, lambda s: s.state.active_player == "B"
                   and s.state.phase == "precombat_main" and s.state.priority_player == "B")
        self.cast(game, "B", steal, [plate.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertEqual("B", game.state.cards[plate.object_id].controller)
        self.assertEqual("A", game.state.cards[bearer.object_id].controller)
        self.assertEqual({"W", "B", "R", "G"}, self.protections(game, bearer))
        self.assertEqual(5, game.engine._numeric_stat(bearer.object_id, "power"))
        red = self.action_named(game, "B", f"cast:{shock.ref}")
        self.assertNotIn(bearer.ref, red["target_schema"]["legal_refs"])
        self.cast(game, "B", bounce, [bearer.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[bearer.object_id].zone)
        self.assertFalse(game.state.cards[plate.object_id].attached_to)
        self.assertNotIn(private.ref, str(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_exact_equipped_shape_rejects_other_player_and_forged_descriptor(self):
        record = self.db.lookup("Commander's Plate", fuzzy=False)
        result = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertTrue(any(COMMANDER_COMPLEMENT_PROTECTION_CAPABILITY in node.capability_dependencies
                            for face in result.faces for node in face.nodes))
        changed = replace(record, oracle_text=record.oracle_text.replace("your commander's", "its controller's commander's"))
        rejected = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", rejected.status, rejected.material_residuals)
        handler = AttachedCommanderProtectionHandler()
        with self.assertRaises(SemanticNodeError):
            handler.validate({**commander_complement_protection_descriptor(), "subject": "source_owner"})

    def test_mutant_reading_creature_controller_colors_is_killed(self):
        from quorune.semantic_runtime.attached_commander_protection import AttachedCommanderProtectionHandler

        original = AttachedCommanderProtectionHandler.lower

        def use_old_colors(handler, descriptor, context):
            if context.source_controller == "B":
                context = replace(context, source_commander_colors=("W", "R"))
            return original(handler, descriptor, context)

        with patch.object(AttachedCommanderProtectionHandler, "lower", use_old_colors):
            with self.assertRaisesRegex(AssertionError, "Items in the first set"):
                self.test_actual_equip_prices_partner_union_and_changing_source_control_replay()
