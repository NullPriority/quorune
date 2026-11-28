"""Current controller-scoped split second through ordinary stack priority."""
from dataclasses import replace
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
from quorune.record import authoritative_state_hash
from quorune.semantic_runtime import SemanticNodeError
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.static_cast_rules import (
    SPLIT_SECOND_GRANT_CAPABILITY, split_second_is_active,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

SOURCE = "Samut, Tyrant of Naktamun"
PROFILE = "commander_review"


class ControllerSpellSplitSecondTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-spell-split-second.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "controller-spell-split-second-cards.json",
            fixtures / "committed-damage-dealer-optional-draw-cards.json",
            fixtures / "targeted-opponent-library-top-exile-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Controller's spell split second", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_original_compiles_and_forged_scope_or_missing_grant_fails_closed(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        args = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(record, **args)
        self.assertEqual(("exact", ()), (ir.status, ir.material_residuals))
        native, registry = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(native["strict_capability_ready"], native)
        program, = registry.programs_for_oracle(record.oracle_id)
        descriptor, = thaw_value(program.handlers)
        wrong_zone = replace(program, active_zone="graveyard")
        self.assertFalse(bind_semantic_program_runtime(wrong_zone, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])
        with self.assertRaises(SemanticNodeError):
            replace(program, handlers=({**descriptor, "player_scope": "all_players"},))
        dependencies = tuple(key for key in program.capability_dependencies if key != SPLIT_SECOND_GRANT_CAPABILITY)
        candidate = replace(program, capability_dependencies=dependencies,
                            capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(candidate, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        with patch("quorune.compiler.static_cast_rule_templates._split_second_grant", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, **args).status)

    def offers(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def test_actual_granted_instant_blocks_cast_and_nonmana_but_preserves_mana_and_lower_spell(self):
        game = self.session(74234001)
        source = self.add(game.engine, SOURCE, zone="hand")
        own = self.add(game.engine, "Unsummon", zone="hand", ref="split-grant-own")
        opponent = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="split-grant-opponent")
        reply = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="split-grant-reply")
        mana_source = self.add(game.engine, "Mind Stone", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        self.assert_closed(game, source)
        self.prepare(game, mana={"U": 3, "C": 1})
        game.state.players["B"].mana_pool.update({"U": 2, "C": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertFalse(split_second_is_active(game.engine))
        self.resolve_top(game)
        self.cast_for(game, "B", opponent, [creature.ref])
        self.assertFalse(split_second_is_active(game.engine))
        self.cast_for(game, "A", own, [source.ref])
        self.assertTrue(split_second_is_active(game.engine))
        offered = self.offers(game, "B")
        self.assertFalse(any(row.get("card") == reply.ref for row in offered))
        abilities = game.engine._activated_abilities(game.state.cards[mana_source.object_id])
        nonmana, = [ability for ability in abilities if not ability.mana_ability]
        mana, = [row for row in offered if row["id"].startswith(f"activate:{mana_source.ref}:")]
        before = authoritative_state_hash(game.state)
        for request in ({"action_id": f"cast:{reply.ref}", "targets": [creature.ref], "pay": "auto"},
                        {"action_id": f"activate:{mana_source.ref}:{nonmana.ability_id}", "pay": "auto"}):
            self.assertFalse(game.act("pilot:B", request).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.assertTrue(game.act("pilot:B", {"action_id": mana["id"]}).ok)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertFalse(split_second_is_active(game.engine))
        self.assertEqual([opponent.object_id], [item.card_object_id for item in game.state.stack])
        self.assertTrue(any(row.get("card") == reply.ref for row in self.offers(game, "B")))
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_actual_creature_spell_remains_open_and_copy_scope_uses_current_controller(self):
        game = self.session(74234002)
        source = self.add(game.engine, SOURCE)
        creature = self.add(game.engine, "Grizzly Bears", zone="hand")
        opponent = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="split-grant-opponent")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "G": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", creature)
        self.assertFalse(split_second_is_active(game.engine))
        self.resolve_top(game)
        self.cast_for(game, "B", opponent, [creature.ref])
        self.assertFalse(split_second_is_active(game.engine))
        self.replay(game, load=True)
        original, = game.state.stack
        game.engine._copy_stack_item(target=original, controller="A", targets=[source.ref],
                                     target_groups={}, reason="Current spell-controller boundary witness")
        self.assertTrue(split_second_is_active(game.engine))
        with patch.object(game.engine, "semantic_program_is_current_trusted", return_value=False):
            self.assertFalse(split_second_is_active(game.engine))
