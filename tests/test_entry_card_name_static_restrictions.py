"""Native names chosen at entry and their existing public restrictions."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_designations import (
    ENTRY_CARD_NAME_FIELD, ENTRY_CARD_NAME_HANDLER_ID, EntryCardNameChoice, resolve_entry_card_name,
)
from quorune.record import authoritative_state_hash
from quorune.replacement.model import ReplaceableEvent, ReplacementClass, ReplacementEffect, ReplacementEventBatch
from quorune.replacement.ordering import advance_replacement_batch, replacement_choice_payload, resolve_replacement_batch
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Meddling Mage", "Nevermore", "Pithing Needle", "Phyrexian Revoker")


class EntryCardNameStaticRestrictionsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "entry-card-name-static-restrictions.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-card-name-static-restrictions-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original entry names", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        record = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return semantics.programs_for_oracle(record.oracle_id)

    def test_complete_original_pairs_and_oracle_face_name_filters(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                programs = self.programs(name)
                entry, = (program for program in programs if program.event == "zone.change")
                descriptor, = entry.handlers
                self.assertEqual(ENTRY_CARD_NAME_HANDLER_ID, descriptor["handler_id"])
                self.assertEqual(name != "Pithing Needle", descriptor["nonland"])
                restriction = "cast.static.rule" if name in ORIGINALS[:2] else "activation.permission"
                self.assertIn(restriction, {program.event for program in programs})
        cases = (("  island ", False, "Island"), ("fIrE", True, "Fire"), ("Ice", True, "Ice"),
                 ("Bala Ged Recovery", True, "Bala Ged Recovery"), ("Bala Ged Sanctuary", False, "Bala Ged Sanctuary"))
        for submitted, nonland, expected in cases:
            self.assertEqual(expected, resolve_entry_card_name(self.db, submitted, nonland=nonland))
        for submitted, nonland in (("Island", True), ("Bala Ged Sanctuary", True),
                                   ("Fire // Ice", False), ("Human", False), ("Unknown entry name", False)):
            with self.assertRaises(ValueError):
                resolve_entry_card_name(self.db, submitted, nonland=nonland)

    def test_mandatory_single_choice_journals_the_name_and_replays_it(self):
        event = ReplaceableEvent("entry", "zone.change", "A",
                                 {"destination": "battlefield", "origin": "stack", "object_ref": "source"})
        effect = ReplacementEffect("entry-name", "source", "zone.change", ReplacementClass.OTHER,
                                   operations=(EntryCardNameChoice(True),))
        start = ReplacementEventBatch("name-batch", (event,), ("A",))
        pending = advance_replacement_batch(start, (effect,)).pending
        self.assertIsNotNone(pending)
        view = replacement_choice_payload(pending, (effect,))
        self.assertEqual({"entry-name": {"nonland": True}}, view["card_name_choices"])
        result = advance_replacement_batch(start, (effect,), selections=({
            "effect_id": "entry-name", "card_name": "Shock",
        },)).batch
        self.assertEqual("Shock", result.events[0].payload[ENTRY_CARD_NAME_FIELD])
        self.assertEqual("Shock", result.journal[0].card_name)
        self.assertEqual(result, resolve_replacement_batch(start, (effect,), selections=result.journal))

    def choose_name(self, game, source, name, *, nonland):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertNotEqual("battlefield", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["card_name_choices"]
        effect_id, = choices
        self.assertEqual({"nonland": nonland}, choices[effect_id])
        command = {"action_id": "choose", "replacement": effect_id, "card_name": name}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        rejected = ("Unknown entry name", "Fire // Ice", "Island") if nonland else ("Human",)
        for invalid in rejected:
            self.assertFalse(game.act("pilot:A", {**command, "card_name": invalid}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(("battlefield", name), (game.state.cards[source.object_id].zone,
                                                 game.state.cards[source.object_id].annotations.get("chosen_name")))
        return game

    def test_actual_mage_and_nevermore_prohibit_only_the_named_cast_until_departure(self):
        for source_name, mana in (("Meddling Mage", {"W": 1, "U": 1}), ("Nevermore", {"C": 1, "W": 2})):
            with self.subTest(source=source_name):
                game = self.session(20140601 + len(source_name))
                source = self.add(game.engine, source_name, zone="hand")
                shot = self.add(game.engine, "Shock", seat="B", zone="hand", ref="named-shock-private")
                other = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand")
                bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
                for slot in range(2):
                    self.add(game.engine, "Island", seat="B", ref=f"name-b-island-{slot}")
                self.add(game.engine, "Mountain", seat="B", ref="name-b-mountain")
                self.add(game.engine, "Mountain", seat="C", ref="name-c-mountain")
                for card in (source, shot, other, bounce):
                    self.assert_closed(game, card)
                self.prepare(game, mana=mana)
                self.checkpoint(game)
                self.cast_for(game, "A", source)
                self.priority(game, "B")
                previous = self.action(game, "B", shot)["id"]
                game = self.choose_name(game, source, "Shock", nonland=True)
                self.priority(game, "B")
                available = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                self.assertFalse(any(row.get("card") == shot.ref for row in available))
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", {"action_id": previous, "targets": ["C"], "pay": "auto"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.assertNotIn(shot.ref, json.dumps(game.packet("pilot:D", full=True)))
                self.priority(game, "C")
                self.assertIsNotNone(self.action(game, "C", other))
                self.cast_for(game, "B", bounce, [source.ref])
                self.resolve_top(game)
                self.assertEqual("hand", game.state.cards[source.object_id].zone)
                self.cast_for(game, "B", shot, ["C"])
                self.resolve_top(game)
                self.assertEqual(38, game.state.players["C"].life)
                self.replay(game, load=True)

    def test_actual_needle_allows_the_named_mana_ability_and_revoker_prohibits_it(self):
        for source_name, cost, name in (("Pithing Needle", 1, "Sol Ring"), ("Phyrexian Revoker", 2, "Sol Ring"),
                                        ("Pithing Needle", 1, "Island")):
            with self.subTest(source=source_name, chosen=name):
                game = self.session(20140901 + cost + len(name))
                source = self.add(game.engine, source_name, zone="hand")
                ring = self.add(game.engine, "Sol Ring", seat="B")
                for card in (source, ring):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": cost})
                self.checkpoint(game)
                self.cast_for(game, "A", source)
                self.priority(game, "B")
                rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                action, = [row["id"] for row in rows if row["id"].startswith(f"activate:{ring.ref}:")]
                game = self.choose_name(game, source, name, nonland=source_name == "Phyrexian Revoker")
                self.priority(game, "B")
                before = authoritative_state_hash(game.state)
                used = game.act("pilot:B", {"action_id": action, "pay": "auto"})
                if source_name == "Phyrexian Revoker":
                    rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                    self.assertNotIn(action, [row["id"] for row in rows])
                    self.assertFalse(used.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                else:
                    self.assertTrue(used.ok, used.summary)
                    self.assertEqual(2, game.state.players["B"].mana_pool["C"])
                    self.assertTrue(game.state.cards[ring.object_id].tapped)
                self.replay(game, load=True)

    def test_actual_needle_prohibits_cycling_from_hand_without_revealing_the_named_card(self):
        game = self.session(20140941)
        source = self.add(game.engine, "Pithing Needle", zone="hand")
        expunge = self.add(game.engine, "Expunge", seat="B", zone="hand", ref="named-ex-punge-private")
        for slot in range(2):
            self.add(game.engine, "Plains", seat="B", ref=f"needle-private-mana-{slot}")
        self.assert_closed(game, source)
        self.assert_closed(game, expunge)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith(f"activate:{expunge.ref}:")]
        game = self.choose_name(game, source, "Expunge", nonland=False)
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(action, [row["id"] for row in rows])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": action, "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual("hand", game.state.cards[expunge.object_id].zone)
        self.assertNotIn(expunge.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_reviewed_name_only_record_replays_its_resolution_choice(self):
        game = self.session(20149003)
        register_current = self.register_fixture_cards_on_add
        try:
            self.register_fixture_cards_on_add = False
            source = self.add(game.engine, "Pithing Needle", zone="hand")
        finally:
            self.register_fixture_cards_on_add = register_current
        legacy = game.engine.semantics.get(source.oracle_id + ":spell:front")
        self.assertEqual("reviewed", legacy.provenance["review_status"])
        self.assertEqual(({"op": "choose_card_name"},), tuple(dict(effect) for effect in legacy.effects))
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        chosen = game.act("pilot:A", {"action_id": "choose", "card_name": "Sol Ring"})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("Sol Ring", game.state.cards[source.object_id].annotations["chosen_name"])
        self.replay(game, load=True)
