from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.reanimation_templates import fixed_target_reanimation_mana_value_loss_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec, ScalarAmountOrigin
from scripts.build_test_database import build_fixture_database


class ReanimationManaValueLossTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "graveyard-mana-value-reanimation.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/graveyard-mana-value-reanimation-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Reanimation and life", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def reanimate(self, game, spell, card):
        self.assert_closed(game, spell)
        self.cast_for(game, "A", spell, [card.ref])

    def test_actual_reanimate_loses_the_graveyard_amount_before_entry_triggers_resolve(self):
        game = self.session(20203681)
        spell = self.add(game.engine, "Reanimate", zone="hand")
        original = self.add(game.engine, "Clone", seat="B", zone="graveyard")
        model = self.add(game.engine, "Grizzly Bears", seat="C")
        warden = self.add(game.engine, "Soul Warden")
        for card in (original, model, warden):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        self.reanimate(game, spell, original)
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        apply, = (row for row in options if model.ref in row["id"] and not row["id"].startswith("decline:"))
        command = {"action_id": "choose", "replacement": apply["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        entered = game.state.cards[original.object_id]
        self.assertEqual(("battlefield", "B", "A"), (entered.zone, entered.owner, entered.controller))
        self.assertEqual("Grizzly Bears", game.engine.display_name(entered.object_id))
        self.assertEqual(2, game.engine._effective_card_data(entered)["mana_value"])
        self.assertEqual(36, game.state.players["A"].life)
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual(1, self.trigger_count(game, warden))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_reanimate_moves_the_original_zero_mana_value_creature(self):
        game = self.session(20203682)
        spell = self.add(game.engine, "Reanimate", zone="hand")
        original = self.add(game.engine, "Dryad Arbor", seat="D", zone="graveyard")
        self.assert_closed(game, original)
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        self.reanimate(game, spell, original)
        self.assertIsNone(self.resolve(game))
        creature = game.state.cards[original.object_id]
        self.assertEqual(("battlefield", "D", "A"), (creature.zone, creature.owner, creature.controller))
        self.assertEqual(40, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_reanimate_has_no_life_result_for_an_illegal_only_target(self):
        game = self.session(20203683)
        spell = self.add(game.engine, "Reanimate", zone="hand")
        original = self.add(game.engine, "Clone", seat="B", zone="graveyard")
        response = self.add(game.engine, "Cremate", seat="C", zone="hand")
        self.assert_closed(game, response)
        game.state.players["C"].mana_pool["B"] = 1
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        self.reanimate(game, spell, original)
        self.cast_for(game, "C", response, [original.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[original.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_reanimation_reuses_the_graveyard_quantity_in_its_exact_life_loss_slot(self):
        original = self.db.lookup("Reanimate", fuzzy=False)
        compiled = fixed_target_reanimation_mana_value_loss_template(original.oracle_text)
        self.assertIsNotNone(compiled)
        _name, effects, schema, mechanics = compiled.compiled()
        self.assertEqual(["reanimate", "lose_life"], [effect["op"] for effect in effects])
        self.assertEqual((["graveyard"], ["creature"]), (schema["zones"], schema["types_any"]))
        amount = ScalarEffectAmountSpec.from_dict(effects[1]["amount"])
        self.assertEqual((15, 1, ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE),
                         (amount.schema_version, amount.coefficient, amount.origin))
        with self.assertRaises(ValueError):
            ScalarEffectAmountSpec.from_dict({**amount.to_dict(), "coefficient": -1})
        from quorune.compiler.scalar_effect_amounts import scalar_amount_shape_context

        self.assertIsNotNone(scalar_amount_shape_context(effects, mechanics))
        self.assertIsNone(scalar_amount_shape_context(effects[1:], mechanics))
        self.assertIsNone(scalar_amount_shape_context(tuple(reversed(effects)), mechanics))
        negatives = (original.oracle_text.replace("You lose", "You gain"),
                     original.oracle_text.replace("target creature", "up to one target creature"),
                     original.oracle_text.replace("mana value", "power"),
                     original.oracle_text + " Draw a card.")
        for line in negatives:
            with self.subTest(line=line):
                self.assertIsNone(fixed_target_reanimation_mana_value_loss_template(line))

    def test_omitting_life_loss_changes_the_actual_clone_result(self):
        from quorune import scalar_effect_amounts

        original = scalar_effect_amounts._reference

        def omit_loss(host, item, spec, **kwargs):
            reference = original(host, item, spec, **kwargs)
            if spec.origin is ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE:
                return {**reference, "mana_value": 0}
            return reference

        with patch.object(scalar_effect_amounts, "_reference", side_effect=omit_loss):
            with self.assertRaisesRegex(AssertionError, "36 != 40"):
                self.test_actual_reanimate_loses_the_graveyard_amount_before_entry_triggers_resolve()

    def test_only_the_matching_reviewed_reanimation_view_is_superseded(self):
        import json
        from quorune.card_programs.current_view_equivalence import shadowed_reviewed_reanimation_life_loss_keys
        from quorune.compiler.program_generation import generated_programs
        from quorune.semantics import SemanticProgram

        record = self.db.lookup("Reanimate", fuzzy=False)
        compiled = generated_programs(self.db, record, trust_level="trusted",
            capability_registry=self.registry, capability_profile="commander_review")
        pack = json.loads((ROOT / "quorune/semantic_packs/v080-exact-interactions.json").read_bytes())
        row, = (value for value in pack["programs"] if value["oracle_id"] == record.oracle_id)
        reviewed = SemanticProgram.from_dict(row)
        self.assertEqual({reviewed.key},
                         shadowed_reviewed_reanimation_life_loss_keys(record, compiled, [reviewed]))
        for changes in (
            {"provenance": {**row["provenance"], "source_rulings_hash": "0" * 64}},
            {"target_schema": {**row["target_schema"], "owner_relation": "you"}},
        ):
            different = SemanticProgram.from_dict({**row, **changes})
            self.assertFalse(shadowed_reviewed_reanimation_life_loss_keys(record, compiled, [different]))
