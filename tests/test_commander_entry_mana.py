from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.color_set_mana_abilities import COMMANDER_ENTRY_COUNTER_MANA_HANDLER_ID
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_provenance import mana_provenance_lots
from quorune.mana_spend_rules import COMMANDER_ENTRY_COUNTER_MANA_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import semantic_preflight
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.mana_pool_payment import MANA_POOL_PAYMENT_FIELD, PoolManaUnit
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


FIXTURES = (
    ROOT / "tests/fixtures/bound-effect-program-cards.json",
    ROOT / "tests/fixtures/commander-identity-mana-cards.json",
    ROOT / "tests/fixtures/tap-for-mana-production-multipliers-cards.json",
    ROOT / "tests/fixtures/counter-doubling-cards.json",
    ROOT / "tests/fixtures/commander-entry-mana-cards.json",
)
COMMANDERS = ("Thrasios, Triton Hero", "Vial Smasher the Fierce")


class CommanderEntryManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "commander-entry-mana.sqlite3"
        build_fixture_database(list(FIXTURES), db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Commander entry mana",
            [*(DeckEntry(name, 1, "commander") for name in COMMANDERS), DeckEntry("Generic Bound Plains", 30)],
            list(COMMANDERS),
        )

    def commander(self, game, name=COMMANDERS[0]):
        return next(card for card in game.state.cards.values()
                    if card.owner == "A" and card.is_commander and card.printed_name == name)

    def activation(self, game, land, *, paid):
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        abilities = game.engine._activated_abilities(game.state.cards[land.object_id])
        ability = next(row for row in abilities if (row.color_set_mana_output is not None) == paid)
        return next(row for row in options if row["id"] == f"activate:{land.ref}:{ability.ability_id}")

    def reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{actor}", command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activate_paid(self, game, land, color, *, payment):
        offer = self.activation(game, land, paid=True)
        command = {"action_id": offer["id"], "pay": "manual", "payment": payment}
        if color is not None:
            command["mana_output"] = {color: 1}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def cast_from_pool(self, game, card, payment):
        offer = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row.get("card") == card.ref and row["id"].startswith("cast"))
        result = game.act("pilot:A", {
            "action_id": offer["id"], "pay": "manual", MANA_POOL_PAYMENT_FIELD: payment,
        })
        self.assertTrue(result.ok, result.summary)

    def test_commander_entry_mana_is_exact_and_withdrawal_fails_closed(self):
        original = self.db.lookup("Opal Palace", fuzzy=False)
        compiled = compile_oracle_card(original, capability_registry=self.registry,
                                       capability_profile="commander_review")
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        handler, = (handler for face in compiled.faces for node in face.nodes for handler in node.handlers
                    if handler["handler_id"] == COMMANDER_ENTRY_COUNTER_MANA_HANDLER_ID)
        self.assertEqual(1, handler["ability"]["mana_cost"]["GENERIC"])
        self.assertIs(handler["ability"]["commander_entry_counters"], True)
        preflight = semantic_preflight(self.db, DeckDefinition("Opal Palace", [DeckEntry(original.name)], []))
        row, = preflight["cards"]
        self.assertTrue(row["card_program_strict_capability_ready"], row.get("unresolved"))
        self.assertEqual("fully_playable", row["status"])
        for raw in (original.oracle_text.replace("{1}, {T}", "{2}, {T}"),
                    original.oracle_text.replace("your commander, it enters", "a creature, it enters")):
            changed = replace(original, oracle_text=raw)
            result = compile_oracle_card(changed, capability_registry=self.registry,
                                         capability_profile="commander_review")
            self.assertTrue(result.material_residuals)

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == COMMANDER_ENTRY_COUNTER_MANA_CAPABILITY).update(
            status="blocked", blockers=["Commander entry mana withdrawal witness"],
        )
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        ir = compile_oracle_card(original, capability_registry=withdrawn, capability_profile="commander_review")
        blockers = [blocker for item in ir.material_residuals for blocker in item.blockers]
        self.assertTrue(any(f"status:{COMMANDER_ENTRY_COUNTER_MANA_CAPABILITY}:blocked" in blocker
                            for blocker in blockers), blockers)
        self.assertFalse(any("evidence_index:unverified" in blocker for blocker in blockers))
        fixed = ir.faces[0].nodes[0]
        self.assertTrue(fixed.exact)

    def test_actual_palace_taps_for_colorless_or_paid_combined_commander_colors(self):
        game = self.session(801701)
        fixed = self.add(game.engine, "Opal Palace", ref="palace-fixed")
        paid = self.add(game.engine, "Opal Palace", ref="palace-paid")
        private = self.add(game.engine, "Boomerang", seat="D", zone="hand", ref="opal-private")
        self.assert_closed(game, paid)
        self.prepare(game)
        self.checkpoint(game)

        fixed_offer = self.activation(game, fixed, paid=False)
        result = game.act("pilot:A", {"action_id": fixed_offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertFalse(mana_provenance_lots(game.state.players["A"]))

        paid_offer = self.activation(game, paid, paid=True)
        options = paid_offer["choice_schema"]["mana_output"]["options"]
        self.assertEqual([{"U": 1}, {"B": 1}, {"R": 1}, {"G": 1}], [option["value"] for option in options])
        legal = {"action_id": paid_offer["id"], "pay": "manual", "payment": {"C": 1}, "mana_output": {"G": 1}}
        self.reject(game, "B", legal)
        self.reject(game, "A", {**legal, "mana_output": {"W": 1}})
        self.reject(game, "A", {**legal, "payment": {"C": 2}})
        result = game.act("pilot:A", legal)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        lot, = mana_provenance_lots(game.state.players["A"])
        self.assertEqual((("G", 1),), lot.bundle)
        self.reject(game, "A", legal)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        self.replay(game, load=True)

    def test_actual_palace_empty_color_identity_pays_without_producing_mana(self):
        game = self.session(801706)
        land = self.add(game.engine, "Opal Palace")
        game.state.commander_oracle_ids["A"] = [self.db.lookup("Ulamog, the Ceaseless Hunger").oracle_id]
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self.activate_paid(game, land, None, payment={"C": 1})
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertFalse(mana_provenance_lots(game.state.players["A"]))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_palace_auto_pays_its_input_and_first_cast_counts_itself(self):
        game = self.session(801702)
        owned_elsewhere = self.add(game.engine, "Opal Palace", seat="B")
        game.state.players["B"].zones["battlefield"].remove(owned_elsewhere.object_id)
        game.state.players["A"].zones["battlefield"].append(owned_elsewhere.object_id)
        owned_elsewhere.controller = "A"
        commander = self.commander(game)
        partner = self.commander(game, COMMANDERS[1])
        game.state.players["A"].commander_casts[partner.oracle_id] = 3
        self.prepare(game, mana={"C": 1, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", commander)
        self.assertTrue(game.state.cards[owned_elsewhere.object_id].tapped)
        self.assertEqual(1, game.state.players["A"].commander_casts[commander.oracle_id])
        self.assertEqual(1, len(game.state.stack))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[commander.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)

    def test_actual_palace_doubled_spend_counts_each_unit_and_combines_before_replacement(self):
        game = self.session(801703)
        land = self.add(game.engine, "Opal Palace")
        amplifier = self.add(game.engine, "Mana Reflection")
        scales = self.add(game.engine, "Hardened Scales")
        commander = self.commander(game, COMMANDERS[1])
        game.state.players["A"].commander_casts[commander.oracle_id] = 1
        for card in (land, amplifier, scales):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "B": 1})
        self.checkpoint(game)
        self.activate_paid(game, land, "R", payment={"C": 1})
        lot, = mana_provenance_lots(game.state.players["A"])
        self.assertEqual((("R", 2),), lot.bundle)
        self.cast_from_pool(game, commander, {
            PoolManaUnit("R", restriction=lot.restriction).key: 2,
            PoolManaUnit("C").key: 2,
            PoolManaUnit("B").key: 1,
        })
        self.assertEqual(2, game.state.players["A"].commander_casts[commander.oracle_id])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.state.cards[commander.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_palace_hand_cast_uses_prior_count_without_incrementing_it(self):
        from quorune.replacement.ordering import ReplacementChoiceRequired

        game = self.session(801704)
        land = self.add(game.engine, "Opal Palace")
        commander = self.commander(game)
        with self.assertRaises(ReplacementChoiceRequired) as pending:
            game.engine.move_card(commander.object_id, "hand")
        redirect, = pending.exception.pending.choice.options
        game.engine.move_card(commander.object_id, "hand", replacement_selections=[f"decline:{redirect}"])
        game.state.players["A"].commander_casts[commander.oracle_id] = 2
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)
        self.activate_paid(game, land, "G", payment={"C": 1})
        self.cast_for(game, "A", game.state.cards[commander.object_id])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.players["A"].commander_casts[commander.oracle_id])
        self.assertEqual(2, game.state.cards[commander.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_palace_spend_on_another_spell_or_ordinary_mana_adds_no_counters(self):
        game = self.session(801705)
        land = self.add(game.engine, "Opal Palace")
        unrelated = self.add(game.engine, "Grizzly Bears", zone="hand")
        commander = self.commander(game)
        self.prepare(game, mana={"C": 2, "G": 1, "U": 1})
        self.checkpoint(game)
        self.activate_paid(game, land, "G", payment={"C": 1})
        lot, = mana_provenance_lots(game.state.players["A"])
        self.cast_from_pool(game, commander, {PoolManaUnit("G").key: 1, PoolManaUnit("U").key: 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[commander.object_id].counters.get("+1/+1", 0))
        self.assertEqual((lot,), mana_provenance_lots(game.state.players["A"]))
        self.cast_from_pool(game, unrelated, {
            PoolManaUnit("G", restriction=lot.restriction).key: 1, PoolManaUnit("C").key: 1,
        })
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[unrelated.object_id].counters.get("+1/+1", 0))
        self.assertFalse(mana_provenance_lots(game.state.players["A"]))
        self.replay(game, load=True)

    def test_omitting_the_actual_cast_spend_mutant_loses_the_entry_counters(self):
        with patch("quorune.rules.casting.mana_spend_entry.bind_paid_commander_entry"), self.assertRaisesRegex(AssertionError, "1 != 0"):
            self.test_actual_palace_auto_pays_its_input_and_first_cast_counts_itself()
