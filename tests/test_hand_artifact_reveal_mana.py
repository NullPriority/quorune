"""Exercise private artifact choices through ordinary activation and casting."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.artifact_hand_mana_model import ARTIFACT_HAND_MANA_CAPABILITY, ARTIFACT_HAND_MANA_HANDLER_ID
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_undo import available_mana_undo
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.util import normalize_mana_bundle
from scripts.build_test_database import build_fixture_database


class HandArtifactRevealManaTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Test Crucible Appraiser"
    profile = "commander_review"

    @classmethod
    def use_database(cls, path, commander, land):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Artifact hand mana", [DeckEntry(commander, 1, "commander"), DeckEntry(land, 30)],
                                  [commander])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "hand-artifact-reveal-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "qualified-library-search-cards.json",
            "announced-target-cardinality-cards.json", "tap-for-mana-production-multipliers-cards.json",
            "assist-payment-cards.json", "counter-replacement-cards.json", "draw-rules-cards.json",
            "hand-artifact-reveal-mana-cards.json",
        )], path)
        cls.use_database(path, "Generic Bound Commander", "Generic Bound Plains")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def offer(self, game, card, action):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"].startswith(f"{action}:{card.ref}"))

    def reject(self, game, response, actor="A"):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, response)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        return result

    def accept(self, game, response):
        result = game.act("pilot:A", response)
        self.assertTrue(result.ok, result.summary)

    def published_refs(self, game):
        return [event.details.get("object") for event in game.state.events if event.code == "card.reveal"]

    def hand_game(self, seed, *, casting=False, multiplier=False):
        game = self.session(seed)
        source = self.add(game.engine, self.source_name)
        ring = self.add(game.engine, "Sol Ring", zone="hand", ref="selectable-ring")
        land = self.add(game.engine, "Darksteel Citadel", zone="hand", ref="selectable-artifact-land")
        unselected = self.add(game.engine, "Worn Powerstone", zone="hand", ref="unselected-artifact") if not casting else None
        bear = self.add(game.engine, "Grizzly Bears", zone="hand", ref="private-nonartifact")
        foreign = self.add(game.engine, "Sol Ring", zone="hand", seat="B", ref="private-foreign-artifact")
        spell = self.add(game.engine, "Worn Powerstone", zone="hand", ref="now-a-spell") if casting else None
        if multiplier:
            self.add(game.engine, "Mana Reflection")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        return game, source, ring, land, unselected, bear, foreign, spell

    def test_real_priority_reveal_selects_current_artifacts_without_stack_or_undo(self):
        game, source, ring, land, other, bear, foreign, _spell = self.hand_game(6051121)
        offer = self.offer(game, source, "activate")
        choice = offer["choice_schema"]["reveal_cards"]
        self.assertEqual({ring.ref, land.ref, other.ref}, set(choice["legal_refs"]))
        self.assertEqual(0, choice["minimum"])
        response = {"action_id": offer["id"], "reveal_cards": [ring.ref, land.ref]}
        self.reject(game, response, actor="B")
        with self.subTest("duplicate"):
            self.reject(game, {**response, "reveal_cards": [ring.ref, ring.ref]})
        with self.subTest("foreign"):
            self.reject(game, {**response, "reveal_cards": [foreign.ref]})
        with self.subTest("nonartifact"):
            self.reject(game, {**response, "reveal_cards": [bear.ref]})
        self.reject(game, {**response, "mana_output": {"C": 6}})
        self.accept(game, response)
        self.assertEqual(4, game.state.players["A"].mana_pool["C"], "mana comes from exactly two chosen cards")
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual([], game.state.stack)
        self.assertEqual([ring.ref, land.ref], self.published_refs(game))
        self.assertEqual("hand", game.state.cards[land.object_id].zone)
        self.assertNotIn("Worn Powerstone", json.dumps(game.packet("pilot:B", full=True)))
        self.assertIsNone(available_mana_undo(game.state, "A"))
        self.replay(game, load=True)

    def test_zero_cards_is_a_legal_tap_without_a_reveal(self):
        game, source, *_ = self.hand_game(6051122)
        offer = self.offer(game, source, "activate")
        self.accept(game, {"action_id": offer["id"], "reveal_cards": []})
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.assertEqual([], self.published_refs(game))
        self.assertEqual([], game.state.stack)
        self.replay(game, load=True)

    def test_cast_payment_uses_private_offered_refs_after_placing_the_spell(self):
        game, source, ring, land, _other, bear, foreign, spell = self.hand_game(6051123, casting=True)
        offer = self.offer(game, spell, "cast")
        option, = offer["cost_options"]
        field, = [key for key in option["choice_schema"] if key.startswith("artifact_mana_cards:")]
        choice = option["choice_schema"][field]
        self.assertEqual({ring.ref, land.ref}, set(choice["legal_refs"]))
        self.assertNotIn(field, json.dumps(game.packet("pilot:B", full=True)))
        response = {"action_id": offer["id"], "cost_option": option["id"], "pay": "auto"}
        self.reject(game, response)
        self.reject(game, {**response, field: [ring.ref]})
        self.reject(game, {**response, field: [spell.ref, ring.ref]})
        self.reject(game, {**response, field: [foreign.ref, ring.ref]})
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual([], self.published_refs(game))
        self.assertNotIn("Sol Ring", json.dumps(game.packet("pilot:C", full=True)))
        self.accept(game, {**response, field: [ring.ref, land.ref]})
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual([ring.ref, land.ref], self.published_refs(game))
        self.assertNotIn(bear.printed_name, json.dumps(game.packet("pilot:B", full=True)))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_explicit_mana_steps_and_failed_payment_share_the_same_rollback(self):
        game, source, ring, land, _other, _bear, _foreign, spell = self.hand_game(6051124, casting=True)
        activation = self.offer(game, source, "activate")
        ability_id = activation["id"].rsplit(":", 1)[1]
        offer = self.offer(game, spell, "cast")
        option, = offer["cost_options"]
        response = {"action_id": offer["id"], "cost_option": option["id"], "pay": "manual", "payment": {"C": 3}}
        step = {"source": source.ref, "bundle": {"C": 4},
                "artifact_hand_reveal": {"ability_id": ability_id, "cards": [ring.ref, land.ref]}}
        self.reject(game, {**response, "mana": [{**step, "bundle": {"C": 2}}]})
        self.reject(game, {**response, "payment": {"C": 2}, "mana": [step]})
        self.assertEqual([], self.published_refs(game))
        self.accept(game, {**response, "mana": [step]})
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual([ring.ref, land.ref], self.published_refs(game))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def assist_game(self, seed, *, helper_holds_artifacts):
        game = self.session(seed)
        source = self.add(game.engine, self.source_name, seat="B")
        source.acquired_control_turn_count = -1
        names = ("Sol Ring", "Darksteel Citadel") if helper_holds_artifacts else ("Grizzly Bears", "Llanowar Elves")
        sources = [self.add(game.engine, name, seat="B", zone="hand", ref=f"helper-hidden-{number}")
                   for number, name in enumerate(names)]
        spell = self.add(game.engine, "Bring Down", zone="hand")
        target = self.add(game.engine, "Ancient Brontodon", seat="C")
        self.assert_closed(game, source)
        self.prepare(game, mana={"W": 1})
        self.checkpoint(game)
        offer = self.offer(game, spell, "cast")
        option, = offer["cost_options"]
        return game, source, sources, offer, option, target

    def test_assist_uses_public_eligibility_and_keeps_helper_selections_private(self):
        game, source, cards, offer, option, target = self.assist_game(6051126, helper_holds_artifacts=True)
        _control, _other_source, _other_cards, _other_offer, other_option, _other_target = self.assist_game(
            6051126, helper_holds_artifacts=False,
        )
        invited = option["choice_schema"]["assist_helper"]
        self.assertEqual(["B"], invited["legal_values"])
        self.assertEqual(invited, other_option["choice_schema"]["assist_helper"])
        self.accept(game, {"action_id": offer["id"], "cost_option": option["id"], "targets": [target.ref],
                           "assist_helper": "B", "pay": "auto"})
        preview = game.packet("pilot:B", full=True)["decision"]["ctx"]
        mana, = [row for row in preview["legal_actions"] if row.get("assist_step", {}).get("source") == source.ref]
        self.assertEqual({card.ref for card in cards}, set(mana["choice_schema"]["reveal_cards"]["legal_refs"]))
        response = {"action_id": mana["id"], "reveal_cards": [card.ref for card in cards]}
        self.reject(game, response, actor="A")
        self.reject(game, {**response, "reveal_cards": [cards[0].ref, cards[0].ref]}, actor="B")
        self.assertTrue(game.act("pilot:B", response).ok)
        self.assertEqual([], self.published_refs(game))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        for card in cards:
            self.assertNotIn(card.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self.assertTrue(game.act("pilot:B", {"action_id": "assist:finish-mana"}).ok)
        self.accept(game, {"action_id": "assist:finish-mana"})
        self.assertTrue(game.act("pilot:B", {"action_id": "assist:contribute", "assist_amount": 3}).ok)
        self.accept(game, {"action_id": "assist:pay"})
        self.assertEqual(1, game.state.players["B"].mana_pool["C"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual([card.ref for card in cards], self.published_refs(game))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_another_ordinary_activation_can_use_the_offered_artifact_payment(self):
        game = self.session(6051127)
        source = self.add(game.engine, self.source_name)
        stone = self.add(game.engine, "Mind Stone")
        ring = self.add(game.engine, "Sol Ring", zone="hand", ref="activation-payment-artifact")
        self.assert_closed(game, source)
        self.assert_closed(game, stone)
        self.prepare(game)
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        option, = [row for row in actions if row["id"].startswith(f"activate:{stone.ref}:")
                   and any(key.startswith("artifact_mana_cards:") for key in row.get("choice_schema", {}))]
        field, = [key for key in option["choice_schema"] if key.startswith("artifact_mana_cards:")]
        self.assertEqual([ring.ref], option["choice_schema"][field]["legal_refs"])
        self.reject(game, {"action_id": option["id"], "pay": "auto"})
        self.accept(game, {"action_id": option["id"], "pay": "auto", field: [ring.ref]})
        self.assertEqual("graveyard", game.state.cards[stone.object_id].zone)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual([ring.ref], self.published_refs(game))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_tap_multiplier_scales_the_real_reveal_result(self):
        game, source, ring, _land, _other, _bear, _foreign, _spell = self.hand_game(6051125, multiplier=True)
        offer = self.offer(game, source, "activate")
        self.accept(game, {"action_id": offer["id"], "reveal_cards": [ring.ref]})
        self.assertEqual(4, game.state.players["A"].mana_pool["C"])
        self.assertEqual([ring.ref], self.published_refs(game))
        self.replay(game, load=True)

    def test_supported_shape_and_capability_withdrawal(self):
        card = self.db.lookup(self.source_name, fuzzy=False)
        result = compile_oracle_card(card, capability_registry=self.registry, capability_profile=self.profile)
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertTrue(any(handler["handler_id"] == ARTIFACT_HAND_MANA_HANDLER_ID
                            for face in result.faces for node in face.nodes for handler in node.handlers))
        for old, new in (("artifact cards", "creature cards"), ("{C}{C}", "{G}{G}"),
                         ("{T}:", "{1}, {T}:"), ("for each card revealed this way.", ".")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=self.profile)
            self.assertFalse(any(handler["handler_id"] == ARTIFACT_HAND_MANA_HANDLER_ID
                                 for face in result.faces for node in face.nodes for handler in node.handlers))
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == ARTIFACT_HAND_MANA_CAPABILITY).update(
            status="blocked", blockers=["Private artifact reveal authority withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        result = compile_oracle_card(card, capability_registry=registry, capability_profile=self.profile)
        self.assertNotEqual("exact", result.status)

    def test_omitting_the_real_reveal_and_amount_is_detected(self):
        with patch("quorune.rules.hand_artifact_mana.commit_artifact_hand_mana_reveal",
                   return_value=normalize_mana_bundle({})):
            with self.assertRaisesRegex(AssertionError, "mana comes from exactly two chosen cards"):
                self.test_real_priority_reveal_selects_current_artifacts_without_stack_or_undo()
