"""Native source declaration counts, retained recipients and exact replay."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.attack_batches import (
    DECLARED_ATTACK_COUNTER_CAPABILITY, DECLARED_ATTACK_COUNTER_FIELD, DECLARED_ATTACK_COUNTER_REFERENCE,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


PROFILE = "commander_review"
ORIGINAL = "Vrestin, Menoptra Leader"
FIXTURES = ROOT / "tests" / "fixtures"


class DeclaredAttackGroupCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        select = {
            "entry-x-result-cards.json": ORIGINAL,
            "source-attack-alone-cards.json": "Fervor",
            "upkeep-creature-token-absence-cards.json": "Universal Automaton",
            "creature-subtype-set-results-cards.json": "Amoeboid Changeling",
            "source-block-defender-cards.json": "Cloudshift",
            "optional-object-action-counters.json": "Hardened Scales",
        }
        paths = [FIXTURES / "bound-effect-program-cards.json"]
        for filename, expected in select.items():
            fixture = json.loads((FIXTURES / filename).read_bytes())
            fixture["cards"] = [row for row in fixture["cards"] if row["name"] == expected]
            (card,) = fixture["cards"]
            fixture["rulings"] = [row for row in fixture.get("rulings", ()) if row["oracle_id"] == card["oracle_id"]]
            added = folder / filename
            added.write_text(json.dumps(fixture), encoding="utf-8")
            paths.append(added)
        db = folder / "declared-attack-members.sqlite3"
        build_fixture_database(paths, db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Declared matching attackers", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_the_exact_original_requires_a_sealed_untargeted_attack_group(self):
        record = self.db.lookup(ORIGINAL, fuzzy=False)

        def compile_record(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)

        ir = compile_record(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        (trigger,) = (node for node in ir.faces[0].nodes if node.event == "creature.attacks")
        (effect,) = trigger.effects
        self.assertIsNone(trigger.target_schema)
        self.assertIn("one_or_more_event_batch", trigger.runtime_coverage)
        self.assertIn(DECLARED_ATTACK_COUNTER_CAPABILITY, trigger.capability_dependencies)
        self.assertEqual(DECLARED_ATTACK_COUNTER_REFERENCE, effect[DECLARED_ATTACK_COUNTER_FIELD])
        for changed in (
            record.oracle_text.replace("on each of them", "on a chosen one of them"),
            record.oracle_text.replace("one or more Insects", "two or more Insects"),
            record.oracle_text.replace("a +1/+1 counter on each of them", "a flying counter on each of them"),
        ):
            altered = compile_record(replace(record, oracle_text=changed))
            self.assertNotEqual("exact", altered.status)

        game = self.session(50830360)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, source)
        (program,) = (item for item in game.engine.semantics.programs_for_oracle(source.oracle_id)
                      if item.event == "creature.attacks")
        retained = tuple(cap for cap in program.capability_dependencies if cap != DECLARED_ATTACK_COUNTER_CAPABILITY)
        without = replace(program, capability_dependencies=retained,
                          capability_closure=self.registry.closure(retained, profile=PROFILE).to_dict())
        for changed in (
            without, replace(program, event="creature.enter"),
            replace(program, event_condition=None),
            replace(program, effects=[{key: value for key, value in effect.items()
                                      if key != DECLARED_ATTACK_COUNTER_FIELD}]),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        result = bind_semantic_program_runtime(without, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + DECLARED_ATTACK_COUNTER_CAPABILITY,
                     result["blockers"])

    def subtype(self, game, card):
        return game.engine._effective_type_parts(game.state.cards[card.object_id])[1]

    def declared_scene(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        self.add(game.engine, "Fervor")
        replacement = self.add(game.engine, "Hardened Scales")
        automaton = self.add(game.engine, "Universal Automaton", ref="returning-insect")
        bear = self.add(game.engine, "Grizzly Bears", ref="outside-insect-scope")
        changer = self.add(game.engine, "Amoeboid Changeling", seat="B", ref="type-changing-defense")
        changer.acquired_control_turn_count = -1
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        combat_white = self.add(game.engine, "Generic Bound Plains", ref="combat-white-mana")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="attack-group-private")
        for card in (source, replacement, automaton, changer, blink):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "G": 2, "W": 1})
        self.checkpoint(game)
        self.priority(game, "A")
        offered = self.action(game, "A", source)
        played = game.act("pilot:A", {"action_id": offered["id"], "x": 2,
            "pay": "manual", "payment": {"C": 2, "G": 2, "W": 1}})
        self.assertTrue(played.ok, played.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[combat_white.object_id].tapped)
        self.assertEqual(3, game.state.cards[source.object_id].counters.get("+1/+1"))
        first, second = (card for card in game.state.cards.values() if card.is_token
                         and card.zone == "battlefield" and card.controller == "A"
                         and "insect" in self.subtype(game, card))

        self.attack(game, [(first, "B"), (second, "C"), (automaton, "D"), (bear, "B")])
        (trigger,) = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual("triggered_ability", trigger.kind)
        self.assertEqual([], trigger.targets)
        expected = {(card.ref, card.logical_object_id) for card in (first, second, automaton)}
        sealed = {tuple(member) for member in trigger.context[DECLARED_ATTACK_COUNTER_FIELD]}
        self.assertEqual(expected, sealed, "The declaration must retain every matching attacker once")
        return game, source, first, second, automaton, bear, changer, blink, private, sealed

    def test_actual_multi_opponent_attack_retains_subtype_and_original_incarnations(self):
        game, source, first, second, removed, other, changer, blink, private, sealed = self.declared_scene(50830361)
        self.priority(game, "B")
        (ability,) = (value for value in game.engine._activated_abilities(changer)
                      if "loses all creature types" in value.effect_text.casefold())
        change = game.act("pilot:B", {"action_id": "activate:" + changer.ref + ":" + ability.ability_id,
                                      "targets": [first.ref], "pay": "auto"})
        self.assertTrue(change.ok, change.summary)
        self.resolve_top(game)
        self.assertNotIn("insect", self.subtype(game, first))
        old_identity = game.state.cards[removed.object_id].logical_object_id
        self.cast(game, "A", blink, [removed.ref])
        self.resolve_top(game)
        self.assertNotEqual(old_identity, game.state.cards[removed.object_id].logical_object_id)
        self.assertIn("insect", self.subtype(game, removed))
        game = self.replay(game, load=True)
        (trigger,) = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual(sealed, {tuple(member) for member in trigger.context[DECLARED_ATTACK_COUNTER_FIELD]})
        self.assertIsNone(self.resolve(game))
        result = [game.state.cards[card.object_id].counters.get("+1/+1", 0)
                  for card in (source, first, second, removed, other)]
        self.assertEqual([3, 2, 2, 0, 0], result)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_omitted_matching_member_mutation_fails_the_actual_declaration(self):
        from quorune.rules import attack_batches
        actual = attack_batches.qualified_attack_incarnations

        def skip_first(*args, **kwargs):
            return actual(*args, **kwargs)[1:]

        with patch.object(attack_batches, "qualified_attack_incarnations", skip_first):
            with self.assertRaisesRegex(AssertionError, "retain every matching attacker once"):
                self.declared_scene(50830362)
