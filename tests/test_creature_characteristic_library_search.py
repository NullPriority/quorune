"""Creature power and toughness searches through the original recruiter triggers."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
STAT_CAPABILITY = "library.search.creature_stat_bound"
QUALIFIED_CAPABILITY = "library.search.qualified_card"
OPTIONAL_CAPABILITY = "effect.choice.optional_fixed"


class CreatureCharacteristicLibrarySearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "creature-characteristic-search.sqlite3"
        build_fixture_database(
            ROOT / "tests/fixtures/creature-characteristic-library-search-cards.json", path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Creature characteristic search",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def scene(self, seed, *, optional=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        if optional:
            source = self.add(game.engine, "Recruiter of the Guard", seat="B", zone="hand")
        else:
            source = self.add(game.engine, "Imperial Recruiter", seat="B", zone="hand")
        cards = {
            "bound": self.add(game.engine, "Grizzly Bears", seat="B", zone="library", ref="stat-private-bound"),
            "cda": self.add(game.engine, "Lhurgoyf", seat="B", zone="library", ref="stat-private-cda"),
            "above": self.add(game.engine, "Hill Giant", seat="B", zone="library", ref="stat-private-above"),
            "back": self.add(
                game.engine, "Startled Awake // Persistent Nightmare",
                seat="B", zone="library", ref="stat-private-back",
            ),
            "other_library": self.add(game.engine, "Savannah Lions", zone="library", ref="stat-other-library"),
        }
        self.add(game.engine, "Memnite", seat="C", zone="graveyard")
        self.add(game.engine, "Ornithopter", seat="D", zone="graveyard")
        self.assert_closed(game, source)
        self.assert_closed(game, cards["cda"])
        mana = {"C": 2, ("W" if optional else "R"): 1}
        self.prepare(game, active="B", actor="B", mana=mana)
        self.checkpoint(game)
        return game, source, cards

    def start_trigger(self, game, source, *, optional=False):
        self.cast_for(game, "B", source)
        decision = self.resolve(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        trigger, = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual(("triggered_ability", "B"), (trigger.kind, trigger.controller))
        expected = "semantic.choice" if optional else "semantic.search"
        self.assertEqual((expected, ["B"]), (decision.kind, list(decision.actors)))

    def act_ok(self, game, command):
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)

    def assert_rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def search_refs(self, game):
        decision = game.packet("pilot:B", full=True)["decision"]
        self.assertEqual("semantic.search", decision["kind"])
        action, = (row for row in decision["legal_actions"] if row["id"] == "choose")
        schema = action["choice_schema"]
        self.assertEqual((0, 1), (schema["minimum"], schema["maximum"]))
        self.assertTrue(schema["rules_may_fail_to_find"])
        refs = {row["id"] for row in decision["ctx"]["search_cards"]}
        self.assertEqual(refs, set(schema["legal_refs"]))
        return refs

    def shuffle_count(self, game):
        return game.state.players["B"].stats.get("shuffle_count", 0)

    def test_original_etb_search_reveals_inclusive_characteristics_and_replays(self):
        for optional in (False, True):
            with self.subTest(optional=optional):
                game, source, cards = self.scene(45191300 + optional, optional=optional)
                shuffles = self.shuffle_count(game)
                foreign_library = tuple(game.state.players["A"].zones["library"])
                self.start_trigger(game, source, optional=optional)
                if optional:
                    self.act_ok(game, {"action_id": "choose", "choice": "apply"})
                expected = {cards["bound"].ref}
                if not optional:
                    expected.add(cards["cda"].ref)
                self.assertEqual(expected, self.search_refs(game))
                self.assertEqual(shuffles, self.shuffle_count(game))
                game = self.replay(game, load=True)

                selected = cards["bound" if optional else "cda"]
                self.act_ok(game, {"action_id": "choose", "search_cards": [selected.ref]})
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand", game.state.cards[selected.object_id].zone)
                self.assertIn(selected.object_id, game.state.players["B"].zones["hand"])
                self.assertEqual(shuffles + 1, self.shuffle_count(game))
                self.assertEqual(foreign_library, tuple(game.state.players["A"].zones["library"]))
                event, = (event for event in game.state.events if event.code == "library.search")
                self.assertEqual([selected.ref], event.details["objects"])
                self.assertTrue(event.details["revealed"])
                for seat in "ACD":
                    packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
                    self.assertIn(selected.ref, packet)
                    self.assertIn(selected.printed_name, packet)
                    for card in (cards["bound"], cards["cda"], cards["above"], cards["back"]):
                        if card is not selected:
                            self.assertNotIn(card.ref, packet)
                            self.assertNotIn(card.printed_name, packet)
                self.replay(game)

    def test_optional_decline_and_accepted_fail_to_find_have_distinct_shuffle_results(self):
        for index, (optional, decline) in enumerate(((False, False), (True, False), (True, True))):
            with self.subTest(optional=optional, decline=decline):
                game, source, cards = self.scene(45191310 + index, optional=optional)
                shuffles = self.shuffle_count(game)
                library = tuple(game.state.players["B"].zones["library"])
                self.start_trigger(game, source, optional=optional)
                if optional:
                    choice = "decline" if decline else "apply"
                    self.act_ok(game, {"action_id": "choose", "choice": choice})
                if not decline:
                    self.assertIn(cards["bound"].ref, self.search_refs(game))
                    self.act_ok(game, {"action_id": "choose", "search_cards": []})
                self.assertIsNone(self.resolve(game))
                remaining = tuple(game.state.players["B"].zones["library"])
                if decline:
                    self.assertEqual(library, remaining)
                    self.assertEqual(shuffles, self.shuffle_count(game))
                    self.assertFalse(any(event.code == "library.search" for event in game.state.events))
                else:
                    self.assertCountEqual(library, remaining)
                    self.assertNotEqual(library, remaining)
                    self.assertEqual(shuffles + 1, self.shuffle_count(game))
                    event, = (event for event in game.state.events if event.code == "library.search")
                    self.assertEqual((0, []), (event.details["count"], event.details["objects"]))

    def test_only_trigger_controller_can_submit_or_see_search_candidates(self):
        game, source, cards = self.scene(45191320, optional=True)
        self.start_trigger(game, source, optional=True)
        self.assert_rejected(game, "C", {"action_id": "choose", "choice": "apply"})
        self.act_ok(game, {"action_id": "choose", "choice": "apply"})
        self.assertEqual({cards["bound"].ref}, self.search_refs(game))
        for seat in "ACD":
            packet = game.packet(f"pilot:{seat}", full=True)
            self.assertIsNone(packet["decision"])
            projection = json.dumps(packet)
            for key in ("bound", "cda", "above", "back"):
                self.assertNotIn(cards[key].ref, projection)
                self.assertNotIn(cards[key].printed_name, projection)
        self.assert_rejected(game, "C", {"action_id": "choose", "search_cards": [cards["bound"].ref]})
        for key in ("cda", "above", "back", "other_library"):
            with self.subTest(ineligible=key):
                self.assert_rejected(game, "B", {"action_id": "choose", "search_cards": [cards[key].ref]})
        self.assert_rejected(game, "B", {"action_id": "choose", "search_cards": ["forged-stat-card"]})
        self.act_ok(game, {"action_id": "choose", "search_cards": []})
        self.assertIsNone(self.resolve(game))

    def test_recruiters_require_exact_search_and_independent_capability(self):
        imperial = self.db.lookup("Imperial Recruiter", fuzzy=False)
        guard = self.db.lookup("Recruiter of the Guard", fuzzy=False)
        original_bindings = (
            (imperial, *register_card(self.db, self.registry, "Imperial Recruiter"), False),
            (guard, *register_card(self.db, self.registry, "Recruiter of the Guard"), True),
        )

        def compile_card(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)

        for record, report, semantics, optional in original_bindings:
            with self.subTest(source=record.name):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (item for item in semantics.programs_for_oracle(record.oracle_id)
                            if STAT_CAPABILITY in item.capability_dependencies)
                keys = {STAT_CAPABILITY, QUALIFIED_CAPABILITY, "trigger.event.normalized_zone_change"}
                if optional:
                    keys.add(OPTIONAL_CAPABILITY)
                self.assertLessEqual(keys, set(program.capability_dependencies))
                for withheld in sorted(keys):
                    with self.subTest(withheld=withheld):
                        deps = tuple(key for key in program.capability_dependencies if key != withheld)
                        changed = replace(
                            program, capability_dependencies=deps,
                            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict(),
                        )
                        runtime = bind_semantic_program_runtime(
                            changed, capability_registry=self.registry, profile=PROFILE,
                        )
                        self.assertFalse(runtime["strict"])
                        self.assertIn(f"capability:undeclared_runtime_dependency:{withheld}", runtime["blockers"])

                self.assertEqual("exact", compile_card(record).status)
                for before, after in (
                    ("your library", "target player's library"),
                    ("creature card", "artifact card"),
                    ("or less", "or greater"),
                    ("reveal it, ", ""),
                    ("into your hand", "onto the battlefield"),
                ):
                    with self.subTest(altered_text=after):
                        changed = replace(record, oracle_text=record.oracle_text.replace(before, after))
                        self.assertNotEqual("exact", compile_card(changed).status)
                with patch(
                    "quorune.compiler.qualified_library_search_templates.creature_stat_library_search_template",
                    return_value=None,
                ):
                    self.assertNotEqual("exact", compile_card(record).status)

        vanilla = replace(imperial, oracle_text="Draw a card.", type_line="Sorcery", power=None, toughness=None)
        self.assertEqual("exact", compile_card(vanilla).status)


if __name__ == "__main__":
    unittest.main()
