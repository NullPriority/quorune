"""Complete damage occurrences count actual distinct opponents once."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.controlled_subtype_damage_batch import CAPABILITY, EVENT, controlled_subtype_damage_occurrences
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


class ControlledSubtypeDamageBatchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-subtype-damage-batch.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/controlled-subtype-damage-batch-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Grouped Pirate damage", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_complete_source_and_withdrawn_batch_authority(self):
        source = self.db.lookup("Malcolm, Keen-Eyed Navigator")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        code = compile_oracle_card(source, **options)
        self.assertEqual("exact", code.status, code.material_residuals)
        node, = [ability for ability in code.faces[0].nodes if ability.event == EVENT]
        self.assertIn(CAPABILITY, node.capability_dependencies)
        self.assertEqual("$context.amount", node.effects[0]["quantity"])
        for old, new in (("deal damage to your opponents", "deal combat damage to your opponents"),
                         ("each opponent dealt damage", "each creature dealt damage")):
            changed = replace(source, oracle_text=source.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Test-only grouped damage withdrawal"])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        current = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids={row["test_id"] for row in current["declarations"]})
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        changed = compile_oracle_card(source, **{**options, "capability_registry": registry})
        self.assertNotEqual("exact", changed.status)

    def test_group_uses_only_positive_player_rows_from_the_controlled_subtype(self):
        def final(source, target, amount, **changes):
            return SimpleNamespace(**{
                "source_logical_object_id": source, "target": target, "dealt_amount": amount,
                "was_dealt": amount > 0, "target_kind": "player", "source_zone": "battlefield",
                "source_controller": "A", "source_subtypes": ("pirate",), **changes,
            })

        occurrence, = controlled_subtype_damage_occurrences(
            [
                final("p1@1", "B", 3), final("p2@1", "B", 1), final("p3@1", "C", 2),
                final("p3@1", "D", 0), final("p4@1", "planeswalker", 2, target_kind="permanent"),
                final("p1@1", "A", 2), final("other@1", "D", 2, source_subtypes=("bear",)),
                final("spell@1", "D", 2, source_zone="stack"),
            ],
            event_id="complete-batch", active_seats=frozenset("ABCD"), wanted_subtypes=frozenset({"pirate"}),
        )
        self.assertEqual(("B", "C"), occurrence.opponents)
        self.assertEqual(3, len(occurrence.hits))
        self.assertEqual(2, occurrence.semantic_context()["amount"])

    def game(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, "Malcolm, Keen-Eyed Navigator", ref="group-observer")
        self.assert_closed(game, source)
        return game, source

    def grouped_triggers(self, game):
        return [item for item in game.state.stack if item.context.get("event") == EVENT]

    def treasures(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == "Treasure Token"]

    def test_native_first_strike_and_regular_damage_create_separate_grouped_triggers(self):
        game, source = self.game(1204001)
        d = self.add(game.engine, "Generic First Strike Pirate", ref="first-strike-source")
        b1 = self.add(game.engine, "Generic Ordinary Pirate", ref="regular-b-first")
        b2 = self.add(game.engine, "Generic Draw Pirate", ref="regular-b-second")
        c = self.add(game.engine, "Generic Ordinary Pirate", ref="regular-c")
        other = self.add(game.engine, "Grizzly Bears", ref="unrelated-d")
        self.assert_closed(game, b2)
        self.prepare(game, mana={})
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        declared = {d.ref: "D", b1.ref: "B", b2.ref: "B", c.ref: "C", other.ref: "D"}
        accepted = game.act("pilot:A", {"action_id": "attack", "attackers": declared})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(game, lambda current: bool(self.grouped_triggers(current)))
        first, = self.grouped_triggers(game)
        self.assertEqual(source.object_id, first.source_object_id)
        self.assertEqual(["D"], first.context["damaged_opponents"])
        self.assertEqual(1, first.context["amount"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(1, len(self.treasures(game)))
        self.until(game, lambda current: bool(self.grouped_triggers(current)))
        regular, = self.grouped_triggers(game)
        self.assertNotEqual(first.context["event_id"], regular.context["event_id"])
        self.assertEqual(["B", "C"], regular.context["damaged_opponents"])
        self.assertEqual(3, len(regular.context["damage_hits"]))
        self.assertTrue(any(item.source_object_id == b2.object_id and item.kind == "triggered_ability"
                            for item in game.state.stack))
        self.settle(game)
        self.assertEqual(3, len(self.treasures(game)))
        self.replay(game, load=True)

    def noncombat_scene(self, seed, *, departed=False):
        game, source = self.game(seed)
        dealer = self.add(game.engine, "Generic Damage Pirate", ref="noncombat-dealer")
        answer = self.add(game.engine, "Murder", seat="B", zone="hand") if departed else None
        self.assert_closed(game, dealer)
        self.prepare(game, mana={})
        if departed:
            game.state.players["B"].mana_pool.update({"B": 2, "C": 1})
        self.checkpoint(game)
        packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
        offer = next(row for row in packet["legal"]["actions"] if row["id"].startswith(f"activate:{dealer.ref}:"))
        accepted = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        if departed:
            self.cast(game, "B", answer, [dealer.ref])
            self.resolve_top(game)
            self.assertEqual("graveyard", game.state.cards[dealer.object_id].zone)
        self.resolve_top(game)
        triggers = self.grouped_triggers(game)
        self.assertEqual(1, len(triggers))
        self.assertEqual(source.object_id, triggers[0].source_object_id)
        self.assertEqual(["B", "C", "D"], triggers[0].context["damaged_opponents"])
        self.assertEqual(3, triggers[0].context["amount"])
        self.assertEqual([39, 39, 39], [game.state.players[seat].life for seat in "BCD"])
        return game

    def test_native_noncombat_batch_hits_all_opponents_once_and_replays(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.noncombat_scene(1204002 + 2 * departed, departed=departed)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(3, len(self.treasures(game)))
                self.replay(game, load=True)

    def test_missing_group_dispatch_breaks_native_noncombat_witness(self):
        with patch("quorune.rules.controlled_subtype_damage_batch.controlled_subtype_damage_occurrences", return_value=()):
            with self.assertRaises(AssertionError):
                self.noncombat_scene(1204003)
