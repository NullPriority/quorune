"""CR 602.2b, 608.2h and the pinned Syr Ginger ruling."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase, CardRecord
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import SCALAR_REFERENCE_CONTEXT
from scripts.build_test_database import build_fixture_database


NAME = "Syr Ginger, the Meal Ender"
ORACLE_ID = "2eb16535-718f-4b88-92bc-96429d796327"
FIXTURE = "source-sacrifice-power-life-cards.json"
PROFILE = "commander_review"
CAPABILITIES = {"activation.source_zone_change.fixed", "life.change.effect", "quantity_expression.scalar_effect_amount"}


class SourceSacrificePowerLifeCompilerTests(unittest.TestCase):
    def test_bound_source_pronoun_needs_the_complete_ordinary_price_and_result(self):
        registry = load_default_capability_registry()
        original = "{2}, {T}, Sacrifice this creature: You gain life equal to its power."
        card = CardRecord(
            oracle_id="fixture:source-sacrifice-power-life", name="Source Power Witness", mana_cost="{2}",
            mana_value=2, type_line="Artifact Creature", oracle_text=original, power="2", toughness="1",
            loyalty=None, defense=None, colors=(), color_identity=(), keywords=(), produced_mana=(),
            layout="normal", released_at=None, legalities={"commander": "legal"}, faces=(), raw={},
        )

        def compile_text(text):
            return compile_oracle_card(replace(card, oracle_text=text), capability_registry=registry,
                                       capability_profile=PROFILE)

        node, = compile_text(original).faces[0].nodes
        self.assertTrue(node.exact, node.residual_ids)
        self.assertEqual(original, node.text)
        self.assertLessEqual(CAPABILITIES, set(node.capability_dependencies))
        self.assertEqual("source_characteristic", node.effects[0]["delta"]["origin"])
        for text in (
            original.replace("{T}, ", ""),
            original.replace("{2}, ", ""),
            original.replace("Sacrifice this creature", "Sacrifice another creature"),
            original.replace("its power", "its toughness"),
            original.replace("You gain", "Target player gains"),
            original + " Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_text(text).status)


class SourceSacrificePowerLifeNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        parts = [fixtures / FIXTURE]
        supports = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "ability-label-composition-cards.json": {"Island", "Tormod's Crypt"},
            "paid-sacrifice-creature-stat-cards.json": {"Hydrosurge"},
        }
        for filename, names in supports.items():
            data = json.loads((fixtures / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / filename
            part.write_text(json.dumps(data), encoding="utf-8")
            parts.append(part)
        path = folder / "source-sacrifice-power.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid source power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def activate_offer(self, game, source):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]

    def assert_paid_source(self, game, source, incarnation, power, *, owner="A"):
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotEqual(incarnation, game.state.cards[source.object_id].logical_object_id)
        self.assertIn(source.object_id, game.state.players[owner].zones["graveyard"])
        item, = game.state.stack
        snapshot = item.context[SCALAR_REFERENCE_CONTEXT]["source"]
        self.assertEqual((source.object_id, incarnation, "battlefield", power),
                         (snapshot["object_id"], snapshot["logical_object_id"], snapshot["zone"], snapshot["power"]))
        self.assertEqual(("A", incarnation), (item.controller, item.context["source_logical_object_id"]))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

    def test_original_keeps_three_exact_lines_and_rejects_a_changed_price(self):
        game = self.session(608200131)
        source = self.add(game.engine, NAME)
        self.assert_closed(game, source)
        record = self.db.lookup(NAME, fuzzy=False)
        self.assertEqual(ORACLE_ID, record.oracle_id)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        static, trigger, activation = ir.faces[0].nodes
        self.assertEqual(("continuous-self-query-characteristics-v1", "fixed-counter-public-zone-trigger-v1"),
                         (static.template_id, trigger.template_id))
        self.assertEqual({"GENERIC": 2}, {key: amount for key, amount in activation.cost["mana"].items() if amount})
        self.assertTrue(activation.cost["tap_source"] and activation.cost["sacrifice_source"])
        program, = (p for p in game.engine.semantics.programs_for_oracle(record.oracle_id)
                    if p.event == "activate" and p.effects)
        price = deepcopy(thaw_value(program.cost_schema))
        price["mana"]["GENERIC"] = 1
        changed = replace(program, cost_schema=price)
        verdict = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(verdict["strict"], verdict)
        self.assertTrue(any("activation catalog" in reason for reason in verdict["blockers"]), verdict)
        for dependency in CAPABILITIES:
            deps = tuple(value for value in program.capability_dependencies if value != dependency)
            changed = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            with self.subTest(dependency=dependency):
                self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                               profile=PROFILE)["strict"])

    def test_native_artifact_departure_changes_paid_power_and_replays_to_the_activator(self):
        game = self.session(608200132)
        source = self.add(game.engine, NAME)
        source.owner = "C"
        source.acquired_control_turn_count = -1
        crypt = self.add(game.engine, "Tormod's Crypt")
        self.add(game.engine, "Island", seat="D", zone="hand", ref="syr-private-island")
        self.assert_closed(game, source)
        self.assert_closed(game, crypt)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        original = source.logical_object_id
        self.assertEqual(3, game.engine._numeric_stat(source.object_id, "power"))
        action, = self.activate_offer(game, crypt)
        result = game.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(source.object_id, "power"))
        scry = game.state.pending_decision.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual("ordered_partition", scry["shape"])
        self.assertEqual(1, len(scry["legal_refs"]))
        result = game.act("pilot:A", {"action_id": "choose", "cards": {"top": scry["legal_refs"], "bottom": []}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        action, = self.activate_offer(game, source)
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assert_paid_source(game, source, original, 4, owner="C")
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        game = self.replay(game, load=True)
        self.assert_paid_source(game, source, original, 4, owner="C")
        self.assertIsNone(self.resolve(game))
        self.assertEqual([44, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        for seat in "ABC":
            self.assertNotIn("syr-private-island", json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_native_negative_power_is_zero_life_gain_after_a_real_spell(self):
        game = self.session(608200133)
        source = self.add(game.engine, NAME)
        source.acquired_control_turn_count = -1
        spell = self.add(game.engine, "Hydrosurge", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", spell, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(-2, game.engine._numeric_stat(source.object_id, "power"))
        self.priority(game, "A")
        action, = self.activate_offer(game, source)
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(-2, game.state.stack[-1].context[SCALAR_REFERENCE_CONTEXT]["source"]["power"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_native_offer_and_command_require_untapped_mature_source_and_mana(self):
        for index, reason in enumerate(("tapped", "recent", "mana")):
            with self.subTest(reason=reason):
                game = self.session(608200134 + index)
                source = self.add(game.engine, NAME)
                source.tapped = reason == "tapped"
                source.acquired_control_turn_count = game.state.players["A"].turns_begun if reason == "recent" else -1
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 1 if reason == "mana" else 2})
                self.checkpoint(game)
                ability, = game.engine._activated_abilities(source)
                action = f"activate:{source.ref}:{ability.ability_id}"
                self.assertEqual([], self.activate_offer(game, source))
                before = authoritative_state_hash(game.state)
                result = game.act("pilot:A", {"action_id": action, "pay": "auto"})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
