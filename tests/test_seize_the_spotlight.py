from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fame_fortune_choices import FAME_FORTUNE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

SOURCE = "Seize the Spotlight"
PROFILE = "commander_review"


class SeizeTheSpotlightTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "seize-the-spotlight.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/seize-the-spotlight-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Public choices and temporary objects", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, seed, *, flash=False, creatureless=False):
        game = self.session(seed)
        host = game.engine
        spotlight = self.add(host, SOURCE, zone="hand", ref="spotlight-spell")
        if flash:
            self.assert_closed(game, self.add(host, "Vedalken Orrery", ref="spotlight-orrery"))
        creatures = {}
        for seat in "BCD":
            if creatureless and seat == "B":
                continue
            card = self.add(host, "Grizzly Bears", seat=seat, ref=f"spotlight-{seat}-bear")
            card.tapped = True
            creatures[seat] = card
        decline = self.add(host, "Grizzly Bears", seat="B", ref="spotlight-B-unselected") if not creatureless else None
        wrong_type = self.add(host, "Sol Ring", seat="C", ref="spotlight-C-artifact")
        hidden = self.add(host, "Grizzly Bears", seat="A", zone="library", ref="spotlight-drawn-private")
        hidden.known_to = []
        hidden.revealed_to = []
        self.assert_closed(game, spotlight)
        self.prepare(game, mana={"C": 2, "R": 1}, active="C" if flash else "A")
        self.checkpoint(game)
        self.cast_for(game, "A", spotlight)
        decision = self.resolve(game)
        self.assertEqual("semantic.choice", decision.kind)
        return game, creatures, decline, wrong_type, hidden

    def choice(self, game, seat, **fields):
        self.assertEqual((seat,), tuple(game.state.pending_decision.actors))
        result = game.act("pilot:" + seat, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def treasures(self, game):
        return [card for card in game.state.cards.values()
                if card.is_token and card.zone == "battlefield" and card.printed_name.startswith("Treasure")]

    def public_votes(self, game):
        return [(event.actor, event.details["choice"]) for event in game.state.events
                if event.code == "fame_fortune.choose"]

    def test_flash_respects_active_player_order_then_caster_steals_exact_fame_creatures(self):
        game, creatures, unselected, wrong_type, hidden = self.start(7314700, flash=True)
        self.assertEqual("C", game.state.active_player)
        self.reject(game, "A", {"action_id": "choose", "choice": "fame"})
        self.reject(game, "C", {"action_id": "choose", "choice": "decline"})
        self.reject(game, "C", {"action_id": "choose", "choice": "fame", "objects": [creatures["C"].ref]})
        self.choice(game, "C", choice="fame")
        self.assertEqual([("C", "fame")], self.public_votes(game))
        packet = game.packet("pilot:D", full=True)
        self.assertIn("C chose fame", json.dumps(packet))
        game = self.replay(game, load=True)
        self.choice(game, "D", choice="fortune")
        self.choice(game, "B", choice="fame")
        self.assertEqual([("C", "fame"), ("D", "fortune"), ("B", "fame")], self.public_votes(game))
        self.assertEqual([], self.treasures(game))
        self.assertEqual("library", game.state.cards[hidden.object_id].zone)
        self.reject(game, "A", {"action_id": "choose", "objects": [wrong_type.ref]})
        self.reject(game, "A", {"action_id": "choose", "objects": [creatures["B"].ref]})
        self.choice(game, "A", objects=[creatures["C"].ref])
        self.assertEqual("C", game.state.cards[creatures["C"].object_id].controller)
        game = self.replay(game, load=True)
        self.choice(game, "A", objects=[creatures["B"].ref])
        for seat in "BC":
            current = game.state.cards[creatures[seat].object_id]
            self.assertEqual(("A", seat, False), (current.controller, current.owner, current.tapped))
            self.assertIn("haste", game.engine._combat_keywords(current))
        self.assertEqual("B", game.state.cards[unselected.object_id].controller)
        treasure, = self.treasures(game)
        self.assertEqual("A", treasure.controller)
        self.assertEqual(("A", "hand"), (game.state.cards[hidden.object_id].owner, game.state.cards[hidden.object_id].zone))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        sequence = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > sequence)
        for seat in "BC":
            current = game.state.cards[creatures[seat].object_id]
            self.assertEqual(seat, current.controller)
            self.assertNotIn("haste", game.engine._combat_keywords(current))
        self.replay(game, load=True)

    def test_mandatory_fame_without_creature_and_confirmed_departure_leave_other_results_intact(self):
        game, creatures, _, _, hidden = self.start(7314701, creatureless=True)
        self.choice(game, "B", choice="fame")
        self.reject(game, "C", {"action_id": "concede", "confirm_concede": False})
        self.reject(game, "D", {"action_id": "concede", "confirm_concede": True})
        reply = game.act("pilot:C", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(reply.ok, reply.summary)
        self.assertFalse(game.state.players["C"].in_game)
        game = self.replay(game, load=True)
        self.choice(game, "D", choice="fortune")
        self.assertEqual([("B", "fame"), ("D", "fortune")], self.public_votes(game))
        self.assertEqual("D", game.state.cards[creatures["D"].object_id].controller)
        self.assertEqual(1, len(self.treasures(game)))
        self.assertEqual("hand", game.state.cards[hidden.object_id].zone)
        self.replay(game, load=True)

    def test_original_spell_refuses_changed_reward_or_withdrawn_choice_authority(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(card, **options).status)
        report, semantics = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn(FAME_FORTUNE_CAPABILITY, program.capability_dependencies)
        effect, = deepcopy(thaw_value(program.effects))
        effect["treasure"]["quantity"] = 2
        report = bind_semantic_program_runtime(replace(program, effects=[effect]),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(report["strict"], report)
        deps = tuple(cap for cap in program.capability_dependencies if cap != FAME_FORTUNE_CAPABILITY)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + FAME_FORTUNE_CAPABILITY, report["blockers"])
        with patch("quorune.compiler.fame_fortune.fame_fortune_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)
