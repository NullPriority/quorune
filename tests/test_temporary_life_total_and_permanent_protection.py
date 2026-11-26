from __future__ import annotations

"""Temporary life-total and permanent protection through native spell resolution."""

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
from quorune.life_gain_permission import TEMPORARY_LIFE_TOTAL_PROHIBITION_CAPABILITY
from quorune.life_state import available_life_for_payment, prepare_life_payment
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.life_prohibitions import life_payment_prohibited
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Flare of Fortitude"
PROTECTION = {"hexproof", "indestructible"}


class TemporaryLifeTotalAndPermanentProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary life and permanent protection", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "temporary-life-total.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "temporary-life-total-permanent-protection-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "life-gain-prohibition-cards.json",
            fixtures / "mutate-casting-cards.json",
            fixtures / "locked-indestructible-extra-turn-loss-cards.json",
            fixtures / "current-life-effect-amount-cards.json",
            fixtures / "player-life-exchange-cards.json",
        ], path)
        cls.use_database(path)

    def actions(self, game, seat):
        self.priority(game, seat)
        return {row["id"]: row for row in game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]}

    def submit(self, game, seat, command, *, valid=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def keywords(self, game, card):
        current = game.state.cards[card.object_id]
        return {word.casefold() for word in game.engine._effective_card_data(current).get("keywords", ())}

    def test_native_sacrifice_cast_seals_current_permanents_and_expires_at_cleanup(self):
        game = self.session(611_200_101)
        game.state.config.semantic_policy = "trusted_only"
        flare = self.add(game.engine, ORIGINAL, zone="hand")
        price = self.add(game.engine, "Elite Vanguard", ref="flare-price")
        another = self.add(game.engine, "Elite Vanguard", seat="C", ref="flare-other-price")
        bear = self.add(game.engine, "Grizzly Bears", ref="flare-bear")
        ring = self.add(game.engine, "Sol Ring", ref="flare-ring")
        ring.tapped = True
        plains = self.add(game.engine, "Plains", ref="flare-old-land")
        plains.tapped = True
        entrant = self.add(game.engine, "Memnite", zone="hand", ref="flare-late-permanent")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Sol Ring", seat="B", ref="flare-opponent-ring")
        for card in (flare, price, bolt, removal):
            self.assert_closed(game, card)
        token_ref, = game.engine.create_token("A", name="Soldier Token", characteristics={
            "type_line": "Token Creature — Soldier", "colors": ["W"], "power": "1", "toughness": "1",
        })
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1, "R": 1})
        self.checkpoint(game)
        cast = self.actions(game, "A")["cast:" + flare.ref]
        alternative, = (row for row in cast["cost_options"] if row["kind"] == "alternate")
        self.assertEqual([price.ref], alternative["choice_schema"]["sacrifice_cards"]["legal_refs"])
        for viewer in "BCD":
            self.assertNotIn(flare.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        command = {"action_id": cast["id"], "cost_option": alternative["id"], "sacrifice_cards": [price.ref], "pay": "auto"}
        for invalid in (token_ref, another.ref, bear.ref):
            self.submit(game, "A", {**command, "sacrifice_cards": [invalid]}, valid=False)
        self.submit(game, "B", command, valid=False)
        self.submit(game, "A", command)
        self.assertEqual("graveyard", game.state.cards[price.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(life_payment_prohibited(game.engine, "A"))
        self.assertFalse(life_payment_prohibited(game.engine, "B"))
        self.assertEqual(0, available_life_for_payment(game.engine, "A"))
        self.assertEqual(0, prepare_life_payment(game.engine, "A", 0).amount)
        for permanent in (bear, ring, plains):
            self.assertTrue(PROTECTION <= self.keywords(game, permanent), permanent.ref)
        before = game.state.players["A"].life
        game = self.replay(game, load=True)
        self.cast(game, "A", entrant)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[entrant.object_id].zone)
        self.assertFalse(PROTECTION.intersection(self.keywords(game, entrant)))

        self.cast(game, "B", bolt, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, game.state.players["A"].life)
        self.assertTrue(any(event.code == "effect.damage" and event.details.get("target") == "A"
                            and event.details.get("amount") == 3 for event in game.state.events))
        remove = self.actions(game, "B")["cast:" + removal.ref]
        self.submit(game, "B", {"action_id": remove["id"], "targets": [ring.ref], "pay": "auto"}, valid=False)
        self.assertEqual("battlefield", game.state.cards[ring.object_id].zone)
        beginning = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > beginning)
        self.assertFalse(life_payment_prohibited(game.engine, "A"))
        self.assertEqual(before, available_life_for_payment(game.engine, "A"))
        for permanent in (bear, ring, plains, entrant):
            self.assertFalse(PROTECTION.intersection(self.keywords(game, permanent)))
        self.replay(game, load=True)

    def test_native_paid_response_preserves_controller_permanents_through_mass_destruction(self):
        game = self.session(611_200_102)
        game.state.config.semantic_policy = "trusted_only"
        flare = self.add(game.engine, ORIGINAL, zone="hand")
        wrath = self.add(game.engine, "Wrath of God", seat="B", zone="hand")
        protected = self.add(game.engine, "Grizzly Bears", ref="flare-wrath-protected")
        witness = self.add(game.engine, "Grizzly Bears", seat="C", ref="flare-wrath-unprotected")
        self.assert_closed(game, flare)
        self.assert_closed(game, wrath)
        self.prepare(game, actor="B", active="B", mana={"C": 2, "W": 2})
        game.state.players["A"].mana_pool.update({"C": 2, "W": 2})
        self.checkpoint(game)
        self.cast(game, "B", wrath)
        self.cast(game, "A", flare)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[protected.object_id].zone)
        self.assertTrue(PROTECTION <= self.keywords(game, protected))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[protected.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[witness.object_id].zone)
        self.replay(game, load=True)

    def test_complete_temporary_duration_fails_closed_when_authority_or_scope_changes(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        card = self.db.lookup(ORIGINAL)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == TEMPORARY_LIFE_TOTAL_PROHIBITION_CAPABILITY).update(
            status="blocked", blockers=["The exact compound temporary life authority was withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
                            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                                                       capability_profile="commander_review").status)
        for text in (card.oracle_text.replace("end of turn", "your next turn"),
                     card.oracle_text.replace("permanents you control", "all permanents")):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                                            capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_omitting_the_temporary_life_effect_is_detected_by_the_native_witness(self):
        with patch("quorune.semantic_runtime.life_prohibitions.apply_temporary_life_gain_prohibition") as omitted:
            with self.assertRaisesRegex(AssertionError, "False is not true"):
                self.test_native_sacrifice_cast_seals_current_permanents_and_expires_at_cleanup()
        omitted.assert_called_once()
