"""Public single-card reveal and exact placement through live decisions."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.revealed_library_top_templates import one_revealed_card_placement_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.one_revealed_card_placement import OneRevealedCardPlacement
from scripts.build_test_database import build_fixture_database


LENS = "Fixture Forest Lens"
SIGNAL = "Fixture Sunset Signal"
WATCH = "Fixture Dawn Watch"
FIXTURES = ROOT / "tests" / "fixtures"


class OneRevealedCardPlacementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "one-revealed-card-placement.sqlite3"
        build_fixture_database([
            FIXTURES / "bound-effect-program-cards.json",
            FIXTURES / "one-revealed-card-placement-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixture revealed card", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def game(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        return game

    def top(self, game, name, *, ref="public-placement-card"):
        card = self.add(game.engine, name, zone="library", ref=ref)
        card.revealed_to = []
        self.assert_closed(game, card)
        return card

    def reveals(self, game):
        return [event.details["card"] for event in game.state.events if event.code == "library.top.reveal"]

    def end_step_choice(self, game, source):
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "end_step")
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ("A",)), (choice.kind, tuple(choice.actors)))
        self.assertEqual(source.object_id, game.state.stack[-1].source_object_id)
        return choice

    def choose(self, game, actor, **fields):
        return game.act("pilot:" + actor, {"action_id": "choose", **fields})

    def test_closed_text_and_result_descriptor(self):
        bodies = (
            (LENS, OneRevealedCardPlacement("creature", False, "graveyard")),
            (SIGNAL, OneRevealedCardPlacement("permanent", True, "optional_bottom")),
            (WATCH, OneRevealedCardPlacement("creature", False, "optional_bottom")),
        )
        for name, expected in bodies:
            card = self.db.lookup(name, fuzzy=False)
            body = card.oracle_text.split(":", 1)[-1].strip() if name == LENS else card.oracle_text.split(", ", 1)[1]
            with self.subTest(name=name):
                self.assertEqual((expected.effect(),), one_revealed_card_placement_template(body)[1])
                for unsupported in (
                    body.replace("your library", "their library", 1),
                    body.replace("Reveal", "Look at", 1).replace("reveal", "look at", 1),
                    body.replace("onto the battlefield", "into your hand", 1),
                ):
                    self.assertIsNone(one_revealed_card_placement_template(unsupported))
                effect = expected.effect()
                for malformed in ({**effect, "schema_version": True}, {**effect, "entry_optional": 1},
                                  {**effect, "player": "B"}, {**effect, "combined_result": "extra"}):
                    with self.assertRaises(ValueError):
                        OneRevealedCardPlacement.from_effect(malformed)

    def test_activation_routes_only_the_current_top_to_the_printed_destination(self):
        for index, (name, expected) in enumerate((
            ("Generic Bound Body", "battlefield"), ("Generic Bound Draw Loss", "graveyard"), (None, None),
        )):
            with self.subTest(top=name):
                game = self.game(24661001 + index)
                source = self.add(game.engine, LENS)
                self.assert_closed(game, source)
                if name is None:
                    for object_id in tuple(game.state.players["A"].zones["library"]):
                        game.engine.move_card(object_id, "exile", log=False, semantic_events=False)
                    card = None
                else:
                    card = self.top(game, name)
                self.prepare(game, mana={"C": 1, "G": 1})
                self.checkpoint(game)
                action = game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab1"})
                self.assertTrue(action.ok, action.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual([] if card is None else [name], self.reveals(game))
                if card is not None:
                    self.assertEqual(expected, game.state.cards[card.object_id].zone)
                self.replay(game)

    def test_pending_optional_bottom_preserves_hidden_neighbors_and_rejects_foreign_actor(self):
        game = self.game(24661005)
        source = self.add(game.engine, SIGNAL)
        hidden = self.top(game, "Generic Bound Body", ref="deeper-library-card")
        revealed = self.top(game, "Generic Bound Draw Loss")
        self.assert_closed(game, source)
        choice = self.end_step_choice(game, source)
        options = choice.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]["legal_values"]
        self.assertEqual(["put_on_bottom", "keep"], list(options))
        observer = game.packet("pilot:B", full=True)
        self.assertIsNone(observer["decision"])
        self.assertNotIn(hidden.ref, json.dumps(observer))
        self.assertEqual([revealed.printed_name], self.reveals(game))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        self.assertFalse(self.choose(game, "B", choice="put_on_bottom").ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = self.choose(game, "A", choice="keep")
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(revealed.object_id, game.state.players["A"].zones["library"][-1])
        self.assertEqual([], game.state.cards[revealed.object_id].revealed_to)
        self.assertEqual([revealed.printed_name], self.reveals(game))
        self.replay(game)

    def test_aura_attachment_or_only_legal_fallback_survives_replay(self):
        for available in (False, True):
            with self.subTest(attachment_available=available):
                game = self.game(24661006 + int(available))
                source = self.add(game.engine, SIGNAL)
                target = self.add(game.engine, "Generic Bound Body", seat="B") if available else None
                card = self.top(game, "Control Magic")
                self.assert_closed(game, source)
                choice = self.end_step_choice(game, source)
                choices = choice.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]["legal_values"]
                fallback = ["put_on_bottom", "keep"]
                self.assertEqual((["put_onto_battlefield"] + fallback) if available else fallback, list(choices))
                game = self.replay(game, load=True)
                if available:
                    issued = self.choose(game, "A", choice="put_onto_battlefield")
                    self.assertTrue(issued.ok, issued.summary)
                    pending = self.resolve(game)
                    self.assertEqual(("aura.entry", ("A",)), (pending.kind, tuple(pending.actors)))
                    self.assertEqual("library", game.state.cards[card.object_id].zone)
                    game = self.replay(game, load=True)
                    accepted = self.choose(game, "A", aura_target=target.ref)
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.assertEqual(target.object_id, game.state.cards[card.object_id].attached_to)
                    self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
                else:
                    before = authoritative_state_hash(game.state)
                    invalid = self.choose(game, "A", choice="put_onto_battlefield")
                    self.assertFalse(invalid.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    bottom = self.choose(game, "A", choice="put_on_bottom")
                    self.assertTrue(bottom.ok, bottom.summary)
                    self.assertEqual(card.object_id, game.state.players["A"].zones["library"][0])
                self.assertEqual([card.printed_name], self.reveals(game))
                self.replay(game)
