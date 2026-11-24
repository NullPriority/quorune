"""Three ordinary controlled permanent spell copies retain their targets."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_stack_spell_copy import fixed_qualified_stack_copy_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.stack_spell_copy_model import FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD, FixedQualifiedStackCopySpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Myojin of Cryptic Dreams"


class FixedThreeControlledPermanentSpellCopiesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-three-permanent-copies.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/fixed-three-controlled-permanent-spell-copies-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed three controlled copies", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_adds_three_only_to_the_existing_controlled_permanent_scope(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantics.programs_for_oracle(original.oracle_id)
        activation, = (program for program in programs if program.event == "activate")
        self.assertTrue(any(program.event != "activate" for program in programs))
        spec = FixedQualifiedStackCopySpec("permanent_spell", schema_version=2, copy_count=3)
        self.assertEqual(spec.target_schema, activation.target_schema)
        self.assertEqual(spec.effects, tuple(activation.effects))
        effect, = activation.effects
        self.assertEqual(("copy_stack_item", "$target.0", True, 3), (
            effect["op"], effect["stack"], effect["retain_targets_only"],
            effect[FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD],
        ))
        self.assertIn("stack.copy.fixed_qualified_target", activation.capability_dependencies)
        for parts in (
            {"effects": [{**effect, FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD: 3.0}]},
            {"effects": [{**effect, FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD: 2}]},
            {"effects": [{**effect, "retain_targets_only": False}]},
            {"target_schema": {**activation.target_schema, "controller_relation": "opponent"}},
        ):
            with self.subTest(parts=parts):
                recorded = bind_semantic_program_runtime(
                    replace(activation, **parts), capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(recorded["strict"], recorded)
        single = fixed_qualified_stack_copy_template("Copy target permanent spell you control.")
        self.assertEqual(({"op": "copy_stack_item", "stack": "$target.0", "retain_targets_only": True},), single[1])
        for text in (
            original.oracle_text.replace("three times", "two times"),
            original.oracle_text.replace("permanent spell you control", "permanent spell"),
            original.oracle_text.replace("permanent spell", "instant spell"),
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status)

    def _item(self, game, card):
        item, = (row for row in game.state.stack if row.card_object_id == card.object_id)
        return item

    def _cast_source(self, game, source, *, reserve):
        self.cast_for(game, "A", source, [], reserve=reserve)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("indestructible"))

    def _activate_copy(self, game, source, accepted, excluded=()):
        self.priority(game, "A")
        prefix = f"activate:{source.ref}:"
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith(prefix))
        legal = action["target_schema"]["legal_refs"]
        self.assertIn(accepted, legal)
        before = authoritative_state_hash(game.state)
        command = {"action_id": action["id"], "targets": [accepted], "pay": "auto"}
        self.assertFalse(game.act("pilot:B", command).ok)
        for ref in excluded:
            self.assertNotIn(ref, legal)
            self.assertFalse(game.act("pilot:A", {**command, "targets": [ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("indestructible", 0))

    def test_actual_aura_spell_produces_three_token_copies_with_its_original_target(self):
        game = self.session(70710321)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        aura = self.add(game.engine, "Pacifism", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        other_spell = self.add(game.engine, "Ambush Viper", seat="B", zone="hand")
        enchanted = self.add(game.engine, "Runeclaw Bear", seat="B", ref="copy-original-aura-target")
        alternate = self.add(game.engine, "Runeclaw Bear", seat="C", ref="copy-unselected-aura-target")
        for card in (source, aura, bolt, other_spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "U": 3, "W": 1, "R": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(game)
        self._cast_source(game, source, reserve={"C": 1, "W": 1, "R": 1})

        self.cast_for(game, "A", aura, [enchanted.ref], reserve={"R": 1})
        original_ref = self._item(game, aura).ref
        self.cast_for(game, "A", bolt, ["D"])
        self.cast_for(game, "B", other_spell, [])
        self._activate_copy(game, source, original_ref, (self._item(game, bolt).ref, self._item(game, other_spell).ref))
        game = self.replay(game, load=True)
        self.resolve_top(game)

        copies = tuple(item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual(3, len(copies))
        self.assertEqual(3, len({item.ref for item in copies}))
        self.assertTrue(all(tuple(item.targets) == (enchanted.ref,) for item in copies))
        self.assertTrue(all(item.controller == "A" for item in copies))
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.settle(game)
        results = tuple(card for card in game.state.cards.values()
                        if card.zone == "battlefield" and card.oracle_id == aura.oracle_id)
        self.assertEqual((4, 3), (len(results), sum(card.is_token for card in results)))
        self.assertTrue(all(card.attached_to == enchanted.object_id for card in results))
        self.assertEqual([], game.state.cards[alternate.object_id].attachments)
        self.replay(game, load=True)

    def test_actual_countered_original_leaves_the_paid_activation_without_a_target(self):
        game = self.session(70710322)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        aura = self.add(game.engine, "Pacifism", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        elected = self.add(game.engine, "Runeclaw Bear", seat="B")
        for card in (source, aura, counter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "U": 3, "W": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self._cast_source(game, source, reserve={"C": 1, "W": 1})

        self.cast_for(game, "A", aura, [elected.ref])
        original_ref = self._item(game, aura).ref
        self._activate_copy(game, source, original_ref)
        self.cast_for(game, "B", counter, [original_ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("indestructible", 0))
        self.assertFalse(any(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
        self.assertEqual([], game.state.cards[elected.object_id].attachments)
        self.replay(game, load=True)

    def test_actual_copy_batch_rolls_back_when_the_second_copy_fails_then_retries(self):
        from unittest.mock import patch
        from quorune.engine import CommanderEngine
        from quorune.errors import GameRuleError

        game = self.session(70710323)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        model = self.add(game.engine, "Runeclaw Bear", zone="hand")
        for card in (source, model):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "U": 3, "G": 1})
        self.checkpoint(game)
        self._cast_source(game, source, reserve={"C": 1, "G": 1})
        self.cast_for(game, "A", model, [])
        self._activate_copy(game, source, self._item(game, model).ref)

        original_copy = CommanderEngine._copy_stack_item
        calls = []

        def fail_second(engine, **kwargs):
            copied = original_copy(engine, **kwargs)
            calls.append(copied.ref)
            if len(calls) == 2:
                raise GameRuleError("Second copy rollback witness")
            return copied

        with patch.object(CommanderEngine, "_copy_stack_item", fail_second):
            for _ in range(12):
                before = authoritative_state_hash(game.state)
                actor = game.pending_principals()[0]
                rejected = game.act(actor, {"action_id": "pass"})
                if not rejected.ok:
                    self.assertIn("Second copy rollback witness", rejected.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    break
            else:
                self.fail("The injected second copy fault did not reach the resolution command")
        self.assertEqual(2, len(calls))
        self.assertFalse(any(item.kind == "spell_copy" for item in game.state.stack))
        result = game.act(actor, {"action_id": "pass"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(3, sum(item.kind == "spell_copy" for item in game.state.stack))
        self.settle(game)
        self.replay(game, load=True)
