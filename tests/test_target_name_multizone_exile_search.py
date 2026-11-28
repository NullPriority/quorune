"""Native tests for exile searches keyed by the original target's name and controller."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.targeted_library_exile import (
    TARGET_NAME_MULTIZONE_EXILE_CAPABILITY, TargetNameMultizoneExileSpec,
)
from scripts.build_test_database import build_fixture_database

PROFILE = "commander_review"


class TargetNameMultizoneExileSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "target-name-multizone-exile-search.sqlite3"
        fixture = ROOT / "tests/fixtures/target-name-multizone-exile-search-cards.json"
        build_fixture_database([fixture], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Native same-name exile", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Forest", 30),
        ], [commander])

    def begin(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def shuffles(self, game):
        return {seat: game.state.players[seat].stats.get("shuffle_count", 0) for seat in "ABCD"}

    def assert_only_shuffled(self, game, before, seat):
        self.assertEqual({key: value + int(key == seat) for key, value in before.items()},
                         self.shuffles(game))

    def assert_private(self, game, *cards, pending=False):
        for seat in "CD":
            packet = game.packet("pilot:" + seat, full=True)
            if pending:
                self.assertIsNone(packet["decision"])
            rendered = json.dumps(packet)
            for card in cards:
                self.assertNotIn(card.ref, rendered)
                self.assertNotIn(self.current(game, card).ref, rendered)

    def search_offer(self, game):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
        visible = game.packet("pilot:A", full=True)["decision"]
        action, = visible["legal_actions"]
        return action["id"], {row["id"] for row in visible["ctx"]["search_cards"]}

    def choose(self, game, action, *cards):
        command = {"action_id": action, "search_cards": [card.ref for card in cards]}
        self.reject(game, "B", command)
        game = self.replay(game, load=True)
        self.act(game, "A", command)
        self.assertIsNone(self.resolve(game))
        return game

    def copies(self, game, name, *, seat, prefix, count_grave=1):
        grave = tuple(self.add(game.engine, name, seat=seat, zone="graveyard", ref=f"{prefix}-grave-{index}")
                      for index in range(count_grave))
        hand = self.add(game.engine, name, seat=seat, zone="hand", ref=f"{prefix}-hand")
        library = self.add(game.engine, name, seat=seat, zone="library", ref=f"{prefix}-library")
        return grave, hand, library

    def test_actual_four_spells_exile_required_public_matches_and_only_chosen_hidden_match(self):
        cases = (
            ("Splinter", "Sol Ring", "G"),
            ("Eradicate", "Grizzly Bears", "B"),
            ("Sowing Salt", "Reliquary Tower", "R"),
            ("Scour", "Fervor", "W"),
        )
        for index, (spell_name, match_name, color) in enumerate(cases):
            with self.subTest(spell=spell_name):
                game = self.session(61599001 + index)
                spell = self.add(game.engine, spell_name, zone="hand")
                target = self.add(game.engine, match_name, seat="B", ref="exile-original-target")
                other = self.add(game.engine, match_name, seat="B", ref="exile-untargeted-field-match")
                grave, hand, library = self.copies(game, match_name, seat="B",
                                                   prefix="exile-target-matches", count_grave=2)
                illegal_target = self.add(game.engine, "Forest", seat="B", ref="exile-illegal-target")
                wrong_name = self.add(game.engine, "Forest", seat="B", zone="hand", ref="exile-private-foil")
                self.begin(game, spell, target, other, *grave, hand, library, illegal_target, wrong_name,
                           mana={"C": 2, color: 2})
                before = self.shuffles(game)
                offer = self.action(game, "A", spell)
                self.reject(game, "A", {"action_id": offer["id"], "targets": [illegal_target.ref], "pay": "auto"})
                self.cast(game, "A", spell, [target.ref])
                action, candidates = self.search_offer(game)
                self.assertEqual("exile", self.current(game, target).zone)
                self.assertEqual(["graveyard", "graveyard"], [self.current(game, row).zone for row in grave])
                self.assertEqual({hand.ref, library.ref}, candidates)
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                self.reject(game, "A", {"action_id": action, "search_cards": [grave[0].ref]})
                self.assert_private(game, hand, library, wrong_name, pending=True)
                self.reject(game, "A", {"action_id": action, "search_cards": [wrong_name.ref]})
                game = self.choose(game, action, library)
                self.assertEqual(["exile"] * 4, [self.current(game, row).zone for row in (target, *grave, library)])
                self.assertEqual("battlefield", self.current(game, other).zone)
                self.assertEqual("battlefield", self.current(game, illegal_target).zone)
                self.assertEqual("hand", self.current(game, hand).zone)
                self.assertEqual("hand", self.current(game, wrong_name).zone)
                self.assert_private(game, hand, wrong_name)
                self.assert_only_shuffled(game, before, "B")
                self.replay(game, load=True)

    def test_actual_coated_creature_uses_effective_name_without_matching_card_type(self):
        game = self.session(61599005)
        spell = self.add(game.engine, "Splinter", zone="hand")
        coating = self.add(game.engine, "Liquimetal Coating")
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="exile-coated-bear")
        grave, hand, library = self.copies(game, "Grizzly Bears", seat="B", prefix="exile-coating-matches")
        self.begin(game, spell, coating, target, *grave, hand, library, mana={"C": 2, "G": 2})
        before = self.shuffles(game)
        offer = self.action(game, "A", spell)
        self.reject(game, "A", {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activate, = (row for row in rows if row["id"].startswith(f"activate:{coating.ref}:"))
        self.act(game, "A", {"action_id": activate["id"], "targets": [target.ref], "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        data = game.engine._effective_card_data(self.current(game, target))
        types, _, _ = game.engine._type_parts(data["type_line"])
        self.assertEqual({"artifact", "creature"}, set(types))
        self.assertEqual("Grizzly Bears", data["name"])

        self.cast(game, "A", spell, [target.ref])
        action, candidates = self.search_offer(game)
        self.assertLessEqual({hand.ref, library.ref}, candidates)
        game = self.choose(game, action, hand)
        self.assertEqual(["exile"] * 3, [self.current(game, row).zone for row in (target, *grave, hand)])
        self.assertEqual("library", self.current(game, library).zone)
        self.assert_private(game, library)
        self.assert_only_shuffled(game, before, "B")
        self.replay(game, load=True)

    def test_actual_control_change_searches_and_shuffles_controller_not_owner(self):
        game = self.session(61599006)
        steal = self.add(game.engine, "Act of Treason", zone="hand")
        spell = self.add(game.engine, "Eradicate", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="exile-stolen-target")
        a_grave, a_hand, a_library = self.copies(game, "Grizzly Bears", seat="A", prefix="exile-controller")
        b_grave, b_hand, b_library = self.copies(game, "Grizzly Bears", seat="B", prefix="exile-owner")
        self.begin(game, steal, spell, target, *a_grave, a_hand, a_library, *b_grave, b_hand, b_library,
                   mana={"C": 6, "R": 2, "B": 4})
        before = self.shuffles(game)
        owner_library = tuple(game.state.players["B"].zones["library"])
        self.cast(game, "A", steal, [target.ref])
        self.assertIsNone(self.resolve(game))
        stolen = self.current(game, target)
        self.assertEqual(("B", "A"), (stolen.owner, stolen.controller))

        self.cast(game, "A", spell, [stolen.ref])
        action, candidates = self.search_offer(game)
        self.assertLessEqual({a_hand.ref, a_library.ref}, candidates)
        self.assertTrue(candidates.isdisjoint({b_hand.ref, b_library.ref, *(card.ref for card in b_grave)}))
        self.assert_private(game, a_hand, a_library, b_hand, b_library, pending=True)
        self.reject(game, "A", {"action_id": action, "search_cards": [b_library.ref]})
        game = self.choose(game, action, a_library)
        self.assertEqual(["exile"] * 3, [self.current(game, row).zone for row in (target, *a_grave, a_library)])
        self.assertEqual(["graveyard", "hand", "library"],
                         [self.current(game, row).zone for row in (*b_grave, b_hand, b_library)])
        self.assertEqual("hand", self.current(game, a_hand).zone)
        self.assertEqual(owner_library, tuple(game.state.players["B"].zones["library"]))
        self.assert_only_shuffled(game, before, "A")
        self.assert_private(game, b_hand, b_library, a_hand)
        self.replay(game, load=True)

    def test_actual_mindlock_orb_suppresses_library_search_but_not_the_shuffle(self):
        game = self.session(61599007)
        spell = self.add(game.engine, "Scour", zone="hand")
        orb = self.add(game.engine, "Mindlock Orb", seat="C")
        target = self.add(game.engine, "Fervor", seat="B")
        grave, hand, library = self.copies(game, "Fervor", seat="B", prefix="exile-mindlock", count_grave=2)
        self.begin(game, spell, orb, target, *grave, hand, library, mana={"C": 2, "W": 2})
        before = self.shuffles(game)
        target_library = tuple(game.state.players["B"].zones["library"])
        self.cast(game, "A", spell, [target.ref])
        action, candidates = self.search_offer(game)
        self.assertIn(hand.ref, candidates)
        self.assertNotIn(library.ref, candidates)
        self.assertNotIn(library.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assert_private(game, hand, library, pending=True)
        self.reject(game, "A", {"action_id": action, "search_cards": [library.ref]})
        game = self.choose(game, action, hand)
        self.assertEqual(["exile"] * 4, [self.current(game, row).zone for row in (target, *grave, hand)])
        self.assertEqual("library", self.current(game, library).zone)
        self.assert_only_shuffled(game, before, "B")
        shuffled = tuple(game.state.players["B"].zones["library"])
        self.assertCountEqual(target_library, shuffled)
        self.assertNotEqual(target_library, shuffled)
        self.assert_private(game, library)
        self.replay(game, load=True)

    def test_actual_face_down_eradicate_target_has_no_matches_but_controller_shuffles(self):
        game = self.session(61599008)
        spell = self.add(game.engine, "Eradicate", zone="hand")
        target = self.add(game.engine, "Spitting Gourna", zone="hand", ref="eradicate-nameless-target")
        grave = self.add(game.engine, "Spitting Gourna", zone="graveyard", ref="eradicate-printed-grave")
        library = self.add(game.engine, "Spitting Gourna", zone="library", ref="eradicate-printed-library")
        self.begin(game, spell, target, grave, library, mana={"C": 6, "B": 5})
        before = self.shuffles(game)
        library_before = tuple(game.state.players["A"].zones["library"])

        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        morph, = (row for row in rows if row["id"] == f"cast-morph:{target.ref}")
        self.act(game, "A", {"action_id": morph["id"], "pay": "auto"})
        self.resolve_top(game)
        face_down = self.current(game, target)
        self.assertEqual("battlefield", face_down.zone)
        self.assertTrue(face_down.face_down)
        self.assertEqual("Spitting Gourna", face_down.printed_name)

        self.cast(game, "A", spell, [face_down.ref])
        action, candidates = self.search_offer(game)
        self.assertEqual(set(), candidates)
        self.assertEqual("graveyard", self.current(game, grave).zone)
        self.assertEqual("library", self.current(game, library).zone)
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.assert_private(game, library, pending=True)
        for printed_match in (grave, library):
            self.reject(game, "A", {"action_id": action, "search_cards": [printed_match.ref]})

        game = self.choose(game, action)
        self.assertEqual("exile", self.current(game, target).zone)
        self.assertEqual("graveyard", self.current(game, grave).zone)
        self.assertEqual("library", self.current(game, library).zone)
        self.assertCountEqual(library_before, game.state.players["A"].zones["library"])
        self.assert_only_shuffled(game, before, "A")
        self.assert_private(game, library)
        self.replay(game, load=True)

    def test_four_complete_originals_require_exact_target_and_search_authority(self):
        game = self.session(61599010)
        cap = TARGET_NAME_MULTIZONE_EXILE_CAPABILITY
        for spell, qualifier in (
            (self.add(game.engine, "Splinter", zone="hand"), "artifact"),
            (self.add(game.engine, "Eradicate", zone="hand"), "nonblack creature"),
            (self.add(game.engine, "Sowing Salt", zone="hand"), "nonbasic land"),
            (self.add(game.engine, "Scour", zone="hand"), "enchantment"),
        ):
            with self.subTest(card=spell.printed_name):
                self.assert_closed(game, spell)
                program, = (program for program in game.engine.semantics.programs_for_oracle(spell.oracle_id)
                            if cap in program.capability_dependencies)
                effect, = program.effects
                spec = TargetNameMultizoneExileSpec.from_effect(effect)
                self.assertEqual(qualifier, spec.qualifier)
                self.assertEqual(FrozenMap(spec.target_schema()), FrozenMap(program.target_schema))
                remaining = tuple(key for key in program.capability_dependencies if key != cap)
                altered = replace(program, capability_dependencies=remaining,
                                  capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
                missing = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(missing["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", missing["blockers"])
        card = self.db.lookup("Splinter", fuzzy=False)
        for needle, substitute in (
            ("its controller's", "its owner's"),
            ("graveyard, hand, and library", "battlefield, graveyard, hand, and library"),
            ("as that artifact", "as that enchantment"),
            ("Then that player shuffles.", "Then you shuffle."),
        ):
            with self.subTest(change=substitute):
                changed = replace(card, oracle_text=card.oracle_text.replace(needle, substitute))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)
