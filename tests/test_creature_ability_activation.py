import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.creature_ability_activation import (
    CREATURE_ABILITY_ACTIVATED, CREATURE_ABILITY_ACTIVATION_CAPABILITY,
    creature_ability_activator_condition, publish_creature_ability_activation,
)
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


SOURCE = "Elrond, Moon-Reader"
PROFILE = "commander_review"


class CreatureAbilityActivationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "creature-ability-activation.sqlite3"
        fixtures = (
            "bound-effect-program-cards.json", "creature-ability-activation-cards.json",
            "activation-view-equivalence-cards.json", "public-value-cost-cards.json",
            "activation-public-state-cards.json", "fixed-qualified-stack-copy-cards.json",
            "mana-contribution-draw-cards.json", "cycle-or-discard-event-cards.json",
        )
        build_fixture_database([ROOT / "tests/fixtures" / name for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Creature activation observer", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def actions(self, game, seat):
        return game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]

    def activate(self, game, seat, card, *, targets=None, **extra):
        self.priority(game, seat)
        options = [row for row in self.actions(game, seat) if row["id"].startswith(f"activate:{card.ref}:")]
        if targets:
            options = [row for row in options if set(targets) <= set(row.get("target_schema", {}).get("legal_refs", ()))]
        offer, = options
        response = {"action_id": offer["id"], "pay": "auto", **extra}
        if targets is not None:
            response["targets"] = list(targets)
        result = game.act(f"pilot:{seat}", response)
        self.assertTrue(result.ok, result.summary)
        return offer

    def draws(self, game, elrond):
        return [item for item in game.state.stack if item.source_object_id == elrond.object_id
                and item.context.get("event") == CREATURE_ABILITY_ACTIVATED]

    def assert_trigger(self, game, elrond, actor, source, incarnation):
        trigger, = self.draws(game, elrond)
        self.assertEqual(actor, trigger.controller)
        self.assertEqual(source.ref, trigger.context["card"])
        self.assertEqual(incarnation, trigger.context["card_object_identity"])
        return trigger

    def test_original_exact_ability_trigger_and_existing_blink_bind_independently(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        settings = {"capability_registry": self.registry, "capability_profile": PROFILE}
        compiled = compile_oracle_card(card, **settings)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        observer, blink = compiled.faces[0].nodes
        self.assertEqual(CREATURE_ABILITY_ACTIVATED, observer.event)
        self.assertEqual(creature_ability_activator_condition(), observer.event_condition)
        self.assertEqual({"schema_version": 1, "kind": "once_per_turn"}, observer.trigger_limit)
        self.assertEqual("linked-exile-return-v1", blink.template_id)
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(), **settings)
        report = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        trigger, = (item for item in program.abilities if item.event == CREATURE_ABILITY_ACTIVATED)
        deps = tuple(cap for cap in trigger.capability_dependencies if cap != CREATURE_ABILITY_ACTIVATION_CAPABILITY)
        omission = replace(trigger, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        invalid = bind_semantic_program_runtime(omission, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + CREATURE_ABILITY_ACTIVATION_CAPABILITY, invalid["blockers"])
        with patch("quorune.compiler.fixed_public_action_event_bindings._creature_activation_draw_spec", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **settings).status)

    def test_public_opposing_creature_activation_triggers_for_actual_activator(self):
        game = self.session(6500201)
        elrond = self.add(game.engine, SOURCE)
        sailmonger = self.add(game.engine, "Sailmonger", seat="B")
        target = self.add(game.engine, "Llanowar Elves", seat="C")
        self.assert_closed(game, elrond)
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        hand = set(game.state.players["A"].zones["hand"])
        self.activate(game, "A", sailmonger, targets=[target.ref])
        self.assertEqual("B", game.state.cards[sailmonger.object_id].controller)
        cause, draw = game.state.stack
        self.assertEqual(("activated_ability", "A"), (cause.kind, cause.controller))
        self.assertEqual(draw, self.assert_trigger(game, elrond, "A", sailmonger, sailmonger.logical_object_id))
        self.assertEqual(hand, set(game.state.players["A"].zones["hand"]))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        drawn_id, = set(game.state.players["A"].zones["hand"]) - hand
        drawn = game.state.cards[drawn_id].ref
        self.assertIn(drawn, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(drawn, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        self.resolve_top(game)
        self.activate(game, "A", sailmonger, targets=[target.ref])
        self.assertEqual([], self.draws(game, elrond))
        self.resolve_top(game)
        self.replay(game)

    def test_creature_mana_is_queued_on_standalone_and_undo_respects_the_observer(self):
        for mana_source, color, destination in (("Wild Cantor", "U", "graveyard"),
                                                ("Llanowar Elves", "G", "battlefield")):
            with self.subTest(source=mana_source):
                game = self.session(6500210 + int(color == "G"))
                elrond = self.add(game.engine, SOURCE)
                source = self.add(game.engine, mana_source)
                source.acquired_control_turn_count = -1
                self.assert_closed(game, elrond)
                self.prepare(game)
                self.checkpoint(game)
                incarnation = source.logical_object_id
                opts = {"mana_output": {color: 1}} if mana_source == "Wild Cantor" else {}
                self.activate(game, "A", source, **opts)
                self.assertEqual(destination, game.state.cards[source.object_id].zone)
                self.assertEqual(1, game.state.players["A"].mana_pool[color])
                self.assert_trigger(game, elrond, "A", source, incarnation)
                self.assertFalse(any(row["id"].startswith("undo-mana:") for row in self.actions(game, "A")))
                self.assertFalse(any(item.kind == "activated_ability" for item in game.state.stack))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.replay(game)

    def test_mana_payment_publishes_above_cast_and_late_failure_rolls_back(self):
        game = self.session(6500221)
        elrond = self.add(game.engine, SOURCE)
        cantor = self.add(game.engine, "Wild Cantor")
        target = self.add(game.engine, "Llanowar Elves", seat="B")
        spell = self.add(game.engine, "Unsummon", zone="hand")
        self.assert_closed(game, elrond)
        self.prepare(game)
        self.checkpoint(game)
        offer = self.action(game, "A", spell)
        command = {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        incarnation = cantor.logical_object_id

        def fail_after_publish(host, activation):
            observed = publish_creature_ability_activation(host, activation)
            if observed:
                raise GameRuleError("Late payment failure fixture")
            return observed

        with patch("quorune.mana_activation.publish_creature_ability_activation", side_effect=fail_after_publish) as called:
            result = game.act("pilot:A", command)
            self.assertFalse(result.ok, result.summary)
            self.assertIn("Late payment failure fixture", result.summary)
            called.assert_called_once()
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual("battlefield", game.state.cards[cantor.object_id].zone)
        self.assertEqual([], self.draws(game, elrond))
        self.cast_for(game, "A", spell, [target.ref])
        cause, trigger = game.state.stack
        self.assertEqual(spell.object_id, cause.card_object_id)
        self.assertEqual(trigger, self.assert_trigger(game, elrond, "A", cantor, incarnation))
        self.assertEqual("graveyard", game.state.cards[cantor.object_id].zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.replay(game)

    def test_other_actors_nonzoned_creature_cards_and_activation_copies_do_not_count(self):
        game = self.session(6500231)
        elrond = self.add(game.engine, SOURCE)
        hydra = self.add(game.engine, "Feral Hydra", seat="B")
        hydra.counters["+1/+1"] = 1
        cycling = self.add(game.engine, "Curator of Mysteries", zone="hand")
        plain = self.add(game.engine, "Plains")
        self.assert_closed(game, elrond)
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["B"].mana_pool["C"] = 3
        self.checkpoint(game)
        self.activate(game, "A", plain)
        self.assertEqual([], self.draws(game, elrond))
        self.assertTrue(any(row["id"].startswith("undo-mana:") for row in self.actions(game, "A")))
        self.activate(game, "A", cycling)
        self.assertEqual([], self.draws(game, elrond))
        self.resolve_top(game)
        self.activate(game, "B", hydra)
        self.assertEqual([], self.draws(game, elrond))
        self.resolve_top(game)
        self.replay(game)

        game = self.session(6500232)
        elrond = self.add(game.engine, SOURCE, zone="hand")
        hydra = self.add(game.engine, "Feral Hydra", seat="B")
        hydra.counters["+1/+1"] = 1
        copier = self.add(game.engine, "Lithoform Engine")
        timing = self.add(game.engine, "Vedalken Orrery")
        self.assert_closed(game, timing)
        self.prepare(game, mana={"C": 11, "U": 1})
        self.checkpoint(game)
        self.activate(game, "A", hydra)
        causing_activation, = game.state.stack
        self.cast_for(game, "A", elrond)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[elrond.object_id].zone)
        self.assertEqual([causing_activation.ref], [item.ref for item in game.state.stack])
        game = self.replay(game, load=True)
        self.activate(game, "A", copier, targets=[causing_activation.ref])
        self.resolve_top(game)
        self.assertEqual(2, len(game.state.stack))
        self.assertEqual([], self.draws(game, elrond))
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual(3, game.state.cards[hydra.object_id].counters["+1/+1"])
        self.activate(game, "A", hydra)
        self.assert_trigger(game, elrond, "A", hydra, hydra.logical_object_id)
        self.replay(game)

    def test_elrond_zero_target_activation_still_triggers_without_changing_the_blink(self):
        game = self.session(6500241)
        elrond = self.add(game.engine, SOURCE)
        legal = self.add(game.engine, "Llanowar Elves")
        land = self.add(game.engine, "Plains")
        self.assert_closed(game, elrond)
        self.prepare(game, mana={"U": 2, "C": 5})
        self.checkpoint(game)
        current, = [row for row in self.actions(game, "A") if row["id"].startswith(f"activate:{elrond.ref}:")]
        refs = current["target_schema"]["legal_refs"]
        self.assertIn(legal.ref, refs)
        self.assertNotIn(land.ref, refs)
        self.assertNotIn(elrond.ref, refs)
        self.activate(game, "A", elrond, targets=[])
        causing_activation, draw = game.state.stack
        self.assertFalse(causing_activation.targets)
        self.assertEqual(draw, self.assert_trigger(game, elrond, "A", elrond, elrond.logical_object_id))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[legal.object_id].zone)
        self.replay(game)

    def test_mana_during_contribution_defers_the_draw_until_the_spell_finishes(self):
        game = self.session(6500251)
        spell = self.add(game.engine, "Minds Aglow", zone="hand")
        elrond = self.add(game.engine, SOURCE, seat="B")
        cantor = self.add(game.engine, "Wild Cantor", seat="B")
        self.assert_closed(game, elrond)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)

        def contribute(session, actor, amount):
            result = session.act(f"pilot:{actor}", {"action_id": "choose", "x": amount})
            self.assertTrue(result.ok, result.summary)

        self.assertEqual("semantic.choice", self.resolve(game).kind)
        contribute(game, "A", 0)
        before = len(game.state.players["B"].zones["hand"])
        payload = game.packet("pilot:B", full=True)["decision"]["ctx"]
        candidate, = [item for item in payload["legal_actions"]
                      if item["id"].startswith("mana-contribution:mana:") and "{U}" in item["label"]]
        incarnation = cantor.logical_object_id
        result = game.act("pilot:B", {"action_id": candidate["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[cantor.object_id].zone)
        self.assertEqual(1, game.state.players["B"].mana_pool["U"])
        self.assertEqual(before, len(game.state.players["B"].zones["hand"]))
        self.assertEqual([], self.draws(game, elrond))
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.assertTrue(game.state.pending_trigger_batches)
        game = self.replay(game, load=True)
        contribute(game, "B", 1)
        contribute(game, "C", 0)
        contribute(game, "D", 0)
        self.assert_trigger(game, elrond, "B", cantor, incarnation)
        self.assertEqual(before + 1, len(game.state.players["B"].zones["hand"]))
        self.resolve_top(game)
        self.assertEqual(before + 2, len(game.state.players["B"].zones["hand"]))
        self.replay(game)
