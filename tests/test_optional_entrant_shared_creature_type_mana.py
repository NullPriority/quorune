"""Whole-card and native entry-trigger evidence for optional counted colorless mana."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import (
    ENTRY_SHARED_TYPE_AMOUNT_CAPABILITY, PublicQueryAmountSpec, entry_shared_type_amount_spec,
)
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.entry_creature_type_mana import (
    ENTRY_CREATURE_TYPES_CONTEXT, entry_shared_type_mana_runtime_dependencies,
)
from quorune.shared_creature_type_model import SharedCreatureTypeObject
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ECHOES = "Mana Echoes"
PROFILE = "commander_review"


class OptionalEntrantSharedCreatureTypeManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entrant-type-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/optional-entrant-shared-creature-type-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Entrant type mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def count_colorless(self, game, seat="A"):
        return game.state.players[seat].mana_pool.get("C", 0)

    def act_ok(self, game, seat, request):
        outcome = game.act("pilot:" + seat, request)
        self.assertTrue(outcome.ok, outcome.summary)

    def start(self, game, source, **kwargs):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, **kwargs)
        self.checkpoint(game)

    def resolve_entry(self, game, source, entrant, seat):
        self.assert_closed(game, entrant)
        self.cast_for(game, seat, entrant)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        trigger = game.state.stack[-1]
        self.assertEqual(("creature.enter", entrant.ref, "A"), (
            trigger.context["event"], trigger.context["card"], trigger.controller,
        ))
        self.assertEqual([], list(trigger.targets))
        self.assertIsNone(trigger.context[ENTRY_CREATURE_TYPES_CONTEXT])

    def offer(self, game):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        action, = (a for a in pending.payload_by_actor["A"]["legal_actions"] if a["id"] == "choose")
        self.assertEqual(["apply", "decline"], list(action["choice_schema"]["legal_values"]))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])

    def test_actual_entry_counts_itself_and_each_other_matching_creature_once(self):
        game = self.session(61235801)
        source = self.add(game.engine, ECHOES)
        entrant = self.add(game.engine, "Goblin Piker", zone="hand")
        self.add(game.engine, "Universal Automaton")
        self.add(game.engine, "Goblin Piker", seat="B", ref="matching-foreign-piker")
        self.add(game.engine, "Grizzly Bears")
        self.start(game, source, mana={"R": 1, "C": 1})
        self.resolve_entry(game, source, entrant, "A")
        current = game.state.cards[entrant.object_id]
        self.assertEqual({"goblin", "warrior"}, game.engine._effective_type_parts(current)[1])
        self.assertEqual(0, self.count_colorless(game))
        self.offer(game)
        game = self.replay(game, load=True)
        self.act_ok(game, "A", {"action_id": "choose", "choice": "apply"})
        self.assertEqual((2, 0), (self.count_colorless(game), self.count_colorless(game, "B")))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_changed_types_are_read_live_or_frozen_at_departure(self):
        for seed, removal_name in (
            (61235802, None), (61235803, "Unsummon"), (61235804, "Reality Ripple"),
        ):
            with self.subTest(removal=removal_name):
                game = self.session(seed)
                source = self.add(game.engine, ECHOES)
                entrant = self.add(game.engine, "Goblin Piker", zone="hand")
                self.add(game.engine, "Grizzly Bears")
                self.add(game.engine, "Grizzly Bears", ref="current-bear-peer-two")
                self.add(game.engine, "Goblin Piker", ref="old-subtype-piker")
                crafter = self.add(game.engine, "Imagecrafter")
                crafter.acquired_control_turn_count = -1
                removal = None
                if removal_name:
                    removal = self.add(game.engine, removal_name, zone="hand", seat="B")
                    self.add(game.engine, "Island", seat="B")
                    self.add(game.engine, "Island", seat="B", ref="second-blue-land")
                    self.assert_closed(game, removal)
                self.assert_closed(game, crafter)
                self.start(game, source, mana={"R": 1, "C": 1})
                self.resolve_entry(game, source, entrant, "A")
                self.priority(game, "A")
                offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                activation, = (a for a in offered if a["id"].startswith("activate:" + crafter.ref + ":"))
                self.act_ok(game, "A", {"action_id": activation["id"], "targets": [entrant.ref], "pay": "auto"})
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                self.act_ok(game, "A", {"action_id": "choose", "creature_type": "bear"})
                current = game.state.cards[entrant.object_id]
                self.assertEqual({"bear"}, game.engine._effective_type_parts(current)[1])
                original = current.logical_object_id
                if removal:
                    self.cast(game, "B", removal, [entrant.ref])
                    self.resolve_top(game)
                    current = game.state.cards[entrant.object_id]
                    self.assertTrue(current.phased_out if removal_name == "Reality Ripple" else current.zone == "hand")
                    observation = SharedCreatureTypeObject.from_dict(game.state.stack[-1].context[
                        ENTRY_CREATURE_TYPES_CONTEXT
                    ])
                    self.assertEqual((original, ("bear",)), (observation.identity.logical_object_id, observation.creature_types))
                    game = self.replay(game, load=True)
                self.offer(game)
                self.act_ok(game, "A", {"action_id": "choose", "choice": "apply"})
                self.assertEqual(2 if removal else 3, self.count_colorless(game))
                self.replay(game, load=True)

    def test_actual_foreign_entry_still_offers_its_controller_optional_mana(self):
        game = self.session(61235805)
        source = self.add(game.engine, ECHOES)
        entrant = self.add(game.engine, "Goblin Piker", seat="B", zone="hand")
        self.add(game.engine, "Goblin Piker", ref="own-goblin-piker")
        self.start(game, source, actor="B", active="B", mana={"R": 1, "C": 1})
        self.resolve_entry(game, source, entrant, "B")
        self.offer(game)
        before = authoritative_state_hash(game.state)
        for seat, choice in (("B", "apply"), ("A", "extra")):
            rejected = game.act("pilot:" + seat, {"action_id": "choose", "choice": choice})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.act_ok(game, "A", {"action_id": "choose", "choice": "decline"})
        self.assertEqual((0, 0), (self.count_colorless(game), self.count_colorless(game, "B")))
        self.replay(game, load=True)

    def test_actual_face_down_entry_yields_zero_without_exposing_its_printed_types(self):
        game = self.session(61235806)
        source = self.add(game.engine, ECHOES)
        entrant = self.add(game.engine, "Daru Lancer", zone="hand")
        self.add(game.engine, "Universal Automaton")
        self.assert_closed(game, entrant)
        self.start(game, source, mana={"C": 3})
        self.act_ok(game, "A", {"action_id": "cast-morph:" + entrant.ref, "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.offer(game)
        for seat in "BCD":
            self.assertNotIn("Daru Lancer", json.dumps(game.packet("pilot:" + seat, full=True)))
        self.act_ok(game, "A", {"action_id": "choose", "choice": "apply"})
        self.assertEqual(0, self.count_colorless(game))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_complete_original_and_runtime_authority_require_the_exact_optional_entry_body(self):
        card = self.db.lookup(ECHOES, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Mana Echoes")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(("creature.enter", "battlefield", None), (program.event, program.active_zone, program.target_schema))
        self.assertIn(ENTRY_SHARED_TYPE_AMOUNT_CAPABILITY, entry_shared_type_mana_runtime_dependencies(program))
        award, = program.effects[0]["effects"]
        self.assertEqual(entry_shared_type_amount_spec(), PublicQueryAmountSpec.from_dict(award["amount"]))

        for before, after in (
            ("a creature enters", "a creature you control enters"),
            ("you may add", "add"),
            ("of {C}", "of {G}"),
            ("creatures you control", "other creatures you control"),
            ("with it.", "with it. You gain 1 life."),
        ):
            with self.subTest(excluded=after):
                changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        requirements = tuple(cap for cap in program.capability_dependencies if cap != ENTRY_SHARED_TYPE_AMOUNT_CAPABILITY)
        without_amount = replace(program, capability_dependencies=requirements,
            capability_closure=self.registry.closure(requirements, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(without_amount, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{ENTRY_SHARED_TYPE_AMOUNT_CAPABILITY}", bound["blockers"])

        forged = thaw_value(program.effects[0])
        forged["effects"][0]["player"] = "$active"
        for changes in ({"event": "permanent.enter"}, {"effects": (forged,)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                entry_shared_type_mana_runtime_dependencies(replace(program, **changes))
        with patch("quorune.compiler.optional_effect_templates.optional_shared_entrant_type_mana_trigger_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
