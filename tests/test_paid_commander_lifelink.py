"""Native paid lifelink, commander designation, and controller replay."""

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.commander_lifelink import CAPABILITY
from scripts.build_test_database import build_fixture_database


class PaidCommanderLifelinkTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "paid-commander-lifelink.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/paid-commander-lifelink-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Commander lifelink", [DeckEntry(commander, 1, "commander"),
                                  DeckEntry("Plains", 30)], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_exact_paid_tap_and_designated_commander_scope_bind(self):
        card = self.db.lookup("Witch's Clinic", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, grant = ir.faces[0].nodes
        self.assertEqual("mana_ability", mana.kind)
        self.assertIn(CAPABILITY, grant.capability_dependencies)
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "commander": True},
                         grant.target_schema)
        self.assertEqual(2, grant.cost["mana"]["GENERIC"])
        self.assertIs(True, grant.cost["tap_source"])
        game = self.session(748045000)
        source = self.add(game.engine, card.name)
        self.assert_closed(game, source)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id) if row.target_schema)
        for mutant in (replace(program, target_schema={**program.target_schema, "commander": False}),
                       replace(program, active_zone="stack"),
                       replace(program, cost_schema={**program.cost_schema, "tap_source": False})):
            with self.subTest(event=mutant.event):
                result = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(result["strict"], result)
        for text in (card.oracle_text.replace("{2}", "{1}"), card.oracle_text.replace("Target commander", "Target creature")):
            changed = self.compile(replace(card, oracle_text=text))
            self.assertFalse(any(CAPABILITY in node.capability_dependencies for node in changed.faces[0].nodes))

    def put_commander_on_battlefield(self, game, seat):
        card = next(row for row in game.state.cards.values() if row.owner == seat and row.zone == "command")
        return game.engine.move_card(card.object_id, "battlefield", controller=seat, reason="commander witness")

    def grant(self, game, source, target, *, accepted=True):
        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab2", "targets": [target.ref], "pay": "auto"})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_nonaura_planeswalker_commander_is_legal_but_a_copy_name_is_not(self):
        name = "Grist, the Hunger Tide"
        self.deck = DeckDefinition("Grist commander", [DeckEntry(name, 1, "commander"),
                                   DeckEntry("Forest", 30)], [name])
        game = self.session(748045001)
        source = self.add(game.engine, "Witch's Clinic")
        target = self.put_commander_on_battlefield(game, "B")
        same_name = self.add(game.engine, name, ref="grist-without-designation")
        self.assert_closed(game, source)
        self.assertTrue(target.is_commander)
        types, _, _ = game.engine._type_parts(game.engine._effective_card_data(target)["type_line"])
        self.assertEqual({"planeswalker"}, types)
        self.assertFalse(same_name.is_commander)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.grant(game, source, same_name, accepted=False)
        self.grant(game, source, target)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIn("lifelink", game.engine._combat_keywords(game.state.cards[target.object_id]))
        self.assertNotIn("lifelink", game.engine._combat_keywords(game.state.cards[same_name.object_id]))
        self.replay(game, load=True)

    def test_actual_opponent_commander_damage_gains_life_for_its_controller(self):
        game = self.session(748045002)
        source = self.add(game.engine, "Witch's Clinic")
        target = self.put_commander_on_battlefield(game, "B")
        target.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game, active="B", actor="B")
        game.state.players["A"].mana_pool["C"] = 2
        self.checkpoint(game)
        self.grant(game, source, target)
        self.resolve_top(game)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {target.ref: "C"}})
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.players["C"].life == 38)
        self.assertEqual((40, 42, 38), tuple(game.state.players[seat].life for seat in "ABC"))
        self.replay(game, load=True)

    def test_actual_printed_colorless_mana_keeps_its_normal_offer(self):
        game = self.session(748045003)
        source = self.add(game.engine, "Witch's Clinic")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        result = game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab1"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)
