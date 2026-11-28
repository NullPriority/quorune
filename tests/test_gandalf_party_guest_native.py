"""Native combat timing and private hand casting for Gandalf, Party Guest."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.bounded_hand_cast_model import BOUNDED_HAND_CAST_CAPABILITY, LEGENDARY_WIZARD_HAND_CAST_CAPABILITY
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bounded_hand_casts as hand_cast
from test_controller_creature_keyword_absence import register_card


CARD = "Gandalf, Party Guest"
PROFILE = "commander_review"


class GandalfPartyGuestNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = hand_cast.BoundedHandCastRuntimeTests.start

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "gandalf-party-guest.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "gandalf-party-guest-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "bounded-hand-cast-cards.json",
            fixtures / "conditional-free-cast-cards.json",
            fixtures / "next-spell-cost-reduction-cards.json",
            fixtures / "same-turn-graveyard-return-sets-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Gandalf hand casting",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def ready(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def starting_combat(self, game, seat):
        self.until(game, lambda current: (
            current.state.active_player == seat and current.state.step == "beginning_combat"
        ))

    def open_trigger(self, game, source):
        self.starting_combat(game, "A")
        item, = game.state.stack
        self.assertEqual(("A", "triggered_ability", source.object_id),
                         (item.controller, item.kind, item.source_object_id))
        self.assertFalse(item.targets)

    def hand_options(self, game):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertTrue(game.state.stack[-1].context["currently_resolving"])
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]

    def cast_command(self, option, *, targets=(), **costs):
        return {
            "action_id": "choose",
            "choice": "cast",
            "cast": {"card": option["card"], "cost_option": option["id"], "targets": list(targets), **costs},
        }

    def assert_private(self, game, *owner_cards, foreign=None):
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for card in owner_cards:
                self.assertNotIn(card.ref, packet)
        if foreign is not None:
            self.assertNotIn(foreign.ref, json.dumps(game.packet("pilot:A", full=True)))

    def test_complete_original_binds_only_the_own_combat_hand_cast(self):
        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        self.assertEqual("capability_closed", admitted["trust_basis"])
        card = self.db.lookup(CARD, fuzzy=False)
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn(BOUNDED_HAND_CAST_CAPABILITY, program.capability_dependencies)
        capability = LEGENDARY_WIZARD_HAND_CAST_CAPABILITY
        self.assertIn(capability, program.capability_dependencies)
        deps = tuple(dep for dep in program.capability_dependencies if dep != capability)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", bound["blockers"])
        altered = replace(card, oracle_text=card.oracle_text.replace("your hand", "your graveyard"))
        self.assertNotEqual("exact", compile_oracle_card(
            altered, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)

    def test_actual_own_combat_counts_legendary_wizards_and_casts_a_sorcery(self):
        game = self.start(60827001)
        source = self.add(game.engine, CARD, ref="gandalf-combat-source")
        opposing_wizard = self.add(game.engine, "Kaza, Roil Chaser", seat="B")
        ordinary_wizard = self.add(game.engine, "Prodigal Sorcerer")
        other_legend = self.add(game.engine, "Isamaru, Hound of Konda")
        child = self.add(game.engine, "Flame Rift", zone="hand", ref="gandalf-private-sorcery")
        above = self.add(game.engine, "Kari Zev's Expertise", zone="hand", ref="gandalf-above-limit")
        permanent = self.add(game.engine, "Sol Ring", zone="hand", ref="gandalf-ineligible-permanent")
        foreign = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand", ref="gandalf-foreign-hand")
        self.ready(game, source, opposing_wizard, ordinary_wizard, other_legend, child, above, permanent)
        self.open_trigger(game, source)

        options = self.hand_options(game)
        self.assertEqual({child.ref}, {option["card"] for option in options})
        offer, = options
        self.assertEqual(0, sum(offer["requirements"].values()))
        self.assert_private(game, child, above, permanent, foreign=foreign)
        self.assertNotIn(child.printed_name, json.dumps(game.packet("pilot:B", full=True)))
        command = self.cast_command(offer)
        self.reject(game, "D", command)
        self.reject(game, "A", {"action_id": "choose", "choice": "cast", "cast": {"card": above.ref}})

        game = self.replay(game, load=True)
        self.accept(game, "A", command)
        self.assertEqual("beginning_combat", game.state.step)
        spell, = game.state.stack
        self.assertEqual(("spell", child.object_id, "A"), (spell.kind, spell.card_object_id, spell.controller))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIn(child.printed_name, json.dumps(game.packet("pilot:B", full=True)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, child).zone)
        self.assertEqual([36] * 4, [game.state.players[seat].life for seat in "ABCD"])

        self.starting_combat(game, "B")
        self.assertFalse(game.state.stack)
        self.assertIsNone(self.resolve(game))
        self.assert_private(game, above, permanent, foreign=foreign)
        self.replay(game)

    def test_actual_source_departure_yields_zero_limit_and_private_decline(self):
        game = self.start(60827002)
        source = self.add(game.engine, CARD)
        removed = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        zero = self.add(game.engine, "Ancestral Vision", zone="hand", ref="gandalf-private-zero")
        above = self.add(game.engine, "Flame Rift", zone="hand", ref="gandalf-two-after-source-loss")
        foreign = self.add(game.engine, "Ancestral Vision", seat="C", zone="hand", ref="gandalf-foreign-zero")
        self.ready(game, source, removed, zero, above)
        self.open_trigger(game, source)

        self.cast(game, "B", removed, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.current(game, source).zone)
        self.assertEqual(1, len(game.state.stack))
        offer, = self.hand_options(game)
        self.assertEqual(zero.ref, offer["card"])
        self.assert_private(game, zero, above, foreign=foreign)
        self.reject(game, "A", {"action_id": "choose", "choice": "cast", "cast": {"card": above.ref}})
        decline = {"action_id": "choose", "choice": "decline"}
        self.reject(game, "B", decline)

        cast_game = self.replay(game, load=True)
        initial_hand = tuple(game.state.players["A"].zones["hand"])
        self.accept(game, "A", decline)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.stack)
        self.assertEqual(initial_hand, tuple(game.state.players["A"].zones["hand"]))
        self.assert_private(game, zero, above, foreign=foreign)

        drawn = len(cast_game.state.players["A"].draw_history)
        self.accept(cast_game, "A", self.cast_command(offer, targets=["A"]))
        self.assertIsNone(self.resolve(cast_game))
        self.assertEqual("graveyard", self.current(cast_game, zero).zone)
        self.assertEqual(drawn + 3, len(cast_game.state.players["A"].draw_history))
        self.replay(cast_game)

    def test_actual_cast_preserves_target_legality_and_additional_costs(self):
        game = self.start(60827003)
        source = self.add(game.engine, CARD)
        wizard = self.add(game.engine, "Kaza, Roil Chaser")
        self.add(game.engine, "Mountain")
        self.add(game.engine, "Island", seat="B")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        targeted = self.add(game.engine, "Mogg Salvage", zone="hand", ref="gandalf-two-prices")
        no_target = self.add(game.engine, "Counterspell", zone="hand", ref="gandalf-missing-spell-target")
        with_discard = self.add(game.engine, "Thrill of Possibility", zone="hand", ref="gandalf-mandatory-discard")
        spare = self.add(game.engine, "Plains", zone="hand", ref="gandalf-private-discard")
        foreign = self.add(game.engine, "Plains", seat="B", zone="hand", ref="gandalf-foreign-discard")
        four = self.add(game.engine, "Concentrate", zone="hand", ref="gandalf-four-at-two-wizards")
        self.ready(game, source, wizard, targeted, no_target, with_discard, four)

        ordinary = self.action(game, "A", targeted)
        other_price = next(price["id"] for price in ordinary["cost_options"]
                           if price["id"].startswith("plain-alternative-"))
        self.open_trigger(game, source)
        options = self.hand_options(game)
        self.assertEqual({targeted.ref, with_discard.ref, four.ref}, {option["card"] for option in options})
        target_offer, = (option for option in options if option["card"] == targeted.ref)
        discard_offer, = (option for option in options if option["card"] == with_discard.ref)
        self.assertNotEqual(other_price, target_offer["id"])
        invalid_price = self.cast_command({**target_offer, "id": other_price}, targets=[artifact.ref])
        self.reject(game, "A", invalid_price)
        self.reject(game, "A", self.cast_command(target_offer, targets=[wizard.ref]))
        self.reject(game, "A", self.cast_command(discard_offer))
        self.reject(game, "A", self.cast_command(discard_offer, discard_cards=[foreign.ref]))
        self.assert_private(game, targeted, no_target, with_discard, spare, four, foreign=foreign)

        target_game = self.replay(game, load=True)
        self.accept(target_game, "A", self.cast_command(target_offer, targets=[artifact.ref]))
        self.assertIsNone(self.resolve(target_game))
        self.assertEqual("graveyard", self.current(target_game, artifact).zone)
        self.assertEqual("graveyard", self.current(target_game, targeted).zone)

        drawn = len(game.state.players["A"].draw_history)
        self.accept(game, "A", self.cast_command(discard_offer, discard_cards=[spare.ref]))
        self.assertEqual("graveyard", self.current(game, spare).zone)
        self.assertEqual("hand", self.current(game, foreign).zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, with_discard).zone)
        self.assertEqual(drawn + 2, len(game.state.players["A"].draw_history))
        self.replay(game)
