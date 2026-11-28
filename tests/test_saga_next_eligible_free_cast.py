"""One use of a filtered mana alternative and a targeted doubling chapter."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.next_spell_cost_model import next_eligible_free_spell_effect, validate_next_spell_cost_instruction
from quorune.object_predicate import ObjectQuerySpec
from quorune.record import authoritative_state_hash
from quorune.resolution_characteristic_model import CHARACTERISTIC_OPERATION
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_stat_multipliers import MultipliedStat, decode_fixed_stat_multiplier
from scripts.build_test_database import build_fixture_database


class SagaNextEligibleFreeCastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "saga-next-eligible-free-cast.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "reflexive-payment-cards.json",
            fixtures / "saga-next-eligible-free-cast-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga filtered free cast", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_exact_mana_window_and_trample_doubling_reject_open_shapes(self):
        model = next_eligible_free_spell_effect()
        validate_next_spell_cost_instruction(model)
        for changed in ({**model, "amount": 1},
                        {**model, "spell_query": {**model["spell_query"], "colors_any": []}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_next_spell_cost_instruction(changed)
        base = {"op": CHARACTERISTIC_OPERATION, "schema_version": 19, "factor": 2,
                "stat": "power and toughness", "card": "$target.0", "grant_trample": True}
        multiplier, _, _ = decode_fixed_stat_multiplier(base)
        self.assertEqual((MultipliedStat.BOTH, "Trample"), (multiplier.stat, multiplier.granted_keyword))
        for edited in ({"grant_first_strike": True}, {"factor": 3}, {"card": "$source.zone_object"}):
            with self.subTest(edited=edited), self.assertRaises(ValueError):
                decode_fixed_stat_multiplier({**base, **edited})

    def test_unqualified_color_query_is_a_killed_mutation(self):
        def assert_printed_colors():
            effect = next_eligible_free_spell_effect()
            self.assertEqual({"R", "G"}, set(effect["spell_query"]["colors_any"]))
        assert_printed_colors()
        with patch("quorune.next_spell_cost_model.next_eligible_free_spell_query",
                   return_value=ObjectQuerySpec(types_any=("creature",))):
            with self.assertRaises(AssertionError):
                assert_printed_colors()

    @staticmethod
    def cast_offer(session, card):
        return next(row for row in session.packet("pilot:A", full=True)["decision"]["legal_actions"]
                    if row["action"] == "cast" and row.get("card") == card.ref)

    @staticmethod
    def choices(offer):
        return tuple(offer.get("cost_options") or offer.get("choice_schema", {}).get("cast_options") or ())

    def test_original_next_eligible_cast_uses_optional_mana_base_and_replays_doubling(self):
        session = self.session(118601210)
        source = self.add(session.engine, "World War Hulk", zone="hand")
        first = self.add(session.engine, "Printed Green Creature", zone="hand", ref="free-green-creature")
        next_eligible = self.add(session.engine, "Printed Red Creature", zone="hand", ref="second-red-creature")
        other = self.add(session.engine, "Printed Red Noncreature", zone="hand", ref="red-noncreature")
        grave = self.add(session.engine, "Printed Red Creature", zone="graveyard", ref="red-graveyard-creature")
        recipient = self.add(session.engine, "Generic Bound Body", ref="asymmetric-stats")
        foreign = self.add(session.engine, "Generic Bound Body", seat="B", ref="foreign-stats")
        second = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="hulk-second-lore")
        final = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="hulk-final-lore")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 12, "G": 6, "R": 3})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.assertIsNone(self.resolve(session))

        other_offer = self.cast_offer(session, other)
        self.assertFalse(any(option["id"].startswith("next-eligible-free:") for option in self.choices(other_offer)))
        self.assertFalse(any(row.get("card") == grave.ref
                             for row in session.packet("pilot:A", full=True)["decision"]["legal_actions"]))
        self.cast(session, "A", other)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(any(option["id"].startswith("next-eligible-free:")
                            for option in self.choices(self.cast_offer(session, next_eligible))))

        for use_free in (False, True):
            with self.subTest(use_free=use_free):
                resumed = self.replay(session, load=True)
                offer = self.cast_offer(resumed, first)
                choices = self.choices(offer)
                free, = (option for option in choices if option["id"].startswith("next-eligible-free:"))
                selected = free["id"] if use_free else "normal"
                before = sum(resumed.state.players["A"].mana_pool.values())
                paid = resumed.act("pilot:A", {
                    "action_id": offer["id"], "cost_option": selected, "pay": "auto",
                })
                self.assertTrue(paid.ok, paid.summary)
                self.assertEqual(before - (0 if use_free else 2), sum(resumed.state.players["A"].mana_pool.values()))
                self.assertIsNone(self.resolve(resumed))
                self.assertFalse(any(option["id"].startswith("next-eligible-free:")
                                     for option in self.choices(self.cast_offer(resumed, next_eligible))))
                self.cast(resumed, "A", second, [source.ref])
                self.assertEqual("semantic.target", self.resolve(resumed).kind)
                self.target(resumed, "A", [recipient.ref])
                self.assertIsNone(self.resolve(resumed))
                self.assertEqual(3, resumed.state.cards[recipient.object_id].counters["+1/+1"])
                self.cast(resumed, "A", final, [source.ref])
                self.assertEqual("semantic.target", self.resolve(resumed).kind)
                before = authoritative_state_hash(resumed.state)
                self.assertFalse(resumed.act("pilot:A", {"action_id": "choose", "targets": [foreign.ref]}).ok)
                self.assertEqual(before, authoritative_state_hash(resumed.state))
                self.target(resumed, "A", [recipient.ref])
                self.assertIsNone(self.resolve(resumed))
                self.assertEqual((10, 18), tuple(resumed.engine._numeric_stat(recipient.object_id, stat)
                                                for stat in ("power", "toughness")))
                self.assertIn("trample", resumed.engine._combat_keywords(resumed.state.cards[recipient.object_id]))
                self.assertEqual("graveyard", resumed.state.cards[source.object_id].zone)
                self.replay(resumed)
