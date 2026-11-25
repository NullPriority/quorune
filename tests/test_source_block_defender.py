"""Pin the declared-source and exact no-duration printed result grammar."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.carddb import CardDatabase
from quorune.compiler.source_block_defender import source_block_defender_result
from quorune.continuous_effect_model import Layer
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from quorune.source_block_defender_model import SOURCE_BLOCK_DEFENDER_FIELD, SourceBlockDefenderSpec


class SourceBlockDefenderCompilerTests(unittest.TestCase):
    def test_actual_printed_bodies_pin_the_source_and_only_the_creature_subtypes(self):
        fixture = Path(__file__).parent / "fixtures/source-block-defender-cards.json"
        cards = json.loads(fixture.read_bytes())["cards"]
        expected = {"Elder Land Wurm": (), "Ageless Sentinels": ("bird", "giant")}
        cards = [card for card in cards if card["name"] in expected]
        self.assertEqual(set(expected), {card["name"] for card in cards})
        binding = SimpleNamespace(event=SimpleNamespace(value="creature.blocks"),
                                  variant="this_creature_blocks", active_zone="battlefield")
        for record in cards:
            line = next(line for line in record["oracle_text"].splitlines() if line.startswith("When this creature blocks"))
            body = line.split(", ", 1)[1].partition(" (")[0]
            result, exact_scope = source_block_defender_result(binding, body)
            self.assertTrue(exact_scope)
            self.assertIsNone(result[2])
            instruction, = result[1]
            self.assertEqual("$source.zone_object", instruction["card"])
            spec = SourceBlockDefenderSpec.from_dict(instruction[SOURCE_BLOCK_DEFENDER_FIELD])
            self.assertEqual(expected[record["name"]], spec.creature_subtypes)

    def test_other_events_types_and_duration_wording_fail_closed(self):
        scope = {"event": SimpleNamespace(value="creature.blocks"),
                 "variant": "this_creature_blocks", "active_zone": "battlefield"}
        for altered in ({"event": SimpleNamespace(value="creature.becomes_blocked")},
                        {"variant": "a_creature_blocks"}, {"active_zone": "graveyard"}):
            self.assertIsNone(source_block_defender_result(SimpleNamespace(**{**scope, **altered}),
                                                           "it loses defender."))
        for line in ("that creature loses defender.", "it loses defender until end of turn.",
                     "it becomes a Wall Wall, and it loses defender.",
                     "it becomes a Forest Island, and it loses defender.",
                     "it becomes a Bird Giant, and it loses flying."):
            self.assertIsNone(source_block_defender_result(SimpleNamespace(**scope), line), line)


    def test_runtime_depends_on_the_original_sources_closed_block_condition(self):
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
        from quorune.rules.source_block_defender import (
            source_block_defender_instruction, source_block_defender_runtime_dependencies,
        )
        from quorune.source_block_defender_model import SOURCE_BLOCK_DEFENDER_CAPABILITY

        args = {
            "event": "creature.blocks", "active_zone": "battlefield",
            "target_schema": None, "handlers": (), "effects": (source_block_defender_instruction(SourceBlockDefenderSpec()),),
            "event_condition": {"field": "card", "op": "eq", "value": "$source.ref"},
            "coverage": (CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        }
        self.assertIn(SOURCE_BLOCK_DEFENDER_CAPABILITY,
                      source_block_defender_runtime_dependencies(SimpleNamespace(**args)))
        for changed in ({"event": "creature.becomes_blocked"}, {"active_zone": "graveyard"},
                        {"event_condition": {}}, {"coverage": ()}, {"target_schema": {"count": 1}},
                        {"effects": (dict(args["effects"][0], card="$target.0"),)}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                source_block_defender_runtime_dependencies(SimpleNamespace(**{**args, **changed}))


class SourceBlockDefenderNativeTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "source-block.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/source-block-defender-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Source blocks", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def scene(self, seed, *, elder=False, blink=False):
        game = self.session(seed)
        if elder:
            source = self.add(game.engine, "Elder Land Wurm", seat="B")
        else:
            source = self.add(game.engine, "Ageless Sentinels", seat="B")
        attacker = self.add(game.engine, "Walking Corpse")
        private = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="source-block-hidden")
        spell = None
        if blink:
            spell = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
            self.add(game.engine, "Generic Bound Plains", seat="B", ref="block-blink-plains")
            self.assert_closed(game, spell)
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.assertIn("defender", self.keywords(game, source))
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.blockers")
        command = {"action_id": "block", "blocks": {source.ref: attacker.ref}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, self.trigger_count(game, source))
        return game, source, private, spell

    @staticmethod
    def current(game, source):
        return game.state.cards[source.object_id]

    def keywords(self, game, source):
        data = game.engine._effective_card_data(self.current(game, source))
        return {word.casefold() for word in data["keywords"]}

    def subtypes(self, game, source):
        data = game.engine._effective_card_data(self.current(game, source))
        return game.engine._type_parts(data["type_line"])[1]

    def assert_lost_defender(self, game, source):
        self.assertNotIn("defender", self.keywords(game, source), "The blocking source must lose defender")

    def test_actual_elders_block_permanently_removes_defender_and_allows_its_next_attack(self):
        game, source, private, _ = self.scene(50930001, elder=True)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assert_lost_defender(game, source)
        self.assertEqual({"dragon", "wurm"}, self.subtypes(game, source))
        self.assertIn("trample", self.keywords(game, source))
        for seat in ("A", "B", "C"):
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.finish_turn(game)
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {self.current(game, source).ref: "A"}})
        self.assertTrue(result.ok, result.summary)
        self.replay(game, load=True)

    def test_actual_sentinels_replaces_only_creature_subtypes_and_keeps_flying(self):
        game, source, _, _ = self.scene(50930002)
        self.assertEqual({"wall"}, self.subtypes(game, source))
        self.assertIsNone(self.resolve(game))
        self.assert_lost_defender(game, source)
        self.assertEqual({"bird", "giant"}, self.subtypes(game, source))
        self.assertIn("flying", self.keywords(game, source))
        game = self.replay(game, load=True)
        self.finish_turn(game)
        self.assert_lost_defender(game, source)
        self.assertEqual({"bird", "giant"}, self.subtypes(game, source))
        self.replay(game, load=True)

    def test_actual_blinked_sentinels_does_not_inherit_the_original_block_result(self):
        game, source, _, blink = self.scene(50930003, blink=True)
        before = source.logical_object_id
        self.cast_for(game, "B", blink, [source.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        returned = self.current(game, source)
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(before, returned.logical_object_id)
        self.assertIn("defender", self.keywords(game, source))
        self.assertEqual({"wall"}, self.subtypes(game, source))
        self.replay(game, load=True)

    def test_actual_defender_witness_detects_an_omitted_ability_layer(self):
        original = SourceBlockDefenderSpec.layer_operations

        def type_only(spec):
            return tuple(group for group in original(spec) if group[0] is not Layer.ABILITY)

        with patch.object(SourceBlockDefenderSpec, "layer_operations", type_only):
            game, source, _, _ = self.scene(50930004)
            self.assertIsNone(self.resolve(game))
            self.assertEqual({"bird", "giant"}, self.subtypes(game, source))
            with self.assertRaisesRegex(AssertionError, "blocking source must lose defender"):
                self.assert_lost_defender(game, source)
