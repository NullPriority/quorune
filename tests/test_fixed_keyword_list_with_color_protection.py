"""A complete fixed keyword list retains both typed color protections."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import attached_ability_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Akroma's Memorial"
PROFILE = "commander_review"
PROTECTION_CAPABILITY = "protection.typed.debt"
KEYWORDS = {"flying", "first strike", "vigilance", "trample", "haste"}


class FixedKeywordListWithColorProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-keywords-with-protection.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-keyword-list-with-color-protection-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed keyword and color protections", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id, event="characteristics.evaluate")
        return card, program

    def test_complete_original_retains_the_full_keyword_list_and_both_color_fragments(self):
        card, program = self.program()
        ordinary, protected = program.handlers
        self.assertIn(PROTECTION_CAPABILITY, program.capability_dependencies)
        self.assertIn("continuous.ability.fixed_query_keyword_grant", program.capability_dependencies)
        self.assertEqual(ordinary["condition"], protected["condition"])
        self.assertEqual(KEYWORDS, {word.casefold() for word in ordinary["modifier"]["add_abilities"]})
        self.assertEqual(["Protection"], protected["modifier"]["add_abilities"])
        fragments = protected["modifier"]["add_ability_fragments"]
        self.assertEqual(["B", "R"], [item["value"]["quality"] for item in fragments])
        self.assertEqual({"protection"}, {item["kind"] for item in fragments})
        for text in (
            card.oracle_text.replace("haste", "an unknown ability"),
            card.oracle_text.replace("and from red", "and from the chosen color"),
            card.oracle_text.replace("and from red", "and from red, and draw a card"),
        ):
            result = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", result.status)

    def test_current_mixed_program_still_requires_the_independent_protection_owner(self):
        _, program = self.program()
        ordinary, protected = program.handlers
        different = {**protected, "condition": {**protected["condition"], "exclude_source": True}}
        for handlers in ((ordinary,), (ordinary, different)):
            binding = bind_semantic_program_runtime(replace(program, handlers=handlers),
                capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)
        deps = [cap for cap in program.capability_dependencies if cap != PROTECTION_CAPABILITY]
        altered = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        self.assertIn("capability:undeclared_runtime_dependency:" + PROTECTION_CAPABILITY, binding["blockers"])

    def scene(self, seed):
        game = self.session(seed)
        engine = game.engine
        memorial = self.add(engine, NAME, zone="hand")
        creature = self.add(engine, "Grizzly Bears", zone="hand")
        red = self.add(engine, "Lightning Bolt", seat="B", zone="hand")
        black = self.add(engine, "Murder", seat="B", zone="hand")
        white = self.add(engine, "Mending Hands", seat="B", zone="hand")
        redacted = self.add(engine, "Counterspell", seat="D", zone="hand", ref="memorial-private-d")
        excluded_blocker = self.add(engine, "Vampire Nighthawk", seat="B")
        legal_blocker = self.add(engine, "Storm Crow", seat="B")
        self.add(engine, "Forest")
        self.add(engine, "Plains", ref="memorial-a-plains")
        self.add(engine, "Mountain", seat="B")
        self.add(engine, "Swamp", seat="B", ref="memorial-b-swamp-one")
        self.add(engine, "Swamp", seat="B", ref="memorial-b-swamp-two")
        self.add(engine, "Plains", seat="B", ref="memorial-b-plains")
        for card in (memorial, creature, red, black, white, excluded_blocker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 7})
        self.checkpoint(game)
        self.cast_for(game, "A", memorial)
        self.resolve_top(game)
        self.cast_for(game, "A", creature)
        self.resolve_top(game)
        return game, creature, red, black, white, excluded_blocker, legal_blocker, redacted

    def color_targets(self, game, creature, red, black, legal):
        self.priority(game, "B")
        offers = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        for card, color in ((red, "red"), (black, "black")):
            action, = [row for row in offers if row["id"] == f"cast:{card.ref}"]
            legal_refs = action["target_schema"]["legal_refs"]
            self.assertNotIn(creature.ref, legal_refs, f"{color} targeting is prohibited")
            self.assertIn(legal.ref, legal_refs)
            before = authoritative_state_hash(game.state)
            result = game.act("pilot:B", {"action_id": action["id"], "targets": [creature.ref], "pay": "auto"})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_both_colors_change_targets_and_blocks_while_haste_remains_usable(self):
        game, body, red, black, white, excluded, allowed, private = self.scene(70216101)
        self.assertTrue(KEYWORDS <= set(game.engine._combat_keywords(game.state.cards[body.object_id])))
        self.assertFalse(KEYWORDS <= set(game.engine._combat_keywords(game.state.cards[allowed.object_id])))
        self.color_targets(game, body, red, black, allowed)
        self.cast_for(game, "B", white, [body.ref])
        self.resolve_top(game)

        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.assertEqual(game.state.players["A"].turns_begun,
                         game.state.cards[body.object_id].acquired_control_turn_count)
        attack = game.act("pilot:A", {"action_id": "attack", "attackers": {body.ref: "B"}})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        before = authoritative_state_hash(game.state)
        forbidden = game.act("pilot:B", {"action_id": "block", "blocks": {excluded.ref: body.ref}})
        self.assertFalse(forbidden.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        block = game.act("pilot:B", {"action_id": "block", "blocks": {allowed.ref: body.ref}})
        self.assertTrue(block.ok, block.summary)
        self.assertEqual([allowed.object_id], game.state.combat.blockers[body.object_id])
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, str(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_second_color_witness_kills_dropping_the_trailing_protection(self):
        original = attached_ability_templates.parse_protection_line

        def drop_last(value):
            parsed = original(value)
            return parsed[:-1] if parsed is not None and len(parsed) > 1 else parsed

        with patch.object(attached_ability_templates, "parse_protection_line", drop_last):
            with self.assertRaisesRegex(AssertionError, "red targeting is prohibited"):
                game, body, red, black, _white, _excluded, allowed, _private = self.scene(70216102)
                self.color_targets(game, body, red, black, allowed)
