"""The original domain abilities privately keep at most one current top card."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.query_effect_amount_model import PublicQueryAmountSpec
from quorune.record import authoritative_state_hash
from quorune.resolution_conditions import (
    CONTROLLER_BASIC_LAND_TYPES, FIVE_BASIC_LAND_TYPES_CONDITION,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.variable_library_selection_model import (
    DOMAIN_TOP_SELECTION_VERSION, validate_variable_library_selection,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


VODA = "Voda Sea Scavenger"
SURVEY = "Slimefoot's Survey"
NAEL = "Nael, Avizoa Aeronaut"


class DomainPrivateTopSelectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "domain-private-top-selection.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/domain-private-top-selection-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Count types, then look", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_complete_originals_share_the_domain_partition_and_keep_each_sibling(self):
        for name, event, ordered in (
            (VODA, "permanent.enter.self", ("variable_library_selection",)),
            (SURVEY, "resolve", ("search", "variable_library_selection")),
            (NAEL, "damage.dealt.self", ("variable_library_selection", "apply_if_public_condition")),
        ):
            with self.subTest(card=name):
                result, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(result["strict_capability_ready"], result["blockers"])
                original = self.db.lookup(name, fuzzy=False)
                program, = (row for row in semantics.programs_for_oracle(original.oracle_id) if row.event == event)
                self.assertEqual(ordered, tuple(effect["op"] for effect in program.effects))
                self.assertIsNone(program.target_schema)
                selection, = (effect for effect in program.effects if effect["op"] == "variable_library_selection")
                validate_variable_library_selection(selection)
                self.assertEqual(DOMAIN_TOP_SELECTION_VERSION, selection["schema_version"])
                amount = PublicQueryAmountSpec.from_dict(selection["look_count"])
                self.assertEqual(CONTROLLER_BASIC_LAND_TYPES, amount.quantity)
                self.assertEqual(("library_top", "library_bottom", "random"), (
                    selection["selected_destination"], selection["remainder_destination"], selection["remainder_order"],
                ))
                for change in ({"public_reveal": True}, {"maximum": 2}, {"remainder_order": "chosen"},
                               {"selected_destination": "hand"}, {"look_count": 6}):
                    with self.assertRaises(ValueError):
                        validate_variable_library_selection({**selection, **change})
                if name == NAEL:
                    self.assertEqual(FIVE_BASIC_LAND_TYPES_CONDITION.to_dict(), program.effects[-1]["condition"])

    def add_lands(self, game, names):
        for index, name in enumerate(names):
            self.add(game.engine, name, ref=f"domain-own-{index}")

    def add_private_top(self, game):
        return tuple(self.add(game.engine, name, zone="library", ref=f"domain-look-{index}")
                     for index, name in enumerate(("Island", "Forest", "Mountain", "Swamp", VODA)))

    def inspection(self, game, count):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("fixed_library_selection", ctx["operation"])
        self.assertIn("random order", ctx["prompt"])
        objects = [value["id"] for value in ctx["objects"]]
        library = game.state.players["A"].zones["library"]
        self.assertEqual([game.state.cards[oid].ref for oid in reversed(library[-count:])], objects)
        for other in ("B", "C", "D"):
            packet = game.packet("pilot:" + other, full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(all(ref not in json.dumps(packet) for ref in objects))
        return objects

    def choose(self, game, refs):
        result = game.act("pilot:A", {"action_id": "choose", "cards": list(refs)})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_voda_keeps_one_current_card_and_randomizes_only_its_private_remainder(self):
        game = self.session(30506401)
        voda = self.add(game.engine, VODA, zone="hand", ref="cast-voda")
        self.add_lands(game, ("Forest", "Tropical Island", "Mountain"))
        self.add(game.engine, "Swamp", seat="B")
        self.add_private_top(game)
        self.assert_closed(game, voda)
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", voda)
        chosen_refs = self.inspection(game, 3)
        before = authoritative_state_hash(game.state)
        keep = [chosen_refs[1]]
        for principal, cards in (
            ("pilot:B", keep),
            ("pilot:A", chosen_refs[:2]),
            ("pilot:A", {"top": keep, "bottom": [chosen_refs[2], chosen_refs[0]]}),
            ("pilot:A", ["domain-look-0"]),
        ):
            result = game.act(principal, {"action_id": "choose", "cards": cards})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        old = list(game.state.players["A"].zones["library"])
        original_identities = {oid: game.state.cards[oid].logical_object_id for oid in old[-3:]}
        self.choose(game, keep)
        new = game.state.players["A"].zones["library"]
        self.assertEqual(old[-2], new[-1])
        self.assertEqual({old[-3], old[-1]}, set(new[:2]))
        self.assertEqual(old[:-3], new[2:-1])
        for oid in new[:2]:
            self.assertEqual([], game.state.cards[oid].known_to)
        self.assertEqual(original_identities, {oid: game.state.cards[oid].logical_object_id for oid in old[-3:]})
        self.assertTrue(all(ref not in json.dumps(game.packet("pilot:B", full=True)) for ref in chosen_refs))
        self.replay(game, load=True)

    def test_actual_voda_with_zero_domain_has_no_choice_or_library_movement(self):
        game = self.session(30506402)
        voda = self.add(game.engine, VODA, zone="hand", ref="cast-voda")
        self.add(game.engine, "Tropical Island", seat="B")
        self.add_private_top(game)
        self.assert_closed(game, voda)
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        library = list(game.state.players["A"].zones["library"])
        self.cast_for(game, "A", voda)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(library, game.state.players["A"].zones["library"])
        self.replay(game, load=True)

    def test_actual_survey_searches_for_a_nonbasic_land_then_uses_the_committed_domain(self):
        game = self.session(30506403)
        survey = self.add(game.engine, SURVEY, zone="hand")
        self.add_lands(game, ("Forest", "Forest"))
        self.add_private_top(game)
        found = tuple(self.add(game.engine, name, zone="library", ref=f"domain-find-{index}")
                      for index, name in enumerate(("Tropical Island", "Swamp")))
        self.assert_closed(game, survey)
        self.prepare(game, mana={"C": 4, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", survey)
        search = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (search.kind, search.actors))
        before = game.state.players["A"].stats.get("shuffle_count", 0)
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [card.ref for card in found]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before + 1, game.state.players["A"].stats.get("shuffle_count", 0))
        for card in found:
            current = game.state.cards[card.object_id]
            self.assertEqual(("battlefield", "A", True), (current.zone, current.controller, current.tapped))
        self.inspection(game, 3)
        library = list(game.state.players["A"].zones["library"])
        game = self.replay(game, load=True)
        self.choose(game, ())
        new = game.state.players["A"].zones["library"]
        self.assertEqual(set(library[-3:]), set(new[:3]))
        self.assertEqual(library[:-3], new[3:])
        self.assertEqual("graveyard", game.state.cards[survey.object_id].zone)
        self.replay(game, load=True)

    def test_actual_nael_draws_the_kept_card_only_after_full_domain_combat_damage(self):
        for full in (False, True):
            with self.subTest(full_domain=full):
                game = self.session(30506404 + int(full))
                nael = self.add(game.engine, NAEL)
                nael.acquired_control_turn_count = -1
                self.add_lands(game, ("Tropical Island", "Mountain", "Plains") + (("Swamp",) if full else ()))
                self.add(game.engine, "Swamp", seat="B")
                self.add_private_top(game)
                self.assert_closed(game, nael)
                self.prepare(game)
                self.checkpoint(game)
                life = game.state.players["B"].life
                self.attack(game, [(nael, "B")])
                self.until(game, lambda current: self.trigger_count(current, nael) == 1)
                self.assertEqual(life - 2, game.state.players["B"].life)
                refs = self.inspection(game, 5 if full else 4)
                chosen = next(card for card in game.state.cards.values() if card.ref == refs[2])
                before = list(game.state.players["A"].zones["hand"])
                game = self.replay(game, load=True)
                self.choose(game, [chosen.ref])
                self.assertEqual(before + ([chosen.object_id] if full else []), game.state.players["A"].zones["hand"])
                if not full:
                    self.assertEqual(chosen.object_id, game.state.players["A"].zones["library"][-1])
                self.replay(game, load=True)
