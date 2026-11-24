"""A controlled creature's actual combat damage to a creature offers one quest counter."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.optional_counter_capability_shapes import CONTROLLED_COMBAT_QUEST_COUNTER_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Quest for the Gemblades"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class ControlledCreatureCombatOptionalSourceQuestCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "combat-quest.sqlite3"
        build_fixture_database([basic, FIXTURES / "controlled-creature-combat-optional-source-quest-counter-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled creature damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        return record, {program.event: program for program in semantics.programs_for_oracle(record.oracle_id)}

    def test_original_keeps_single_combat_quest_counter_and_its_paid_four_counter_activation(self):
        record, programs = self.programs()
        self.assertEqual({"damage.dealt", "activate"}, set(programs))
        trigger = programs["damage.dealt"]
        self.assertIn(CAPABILITY, trigger.capability_dependencies)
        self.assertIsNone(trigger.target_schema)
        wrapper, = trigger.effects
        self.assertEqual("offer_optional_counter_placement", wrapper["op"])
        effect = wrapper["effect"]
        self.assertEqual(("$source.zone_object", "quest", 1), (effect["card"], effect["counter"], effect["amount"]))
        self.assertEqual(4, programs["activate"].effects[0]["amount"])
        for text in (
            record.oracle_text.replace("combat damage", "damage"),
            record.oracle_text.replace("damage to a creature", "damage to a player"),
            record.oracle_text.replace("a quest counter", "two quest counters", 1),
            record.oracle_text.replace("a creature you control", "a creature", 1),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                    capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                 for face in ir.faces for node in face.nodes))
        unsupported = compile_oracle_card(replace(record, oracle_text=record.oracle_text + " Invent a result."),
                                          capability_registry=self.registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", unsupported.status)

    def test_raw_quest_binds_creature_combat_and_exact_original_source(self):
        _, programs = self.programs()
        trigger = programs["damage.dealt"]
        wrapper, = trigger.effects
        clauses = trigger.event_condition["all"]
        for changed in (
            replace(trigger, event_condition={"all": clauses[:-1]}),
            replace(trigger, event_condition={"all": [*clauses[:-1], {**clauses[-1], "value": 1}]}),
            replace(trigger, effects=({**wrapper, "effect": {**wrapper["effect"], "amount": 2}},)),
            replace(trigger, effects=({**wrapper, "player": "$context.controller"},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"], bound)
        deps = [cap for cap in trigger.capability_dependencies if cap != CAPABILITY]
        changed = replace(trigger, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def before_combat_damage(self, game, attackers, blockers, *, block):
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:A", {"action_id": "attack", "attackers": {row.ref: "B" for row in attackers}})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        assignments = {b.ref: a.ref for a, b in zip(attackers, blockers, strict=True)} if block else {}
        chosen = game.act("pilot:B", {"action_id": "block", "blocks": assignments})
        self.assertTrue(chosen.ok, chosen.summary)

    def combat_fixture(self, seed, count):
        game = self.session(seed)
        quest = self.add(game.engine, NAME)
        attackers = [self.add(game.engine, "Grizzly Bears", ref=f"quest-attacker-{i}") for i in range(count)]
        blockers = [self.add(game.engine, "Grizzly Bears", seat="B", ref=f"quest-blocker-{i}") for i in range(count)]
        for card in attackers:
            card.acquired_control_turn_count = -1
        self.assert_closed(game, quest)
        return game, quest, attackers, blockers

    def test_actual_lethal_creature_combat_offers_each_occurrence_before_the_paid_activation(self):
        game, quest, attackers, blockers = self.combat_fixture(120460613, 2)
        survivor = self.add(game.engine, "Grizzly Bears", ref="quest-surviving-target")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="gemblades-private")
        self.prepare(game)
        self.checkpoint(game)
        self.before_combat_damage(game, attackers, blockers, block=True)
        self.until(game, lambda s: s.state.pending_decision.kind == "trigger.order")
        self.assertEqual(["A"], list(game.state.pending_decision.actors))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in (*attackers, *blockers)))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        self.assertEqual(2, len(options))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "order", "triggers": [row["id"] for row in options]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        chosen = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in options]})
        self.assertTrue(chosen.ok, chosen.summary)
        game = self.replay(game, load=True)
        for choice in ("put", "decline"):
            pending = self.resolve(game)
            self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
            command = {"action_id": "choose", "choice": choice}
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:B", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            result = game.act("pilot:A", command)
            self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[quest.object_id].counters.get("quest", 0))
        self.priority(game, "A")
        action, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith(f"activate:{quest.ref}:")]
        paid = game.act("pilot:A", {"action_id": action["id"], "targets": [survivor.ref], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", game.state.cards[quest.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.state.cards[survivor.object_id].counters["+1/+1"])
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_player_damage_fight_and_prevented_combat_do_not_offer_quest(self):
        game, quest, attackers, blockers = self.combat_fixture(120460614, 1)
        self.prepare(game)
        self.checkpoint(game)
        self.before_combat_damage(game, attackers, blockers, block=False)
        self.until(game, lambda s: s.state.step == "combat_damage" and s.state.pending_decision.kind == "priority")
        self.assertEqual(38, game.state.players["B"].life)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

        game, quest, attackers, blockers = self.combat_fixture(120460615, 1)
        fight = self.add(game.engine, "Prey Upon", zone="hand")
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", fight, [attackers[0].ref, blockers[0].ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in (*attackers, *blockers)))
        self.assertEqual(0, game.state.cards[quest.object_id].counters.get("quest", 0))
        self.replay(game, load=True)

        game, quest, attackers, blockers = self.combat_fixture(120460616, 1)
        fog = self.add(game.engine, "Fog", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        self.prepare(game)
        self.checkpoint(game)
        self.before_combat_damage(game, attackers, blockers, block=True)
        self.cast_for(game, "B", fog)
        self.resolve_top(game)
        self.until(game, lambda s: s.state.step == "combat_damage" and s.state.pending_decision.kind == "priority")
        self.assertFalse(game.state.stack)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in (*attackers, *blockers)))
        self.assertEqual(0, game.state.cards[quest.object_id].counters.get("quest", 0))
        self.replay(game, load=True)

    def test_native_combat_witness_kills_omitted_accepted_quest_counter_mutation(self):
        from quorune.semantic_choices.optional_counter_placement import OptionalCounterPlacementHandler

        complete = OptionalCounterPlacementHandler.complete

        def drop_quest(handler, continuation, response, query):
            result = complete(handler, continuation, response, query)
            if any(effect.get("counter") == "quest" for effect in result.prepend_effects):
                return replace(result, prepend_effects=())
            return result

        with patch.object(OptionalCounterPlacementHandler, "complete", new=drop_quest):
            with self.assertRaisesRegex(AssertionError, "1 != 0"):
                self.test_actual_lethal_creature_combat_offers_each_occurrence_before_the_paid_activation()
