"""Native private selection and sacrifice witnesses for one-card discard Echo."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.control_history import record_control_acquisition
from quorune.deck import DeckDefinition, DeckEntry
from quorune.echo import DISCARD_ECHO_CAPABILITY, DISCARD_ECHO_TEMPLATE, validate_discard_echo_effect
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


PAY = "Deepcavern Imp"
DECLINE = "Rakdos Headliner"


class DiscardEchoTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Discard Echo", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "discard-echo.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/discard-echo-cards.json", path)
        cls.use_database(path)

    def echo_case(self, original, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        echoer = self.add(game.engine, original, ref="echo-source")
        own = self.add(game.engine, "Grizzly Bears", zone="hand", ref="echo-private-payment")
        foreign = self.add(game.engine, "Grizzly Bears", zone="hand", seat="B", ref="echo-foreign-hand")
        self.assert_closed(game, echoer)
        engine = game.engine
        record_control_acquisition(
            echoer, controller_turns_begun=game.state.players["A"].turns_begun,
            timestamp=engine._next_zone_timestamp(), history_version=game.state.control_history_version,
        )
        engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.active_player = "A"
        game.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))
        engine._enter_step()
        engine.pump()
        self.checkpoint(game)
        self.assertIsNotNone(self.resolve(game))
        return game, echoer, own, foreign

    def test_actual_optional_echo_discard_is_private_and_pay_or_sacrifice_replays(self):
        for index, original in enumerate((PAY, DECLINE)):
            with self.subTest(original=original):
                game, echoer, own, foreign = self.echo_case(original, 702300120 + index)
                game = self.replay(game, load=True)
                owner = game.packet("pilot:A", full=True)["decision"]
                self.assertIn(own.ref, json.dumps(owner))
                self.assertNotIn(foreign.ref, json.dumps(owner))
                for seat in "BCD":
                    self.assertNotIn(own.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                before = authoritative_state_hash(game.state)
                for principal, command in (
                    ("pilot:B", {"action_id": "choose", "cards": [own.ref]}),
                    ("pilot:A", {"action_id": "choose", "cards": [foreign.ref]}),
                    ("pilot:A", {"action_id": "choose", "cards": [own.ref, own.ref]}),
                ):
                    self.assertFalse(game.act(principal, command).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                mana = dict(game.state.players["A"].mana_pool)
                payment = {"action_id": "choose", "cards": [own.ref]} if index == 0 else {"action_id": "choose", "pay": False}
                result = game.act("pilot:A", payment)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(mana, game.state.players["A"].mana_pool)
                self.assertEqual("hand", game.state.cards[foreign.object_id].zone)
                self.assertEqual("graveyard" if index == 0 else "hand", game.state.cards[own.object_id].zone)
                self.assertEqual("battlefield" if index == 0 else "graveyard", game.state.cards[echoer.object_id].zone)
                self.replay(game, load=True)

    def test_discard_echo_compiles_the_complete_originals_and_withholds_unsafe_prices(self):
        for name in (PAY, DECLINE):
            card = self.db.lookup(name)
            program = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual("exact", program.status, program.material_residuals)
            node, = (node for face in program.faces for node in face.nodes if node.template_id == DISCARD_ECHO_TEMPLATE)
            self.assertIn(DISCARD_ECHO_CAPABILITY, node.capability_dependencies)
            self.assertEqual("discard", validate_discard_echo_effect(
                node.effects[0], player="$controller", source="$source",
            ).kind)
            for text in ("Echo—Discard two cards.", "Echo—Discard a creature card.", "Echo—Sacrifice a land."):
                forged = replace(card, oracle_text=text)
                self.assertNotEqual("exact", compile_oracle_card(
                    forged, capability_registry=self.registry, capability_profile="commander_review",
                ).status)

    def test_discard_echo_dependency_and_compiler_omissions_withhold_exact_closure(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == DISCARD_ECHO_CAPABILITY).update(
            status="blocked", blockers=["Withhold discard Echo"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in (PAY, DECLINE):
            card = self.db.lookup(name)
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=registry, capability_profile="commander_review",
            ).status)
            with patch("quorune.compiler.echo_nodes.compile_discard_echo", return_value=None):
                self.assertNotEqual("exact", compile_oracle_card(
                    card, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
