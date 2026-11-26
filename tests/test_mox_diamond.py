"""Native private land cost before the original artifact enters."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class EntryLandDiscardTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-land-discard.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-land-discard-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Entry land discard", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_mox_diamond_exact_entry_rejects_changed_price_or_declined_destination(self):
        card = self.db.lookup("Mox Diamond", fuzzy=False)
        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", compile_card(card).status)
        for changed in (card.oracle_text.replace("land card", "artifact card"),
                        card.oracle_text.replace("owner's graveyard", "owner's hand")):
            with self.subTest(changed=changed):
                self.assertNotEqual("exact", compile_card(replace(card, oracle_text=changed)).status)

    def test_actual_mox_diamond_private_discard_commits_before_entry_with_replay(self):
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                game = self.session(17631110 + replaced)
                source = self.add(game.engine, "Mox Diamond", zone="hand", ref="attempted-source")
                land = self.add(game.engine, "Plains", zone="hand", ref="qualified-private-land")
                other = self.add(game.engine, "Sol Ring", zone="hand", ref="invalid-private-artifact")
                if replaced:
                    self.add(game.engine, "Rest in Peace", seat="B")
                self.prepare(game, mana={"C": 0})
                self.assert_closed(game, source)
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.assertEqual("replacement.order", self.resolve(game).kind)
                self.assertEqual("stack", game.state.cards[source.object_id].zone)
                self.assertEqual("hand", game.state.cards[land.object_id].zone)
                decision = game.packet("pilot:A", full=True)["decision"]
                option, = (row for row in decision["ctx"]["options"] if land.ref in json.dumps(row))
                self.assertNotIn(other.ref, json.dumps(decision))
                command = {"action_id": "choose", "replacement": option["id"]}
                self.reject(game, "C", command)
                self.reject(game, "A", {"action_id": "choose", "replacement": other.ref})
                for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
                    self.assertNotIn(land.ref, json.dumps(game.packet(principal, full=True)))
                game = self.replay(game, load=True)
                self.act(game, "A", command)
                self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                self.assertEqual("exile" if replaced else "graveyard", game.state.cards[land.object_id].zone)
                self.assertEqual("hand", game.state.cards[other.object_id].zone)
                self.priority(game, "A")
                actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                action, = (row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
                self.act(game, "A", {"action_id": action["id"], "mana_output": {"G": 1}})
                self.assertEqual(1, game.state.players["A"].mana_pool["G"])
                self.replay(game, load=True)

    def test_actual_mox_diamond_decline_never_enters_and_preserves_private_land(self):
        game = self.session(17631120)
        source = self.add(game.engine, "Mox Diamond", zone="hand")
        land = self.add(game.engine, "Plains", zone="hand", ref="declined-private-land")
        self.prepare(game, mana={"C": 0})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        decision = game.packet("pilot:A", full=True)["decision"]
        option, = (row for row in decision["ctx"]["options"] if "Decline" in json.dumps(row))
        game = self.replay(game, load=True)
        self.act(game, "A", {"action_id": "choose", "replacement": option["id"]})
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("hand", game.state.cards[land.object_id].zone)
        self.assertNotIn(land.ref, json.dumps(game.packet("spectator", full=True)))
        self.assertNotIn(source.ref, {row.ref for row in game.engine.available_mana_sources("A")})
        self.replay(game, load=True)

    def test_simultaneous_entry_reserves_a_land_once_before_mutation(self):
        from quorune.replacement import ReplacementChoiceRequired
        from quorune.semantic_runtime.zone_replacements import (
            capture_zone_change_replacement_snapshot, prepare_zone_change_replacement_snapshot,
        )

        game = self.session(17631130)
        first = self.add(game.engine, "Mox Diamond", zone="graveyard", ref="first-entry")
        second = self.add(game.engine, "Mox Diamond", zone="graveyard", ref="second-entry")
        land = self.add(game.engine, "Plains", zone="hand", ref="single-land")
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            if object_id != land.object_id:
                game.engine.move_card(object_id, "library", log=False)
        before = authoritative_state_hash(game.state)
        snapshot = capture_zone_change_replacement_snapshot(
            game.engine, ((first.object_id, "battlefield"), (second.object_id, "battlefield")),
        )
        with self.assertRaises(ReplacementChoiceRequired) as pending:
            prepare_zone_change_replacement_snapshot(snapshot, host=game.engine)
        choice = pending.exception.pending.choice
        paid, = (effect.effect_id for effect in snapshot.effects
                 if effect.effect_id in choice.options and effect.label.startswith("Discard"))
        prepared = prepare_zone_change_replacement_snapshot(snapshot, host=game.engine, selections=(paid,))
        successes = [plan for plan in prepared.values() if plan.destination == "battlefield"]
        failures = [plan for plan in prepared.values() if plan.destination == "graveyard"]
        self.assertEqual((1, 1), (len(successes), len(failures)))
        self.assertEqual(land.object_id, successes[0].entry_land_discard.object_id)
        self.assertIsNone(failures[0].entry_land_discard)
        self.assertEqual(before, authoritative_state_hash(game.state))
