"""Repeated printed X, bounded legal targets and equal committed damage."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_declared_effect_amounts import generic_spell
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import CastXAmountSpec, SCALED_ANNOUNCED_TARGET_DAMAGE_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.announced_x_damage_sets import scaled_announced_target_damage_schema
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


CARD = "Crackle with Power"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ScaledAnnouncedTargetDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "scaled-announced-target-damage.sqlite3"
        build_fixture_database([FIXTURES / name for name in (
            "scaled-announced-target-damage-cards.json", "compiled-reviewed-spell-cards.json",
            "activation-result-views-cards.json", "bound-effect-program-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Scaled announced target damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_closed_compiler_targets_and_independent_amount_and_target_authorities(self):
        card = self.db.lookup(CARD, fuzzy=False)
        name = "Scaled Spell Witness"
        text = card.oracle_text.replace(CARD, name)
        record = replace(generic_spell(text, mana_cost=card.mana_cost), name=name)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertEqual(scaled_announced_target_damage_schema(), node.target_schema)
        effect, = node.effects
        self.assertEqual(CastXAmountSpec(coefficient=5, schema_version=4), CastXAmountSpec.from_dict(effect["amount"]))
        for original, changed in (
            ("five times X", "four times X"), ("up to X targets", "X targets"),
            ("each of up to X targets", "a number of targets chosen later"),
        ):
            with self.subTest(changed=changed):
                mutated = replace(record, oracle_text=text.replace(original, changed))
                self.assertNotEqual("exact", compile_oracle_card(mutated, capability_registry=self.registry,
                                                                capability_profile=PROFILE).status)
        self.assertNotEqual("exact", compile_oracle_card(replace(record, mana_cost="{R}{R}"),
                                                        capability_registry=self.registry, capability_profile=PROFILE).status)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        for capability in (SCALED_ANNOUNCED_TARGET_DAMAGE_CAPABILITY, "quantity_expression.declared_effect_amount",
                           "target.cardinality.mana_announced_x", "target.revalidate_resolution"):
            with self.subTest(capability=capability):
                deps = [key for key in program.capability_dependencies if key != capability]
                altered = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                self.assertFalse(bind_semantic_program_runtime(altered, capability_registry=self.registry,
                                                               profile=PROFILE)["strict"])
        effect = dict(program.effects[0])
        effect["amount"] = CastXAmountSpec(coefficient=3, schema_version=2).to_dict()
        altered = replace(program, effects=[effect])
        self.assertFalse(bind_semantic_program_runtime(altered, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])

    def offer(self, game, source, *, generic):
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": generic, "R": 2})
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"] == "cast:" + source.ref)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def cast_at_x(self, game, action, x, refs):
        command = {"action_id": action["id"], "x": x, "targets": refs, "pay": "auto"}
        self.reject(game, "D", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

    def resolve_damage_amounts(self, game):
        from quorune import fixed_damage_set

        with patch.object(fixed_damage_set, "resolve_damage_batch", wraps=fixed_damage_set.resolve_damage_batch) as commit:
            self.assertIsNone(self.resolve(game))
        batches = [call.args[1] for call in commit.call_args_list if call.args[1]]
        self.assertLessEqual(len(batches), 1)
        return [proposal.amount for batch in batches for proposal in batch]

    def test_x_two_pays_six_generic_and_hits_two_distinct_targets_for_ten_each(self):
        game = self.session(10731001)
        source = self.add(game.engine, CARD, zone="hand")
        creature = self.add(game.engine, "Darksteel Myr", seat="B")
        artifact = self.add(game.engine, "Sol Ring", seat="C")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="scaled-x-private")
        offer = self.offer(game, source, generic=6)
        self.assertEqual(2, offer["cost_options"][0]["choice_schema"]["x"]["maximum"])
        self.assertEqual("x", offer["target_schema"]["maximum_from"])
        self.assertNotIn("damage_allocation", offer["target_schema"])
        self.assertNotIn(artifact.ref, offer["target_schema"]["legal_refs"])
        command = {"action_id": offer["id"], "pay": "auto"}
        for x, refs in (
            (True, ["B"]), (-1, []), (3, ["B"]), (1, ["B", "C"]),
            (2, ["B", "B"]), (2, [artifact.ref]), (0, ["B"]),
        ):
            self.reject(game, "A", {**command, "x": x, "targets": refs})
        self.cast_at_x(game, offer, 2, [creature.ref, "C"])
        game = self.replay(game, load=True)
        self.assertEqual([10, 10], self.resolve_damage_amounts(game))
        self.assertEqual(10, game.state.cards[creature.object_id].marked_damage)
        self.assertEqual([40, 40, 30, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_zero_choices_are_legal_and_x_one_pays_three_generic(self):
        for x, refs in ((0, []), (1, []), (1, ["B"])):
            with self.subTest(x=x, refs=refs):
                game = self.session(10731010 + int(bool(refs)) + x * 2)
                source = self.add(game.engine, CARD, zone="hand")
                offer = self.offer(game, source, generic=3 * x)
                self.cast_at_x(game, offer, x, refs)
                game = self.replay(game, load=True)
                self.assertEqual([5] if refs else [], self.resolve_damage_amounts(game))
                self.assertEqual(35 if refs else 40, game.state.players["B"].life)
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_lost_target_does_not_reduce_the_locked_amount_for_a_survivor(self):
        for others in (False, True):
            with self.subTest(others=others):
                game = self.session(10731020 + others)
                source = self.add(game.engine, CARD, zone="hand")
                creature = self.add(game.engine, "Darksteel Myr", seat="B")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.add(game.engine, "Island", seat="B")
                offer = self.offer(game, source, generic=6)
                refs = [creature.ref, "C"] if others else [creature.ref]
                self.cast_at_x(game, offer, 2, refs)
                self.cast(game, "B", bounce, [creature.ref])
                self.resolve_top(game)
                self.assertEqual("hand", game.state.cards[creature.object_id].zone)
                game = self.replay(game, load=True)
                self.assertEqual([10] if others else [], self.resolve_damage_amounts(game))
                self.assertEqual(30 if others else 40, game.state.players["C"].life)
                self.replay(game, load=True)
