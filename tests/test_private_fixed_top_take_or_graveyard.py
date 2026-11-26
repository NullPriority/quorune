"""Native witnesses for fixed private top-card take, graveyard, and keep choices."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_library_imprint_model import uses_library_imprint_selection
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_selection_capability_shapes import (
    PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_CAPABILITY,
    validate_private_fixed_top_take_or_graveyard,
)
from scripts.build_test_database import build_fixture_database

PROFILE = "commander_review"


class PrivateFixedTopTakeOrGraveyardTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-fixed-top-take-or-graveyard.sqlite3"
        fixture = ROOT / "tests/fixtures/private-fixed-top-take-or-graveyard-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Native private top card", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Forest", 30),
        ], [commander])

    def begin(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        for seat, pool in (mana or {}).items():
            game.state.players[seat].mana_pool.update(pool)
        self.prepare(game)
        self.checkpoint(game)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def assert_hidden_from_opponents(self, game, top):
        current = game.state.cards[top.object_id]
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(top.ref, packet)
            self.assertNotIn(current.ref, packet)

    def private_offer(self, game, source, top, *, matches):
        self.assertEqual(1, self.trigger_count(game, source))
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        visible = game.packet("pilot:A", full=True)["decision"]
        looked, = visible["ctx"]["objects"]
        self.assertEqual((top.ref, top.printed_name), (looked["id"], looked["name"]))
        action, = visible["legal_actions"]
        schema = action["choice_schema"]
        self.assertEqual("choice", schema["field"])
        expected = (("reveal_and_take",) if matches else ()) + ("put_into_graveyard", "keep")
        self.assertEqual(expected, tuple(schema["legal_values"]))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        self.assert_hidden_from_opponents(game, top)
        return action["id"]

    def choose_top(self, game, action, choice):
        command = {"action_id": action, "choice": choice}
        self.reject(game, "B", command)
        game = self.replay(game, load=True)
        before = len(game.state.events)
        self.act(game, "A", command)
        self.assertIsNone(self.resolve(game))
        if choice == "put_into_graveyard":
            self.assertFalse(any(event.code == "library.look" for event in game.state.events[before:]))
        return game

    def assert_take(self, game, top):
        self.assertEqual("hand", game.state.cards[top.object_id].zone)
        self.assertIn(top.object_id, game.state.players["A"].zones["hand"])
        self.assertEqual(set("ABCD"), set(game.state.cards[top.object_id].revealed_to))

    def assert_kept(self, game, top):
        self.assertEqual("library", game.state.cards[top.object_id].zone)
        self.assertEqual(top.object_id, game.state.players["A"].zones["library"][-1])
        self.assert_hidden_from_opponents(game, top)

    def test_actual_artifact_entry_can_reveal_land_or_put_it_into_graveyard_unrevealed(self):
        for choice in ("reveal_and_take", "put_into_graveyard"):
            with self.subTest(choice=choice):
                game = self.session(61598001 + int(choice == "put_into_graveyard"))
                seeker = self.add(game.engine, "Sarinth Steelseeker")
                ring = self.add(game.engine, "Sol Ring", zone="hand")
                top = self.add(game.engine, "Forest", zone="library", ref="steelseeker-private-top")
                self.begin(game, seeker, ring, mana={"A": {"C": 1}})
                self.assertEqual(top.object_id, game.state.players["A"].zones["library"][-1])
                self.cast(game, "A", ring)
                self.resolve_top(game)
                self.assertEqual("battlefield", game.state.cards[ring.object_id].zone)
                action = self.private_offer(game, seeker, top, matches=True)
                game = self.choose_top(game, action, choice)
                if choice == "reveal_and_take":
                    self.assert_take(game, top)
                else:
                    self.assertEqual("graveyard", game.state.cards[top.object_id].zone)
                    self.assertNotIn(top.object_id, game.state.players["A"].zones["hand"])
                self.replay(game, load=True)

    def test_actual_botanist_attack_offers_only_graveyard_or_keep_for_nonland(self):
        for choice in ("keep", "put_into_graveyard"):
            with self.subTest(choice=choice):
                game = self.session(61598003 + int(choice == "put_into_graveyard"))
                botanist = self.add(game.engine, "Traveling Botanist")
                botanist.acquired_control_turn_count = -1
                top = self.add(game.engine, "Walking Corpse", zone="library", ref="botanist-private-nonland")
                self.begin(game, botanist, top)
                self.assertFalse(game.state.cards[botanist.object_id].tapped)
                self.attack(game, [(botanist, "B")])
                self.assertTrue(game.state.cards[botanist.object_id].tapped)
                action = self.private_offer(game, botanist, top, matches=False)
                self.reject(game, "A", {"action_id": action, "choice": "reveal_and_take"})
                game = self.choose_top(game, action, choice)
                if choice == "keep":
                    self.assert_kept(game, top)
                else:
                    self.assertEqual("graveyard", game.state.cards[top.object_id].zone)
                self.replay(game, load=True)

    def test_actual_land_entry_reveals_eligible_creature_to_hand(self):
        game = self.session(61598005)
        culler = self.add(game.engine, "Territory Culler")
        land = self.add(game.engine, "Forest", zone="hand", ref="culler-played-land")
        top = self.add(game.engine, "Grizzly Bears", zone="library", ref="culler-private-creature")
        self.begin(game, culler, land, top)
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        play, = (row for row in rows if row["id"] == f"play-land:{land.ref}")
        self.act(game, "A", {"action_id": play["id"]})
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        action = self.private_offer(game, culler, top, matches=True)
        game = self.choose_top(game, action, "reveal_and_take")
        self.assert_take(game, top)
        self.replay(game, load=True)

    def test_actual_zombie_death_checks_zombie_type_and_empty_library(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                game = self.session(61598006 + int(empty))
                ghoul = self.add(game.engine, "Archghoul of Thraben")
                murder = self.add(game.engine, "Murder", seat="B", zone="hand", ref="archghoul-murder-zombie")
                if empty:
                    for oid in tuple(game.state.players["A"].zones["library"]):
                        game.engine.move_card(oid, "graveyard", reason="Prepare empty-library boundary",
                                              log=False, semantic_events=False)
                    self.begin(game, ghoul, murder, mana={"B": {"B": 2, "C": 1}})
                    dying = ghoul
                else:
                    zombie = self.add(game.engine, "Walking Corpse", ref="archghoul-dying-zombie")
                    bear = self.add(game.engine, "Grizzly Bears", ref="archghoul-other-creature")
                    first = self.add(game.engine, "Murder", seat="B", zone="hand", ref="archghoul-murder-bear")
                    top = self.add(game.engine, "Walking Corpse", zone="library", ref="archghoul-private-zombie")
                    self.begin(game, ghoul, murder, zombie, bear, first, top, mana={"B": {"B": 4, "C": 2}})
                    self.cast(game, "B", first, [bear.ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[bear.object_id].zone)
                    self.assertEqual(0, self.trigger_count(game, ghoul))
                    dying = zombie
                self.cast(game, "B", murder, [dying.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[dying.object_id].zone)
                if empty:
                    self.assertEqual(1, self.trigger_count(game, ghoul))
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual([], game.state.players["A"].zones["library"])
                    self.assertTrue(game.state.players["A"].in_game)
                else:
                    action = self.private_offer(game, ghoul, top, matches=True)
                    game = self.choose_top(game, action, "reveal_and_take")
                    self.assert_take(game, top)
                self.replay(game, load=True)

    def test_four_complete_originals_require_their_exact_fixed_predicate_and_authority(self):
        cap = PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_CAPABILITY
        game = self.session(61598008)
        for source, types, subtypes in (
            (self.add(game.engine, "Sarinth Steelseeker"), ("land",), ()),
            (self.add(game.engine, "Traveling Botanist"), ("land",), ()),
            (self.add(game.engine, "Territory Culler"), ("creature",), ()),
            (self.add(game.engine, "Archghoul of Thraben"), (), ("zombie",)),
        ):
            with self.subTest(card=source.printed_name):
                self.assert_closed(game, source)
                program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                            if cap in program.capability_dependencies)
                effect, = program.effects
                self.assertFalse(uses_library_imprint_selection(effect))
                query = validate_private_fixed_top_take_or_graveyard(effect)
                self.assertEqual((("library",), types, subtypes), (query.zones, query.types_all, query.subtypes_any))
                self.assertIsNone(program.target_schema)
                remaining = tuple(key for key in program.capability_dependencies if key != cap)
                altered = replace(program, capability_dependencies=remaining,
                                  capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
                missing = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(missing["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", missing["blockers"])
                for change in ({"public_reveal": True}, {"player": "$event.controller"}, {"minimum": 1}):
                    with self.assertRaises(ValueError):
                        validate_private_fixed_top_take_or_graveyard({**effect, **change})
        card = self.db.lookup("Sarinth Steelseeker", fuzzy=False)
        for needle, substitute in (
            ("a land card", "an artifact card"),
            ("you may put it into your graveyard", "put it onto the battlefield"),
            ("top card of your library", "top card of their library"),
        ):
            with self.subTest(change=substitute):
                changed = replace(card, oracle_text=card.oracle_text.replace(needle, substitute))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)
