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
from quorune.compiler.direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_fixed_direct_target_predicates import record


STONE = "Magewright's Stone"
SUBJECT = "target creature that has an activated ability with {T} in its cost"
CAPABILITY = "target.permanent.characteristic_predicate"
PROFILE = "commander_review"
FIXTURE = ROOT / "tests/fixtures/magewright-stone-target-cards.json"


class ActivatedTapCostTargetCompilerTests(unittest.TestCase):
    def test_closed_tap_symbol_target_preserves_the_cost_and_untap_result(self):
        text = f"{{1}}, {{T}}: Untap {SUBJECT}."
        artifact = record(text, suffix=9501, type_line="Artifact")
        registry = load_default_capability_registry()
        ir = compile_oracle_card(artifact, capability_registry=registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text), 1), (node.span.start, node.span.end, node.span.line))
        self.assertTrue(node.cost["tap_source"])
        self.assertEqual(1, node.cost["mana"]["GENERIC"])
        self.assertEqual([{"op": "untap", "card": "$target.0"}], list(node.effects))
        self.assertEqual({
            "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
            "types_any": ["creature"], "predicate": "creature_with_activated_tap_cost",
        }, dict(node.target_schema))
        self.assertEqual(direct_permanent_target_spec(SUBJECT),
                         DirectPermanentTargetSpec.from_target_schema(node.target_schema))
        self.assertIn(CAPABILITY, node.capability_dependencies)
        self.assertIn("target.revalidate_resolution", node.capability_dependencies)

        for unsupported in (
            text.replace("{T} in its cost", "{Q} in its cost"),
            text.replace("{T} in its cost", "a tap cost"),
            text.replace("target creature", "target artifact"),
            text.replace("target creature", "target creature card in your graveyard"),
        ):
            with self.subTest(unsupported=unsupported):
                neighbor = replace(artifact, oracle_text=unsupported)
                self.assertNotEqual("exact", compile_oracle_card(neighbor, capability_registry=registry).status)


class ActivatedTapCostTargetNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "magewright-stone.sqlite3"
        build_fixture_database(FIXTURE, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Activated tap-cost target", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def actions(self, game, seat):
        packet = game.packet(f"pilot:{seat}", full=True)
        return packet["decision"]["ctx"]["legal"]["actions"]

    def stone_offer(self, game, stone):
        self.priority(game, "A")
        action, = (row for row in self.actions(game, "A") if row["id"].startswith(f"activate:{stone.ref}:"))
        return action

    def submit(self, game, action, target, *, seat="A"):
        return game.act(f"pilot:{seat}", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})

    def assert_private_projection(self, game, hidden):
        for seat in "ABCD":
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            if seat == hidden.owner:
                self.assertIn(hidden.ref, packet)
            else:
                self.assertNotIn(hidden.ref, packet)
                self.assertNotIn(hidden.object_id, packet)

    def test_actual_stone_offers_current_symbol_costs_across_seats_and_rejects_invalid_commands(self):
        game = self.session(10705001)
        stone = self.add(game.engine, STONE, zone="hand")
        mana = self.add(game.engine, "Llanowar Elves", seat="B")
        nonmana = self.add(game.engine, "Master Decoy", seat="C")
        verbal = self.add(game.engine, "Heritage Druid", seat="D")
        untap_cost = self.add(game.engine, "Gilder Bairn", seat="D")
        vanilla = self.add(game.engine, "Grizzly Bears", seat="B")
        artifact = self.add(game.engine, "Sol Ring", seat="C")
        hidden = self.add(game.engine, "Llanowar Elves", seat="D", zone="hand", ref="private-stone-elf")
        for card in (stone, mana, nonmana, verbal, untap_cost, vanilla, artifact):
            self.assert_closed(game, card)
        for card in (mana, vanilla, artifact, untap_cost):
            card.tapped = True
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", stone)
        self.assertIsNone(self.resolve(game))
        action = self.stone_offer(game, stone)
        self.assertEqual({mana.ref, nonmana.ref}, set(action["target_schema"]["legal_refs"]))
        self.assert_private_projection(game, hidden)
        before = authoritative_state_hash(game.state)
        journal_size = len(game.commands)
        for target in (verbal, untap_cost, vanilla, artifact, hidden):
            with self.subTest(illegal=target.ref):
                self.assertFalse(self.submit(game, action, target).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.assertEqual(journal_size, len(game.commands))
        self.assertFalse(self.submit(game, action, mana, seat="B").ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(journal_size, len(game.commands))
        result = self.submit(game, action, mana)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(self.current(game, stone).tapped)
        self.assertTrue(self.current(game, mana).tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.priority(game, "C")
        self.assertFalse(any(row["id"].startswith(f"activate:{nonmana.ref}:") for row in self.actions(game, "C")))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.current(game, mana).tapped)
        self.assertTrue(self.current(game, vanilla).tapped)
        self.assertTrue(self.current(game, artifact).tapped)
        self.assert_private_projection(game, hidden)
        self.replay(game, load=True)

    def test_actual_granted_tap_cost_qualifies_and_losing_its_grant_cancels_the_result(self):
        game = self.session(10705002)
        stone = self.add(game.engine, STONE)
        aura = self.add(game.engine, "Hypervolt Grasp", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        backup = self.add(game.engine, "Llanowar Elves", seat="C")
        response = self.add(game.engine, "Disenchant", seat="D", zone="hand")
        hidden = self.add(game.engine, "Master Decoy", seat="D", zone="hand", ref="private-grant-decoy")
        target.tapped = True
        for card in (stone, aura, target, backup, response):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 1})
        game.state.players["D"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.assertEqual([backup.ref], self.stone_offer(game, stone)["target_schema"]["legal_refs"])
        self.cast_for(game, "A", aura, [target.ref])
        self.assertIsNone(self.resolve(game))
        action = self.stone_offer(game, stone)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        result = self.submit(game, action, target)
        self.assertTrue(result.ok, result.summary)
        self.cast_for(game, "D", response, [aura.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.assertFalse(any(a.tap_source for a in game.engine._activated_abilities(self.current(game, target))))
        self.assertIn("Creature", game.engine._effective_card_data(self.current(game, target))["type_line"])
        self.assert_private_projection(game, hidden)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.current(game, target).tapped)
        self.assertTrue(self.current(game, stone).tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_actual_animation_loss_cancels_untap_with_its_tap_ability_still_present(self):
        game = self.session(10705003)
        stone = self.add(game.engine, STONE)
        aura = self.add(game.engine, "Ensoul Artifact", zone="hand")
        target = self.add(game.engine, "Sol Ring", seat="B")
        backup = self.add(game.engine, "Master Decoy", seat="C")
        response = self.add(game.engine, "Disenchant", seat="D", zone="hand")
        target.tapped = True
        for card in (stone, aura, target, backup, response):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["D"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.assertNotIn(target.ref, self.stone_offer(game, stone)["target_schema"]["legal_refs"])
        self.cast_for(game, "A", aura, [target.ref])
        self.assertIsNone(self.resolve(game))
        action = self.stone_offer(game, stone)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        result = self.submit(game, action, target)
        self.assertTrue(result.ok, result.summary)
        self.cast_for(game, "D", response, [aura.ref])
        self.resolve_top(game)
        current = self.current(game, target)
        self.assertTrue(any(a.tap_source for a in game.engine._activated_abilities(current)))
        self.assertNotIn("Creature", game.engine._effective_card_data(current)["type_line"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.current(game, target).tapped)
        self.assertTrue(self.current(game, stone).tapped)
        self.replay(game, load=True)

    def test_binding_rejects_a_dropped_creature_qualification_and_withdrawn_capability(self):
        game = self.session(10705004)
        stone = self.add(game.engine, STONE)
        programs = game.engine.semantics.programs_for_oracle(stone.oracle_id)
        program, = (program for program in programs if program.effects and program.event == "activate")
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry, profile=PROFILE)["strict"])
        widened = replace(program, target_schema={k: v for k, v in program.target_schema.items() if k != "predicate"})
        binding = bind_semantic_program_runtime(widened, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertTrue(any(blocker.startswith("runtime_activated_tap_cost_target:invalid:")
                            for blocker in binding["blockers"]))
        deps = tuple(capability for capability in program.capability_dependencies if capability != CAPABILITY)
        unowned = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(unowned, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{CAPABILITY}", binding["blockers"])

    def test_omitting_symbol_eligibility_breaks_the_actual_offer(self):
        from quorune import target_predicates as owner

        original = owner.target_predicate_matches
        def omit_symbol_check(host, group, row, **context):
            if group.predicate == "creature_with_activated_tap_cost":
                return True
            return original(host, group, row, **context)

        with patch.object(owner, "target_predicate_matches", side_effect=omit_symbol_check), patch(
            "quorune.selection.targeting.target_predicate_matches", side_effect=omit_symbol_check,
        ):
            with self.assertRaises(AssertionError):
                self.test_actual_stone_offers_current_symbol_costs_across_seats_and_rejects_invalid_commands()


if __name__ == "__main__":
    unittest.main()
