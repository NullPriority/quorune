"""Strict native evidence for a source-linked card type and its actual blink."""

from dataclasses import replace
import json
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_card_type import ANNOTATION, CAPABILITY, COST_TEMPLATE, ENTRY_TEMPLATE
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database

NAME = "Stenn, Paranoid Partisan"
PROFILE = "commander_review"
EXPECTED_TYPES = {
    "artifact", "battle", "conspiracy", "dungeon", "enchantment", "instant", "kindred",
    "phenomenon", "plane", "planeswalker", "scheme", "sorcery", "vanguard",
}


class LinkedCardTypeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        pin = os.environ.get("QUORUNE_STENN_TEST_PIN")
        db_path = Path(pin) if pin else Path(cls.temporary.name) / "linked-card-type.sqlite3"
        if pin is None:
            build_fixture_database([ROOT / "tests/fixtures/linked-card-type-cards.json"], db_path)
        cls.db = CardDatabase(db_path)
        if pin is not None:
            connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
            connection.row_factory = sqlite3.Row
            cls.db._thread_local.connection = connection
            cls.db._connections.append(connection)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Linked card type", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_exact_pair_and_linked_blink_close_as_one_card(self):
        record = self.db.lookup(NAME)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        pair = [node for node in ir.faces[0].nodes if node.template_id in {ENTRY_TEMPLATE, COST_TEMPLATE}]
        self.assertEqual(2, len(pair))
        self.assertTrue(all(CAPABILITY in node.capability_dependencies for node in pair))
        self.assertTrue(any("zone.linked_exile_return.fixed" in node.capability_dependencies for node in ir.faces[0].nodes))
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(), capability_registry=self.registry,
            capability_profile=PROFILE,
        )
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        for alternative in (
            record.oracle_text.splitlines()[0],
            record.oracle_text.replace("Spells you cast", "Spells opponents cast"),
            record.oracle_text.replace("{1} less", "{2} less"),
        ):
            with self.subTest(alternative=alternative):
                changed = compile_oracle_card(replace(record, oracle_text=alternative),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertTrue(changed.material_residuals)

    def test_linked_card_type_withdrawal_and_compiler_omission_fail_closed(self):
        record = self.db.lookup(NAME)
        registry_path = ROOT / "quorune/rules/capability-registry.json"
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        entry.update(status="blocked", blockers=["Scoped card-type withdrawal witness"])
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified(self.registry.evidence_fingerprint)
        withheld = compile_oracle_card(record, capability_registry=withdrawn, capability_profile=PROFILE)
        with patch("quorune.compiler.linked_card_type_nodes.linked_card_type_face_rows", return_value={}):
            omitted = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        for ir in (withheld, omitted):
            self.assertTrue(ir.material_residuals)
            self.assertFalse(any(node.exact and node.template_id == ENTRY_TEMPLATE for node in ir.faces[0].nodes))

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose_type(self, game, seat, card_type, *, resume=False, stale=None):
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        view = game.packet("pilot:" + seat, full=True)["decision"]
        options = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in view["ctx"]["options"]}
        self.assertEqual(EXPECTED_TYPES, set(options))
        selected = options[card_type]
        command = {"action_id": "choose", "choices": {"replacement": selected}}
        self.reject(game, "D", command)
        for forbidden in ("creature", "land", "wizard", "legendary"):
            self.reject(game, seat, {"action_id": "choose", "choices": {
                "replacement": selected.rsplit(":", 1)[0] + ":" + forbidden,
            }})
        if stale is not None:
            self.assertNotIn(stale, options.values())
            self.reject(game, seat, {"action_id": "choose", "choices": {"replacement": stale}})
        if resume:
            game = self.replay(game, load=True)
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        return game, selected

    def begin(self, game, stenn, *, choice="artifact", mana=None, resume=False):
        self.assert_closed(game, stenn)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana or {"W": 3, "U": 3, "C": 6})
        self.checkpoint(game)
        self.cast(game, "A", stenn)
        self.resolve(game)
        return self.choose_type(game, "A", choice, resume=resume)

    def actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def normal_price(self, game, seat, spell):
        action = next(row for row in self.actions(game, seat) if row["id"] == "cast:" + spell.ref)
        return next(row["requirements"] for row in action["cost_options"] if row["id"] == "normal")

    def activate(self, game, seat, stenn):
        offer, = (row for row in self.actions(game, seat) if row["id"].startswith("activate:" + stenn.ref + ":"))
        command = {"action_id": offer["id"], "pay": "auto"}
        self.reject(game, "D", command)
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_stenn_sets_its_own_cost_and_returns_with_a_new_choice(self):
        game = self.session(576_008_001)
        stenn = self.add(game.engine, NAME, zone="hand", ref="stenn-main")
        artifact = self.add(game.engine, "Ornithopter of Paradise", zone="hand")
        instant = self.add(game.engine, "Inspiration", zone="hand")
        for index, name in enumerate(("Island", "Plains", "Plains")):
            self.add(game.engine, name, ref=f"stenn-return-mana-{index}")
        game, previous = self.begin(game, stenn, resume=True)
        first = game.state.cards[stenn.object_id].logical_object_id
        self.assertEqual(1, self.normal_price(game, "A", artifact)["GENERIC"])
        self.assertEqual(3, self.normal_price(game, "A", instant)["GENERIC"])
        self.cast(game, "A", artifact)
        self.resolve(game)
        self.assertEqual("battlefield", game.state.cards[artifact.object_id].zone)
        pool = game.state.players["A"].mana_pool.copy()
        self.activate(game, "A", stenn)
        self.assertEqual("battlefield", game.state.cards[stenn.object_id].zone)
        paid = game.state.players["A"].mana_pool
        self.assertEqual((pool["W"] - 1, pool["U"] - 1, pool["C"] - 1), (paid["W"], paid["U"], paid["C"]))
        self.resolve(game)
        exiled = game.state.cards[stenn.object_id]
        self.assertEqual("exile", exiled.zone)
        self.assertNotIn(ANNOTATION, exiled.annotations)
        self.assertNotEqual(first, exiled.logical_object_id)
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.pending_decision.kind == "replacement.order")
        game, _ = self.choose_type(game, "A", "instant", resume=True, stale=previous)
        returned = game.state.cards[stenn.object_id]
        self.assertEqual(("battlefield", "A", "A"), (returned.zone, returned.owner, returned.controller))
        self.assertNotEqual(first, returned.logical_object_id)
        self.assertNotEqual(exiled.logical_object_id, returned.logical_object_id)
        self.assertEqual("instant", returned.annotations[ANNOTATION]["choice"]["card_type"])
        price = self.normal_price(game, "A", instant)
        self.assertEqual((2, 1), (price["GENERIC"], price["U"]))
        self.replay(game, load=True)

    def test_actual_stenn_stale_activation_and_exile_departure_do_not_return(self):
        game = self.session(576_008_002)
        stenn = self.add(game.engine, NAME, zone="hand", ref="stenn-stale")
        pull = self.add(game.engine, "Pull from Eternity", zone="hand")
        game, _ = self.begin(game, stenn, choice="dungeon", mana={"W": 5, "U": 4, "C": 5})
        self.activate(game, "A", stenn)
        self.activate(game, "A", stenn)
        self.resolve(game)
        exiled = game.state.cards[stenn.object_id]
        self.assertEqual("exile", exiled.zone)
        self.assertEqual(1, sum(trigger.active for trigger in game.state.delayed_triggers))
        self.cast(game, "A", pull, [exiled.ref])
        self.resolve(game)
        self.assertEqual("graveyard", game.state.cards[stenn.object_id].zone)
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertEqual("graveyard", game.state.cards[stenn.object_id].zone)
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_actual_stolen_stenn_reduces_the_thief_spell_and_returns_to_owner(self):
        game = self.session(576_008_003)
        stenn = self.add(game.engine, NAME, zone="hand", ref="stenn-borrowed")
        theft = self.add(game.engine, "Act of Treason", seat="B", zone="hand")
        artifact = self.add(game.engine, "Ornithopter of Paradise", seat="B", zone="hand")
        for index, name in enumerate(("Mountain", *("Island",) * 5, *("Plains",) * 5)):
            self.add(game.engine, name, seat="B", ref=f"stenn-thief-mana-{index}")
        game, _ = self.begin(game, stenn)
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.phase == "precombat_main")
        self.assertEqual(2, self.normal_price(game, "B", artifact)["GENERIC"])
        self.cast(game, "B", theft, [stenn.ref])
        self.resolve(game)
        taken = game.state.cards[stenn.object_id]
        self.assertEqual(("A", "B"), (taken.owner, taken.controller))
        self.assertEqual(1, self.normal_price(game, "B", artifact)["GENERIC"])
        self.cast(game, "B", artifact)
        self.resolve(game)
        self.activate(game, "B", taken)
        self.resolve(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "replacement.order")
        self.assertEqual(["A"], game.state.pending_decision.actors)
        game, _ = self.choose_type(game, "A", "kindred", resume=True)
        returned = game.state.cards[stenn.object_id]
        self.assertEqual(("A", "A", "battlefield"), (returned.owner, returned.controller, returned.zone))
        self.replay(game, load=True)

    def test_actual_stenn_activated_in_end_step_returns_at_the_following_end_step(self):
        game = self.session(576_008_004)
        stenn = self.add(game.engine, NAME, zone="hand", ref="stenn-end-step")
        for index, name in enumerate(("Island", "Plains", "Plains")):
            self.add(game.engine, name, ref=f"stenn-end-mana-{index}")
        game, _ = self.begin(game, stenn)
        self.until(game, lambda current: current.state.step == "end_step")
        self.activate(game, "A", stenn)
        self.resolve(game)
        exiled = game.state.cards[stenn.object_id]
        self.assertEqual("exile", exiled.zone)
        current_turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > current_turn)
        self.assertEqual("exile", game.state.cards[stenn.object_id].zone)
        self.until(game, lambda current: current.state.pending_decision.kind == "replacement.order")
        self.assertEqual("B", game.state.active_player)
        self.assertEqual("end_step", game.state.step)
        game, _ = self.choose_type(game, "A", "plane", resume=True)
        self.assertEqual("battlefield", game.state.cards[stenn.object_id].zone)
        self.replay(game, load=True)
