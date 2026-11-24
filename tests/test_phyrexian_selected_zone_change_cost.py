"""Fixed Phyrexian mana pairs compose with one already-owned selected cost."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ZOPANDREL = "Zopandrel, Hunger Dominus"
SOLPHIM = "Solphim, Mayhem Dominus"
DRIVNOD = "Drivnod, Carnage Dominus"
MANA_CAPABILITY = "activation.mana_cost.fixed_complex"
SELECTION_CAPABILITY = "activation.selected_zone_change.fixed"


class PhyrexianSelectedZoneChangeCostTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "phyrexian-selected-zone-change-cost.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "phyrexian-selected-zone-change-cost-cards.json",
            fixtures / "combat-prevention-source-power-ceiling-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Phyrexian selected payments", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_three_exact_originals_keep_each_price_component_and_all_printed_abilities(self):
        for name, color, generic, operation, count, other in (
            (ZOPANDREL, "G", 0, "sacrifice_one", 2, True),
            (SOLPHIM, "R", 1, "discard_one", 2, False),
            (DRIVNOD, "B", 0, "exile_one_from_graveyard", 3, False),
        ):
            with self.subTest(name=name):
                bound, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                programs = semantics.programs_for_oracle(card.oracle_id)
                activation, = (program for program in programs if program.event == "activate")
                self.assertGreater(len(programs), 1)
                self.assertIn(MANA_CAPABILITY, activation.capability_dependencies)
                self.assertIn(SELECTION_CAPABILITY, activation.capability_dependencies)
                price = activation.cost_schema
                self.assertEqual([], price["complex_symbols"])
                self.assertEqual([], price["uncompiled_costs"])
                choice, = price["choices"]
                self.assertEqual((operation, count, other), (choice["k"], choice["n"], bool(choice.get("other"))))
                self.assertEqual([(2, 0, generic), (1, 2, generic), (0, 4, generic)], [
                    (arm["requirements"][color], arm["life_payment"], arm["requirements"]["GENERIC"])
                    for arm in price["mana_cost_options"]
                ])
                self.assertEqual([{
                    "op": "place_counters", "card": "$source.zone_object", "counter": "indestructible",
                    "amount": 1, "source": "$source",
                }], activation.effects)
        original = self.db.lookup(ZOPANDREL, fuzzy=False)
        for price in (
            "{G/P}{R/P}, Sacrifice two other creatures",
            "{G/P}{G/P}, Sacrifice three other creatures",
            "{G/P}{G/P}, Sacrifice two other creatures, {T}",
        ):
            card = replace(original, oracle_text=f"{price}: Put an indestructible counter on Zopandrel.")
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)

    def offer(self, game, source):
        prefix = f"activate:{source.ref}:"
        return next(action for action in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if action["id"].startswith(prefix))

    def rejected_payment(self, game, command, *, seat="A"):
        before = authoritative_state_hash(game.state)
        rejected = game.act(f"pilot:{seat}", command)
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def pay(self, game, offer, cards, *, option, invalid=()):
        self.assertIn(option, {row["id"] for row in offer["cost_options"]})
        command = {"action_id": offer["id"], "cost_option": option, "pay": "auto",
                   "cost_cards": [card.ref for card in cards]}
        for refs in invalid:
            self.rejected_payment(game, {**command, "cost_cards": list(refs)})
        self.rejected_payment(game, command, seat="B")
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_zopandrel_atomically_spends_mixed_mana_life_and_two_other_creatures(self):
        game = self.session(10746021)
        source = self.add(game.engine, ZOPANDREL)
        payments = [self.add(game.engine, "Runeclaw Bear", ref=f"chosen-sacrifice-{index}") for index in range(2)]
        foreign = self.add(game.engine, "Runeclaw Bear", seat="B", ref="opponent-cannot-sacrifice")
        attacker = self.add(game.engine, "Runeclaw Bear", ref="combat-doubling-bear")
        attacker.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)

        offer = self.offer(game, source)
        selection, = offer["cost_summary"]["choose_cost"]
        self.assertEqual(2, selection["n"])
        self.assertEqual({card.ref for card in (*payments, attacker)}, set(selection["legal_refs"]))
        self.pay(game, offer, payments, option="complex-2", invalid=(
            (payments[0].ref, source.ref), (payments[0].ref, foreign.ref),
            (payments[0].ref, payments[0].ref), (payments[0].ref,),
        ))
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.assertEqual(["graveyard", "graveyard"], [game.state.cards[card.object_id].zone for card in payments])
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("indestructible", 0))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("indestructible"))
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(36, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_solphim_spends_generic_and_colored_mana_and_discards_its_own_private_cards(self):
        game = self.session(10746022)
        source = self.add(game.engine, SOLPHIM)
        first = self.add(game.engine, "Runeclaw Bear", zone="hand", ref="first-private-cost")
        second = self.add(game.engine, "Island", zone="hand", ref="second-private-cost")
        private = self.add(game.engine, "Island", seat="D", zone="hand", ref="other-private-card")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        for card in (source, bolt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "R": 3})
        self.checkpoint(game)

        self.cast_for(game, "A", bolt, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(34, game.state.players["B"].life)
        self.priority(game, "A")
        offer = self.offer(game, source)
        selection, = offer["cost_summary"]["choose_cost"]
        self.assertIn(first.ref, selection["legal_refs"])
        self.assertIn(second.ref, selection["legal_refs"])
        self.assertNotIn(private.ref, selection["legal_refs"])
        for seat in "BCD":
            projected = json.dumps(game.packet(f"pilot:{seat}", full=True))
            self.assertNotIn(first.ref, projected)
            self.assertNotIn(second.ref, projected)
        self.pay(game, offer, [first, second], option="complex-1", invalid=((first.ref, private.ref),))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual((0, 0), tuple(game.state.players["A"].mana_pool[color] for color in ("C", "R")))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in (first, second)))
        self.assertEqual("hand", game.state.cards[private.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("indestructible"))
        self.replay(game, load=True)

    def test_actual_drivnod_exiles_only_own_graveyard_creatures_and_keeps_the_death_multiplier(self):
        game = self.session(10746023)
        source = self.add(game.engine, DRIVNOD)
        pays = [self.add(game.engine, "Runeclaw Bear", zone="graveyard", ref=f"own-graveyard-{index}")
                for index in range(3)]
        land = self.add(game.engine, "Island", zone="graveyard", ref="own-noncreature")
        opposing = self.add(game.engine, "Runeclaw Bear", seat="B", zone="graveyard", ref="opposing-creature")
        feeder = self.add(game.engine, "Carrion Feeder")
        dying = self.add(game.engine, "Doomed Dissenter")
        for card in (source, feeder, dying):
            self.assert_closed(game, card)
        self.prepare(game, mana={})
        self.checkpoint(game)

        offer = self.offer(game, source)
        selection, = offer["cost_summary"]["choose_cost"]
        self.assertEqual({card.ref for card in pays}, set(selection["legal_refs"]))
        self.assertEqual(["complex-3"], [row["id"] for row in offer["cost_options"]])
        self.pay(game, offer, pays, option="complex-3", invalid=(
            (pays[0].ref, pays[1].ref, opposing.ref),
            (pays[0].ref, pays[1].ref, land.ref),
        ))
        self.assertEqual(36, game.state.players["A"].life)
        self.assertEqual(["exile"] * 3, [game.state.cards[card.object_id].zone for card in pays])
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[source.object_id]
        self.assertEqual(1, live.counters.get("indestructible"))
        self.priority(game, "A")
        action = self.offer(game, feeder)
        result = game.act("pilot:A", {"action_id": action["id"], "cost_cards": [dying.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        self.assertEqual("graveyard", game.state.cards[dying.object_id].zone)
        self.assertEqual(2, sum(card.is_token and card.zone == "battlefield" and card.controller == "A"
                                for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_actual_drivnod_has_no_legal_payment_at_three_life_with_no_black_mana(self):
        game = self.session(10746024)
        source = self.add(game.engine, DRIVNOD)
        cards = [self.add(game.engine, "Runeclaw Bear", zone="graveyard", ref=f"short-life-{index}")
                 for index in range(3)]
        game.state.players["A"].life = 3
        self.assert_closed(game, source)
        self.prepare(game, mana={})
        self.checkpoint(game)
        ability, = game.engine._activated_abilities(source)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(action_id, {action["id"] for action in actions})
        self.rejected_payment(game, {"action_id": action_id, "cost_option": "complex-3",
                                     "cost_cards": [card.ref for card in cards], "pay": "auto"})
        self.assertEqual(["graveyard"] * 3, [game.state.cards[card.object_id].zone for card in cards])
