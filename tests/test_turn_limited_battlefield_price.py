from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.model import CastProposalError
from quorune.rules.global_alternative_cost_model import TURN_LIMITED_ALTERNATIVE_CAPABILITY
from quorune.shared_type_doors import SharedTypeDoors
from scripts.build_test_database import build_fixture_database

import test_global_alternative_costs as shared


ORIGINALS = ("As Foretold", "Charred Foyer // Warped Space", "Darksteel Monolith")
PROFILE = "commander_review"


class TurnLimitedBattlefieldPriceTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    controls = shared.GlobalAlternativeCostTests
    session = controls.session
    add = controls.add
    prepare = controls.prepare
    checkpoint = controls.checkpoint
    resolve = controls.resolve
    replay = controls.replay
    binding = controls.binding
    private_replay = controls.private_replay
    priority = controls.priority
    advance = controls.advance
    cast_row = controls.cast_row
    cast = controls.cast
    reject = controls.reject
    grant_options = controls.grant_options

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "turn-limited-prices.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "global-alternative-cost-cards.json",
            fixtures / "turn-limited-battlefield-price-cards.json",
        ], path)
        cls.use_database(path)

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Turn-limited prices", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_complete_originals_bind_their_turn_limited_price(self):
        for index, name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                game = self.session(761000 + index)
                source = self.add(game.engine, name, zone="hand")
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                                         capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                bound = self.binding(game, source)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
                self.assertEqual("capability_closed", bound["trust_basis"])

    def test_time_counters_bound_selected_face_and_each_source_has_its_own_use(self):
        game = self.session(761010)
        first = self.add(game.engine, "As Foretold", ref="first-time-grant")
        first.counters["time"] = 1
        second = self.add(game.engine, "As Foretold", ref="second-time-grant")
        second.counters["time"] = 2
        split = self.add(game.engine, "Fire // Ice", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        next_turn = self.add(game.engine, "Lightning Bolt", zone="hand", ref="next-turn-bolt")
        opponent_turn = self.add(game.engine, "Lightning Bolt", zone="hand", ref="opponent-turn-bolt")
        hidden = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        self.prepare(game)
        self.checkpoint(game)

        packet = game.packet("pilot:A", full=True)
        split_offer, = [row for row in packet["decision"]["ctx"]["legal"]["actions"]
                        if row.get("card") == split.ref and row.get("face") == "Fire"]
        eligible = [option["id"] for option in split_offer["cost_options"]
                    if option["id"].startswith("battlefield-alternative:")]
        self.assertEqual(1, len(eligible))
        options = self.grant_options(game, bolt)
        self.assertEqual(2, len(options))
        first_use = next(row["id"] for row in options if row["id"] not in eligible)
        self.cast(game, bolt, {}, ["B"], option=first_use)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        another = game.state.cards[next_turn.object_id]
        self.assertEqual(eligible, [row["id"] for row in self.grant_options(game, another)])
        self.cast(game, another, {}, ["B"], grant=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.grant_options(game, game.state.cards[opponent_turn.object_id]))

        self.advance(game, lambda session: session.state.active_player == "B"
                     and session.state.pending_decision is not None
                     and session.state.pending_decision.kind == "priority")
        self.priority(game, "A")
        during_other_turn = game.state.cards[opponent_turn.object_id]
        options = self.grant_options(game, during_other_turn)
        self.assertEqual(2, len(options))
        self.assertNotIn(first_use, {row["id"] for row in options})
        self.cast(game, during_other_turn, {}, ["B"], option=options[0]["id"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(31, game.state.players["B"].life)
        self.private_replay(game, hidden)

    def test_mana_x_and_additional_cost_failure_preserve_the_allowance(self):
        game = self.session(761011)
        source = self.add(game.engine, "As Foretold")
        source.counters["time"] = 2
        spell = self.add(game.engine, "Blaze", zone="hand")
        voice = self.add(game.engine, "Tormenting Voice", zone="hand")
        discard = self.add(game.engine, "Lightning Bolt", zone="hand")
        self.prepare(game)
        self.checkpoint(game)

        x_price, = self.grant_options(game, spell)
        self.reject(game, {"action_id": "cast:" + spell.ref, "cost_option": x_price["id"],
                           "pay": "manual", "payment": {}, "x": 1, "targets": ["B"]})
        price, = self.grant_options(game, voice)
        command = {"action_id": "cast:" + voice.ref, "cost_option": price["id"],
                   "pay": "manual", "payment": {}, "discard_cards": [discard.ref]}
        with patch("quorune.rules.casting.commit._commit_additional_costs",
                   side_effect=CastProposalError("failed after the allowance was reserved")) as payment:
            self.reject(game, command)
            payment.assert_called_once()
        self.assertEqual([price["id"]], [row["id"] for row in self.grant_options(game, voice)])
        self.cast(game, voice, {}, grant=True, discard_cards=[discard.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[discard.object_id].zone)
        self.assertEqual([], self.grant_options(game, game.state.cards[spell.object_id]))
        self.replay(game)

    def test_colorless_from_hand_preserves_printed_cost_and_ignores_colored_spells(self):
        game = self.session(761012)
        self.add(game.engine, "Darksteel Monolith")
        sphere = self.add(game.engine, "Sphere of Resistance", zone="hand")
        next_artifact = self.add(game.engine, "Fist of Suns", zone="hand")
        colored = self.add(game.engine, "Lightning Bolt", zone="hand")
        self.prepare(game, mana={"R": 1, "C": 4})
        self.checkpoint(game)

        self.assertEqual([], self.grant_options(game, colored))
        offered = self.cast_row(game, sphere)["cost_options"]
        self.assertTrue(any(sum(row["requirements"].values()) == 2 for row in offered))
        self.cast(game, sphere, {}, grant=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.grant_options(game, next_artifact))
        self.cast(game, next_artifact, {"C": 4})
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_unlocked_room_prices_authorized_exile_once_and_does_not_grant_access(self):
        game = self.session(761013)
        room = self.add(game.engine, "Charred Foyer // Warped Space")
        room.shared_type_doors = SharedTypeDoors(("Charred Foyer", "Warped Space"), ("Warped Space",))
        impulse = self.add(game.engine, "Reckless Impulse", zone="hand")
        first = self.add(game.engine, "Lightning Bolt", zone="library", ref="authorized-first")
        second = self.add(game.engine, "Lightning Bolt", zone="library", ref="authorized-second")
        unrelated = self.add(game.engine, "Lightning Bolt", zone="exile", ref="unrelated-exile")
        hidden = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        self.prepare(game, mana={"R": 2, "C": 1})
        self.checkpoint(game)

        self.assertEqual([], self.grant_options(game, impulse))
        self.assertIsNone(self.cast_row(game, unrelated))
        self.cast(game, impulse, {"C": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[first.object_id].zone)
        self.assertEqual("exile", game.state.cards[second.object_id].zone)
        self.assertIsNone(self.cast_row(game, unrelated))
        self.cast(game, game.state.cards[first.object_id], {}, ["B"], grant=True)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        another = game.state.cards[second.object_id]
        self.assertEqual([], self.grant_options(game, another))
        self.cast(game, another, {"R": 1}, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(34, game.state.players["B"].life)
        self.private_replay(game, hidden)

    def test_descriptor_version_owns_its_dependency_independently(self):
        from quorune.compiler.global_alternative_cost_templates import turn_limited_global_alternative_cost_handler
        from quorune.rules.global_alternative_cost_model import TurnLimitedAlternativeScope
        from quorune.rules.keyword_runtime_requirements import registered_component_capabilities
        from quorune.semantic_runtime.global_alternative_costs import GlobalAlternativeCostHandler

        text = self.db.lookup("As Foretold").oracle_text.splitlines()[-1]
        _, descriptor, declared = turn_limited_global_alternative_cost_handler(text)
        self.assertEqual(TURN_LIMITED_ALTERNATIVE_CAPABILITY, declared)
        self.assertEqual((declared,), registered_component_capabilities(descriptor, {"capability_dependencies": []}))
        handler = GlobalAlternativeCostHandler()
        scope = TurnLimitedAlternativeScope.NONOWNED_MANA_VALUE_THREE
        self.assertTrue(scope.permits_value_and_owner(mana_value=3, actor="A", owner="B", time_counters=0))
        self.assertFalse(scope.permits_value_and_owner(mana_value=4, actor="A", owner="B", time_counters=0))
        self.assertFalse(scope.permits_value_and_owner(mana_value=3, actor="A", owner="A", time_counters=0))
        with self.assertRaises(ValueError):
            handler.validate({**descriptor, "grant": {**descriptor["grant"], "origin": "library"}})


    def test_turn_limited_price_withdrawal_and_adjacent_scope_fail_closed(self):
        from dataclasses import replace
        import json

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == TURN_LIMITED_ALTERNATIVE_CAPABILITY).update(
            status="blocked", blockers=["The turn-limited price authority was withdrawn"],
            dependency_fail_closed_status="not_run",
        )
        withheld = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        withheld.mark_evidence_verified(validate_capability_evidence_index(index, registry=withheld))
        for name in ORIGINALS:
            with self.subTest(card=name):
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=withheld, capability_profile=PROFILE)
                blockers = {value for residual in ir.material_residuals for value in residual.blockers}
                self.assertTrue(any("status:" + TURN_LIMITED_ALTERNATIVE_CAPABILITY + ":blocked" in b for b in blockers), blockers)
                self.assertFalse(any("evidence_index:unverified" in b for b in blockers))
        original = self.db.lookup("As Foretold")
        for earlier, adjacent in (("Once each turn", "Twice each turn"), ("time counters", "charge counters")):
            changed = replace(original, oracle_text=original.oracle_text.replace(earlier, adjacent, 1))
            self.assertNotEqual(original.oracle_text, changed.oracle_text)
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)

    def test_omitting_consumption_reopens_the_same_allowance_after_a_real_cast(self):
        def second_price_available(seed):
            game = self.session(seed)
            source = self.add(game.engine, "As Foretold")
            source.counters["time"] = 1
            first = self.add(game.engine, "Lightning Bolt", zone="hand", ref="first")
            second = self.add(game.engine, "Lightning Bolt", zone="hand", ref="second")
            self.prepare(game)
            self.checkpoint(game)
            self.cast(game, first, {}, ["B"], grant=True)
            self.assertIsNone(self.resolve(game))
            return bool(self.grant_options(game, second))

        self.assertFalse(second_price_available(761014))
        with patch("quorune.rules.casting.global_alternatives.commit_cast_grant_usage") as consume:
            self.assertTrue(second_price_available(761015))
            consume.assert_called_once()
