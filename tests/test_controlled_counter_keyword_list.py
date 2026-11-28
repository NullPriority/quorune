"""One original controlled target for the ordered counter and temporary keyword list."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

CARD = "Gaea's Gift"
PROFILE = "commander_review"
ORACLE = ("Put a +1/+1 counter on target creature you control. "
          "It gains reach, trample, hexproof, and indestructible until end of turn.")
KEYWORDS = ("Reach", "Trample", "Hexproof", "Indestructible")
CAPABILITIES = ("counter.producer.fixed_effect", "continuous.resolution.fixed_characteristics_until_end_of_turn",
                "resolution.effect_sequence.fixed_target", "target.revalidate_resolution")


class ControlledCounterKeywordListTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "controlled-counter-keyword-list.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "target-counter-temporary-keywords-cards.json",
            "cast-target-copy-cards.json", "state-qualified-zone-cards.json", "counter-prohibition-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Counter and keywords", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compiler_keeps_one_controlled_target_and_four_distinct_temporary_keywords(self):
        record = replace(query_record(ORACLE), type_line="Instant", mana_cost="{1}{G}")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(ORACLE, ORACLE[node.span.start:node.span.end])
        self.assertEqual(("you", ["creature"], 1), (node.target_schema["controller_relation"],
                         node.target_schema["types_any"], node.target_schema["count"]))
        counter, *grants = node.effects
        self.assertEqual(("place_counters", "+1/+1", 1), (counter["op"], counter["counter"], counter["amount"]))
        self.assertEqual(KEYWORDS, tuple(effect["keyword"] for effect in grants))
        self.assertTrue(all(effect["op"] == "grant_keyword_until_end_of_turn" for effect in grants))
        self.assertTrue(all(effect["card"] == "$target.0" for effect in node.effects))
        self.assertTrue(set(CAPABILITIES).issubset(node.capability_dependencies))
        for old, new in (("creature you control", "creature"),
                         ("hexproof, and", "trample, and"),
                         ("and indestructible", "indestructible, and lifelink"),
                         ("It gains", "Another target creature gains"),
                         ("until end of turn", "for the rest of the game")):
            with self.subTest(new=new):
                changed = replace(record, oracle_text=ORACLE.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE).status)
        binding, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(self.db.lookup(CARD, fuzzy=False).oracle_id)
        for missing in CAPABILITIES:
            with self.subTest(missing=missing):
                deps = [key for key in program.capability_dependencies if key != missing]
                altered = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                self.assertFalse(bind_semantic_program_runtime(altered, capability_registry=self.registry,
                                                               profile=PROFILE)["strict"])

    def start(self, game, *cards, mana=None, ending=False):
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana=mana or {"C": 1, "G": 1})
        if ending:
            game.engine.permissions.invalidate_current()
            game.state.pending_decision = None
            game.state.priority_player = None
            game.state.phase = "postcombat_main"
            game.state.phase_index = TURN_STEPS.index(("postcombat_main", "main"))
            game.engine._grant_priority("A")
            game.engine.pump()
        self.checkpoint(game)

    @staticmethod
    def current(game, creature):
        return game.state.cards[creature.object_id]

    def assert_keyword_list(self, game, creature, expected):
        effective = set(game.engine._combat_keywords(self.current(game, creature)))
        self.assertEqual({word.casefold() for word in KEYWORDS} if expected else set(),
                         effective.intersection(word.casefold() for word in KEYWORDS))

    def test_valid_target_gets_counter_and_all_keywords_then_only_keywords_expire(self):
        game = self.session(12216001)
        spell = self.add(game.engine, CARD, zone="hand")
        removal = self.add(game.engine, "Murder", zone="hand")
        body = self.add(game.engine, "Grizzly Bears", ref="own-eligible-creature")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-creature")
        self.start(game, spell, removal, mana={"C": 2, "G": 1, "B": 2}, ending=True)
        action = self.action(game, "A", spell)
        self.assertEqual([body.ref], action["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        illegal = game.act("pilot:A", {"action_id": action["id"], "targets": [other.ref], "pay": "auto"})
        self.assertFalse(illegal.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [body.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1}, self.current(game, body).counters)
        self.assert_keyword_list(game, body, True)
        self.cast_for(game, "A", removal, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self.current(game, body).zone)
        self.until(game, lambda session: session.state.active_player == "B" and session.state.phase == "precombat_main")
        self.assertEqual({"+1/+1": 1}, self.current(game, body).counters)
        self.assert_keyword_list(game, body, False)
        self.replay(game, load=True)

    def test_prevented_counter_leaves_the_independent_four_keyword_grants(self):
        game = self.session(12216002)
        spell = self.add(game.engine, CARD, zone="hand")
        body = self.add(game.engine, "Grizzly Bears")
        prohibition = self.add(game.engine, "Solemnity", seat="C")
        self.start(game, spell, prohibition)
        self.cast_for(game, "A", spell, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({}, self.current(game, body).counters)
        self.assertEqual(2, game.engine._numeric_stat(body.object_id, "power"))
        self.assert_keyword_list(game, body, True)
        self.replay(game, load=True)

    def test_reentered_physical_target_remains_illegal_for_both_instructions(self):
        game = self.session(12216003)
        spell = self.add(game.engine, CARD, zone="hand")
        body = self.add(game.engine, "Grizzly Bears")
        flicker = self.add(game.engine, "Flicker of Fate", zone="hand", seat="B")
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        old = body.logical_object_id
        self.start(game, spell, flicker)
        self.cast_for(game, "A", spell, [body.ref])
        self.cast_for(game, "B", flicker, [body.ref])
        self.resolve_top(game)
        self.assertEqual(("battlefield", "A"), (self.current(game, body).zone, self.current(game, body).controller))
        self.assertNotEqual(old, self.current(game, body).logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({}, self.current(game, body).counters)
        self.assert_keyword_list(game, body, False)
        self.assertEqual("graveyard", self.current(game, spell).zone)
        self.replay(game, load=True)
