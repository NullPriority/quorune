"""The live static spell minimum applies to the total locked before payment."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_cost_modifiers import SPELL_MANA_FLOOR_CAPABILITY, SPELL_MANA_FLOOR_HANDLER_ID
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class FinalSpellManaCostFloorTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "final-spell-mana-floor.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/final-spell-mana-cost-floor-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Final spell mana floor", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Forest", 30),
        ], [commander])

    def ready(self, game, *cards, active="A", mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, active=active, actor=active, mana=mana)
        self.checkpoint(game)

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def offers(self, game, seat, card):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return [action for action in actions if action["id"] == "cast:" + card.ref]

    def option(self, game, seat, card, *, kind="normal"):
        offer, = self.offers(game, seat, card)
        if kind == "normal":
            price, = (row for row in offer["cost_options"] if row["id"] == "normal")
        else:
            price, = (row for row in offer["cost_options"] if row["kind"] == kind)
        return offer["id"], price

    def test_actual_minimum_follows_reductions_and_tapped_sources_supply_no_floor(self):
        for tapped in (False, True):
            with self.subTest(tapped=tapped):
                game = self.session(601020601 + tapped)
                source = self.add(game.engine, "Trinisphere", seat="D", ref="floor-provider")
                source.tapped = tapped
                if not tapped:
                    self.add(game.engine, "Trinisphere", seat="B", ref="floor-second-provider")
                discount = self.add(game.engine, "Goblin Electromancer")
                spell = self.add(game.engine, "Divination", zone="hand")
                self.ready(game, source, discount, spell, mana={"U": 1, "C": 2})
                action, price = self.option(game, "A", spell)
                self.assertEqual(1 if tapped else 2, price["requirements"]["GENERIC"])
                self.assertEqual(1, price["requirements"]["U"])
                self.act(game, "A", {"action_id": action, "cost_option": price["id"], "pay": "auto"})
                self.assertEqual(1 if tapped else 0, sum(game.state.players["A"].mana_pool.values()))
                active = game.state.cards[spell.object_id]
                self.assertEqual(3, game.engine._effective_card_data(active)["mana_value"])
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.replay(game, load=True)

    def test_actual_additional_sacrifice_keeps_the_untapped_providers_locked_total(self):
        for enough in (False, True):
            with self.subTest(enough=enough):
                game = self.session(601020603 + enough)
                provider = self.add(game.engine, "Trinisphere")
                spell = self.add(game.engine, "Kuldotha Rebirth", zone="hand")
                self.ready(game, provider, spell, mana={"R": 1, "C": 2 if enough else 1})
                command = {"action_id": "cast:" + spell.ref, "sacrifice_cards": [provider.ref], "pay": "auto"}
                if not enough:
                    self.assertEqual([], self.offers(game, "A", spell))
                    self.reject(game, "A", command)
                    self.assertEqual("battlefield", game.state.cards[provider.object_id].zone)
                else:
                    _, option = self.option(game, "A", spell)
                    self.assertEqual(2, option["requirements"]["GENERIC"])
                    self.assertIn(provider.ref, option["choice_schema"]["sacrifice_cards"]["legal_refs"])
                    self.act(game, "A", command)
                    self.assertEqual("graveyard", game.state.cards[provider.object_id].zone)
                    self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                    game = self.replay(game, load=True)
                    self.assertIsNone(self.resolve(game))
                    summoned = [card for card in game.state.cards.values()
                                if card.zone == "battlefield" and card.is_token and card.controller == "A"]
                    self.assertEqual(3, len(summoned))
                self.replay(game, load=True)

    def test_actual_phyrexian_payment_keeps_life_and_the_selected_colored_price(self):
        for pay_life in (False, True):
            with self.subTest(pay_life=pay_life):
                game = self.session(601020605 + pay_life)
                source = self.add(game.engine, "Trinisphere", seat="C")
                spell = self.add(game.engine, "Gut Shot", zone="hand")
                self.ready(game, source, spell, mana={"R": 1, "C": 3})
                offer, = self.offers(game, "A", spell)
                self.assertEqual({(2, 1), (3, 0)}, {
                    (row["requirements"]["GENERIC"], row["requirements"]["R"]) for row in offer["cost_options"]
                })
                chosen, = (row for row in offer["cost_options"] if row["requirements"]["R"] == int(not pay_life))
                command = {"action_id": offer["id"], "cost_option": chosen["id"], "targets": ["B"], "pay": "auto"}
                self.reject(game, "B", command)
                self.act(game, "A", command)
                self.assertEqual(38 if pay_life else 40, game.state.players["A"].life)
                self.assertEqual(1, sum(game.state.players["A"].mana_pool.values()))
                self.assertEqual(1, game.engine._effective_card_data(game.state.cards[spell.object_id])["mana_value"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(39, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_actual_free_alternative_keeps_its_island_return_and_pays_three_generic(self):
        game = self.session(601020607)
        source = self.add(game.engine, "Trinisphere", seat="D")
        opposing_spell = self.add(game.engine, "Divination", seat="B", zone="hand")
        daze = self.add(game.engine, "Daze", zone="hand")
        island = self.add(game.engine, "Island", ref="floor-island-to-return")
        for index in range(3):
            self.add(game.engine, "Forest", ref=f"floor-generic-source-{index}")
        self.ready(game, source, opposing_spell, daze, active="B", mana={"U": 1, "C": 2})
        self.cast(game, "B", opposing_spell)
        target = game.state.stack[-1]
        action, price = self.option(game, "A", daze, kind="alternate")
        self.assertEqual(3, price["requirements"]["GENERIC"])
        self.assertEqual(0, price["requirements"]["U"])
        command = {"action_id": action, "cost_option": price["id"], "targets": [target.ref],
                   "return_cards": [island.ref], "pay": "auto"}
        self.act(game, "A", command)
        self.assertEqual("hand", game.state.cards[island.object_id].zone)
        game = self.replay(game, load=True)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertFalse(game.packet("pilot:B", full=True)["decision"]["ctx"]["payable"])
        self.act(game, "B", {"action_id": "choose", "pay": False})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[opposing_spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_announced_x_does_not_bypass_the_floor_in_offers_or_commands(self):
        for enough in (False, True):
            with self.subTest(enough=enough):
                game = self.session(601020608 + enough)
                source = self.add(game.engine, "Trinisphere", seat="B")
                spell = self.add(game.engine, "Blaze", zone="hand")
                self.ready(game, source, spell, mana={"R": 1, "C": 2 if enough else 1})
                command = {"action_id": "cast:" + spell.ref, "x": 0, "targets": ["C"], "pay": "auto"}
                if not enough:
                    self.assertEqual([], self.offers(game, "A", spell))
                    self.reject(game, "A", command)
                else:
                    self.act(game, "A", command)
                    self.assertEqual(0, game.state.stack[-1].x_value)
                    self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(40, game.state.players["C"].life)
                self.replay(game, load=True)

    def test_complete_original_requires_its_current_static_provider_and_exact_mana_floor(self):
        card = self.db.lookup("Trinisphere", fuzzy=False)
        cap = SPELL_MANA_FLOOR_CAPABILITY
        for profile in self.registry.profiles:
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(("cast.cost.modify", "battlefield"), (node.event, node.active_zone))
            handler, = node.handlers
            self.assertEqual(SPELL_MANA_FLOOR_HANDLER_ID, handler["handler_id"])
            self.assertEqual({"schema_version": 1, "minimum_mana": 3}, handler["floor"])
        report, semantics = register_card(self.db, self.registry, "Trinisphere")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual((cap,), tuple(program.capability_dependencies))
        without = replace(program, capability_dependencies=(), capability_closure=None)
        result = bind_semantic_program_runtime(without, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", result["blockers"])
        for changed in (replace(program, active_zone="hand"), replace(program, coverage=("static_ability",))):
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
        for before, after in (("is untapped", "is tapped"), ("three mana", "two mana"), ("each spell", "each creature spell")):
            altered = replace(card, oracle_text=card.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(altered, capability_registry=self.registry).status)
