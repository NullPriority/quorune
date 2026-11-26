"""The actual price, target and persistent copy of an X activation stay coupled."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


GARDENS = "The Mycosynth Gardens"


class AnnouncedArtifactCopyTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "announced-artifact-copy.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/announced-artifact-copy-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced artifact copy", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def offers_with_x(self, game, source):
        self.priority(game, "A")
        prefix = f"activate:{source.ref}:"
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in rows if row["id"].startswith(prefix) and row.get("choice_schema", {}).get("x")]

    def submit(self, game, offer, ref, amount, *, seat="A", accepted=True, **payment):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {
            "action_id": offer["id"], "targets": [ref], "x": amount, "pay": "auto", **payment,
        })
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def data(self, game, card):
        return game.engine._effective_card_data(game.state.cards[card.object_id])

    def test_actual_payable_x_filters_tokens_unpayable_and_foreign_models(self):
        game = self.playing(61127275)
        source = self.add(game.engine, GARDENS)
        other_source = self.add(game.engine, GARDENS, ref="second-priced-copy-source")
        one = self.add(game.engine, "Sol Ring")
        zero = self.add(game.engine, "Ornithopter")
        costly = self.add(game.engine, "Darksteel Forge")
        foreign = self.add(game.engine, "Sol Ring", seat="B", ref="foreign-copy-model")
        hidden = self.add(game.engine, "Sol Ring", seat="C", zone="hand", ref="hidden-copy-model")
        land = self.add(game.engine, "Plains", ref="nonartifact-copy-model")
        rich = self.add(game.engine, "Strike It Rich", zone="hand")
        for card in (source, other_source, one, zero, costly, rich):
            self.assert_closed(game, card)
        source.counters["charge"] = 1
        one.counters["charge"] = 2
        physical = (source.object_id, source.logical_object_id, source.zone_timestamp, source.ref)
        self.prepare(game, actor="A", active="A", mana={"R": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", rich)
        self.assertIsNone(self.resolve(game))
        token, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        offer, = self.offers_with_x(game, source)
        self.assertEqual({one.ref: {"minimum": 1, "maximum": 1}, zero.ref: {"minimum": 0, "maximum": 0}},
                         offer["target_schema"]["x_bounds_by_ref"])
        self.assertEqual({one.ref, zero.ref}, set(offer["target_schema"]["legal_refs"]))
        self.assertNotIn(hidden.ref, json.dumps(offer))
        for seat, ref, amount in (("A", one.ref, 0), ("A", one.ref, 2), ("A", zero.ref, True),
                                  ("A", token.ref, 0), ("A", land.ref, 0), ("A", costly.ref, 9),
                                  ("A", foreign.ref, 1), ("A", hidden.ref, 1), ("B", one.ref, 1)):
            self.submit(game, offer, ref, amount, seat=seat, accepted=False)

        self.submit(game, offer, one.ref, 1, pay="manual", payment={"C": 1})
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(1, game.state.stack[-1].x_value)
        self.assertEqual(1, game.state.stack[-1].context["target_schema_override"]["mana_value"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual(physical, (current.object_id, current.logical_object_id, current.zone_timestamp, current.ref))
        self.assertTrue(current.tapped)
        self.assertEqual({"charge": 1}, current.counters)
        self.assertEqual({"charge": 2}, game.state.cards[one.object_id].counters)
        self.assertEqual("Sol Ring", self.data(game, source)["name"])
        self.assertEqual({"artifact"}, game.engine._type_parts(self.data(game, source)["type_line"])[0])

        free, = self.offers_with_x(game, other_source)
        self.submit(game, free, zero.ref, 0)
        self.assertEqual(0, game.state.stack[-1].x_value)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("Ornithopter", self.data(game, other_source)["name"])
        turn = game.state.turn_sequence
        self.until(game, lambda item: item.state.turn_sequence > turn)
        self.assertEqual("Sol Ring", self.data(game, source)["name"])
        self.assertEqual("Ornithopter", self.data(game, other_source)["name"])
        self.replay(game, load=True)

    def test_actual_departure_of_the_target_or_source_prevents_the_copy(self):
        for name in ("Disenchant", "Boomerang"):
            with self.subTest(answer=name):
                game = self.playing(61127276 if name == "Disenchant" else 61127277)
                source = self.add(game.engine, GARDENS)
                model = self.add(game.engine, "Sol Ring")
                answer = self.add(game.engine, name, zone="hand", seat="B")
                for card in (source, model, answer):
                    self.assert_closed(game, card)
                self.prepare(game, actor="A", active="A", mana={"C": 1})
                game.state.players["B"].mana_pool.update({"C": 1, "W": 1, "U": 2})
                self.checkpoint(game)
                offer, = self.offers_with_x(game, source)
                self.submit(game, offer, model.ref, 1)
                self.assertTrue(game.state.cards[source.object_id].tapped)
                target = model if name == "Disenchant" else source
                self.cast_for(game, "B", answer, [target.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(GARDENS, self.data(game, source)["name"])
                self.assertEqual("graveyard" if name == "Disenchant" else "hand",
                                 game.state.cards[target.object_id].zone)
                self.replay(game, load=True)

    def test_actual_artifact_destruction_binds_exact_x_and_double_x_price(self):
        game = self.playing(61127280)
        source = self.add(game.engine, "Gorilla Shaman")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        creature = self.add(game.engine, "Ornithopter", seat="B")
        expensive = self.add(game.engine, "Darksteel Forge", seat="C")
        hidden = self.add(game.engine, "Sol Ring", seat="C", zone="hand", ref="hidden-shaman-possible-artifact")
        rich = self.add(game.engine, "Strike It Rich", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, actor="A", active="A", mana={"C": 4, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", rich)
        self.assertIsNone(self.resolve(game))
        treasure, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        offer, = self.offers_with_x(game, source)
        self.assertEqual(2, offer["cost_summary"]["announced_x_symbols"])
        self.assertEqual({"GENERIC": 1}, offer["cost_summary"]["m"])
        self.assertEqual({treasure.ref: {"minimum": 0, "maximum": 0}, ring.ref: {"minimum": 1, "maximum": 1}},
                         offer["target_schema"]["x_bounds_by_ref"])
        self.assertNotIn(hidden.ref, json.dumps(offer))
        for seat, ref, amount in (("A", ring.ref, 0), ("A", ring.ref, 2), ("A", ring.ref, True),
                                  ("A", creature.ref, 0), ("A", expensive.ref, 9), ("A", hidden.ref, 1),
                                  ("B", ring.ref, 1)):
            self.submit(game, offer, ref, amount, seat=seat, accepted=False)
        self.submit(game, offer, ring.ref, 1, accepted=False, pay="manual", payment={"C": 2})
        self.submit(game, offer, ring.ref, 1, pay="manual", payment={"C": 3})
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual(1, game.state.stack[-1].x_value)
        self.assertEqual(1, game.state.stack[-1].context["target_schema_override"]["mana_value"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[ring.object_id].zone)

        free_x, = self.offers_with_x(game, source)
        self.submit(game, free_x, treasure.ref, 0, pay="manual", payment={"C": 1})
        self.assertEqual(0, game.state.stack[-1].x_value)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(card.object_id == treasure.object_id and card.zone == "battlefield"
                             for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_actual_artifact_destruction_revalidates_value_after_a_copy_response(self):
        game = self.playing(61127281)
        source = self.add(game.engine, "Gorilla Shaman")
        mirror = self.add(game.engine, "Mirage Mirror", seat="B")
        model = self.add(game.engine, "Sol Ring", seat="C")
        self.assert_closed(game, source)
        self.assert_closed(game, mirror)
        self.prepare(game, actor="A", active="A", mana={"C": 7})
        game.state.players["B"].mana_pool["C"] = 2
        self.checkpoint(game)
        offer, = self.offers_with_x(game, source)
        self.assertEqual({"minimum": 3, "maximum": 3}, offer["target_schema"]["x_bounds_by_ref"][mirror.ref])
        self.submit(game, offer, mirror.ref, 3, pay="manual", payment={"C": 7})
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        response, = (row for row in rows if row["id"].startswith(f"activate:{mirror.ref}:"))
        accepted = game.act("pilot:B", {"action_id": response["id"], "targets": [model.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        self.assertEqual("Sol Ring", self.data(game, mirror)["name"])
        self.assertEqual(mirror.logical_object_id, game.state.cards[mirror.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[mirror.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.replay(game, load=True)

    def test_artifact_destruction_requires_its_printed_price_and_exact_target(self):
        original = self.db.lookup("Gorilla Shaman", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, original.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = [view for view in semantics.programs_for_oracle(original.oracle_id) if view.effects]
        self.assertEqual(2, program.cost_schema["announced_x_symbols"])
        self.assertEqual("equal", program.target_schema["announced_mana_value_limit"]["comparison"])
        for old, new in (("{X}{X}{1}", "{X}{1}"), ("noncreature artifact", "artifact"),
                         ("mana value X.", "mana value X or less."), ("Destroy", "Exile")):
            with self.subTest(new=new):
                modified = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    modified, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        with patch("quorune.compiler.announced_mana_value_target.announced_activation_mana_value_target_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_full_program_retains_both_mana_abilities_and_fails_closed_without_copy_grammar(self):
        original = self.db.lookup(GARDENS, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "The Mycosynth Gardens")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        views = semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual(3, len(views))
        self.assertEqual(2, sum(view.provenance.get("template_id") == "activated-mana-fixed-output-v1" for view in views))
        for old, new in (("{X}, {T}:", "{X}:"), ("nontoken artifact", "artifact"),
                         ("mana value X.", "mana value X or less.")):
            with self.subTest(new=new):
                modified = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    modified, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        with patch("quorune.compiler.temporary_self_copy_templates.announced_artifact_self_copy_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
