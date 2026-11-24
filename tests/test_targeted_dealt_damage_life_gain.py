"""CR 120.4 and 608.2: life gain from a committed targeted damage event."""

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
from quorune.fixed_damage_set_model import DEALT_LIFE_GAIN_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.damage_capability_shapes import TARGETED_DEALT_LIFE_GAIN_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


RITE = "Rite of Consumption"
CORRUPT = "Corrupt"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class TargetedDealtDamageLifeGainTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        directory = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = directory / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = directory / "targeted-dealt-life.sqlite3"
        build_fixture_database([basic, FIXTURES / "targeted-dealt-damage-life-gain-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Targeted dealt damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn(CAPABILITY, program.capability_dependencies)
        return card, program

    def test_complete_originals_keep_their_distinct_damage_amounts_and_targets(self):
        for name, target, amount_kind in (
            (RITE, "player_or_planeswalker", "scalar_effect_amount"),
            (CORRUPT, "damageable", "public_query_effect_amount"),
        ):
            with self.subTest(name=name):
                card, program = self.program(name)
                effect, = program.effects
                self.assertEqual("damage", effect["op"])
                self.assertEqual(2, effect[DEALT_LIFE_GAIN_FIELD])
                self.assertEqual(amount_kind, effect["amount"]["kind"])
                self.assertEqual(target, program.target_schema["predicate"])
                self.assertEqual(name == RITE, program.cost_schema is not None)
                for text in (
                    card.oracle_text.replace("damage dealt this way.", "damage dealt this turn."),
                    card.oracle_text + " Invent an unsupported instruction.",
                    card.oracle_text.replace("creature's power", "creature's toughness")
                        if name == RITE else card.oracle_text.replace("Swamps", "creatures"),
                ):
                    altered = compile_oracle_card(
                        replace(card, oracle_text=text), capability_registry=self.registry,
                        capability_profile=PROFILE,
                    )
                    self.assertNotEqual("exact", altered.status)
        for name in ("Fling", "Tendrils of Corruption"):
            result, semantics = register_card(self.db, self.registry, name)
            self.assertTrue(result["strict_capability_ready"], result["blockers"])
            card = self.db.lookup(name, fuzzy=False)
            for program in semantics.programs_for_oracle(card.oracle_id):
                self.assertNotIn(CAPABILITY, program.capability_dependencies)

    def test_raw_targeted_result_rejects_changed_amount_price_and_dependency(self):
        _, rite = self.program(RITE)
        _, corrupt = self.program(CORRUPT)
        effect, = corrupt.effects
        variants = (
            replace(rite, cost_schema=None),
            replace(corrupt, effects=({**effect, "amount": 5},)),
            replace(corrupt, effects=({**effect, DEALT_LIFE_GAIN_FIELD: True},)),
            replace(corrupt, effects=({**effect, "amount": {**effect["amount"], "coefficient": 2}},)),
            replace(corrupt, target_schema=rite.target_schema),
        )
        for program in variants:
            with self.subTest(program=program.to_dict()):
                result = bind_semantic_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        dependencies = [cap for cap in corrupt.capability_dependencies if cap != CAPABILITY]
        missing = replace(corrupt, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def test_actual_corrupt_counts_current_controlled_swamps_and_gains_full_overkill(self):
        game = self.session(120407031)
        source = self.add(game.engine, CORRUPT, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        departed = self.add(game.engine, "Swamp", ref="corrupt-departing-swamp")
        for index, name in enumerate(("Swamp", "Swamp", "Bayou", "Forest")):
            self.add(game.engine, name, ref=f"corrupt-land-{index}")
        self.add(game.engine, "Swamp", seat="C", ref="corrupt-opponent-swamp")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "B": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", source, [bear.ref])
        self.cast_for(game, "B", bounce, [departed.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[departed.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[bear.object_id].zone)
        self.assertEqual(43, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_corrupt_loses_its_only_target_without_gaining_life(self):
        game = self.session(120407032)
        source = self.add(game.engine, CORRUPT, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        self.add(game.engine, "Swamp")
        self.prepare(game, mana={"C": 5, "B": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", source, [bear.ref])
        self.cast_for(game, "B", bounce, [bear.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[bear.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(40, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_rite_uses_sacrificed_power_and_excludes_prevention_aftermath(self):
        game = self.session(120407033)
        source = self.add(game.engine, RITE, zone="hand")
        paid = self.add(game.engine, "Grizzly Bears", ref="rite-paid-creature")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        palm = self.add(game.engine, "Deflecting Palm", seat="B", zone="hand")
        passage = self.add(game.engine, "Safe Passage", seat="B", zone="hand")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", ref="rite-foreign-creature")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="targeted-life-private")
        for card in (source, growth, palm, passage):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 1, "G": 1})
        game.state.players["B"].mana_pool.update({"C": 2, "W": 2, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", growth, [paid.ref], reserve={"C": 1, "B": 1})
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(paid.object_id, "power"))
        self.priority(game, "A")
        action = self.action(game, "A", source)
        command = {"action_id": action["id"], "targets": ["B"], "sacrifice_cards": [paid.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {**command, "targets": [foreign.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        cast = game.act("pilot:A", command)
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        damage_source = game.state.stack[-1].ref
        self.cast_for(game, "B", palm)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, list(pending.actors)))
        answer = game.act("pilot:B", {"action_id": "choose", "source": damage_source})
        self.assertTrue(answer.ok, answer.summary)
        self.cast_for(game, "B", passage)
        self.resolve_top(game)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, list(pending.actors)))
        self.assertEqual(40, game.state.players["A"].life)
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        palm_option, = [option for option in options if "deflecting palm" in str(option).casefold()]
        command = {"action_id": "choose", "replacement": palm_option["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        answer = game.act("pilot:B", command)
        self.assertTrue(answer.ok, answer.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(35, game.state.players["A"].life)
        self.assertEqual(40, game.state.players["B"].life)
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_native_rite_witness_kills_gain_from_assigned_damage(self):
        from quorune.effect_runtime import damage_life_and_turns
        from quorune.rules.committed_quantity_followups import damage_dealt_life_followup

        with patch.object(damage_life_and_turns, "damage_dealt_life_followup",
                          side_effect=lambda _dealt: damage_dealt_life_followup(5)):
            with self.assertRaisesRegex(AssertionError, "35 != 40"):
                self.test_actual_rite_uses_sacrificed_power_and_excludes_prevention_aftermath()
