"""Native graveyard escape prices and source lifetime for Underworld Breach."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.cast_lifecycles import (
    FIXED_CAST_LIFECYCLE_CONTEXT_FIELD,
    GRANTED_ESCAPE_CAPABILITY,
    GRANTED_ESCAPE_LIFECYCLE_VERSION,
    granted_escape_descriptor,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.stack_counter_followup import captured_stack_target_mana_value
from scripts.build_test_database import build_fixture_database
import test_bounded_hand_casts as hand_cast
from test_controller_creature_keyword_absence import register_card


CARD = "Underworld Breach"
PROFILE = "commander_review"


class UnderworldBreachNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = hand_cast.BoundedHandCastRuntimeTests.start

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "underworld-breach.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "underworld-breach-cards.json",
            fixtures / "bounded-hand-cast-cards.json",
            fixtures / "qualified-attack-cards.json",
            fixtures / "targeted-same-turn-graveyard-return-cards.json",
            fixtures / "same-turn-graveyard-return-sets-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Underworld Breach escape",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def ready(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def fodder(self, game, count):
        return tuple(self.add(game.engine, "Plains", zone="graveyard", ref=f"breach-fodder-{index}")
                     for index in range(count))

    def escape_offers(self, game, card, *, seat="A", face=None):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return [
            (action, price) for action in actions
            if (action.get("card") == card.ref or action["id"] == "cast:" + card.ref)
            and (face is None or action.get("face") == face)
            for price in action.get("cost_options", ())
            if price.get("choice_schema", {}).get("exile_cards", {}).get("count") == 3
        ]

    def escape_command(self, offer, payment, *, targets=(), **costs):
        action, price = offer
        command = {
            "action_id": action["id"],
            "from": "graveyard",
            "cost_option": price["id"],
            "pay": "auto",
            "targets": list(targets),
            "exile_cards": [card.ref for card in payment],
            **costs,
        }
        if action.get("face") is not None:
            command["face"] = action["face"]
        return command

    def test_complete_original_binds_escape_grant_and_every_end_step_sacrifice(self):
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        original = self.db.lookup(CARD, fuzzy=False)
        compiled = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        programs = semantics.programs_for_oracle(original.oracle_id)
        trigger, = (program for program in programs if program.event == "step.begin")
        self.assertIsNone(trigger.target_schema)
        self.assertEqual(({
            "op": "offer_optional_mana_payment",
            "schema_version": 3,
            "player": "$controller",
            "source": "$source.zone_object",
            "payment": None,
        },), tuple(trigger.effects))
        grant, = (program for program in programs if GRANTED_ESCAPE_CAPABILITY in program.capability_dependencies)
        self.assertIn(granted_escape_descriptor(), grant.handlers)

        for old, new in (("nonland card", "artifact card"), ("three other cards", "three other creatures")):
            with self.subTest(new=new):
                modified = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    modified, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def test_actual_escape_uses_selected_face_price_and_exiles_exact_current_owned_cards(self):
        game = self.start(70213801)
        breach = self.add(game.engine, CARD, zone="hand")
        split = self.add(game.engine, "Wear // Tear", zone="graveyard")
        enchantment = self.add(game.engine, "Impact Tremors", seat="B")
        self.add(game.engine, "Sol Ring", seat="B")
        cremate = self.add(game.engine, "Cremate", seat="B", zone="hand")
        self.add(game.engine, "Swamp", seat="B")
        payment = self.fodder(game, 4)
        foreign = self.add(game.engine, "Plains", seat="B", zone="graveyard", ref="breach-foreign-fodder")
        private = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand", ref="breach-unrelated-private-hand")
        self.ready(game, breach, split, cremate, mana={"C": 1, "R": 1, "W": 1})
        self.cast_for(game, "A", breach, reserve={"W": 1})
        self.assertIsNone(self.resolve(game))

        first_offer, = self.escape_offers(game, split, face="Tear")
        self.assertFalse(self.escape_offers(game, split, face="Wear"))
        price = first_offer[1]
        self.assertEqual({"W": 1}, {key: amount for key, amount in price["requirements"].items() if amount})
        self.assertEqual({card.ref for card in payment}, set(price["choice_schema"]["exile_cards"]["legal_refs"]))
        self.cast(game, "B", cremate, [payment[-1].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", self.current(game, payment[-1]).zone)

        offer, = self.escape_offers(game, split, face="Tear")
        self.assertEqual({card.ref for card in payment[:-1]}, set(offer[1]["choice_schema"]["exile_cards"]["legal_refs"]))
        command = self.escape_command(offer, payment[:-1], targets=[enchantment.ref])
        self.reject(game, "D", command)
        for refs in (
            [payment[0].ref] * 3,
            [split.ref, payment[0].ref, payment[1].ref],
            [foreign.ref, payment[0].ref, payment[1].ref],
            [payment[-1].ref, payment[0].ref, payment[1].ref],
            [payment[0].ref, payment[1].ref],
        ):
            self.reject(game, "A", {**command, "exile_cards": refs})

        self.accept(game, "A", command)
        spell, = game.state.stack
        self.assertEqual(("graveyard", "Tear", split.object_id),
                         (spell.context["cast_origin"], spell.chosen_face, spell.card_object_id))
        lifecycle = spell.context[FIXED_CAST_LIFECYCLE_CONTEXT_FIELD]
        self.assertEqual(("escape", GRANTED_ESCAPE_LIFECYCLE_VERSION), (lifecycle["kind"], lifecycle["schema_version"]))
        self.assertEqual(1, captured_stack_target_mana_value(game.engine, spell.ref))
        self.assertTrue(all(self.current(game, card).zone == "exile" for card in payment))
        self.assertEqual("graveyard", self.current(game, foreign).zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, enchantment).zone)
        self.assertEqual("graveyard", self.current(game, split).zone)
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game)

    def test_actual_escape_pays_announced_x_and_excludes_unpayable_and_land_cards(self):
        game = self.start(70213802)
        breach = self.add(game.engine, CARD, zone="hand")
        serpent = self.add(game.engine, "Stonecoil Serpent", zone="graveyard")
        vision = self.add(game.engine, "Ancestral Vision", zone="graveyard")
        payment = self.fodder(game, 3)
        self.ready(game, breach, serpent, vision, mana={"C": 3, "R": 1})
        self.cast_for(game, "A", breach, reserve={"C": 2})
        self.assertIsNone(self.resolve(game))

        self.assertFalse(self.escape_offers(game, vision))
        self.assertFalse(self.escape_offers(game, payment[0]))
        offer, = self.escape_offers(game, serpent)
        command = self.escape_command(offer, payment, x=2)
        self.reject(game, "A", {**command, "x": 3})
        self.reject(game, "A", {**command, "action_id": "cast:" + vision.ref})
        self.accept(game, "A", command)
        spell, = game.state.stack
        self.assertEqual(2, spell.x_value)
        self.assertEqual(2, captured_stack_target_mana_value(game.engine, spell.ref))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertTrue(all(self.current(game, card).zone == "exile" for card in payment))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", {"+1/+1": 2}),
                         (self.current(game, serpent).zone, self.current(game, serpent).counters))
        self.assertEqual("graveyard", self.current(game, vision).zone)
        self.replay(game)

    def test_actual_opponent_end_step_sacrifices_breach_and_revokes_the_stale_offer(self):
        game = self.start(70213803)
        breach = self.add(game.engine, CARD, zone="hand")
        orrery = self.add(game.engine, "Vedalken Orrery")
        flicker = self.add(game.engine, "Flicker of Fate", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", zone="graveyard")
        for name, count in (("Mountain", 4), ("Plains", 2)):
            for index in range(count):
                self.add(game.engine, name, ref=f"breach-flash-mana-{name.casefold()}-{index}")
        payment = self.fodder(game, 3)
        self.ready(game, breach, orrery, flicker, bolt)
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.phase == "precombat_main")
        self.cast(game, "A", breach)
        self.assertIsNone(self.resolve(game))
        first_offer, = self.escape_offers(game, bolt)
        stale = self.escape_command(first_offer, payment, targets=["B"])
        original = self.current(game, breach).logical_object_id

        self.cast(game, "A", flicker, [breach.ref])
        self.assertIsNone(self.resolve(game))
        current = self.current(game, breach)
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(original, current.logical_object_id)
        fresh, = self.escape_offers(game, bolt)
        self.assertNotEqual((first_offer[0]["id"], first_offer[1]["id"]), (fresh[0]["id"], fresh[1]["id"]))
        self.reject(game, "A", stale)

        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "end_step")
        trigger, = game.state.stack
        self.assertEqual(("triggered_ability", "A", breach.object_id),
                         (trigger.kind, trigger.controller, trigger.source_object_id))
        self.assertFalse(trigger.targets)
        last_offer, = self.escape_offers(game, bolt)
        last = self.escape_command(last_offer, payment, targets=["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, breach).zone)
        self.assertFalse(self.escape_offers(game, bolt))
        self.reject(game, "A", last)
        self.assertTrue(all(self.current(game, card).zone == "graveyard" for card in (bolt, *payment)))
        self.replay(game)

    def test_actual_escape_can_exile_a_card_discarded_earlier_in_payment(self):
        game = self.start(70213804)
        breach = self.add(game.engine, CARD, zone="hand")
        thrill = self.add(game.engine, "Thrill of Possibility", zone="graveyard")
        discarded = self.add(game.engine, "Plains", zone="hand", ref="breach-new-discard-payment")
        payment = self.fodder(game, 2)
        self.ready(game, breach, thrill, mana={"C": 2, "R": 2})
        self.cast_for(game, "A", breach, reserve={"C": 1, "R": 1})
        self.assertIsNone(self.resolve(game))

        offer, = self.escape_offers(game, thrill)
        choices = offer[1]["choice_schema"]
        self.assertEqual([discarded.ref], choices["discard_cards"]["legal_refs"])
        self.assertEqual({card.ref for card in (*payment, discarded)}, set(choices["exile_cards"]["legal_refs"]))
        old_identity = self.current(game, discarded).logical_object_id
        for seat in "BCD":
            self.assertNotIn(discarded.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        command = self.escape_command(offer, (*payment, discarded), discard_cards=[discarded.ref])
        self.reject(game, "A", {key: value for key, value in command.items() if key != "discard_cards"})
        self.accept(game, "A", command)
        self.assertEqual("stack", self.current(game, thrill).zone)
        self.assertTrue(all(self.current(game, card).zone == "exile" for card in (*payment, discarded)))
        self.assertNotEqual(old_identity, self.current(game, discarded).logical_object_id)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        drawn = len(game.state.players["A"].draw_history)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(drawn + 2, len(game.state.players["A"].draw_history))
        self.assertEqual("graveyard", self.current(game, thrill).zone)
        self.replay(game)
