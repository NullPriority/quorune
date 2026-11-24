"""CR 603.6c observes actual owned card arrivals across their original zones."""

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
from quorune.rules.optional_counter_capability_shapes import OWNED_GRAVEYARD_QUEST_COUNTER_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Quest for Ancient Secrets"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class ControllerGraveyardArrivalQuestCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "owned-graveyard-quest.sqlite3"
        build_fixture_database([basic, FIXTURES / "controller-graveyard-arrival-optional-quest-counter-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Owned graveyard arrival", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        return record, {program.event: program for program in semantics.programs_for_oracle(record.oracle_id)}

    def test_complete_original_keeps_anywhere_counter_and_paid_recycling(self):
        record, programs = self.programs()
        self.assertEqual({"card.enter_graveyard", "activate"}, set(programs))
        trigger = programs["card.enter_graveyard"]
        self.assertIn(CAPABILITY, trigger.capability_dependencies)
        self.assertIsNone(trigger.target_schema)
        wrapper, = trigger.effects
        self.assertEqual("offer_optional_counter_placement", wrapper["op"])
        effect = wrapper["effect"]
        self.assertEqual(("$source.zone_object", "quest", 1), (effect["card"], effect["counter"], effect["amount"]))
        self.assertIn("zone.shuffle.whole_graveyard", programs["activate"].capability_dependencies)
        for text in (
            record.oracle_text.replace("your graveyard", "an opponent's graveyard", 1),
            record.oracle_text.replace("a quest counter", "a charge counter", 1),
            record.oracle_text.replace("a card is put", "a creature card is put", 1),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                        capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                     for face in ir.faces for node in face.nodes))

        unsupported = compile_oracle_card(
            replace(record, oracle_text=record.oracle_text + " Invent an unsupported instruction."),
            capability_registry=self.registry, capability_profile=PROFILE,
        )
        self.assertNotEqual("exact", unsupported.status)
        self.assertTrue(any(CAPABILITY in node.capability_dependencies
                            for face in unsupported.faces for node in face.nodes))

    def test_raw_quest_choice_rejects_wrong_owner_counter_and_missing_authority(self):
        _, programs = self.programs()
        trigger = programs["card.enter_graveyard"]
        wrapper, = trigger.effects
        conditions = trigger.event_condition["all"]
        for changed in (
            replace(trigger, event_condition={"all": [{**conditions[0], "value": 0}, conditions[1]]}),
            replace(trigger, event_condition={"all": [conditions[0], {**conditions[1], "op": "ne"}]}),
            replace(trigger, effects=({**wrapper, "effect": {**wrapper["effect"], "amount": 2}},)),
            replace(trigger, effects=({**wrapper, "player": "$context.owner"},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"], bound)
        deps = [cap for cap in trigger.capability_dependencies if cap != CAPABILITY]
        changed = replace(trigger, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def counter_choice(self, game, choice):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        command = {"action_id": "choose", "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_own_spell_arrival_offers_original_source_put_or_decline(self):
        game = self.session(603604011)
        quest = self.add(game.engine, NAME)
        quest.counters["quest"] = 4
        spell = self.add(game.engine, "Lightning Bolt", zone="hand")
        self.assert_closed(game, quest)
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["D"])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual(37, game.state.players["D"].life)
        game = self.replay(game, load=True)
        self.counter_choice(game, "put")
        self.assertEqual(5, game.state.cards[quest.object_id].counters["quest"])
        self.replay(game, load=True)

    def test_actual_mill_offers_each_card_then_paid_recycling_shuffles_only_the_target(self):
        game = self.session(603604012)
        quest = self.add(game.engine, NAME)
        quest.counters["quest"] = 3
        mill = self.add(game.engine, "Thought Scour", seat="B", zone="hand")
        public = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="quest-public-card")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="quest-private-card")
        self.assert_closed(game, quest)
        self.prepare(game, actor="B", active="B", mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", mill, ["A"])
        pending = self.resolve(game)
        self.assertEqual(("trigger.order", ["A"]), (pending.kind, list(pending.actors)))
        private_view = game.packet("pilot:A", full=True)["decision"]["ctx"]
        refs = [row["id"] for row in private_view["triggers"]]
        self.assertEqual(2, len(refs))
        result = game.act("pilot:A", {"action_id": "order", "triggers": refs})
        self.assertTrue(result.ok, result.summary)
        self.counter_choice(game, "put")
        self.counter_choice(game, "put")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.state.cards[quest.object_id].counters["quest"])
        self.assertEqual(2, len(game.state.players["A"].zones["graveyard"]))
        before = {seat: player.stats.get("shuffle_count", 0) for seat, player in game.state.players.items()}
        self.priority(game, "A")
        action, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith(f"activate:{quest.ref}:")]
        paid = game.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", game.state.cards[quest.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("library", game.state.cards[public.object_id].zone)
        self.assertEqual("library", game.state.cards[mill.object_id].zone)
        self.assertFalse(game.state.players["B"].zones["graveyard"])
        after = {seat: player.stats.get("shuffle_count", 0) for seat, player in game.state.players.items()}
        self.assertEqual({**before, "B": before["B"] + 1}, after)
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_decline_token_death_and_replaced_mill_do_not_add_counters(self):
        game = self.session(603604013)
        quest = self.add(game.engine, NAME)
        make = self.add(game.engine, "Raise the Alarm", zone="hand")
        burn = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.assert_closed(game, quest)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", make)
        self.resolve_top(game)
        self.counter_choice(game, "decline")
        token = next(card for card in game.state.cards.values() if card.controller == "A" and card.is_token)
        self.cast_for(game, "B", burn, [token.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[quest.object_id].counters.get("quest", 0))
        self.replay(game, load=True)

        game = self.session(603604014)
        quest = self.add(game.engine, NAME)
        rip = self.add(game.engine, "Rest in Peace")
        mill = self.add(game.engine, "Thought Scour", seat="B", zone="hand")
        self.assert_closed(game, quest)
        self.assert_closed(game, rip)
        self.prepare(game, actor="B", active="B", mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", mill, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(game.state.players["A"].zones["exile"]))
        self.assertFalse(game.state.players["A"].zones["graveyard"])
        self.assertEqual(0, game.state.cards[quest.object_id].counters.get("quest", 0))
        self.replay(game, load=True)

    def test_native_quest_witness_kills_dropped_accepted_counter_mutation(self):
        from quorune.semantic_choices.optional_counter_placement import OptionalCounterPlacementHandler

        complete = OptionalCounterPlacementHandler.complete

        def drop_quest_counter(handler, continuation, response, query):
            result = complete(handler, continuation, response, query)
            if any(effect.get("counter") == "quest" for effect in result.prepend_effects):
                return replace(result, prepend_effects=())
            return result

        with patch.object(OptionalCounterPlacementHandler, "complete", new=drop_quest_counter):
            with self.assertRaisesRegex(AssertionError, "5 != 4"):
                self.test_actual_own_spell_arrival_offers_original_source_put_or_decline()
