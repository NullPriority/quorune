"""Native paid return of an owned, physically designated battlefield commander."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.owned_commander_battlefield_return import CAPABILITY
from scripts.build_test_database import build_fixture_database


CARD = "Sanctum of Eternity"
COMMANDER = "Grist, the Hunger Tide"


class OwnCommanderBattlefieldReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "own-commander-battlefield-return.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "paid-commander-lifelink-cards.json",
            fixtures / "own-commander-battlefield-return-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Commander battlefield return", [
            DeckEntry(COMMANDER, 1, "commander"), DeckEntry("Forest", 30),
        ], [COMMANDER])

    def commander(self, game, owner, controller):
        card = next(card for card in game.state.cards.values() if card.owner == owner and card.zone == "command")
        return game.engine.move_card(card.object_id, "battlefield", controller=controller, reason="Commander setup")

    def activate(self, game, source, target, *, ability="ab2", accepted=True):
        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        answer = game.act("pilot:A", {
            "action_id": f"activate:{source.ref}:{ability}", "targets": [target.ref], "pay": "auto",
        })
        self.assertEqual(accepted, answer.ok, answer.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_owned_foreign_controlled_commander_uses_owner_choice_and_replays(self):
        for decline, destination in ((True, "hand"), (False, "command")):
            with self.subTest(destination=destination):
                game = self.session(90396200 + int(decline))
                source = self.add(game.engine, CARD)
                clinic = self.add(game.engine, "Witch's Clinic")
                owned = self.commander(game, "A", "B")
                foreign = self.commander(game, "C", "C")
                duplicate = self.add(game.engine, COMMANDER, ref="copy-without-designation")
                designation = owned.commander_designation_id
                old_logical_id = owned.logical_object_id
                self.assertEqual(("A", "B"), (owned.owner, owned.controller))
                self.assertEqual({"planeswalker"}, game.engine._type_parts(
                    game.engine._effective_card_data(owned)["type_line"])[0])
                self.assertFalse(duplicate.is_commander)
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 4})
                self.checkpoint(game)
                self.activate(game, source, foreign, accepted=False)
                self.activate(game, source, duplicate, accepted=False)
                self.activate(game, clinic, foreign)
                self.assertEqual(1, len(game.state.stack))
                self.activate(game, source, owned)
                self.assertEqual(2, len(game.state.stack))
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertEqual(0, game.state.players["A"].mana_pool["C"])
                pending = self.resolve(game)
                self.assertEqual("replacement.order", pending.kind)
                self.assertEqual(["pilot:A"], list(game.pending_principals()))
                self.assertEqual("battlefield", game.state.cards[owned.object_id].zone)
                game = self.replay(game, load=True)
                options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                chosen = next(option for option in options if bool(option.get("decline")) is decline)
                command = {"action_id": "choose", "replacement": chosen["id"]}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(game))
                arrived = game.state.cards[owned.object_id]
                self.assertEqual((destination, "A", designation),
                                 (arrived.zone, arrived.owner, arrived.commander_designation_id))
                self.assertNotEqual(old_logical_id, arrived.logical_object_id)
                self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[duplicate.object_id].zone)
                self.replay(game, load=True)

    def test_actual_opponent_turn_rejects_return_and_keeps_colorless_mana_offer(self):
        game = self.session(90396202)
        source = self.add(game.engine, CARD)
        commander = self.commander(game, "A", "B")
        self.prepare(game, actor="A", active="B", mana={"C": 2})
        self.checkpoint(game)
        self.activate(game, source, commander, accepted=False)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        paid = game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab1"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(3, game.state.players["A"].mana_pool["C"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)

    def test_closed_return_authority_rejects_owner_timing_and_price_mutations(self):
        card = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, return_node = ir.faces[0].nodes
        self.assertEqual("mana_ability", mana.kind)
        self.assertIn(CAPABILITY, return_node.capability_dependencies)
        game = self.session(90396203)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if CAPABILITY in program.capability_dependencies)
        descriptor, = (thaw_value(item) for item in program.handlers)
        descriptor["ability"]["activation_conditions"] = []
        for mutant in (
            replace(program, target_schema={**program.target_schema, "owner_relation": "any"}),
            replace(program, handlers=(descriptor,)),
            replace(program, cost_schema={**program.cost_schema, "tap_source": False}),
        ):
            with self.subTest(program=mutant):
                result = bind_semantic_program_runtime(mutant, capability_registry=self.registry,
                                                       profile="commander_review")
                self.assertFalse(result["strict"], result)
