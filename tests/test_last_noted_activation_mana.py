"""Native paid mana note, counter cost, and replay tests."""

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
from quorune.rules.noted_mana import NOTED_MANA_CAPABILITY, current_noted_mana_type
from scripts.build_test_database import build_fixture_database


class LastNotedActivationManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "last-noted-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/last-noted-activation-mana-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Noted mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

    def test_exact_charge_and_stored_mana_owners_fail_closed(self):
        card = self.db.lookup("Jeweled Amulet", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        charge, mana = ir.faces[0].nodes
        self.assertEqual("activate", charge.event)
        self.assertEqual("mana_ability", mana.kind)
        self.assertIn(NOTED_MANA_CAPABILITY, charge.capability_dependencies)
        self.assertIn(NOTED_MANA_CAPABILITY, mana.capability_dependencies)
        self.assertEqual("$context.paid_mana_type_to_note", charge.effects[0]["note_paid_mana_type"])
        self.assertEqual(3, mana.handlers[0]["ability"]["output"]["schema_version"])
        for old, new in (("there are no charge counters", "there are no stun counters"),
                         ("{1}, {T}", "{2}, {T}"),
                         ("Remove a charge counter", "Remove two charge counters"),
                         ("last noted type", "chosen color")):
            with self.subTest(changed=old):
                altered = self.compile(replace(card, oracle_text=card.oracle_text.replace(old, new)))
                self.assertFalse(all(NOTED_MANA_CAPABILITY in node.capability_dependencies
                                     for node in altered.faces[0].nodes))
                self.assertTrue(any(not node.exact or NOTED_MANA_CAPABILITY not in node.capability_dependencies
                                    for node in altered.faces[0].nodes))
        game = self.session(748038000)
        source = self.add(game.engine, card.name)
        self.assert_closed(game, source)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id) if row.effects)
        changed = replace(program, effects=({**program.effects[0], "note_paid_mana_type": "G"},))
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"], result)

    def scene(self, seed, *, mana, keys=0, tapper=False, bounce=None):
        game = self.session(seed)
        amulet = self.add(game.engine, "Jeweled Amulet")
        untappers = [
            self.add(game.engine, "Voltaic Key", ref=f"amulet-key-{i}")
            for i in range(keys)
        ]
        charger = self.add(game.engine, "Coretapper") if tapper else None
        spell = self.add(game.engine, bounce, zone="hand") if bounce else None
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        if charger is not None:
            charger.acquired_control_turn_count = -1
        for card in (amulet, *untappers, charger, spell):
            if card:
                self.assert_closed(game, card)
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        return game, amulet, untappers, charger, spell, target

    def offer(self, game, card, ability):
        decision = game.packet("pilot:A", full=True)["decision"]
        identity = f"activate:{card.ref}:{ability}"
        return next((row for row in decision["ctx"]["legal"]["actions"] if row["id"] == identity), None)

    def activate(self, game, card, ability, *, accepted=True, **payload):
        action = self.offer(game, card, ability)
        self.assertIsNotNone(action)
        before = authoritative_state_hash(game.state)
        response = game.act("pilot:A", {"action_id": action["id"], **payload})
        self.assertEqual(accepted, response.ok, response.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def current(self, game, amulet):
        return game.state.cards[amulet.object_id]

    def charge(self, game, amulet, mana_type):
        self.activate(game, amulet, "ab1", pay="manual", payment={mana_type: 1})
        self.assertIsNone(current_noted_mana_type(self.current(game, amulet)))
        self.assertFalse(self.current(game, amulet).counters)
        self.resolve_top(game)
        self.assertEqual(mana_type, current_noted_mana_type(self.current(game, amulet)))
        self.assertEqual({"charge": 1}, self.current(game, amulet).counters)

    def untap(self, game, key, amulet):
        self.activate(game, key, "ab1", targets=[amulet.ref], pay="auto")
        self.resolve_top(game)
        self.assertFalse(self.current(game, amulet).tapped)

    def add_external_charge(self, game, charger, amulet):
        self.activate(game, charger, "ab1", targets=[amulet.ref], pay="auto")
        self.resolve_top(game)
        self.assertEqual({"charge": 1}, self.current(game, amulet).counters)

    def test_actual_colorless_note_is_committed_by_resolution_and_survives_an_external_counter(self):
        game, amulet, keys, charger, _, _ = self.scene(748038001, mana={"C": 1, "G": 2}, keys=2, tapper=True)
        self.charge(game, amulet, "C")
        self.untap(game, keys[0], amulet)
        self.assertIsNone(self.offer(game, amulet, "ab1"))
        game = self.replay(game, load=True)
        self.activate(game, amulet, "ab2", accepted=False, mana_output={"G": 1})
        self.activate(game, amulet, "ab2", mana_output={"C": 1})
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertFalse(self.current(game, amulet).counters)
        self.assertFalse(game.state.stack)
        self.add_external_charge(game, charger, amulet)
        self.untap(game, keys[1], amulet)
        self.assertEqual("C", current_noted_mana_type(self.current(game, amulet)))
        payable_colorless = game.state.players["A"].mana_pool["C"]
        self.activate(game, amulet, "ab2")
        self.assertEqual(payable_colorless + 1, game.state.players["A"].mana_pool["C"])
        self.assertFalse(self.current(game, amulet).counters)
        self.replay(game, load=True)

    def test_actual_stored_blue_funds_a_spell_with_automatic_counter_payment(self):
        game, amulet, keys, _, spell, target = self.scene(
            748038002, mana={"U": 1, "C": 1}, keys=1, bounce="Unsummon",
        )
        self.charge(game, amulet, "U")
        self.untap(game, keys[0], amulet)
        self.assertEqual(0, game.state.players["A"].mana_pool["U"])
        game = self.replay(game, load=True)
        self.cast_for(game, "A", spell, [target.ref])
        self.assertTrue(self.current(game, amulet).tapped)
        self.assertFalse(self.current(game, amulet).counters)
        self.assertEqual("U", current_noted_mana_type(self.current(game, amulet)))
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_actual_recast_incarnation_spends_a_new_charge_for_zero_mana(self):
        game, amulet, _, charger, spell, _ = self.scene(
            748038003, mana={"C": 1, "U": 2}, tapper=True, bounce="Boomerang",
        )
        self.charge(game, amulet, "C")
        old_identity = self.current(game, amulet).logical_object_id
        self.cast_for(game, "A", spell, [amulet.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.current(game, amulet).zone)
        self.cast_for(game, "A", self.current(game, amulet))
        self.resolve_top(game)
        self.assertNotEqual(old_identity, self.current(game, amulet).logical_object_id)
        self.assertIsNone(current_noted_mana_type(self.current(game, amulet)))
        self.add_external_charge(game, charger, amulet)
        game = self.replay(game, load=True)
        self.activate(game, self.current(game, amulet), "ab2", accepted=False, mana_output={"C": 1})
        self.activate(game, self.current(game, amulet), "ab2")
        self.assertTrue(self.current(game, amulet).tapped)
        self.assertFalse(self.current(game, amulet).counters)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)
