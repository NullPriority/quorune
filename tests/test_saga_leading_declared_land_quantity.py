"""A printed Saga shares the existing bounded public quantity owner."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import _reviewed_effect_template
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SagaLeadingDeclaredLandQuantityTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "saga-leading-declared-land-quantity.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "reflexive-payment-cards.json",
            fixtures / "saga-leading-declared-land-quantity-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga land quantity", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_leading_where_x_reuses_only_the_existing_closed_quantity_and_target(self):
        leading = ("Until end of turn, another target creature you control gains trample and gets +X/+X, "
                   "where X is the number of lands you control.")
        trailing = ("Another target creature you control gains trample and gets +X/+X until end of turn, "
                    "where X is the number of lands you control.")
        options = dict(card_name="Saga amount fixture", source_is_permanent=True,
                       source_card_types=("enchantment", "creature"))
        result = _reviewed_effect_template(leading, **options)
        self.assertEqual(_reviewed_effect_template(trailing, **options), result)
        self.assertEqual("public-query-characteristic-amount-v1", result[0])
        self.assertTrue(result[2]["source_exclusion"])
        for changed in (leading.replace("lands", "mysteries"),
                        leading.replace("lands you control", "cards in an opponent's hand"),
                        leading + " Rewrite history."):
            with self.subTest(changed=changed):
                self.assertIsNone(_reviewed_effect_template(changed, **options)[0])

    def test_original_chapters_resolve_current_lands_once_and_replay(self):
        session = self.session(714608250)
        source = self.add(session.engine, "Summon: Titan", zone="hand")
        recipient = self.add(session.engine, "Generic Bound Body", ref="titan-eligible-creature")
        opponent = self.add(session.engine, "Generic Bound Body", seat="B", ref="titan-foreign-creature")
        self.add(session.engine, "Generic Bound Plains", seat="B", ref="titan-foreign-land")
        first_land = self.add(session.engine, "Generic Bound Plains", ref="titan-land-lost-before")
        last_land = self.add(session.engine, "Generic Bound Plains", ref="titan-land-lost-after")
        stranded = self.add(session.engine, "Generic Bound Body", zone="graveyard", ref="titan-graveyard-creature")
        response = self.add(session.engine, "Land-count Response", zone="hand", seat="B", ref="titan-decrease-during")
        later = self.add(session.engine, "Land-count Response", zone="hand", seat="B", ref="titan-decrease-after")
        second_lore = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="titan-second-lore")
        final_lore = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="titan-final-lore")
        self.assert_closed(session, source)
        self.assert_closed(session, response)
        self.prepare(session, mana={"C": 12, "G": 5})
        self.checkpoint(session)
        library = session.state.players["A"].zones["library"]
        milled = tuple(library[-5:])
        self.cast(session, "A", source)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(set(milled) <= set(session.state.players["A"].zones["graveyard"]))

        self.cast(session, "A", second_lore, [source.ref])
        self.assertIsNone(self.resolve(session))
        for oid in milled:
            self.assertEqual("battlefield", session.state.cards[oid].zone)
            self.assertTrue(session.state.cards[oid].tapped)
        self.assertEqual("graveyard", session.state.cards[stranded.object_id].zone)

        stats = tuple(session.engine._numeric_stat(recipient.object_id, stat) for stat in ("power", "toughness"))
        self.cast(session, "A", final_lore, [source.ref])
        self.assertEqual("semantic.target", self.resolve(session).kind)
        for disallowed in (source, opponent):
            before = authoritative_state_hash(session.state)
            rejected = session.act("pilot:A", {"action_id": "choose", "targets": [disallowed.ref]})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.target(session, "A", [recipient.ref])
        self.cast(session, "B", response, [first_land.ref])
        self.resolve_top(session)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        expected = tuple(value + 6 for value in stats)
        self.assertEqual(expected, tuple(session.engine._numeric_stat(recipient.object_id, stat)
                                        for stat in ("power", "toughness")))
        self.assertIn("trample", session.engine._combat_keywords(session.state.cards[recipient.object_id]))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.cast(session, "B", later, [last_land.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(expected, tuple(session.engine._numeric_stat(recipient.object_id, stat)
                                        for stat in ("power", "toughness")))
        self.replay(session)
