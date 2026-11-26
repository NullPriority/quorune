from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bargain_fixture import BargainFixture
from common import ROOT
from test_bound_effect_programs import record
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.bargain_search import bargain_search_limit
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_result_capability_shapes import library_result_payload_dependencies
from scripts.build_test_database import build_fixture_database


_BODY = (
    "Search your library for a card, exile it face down, then shuffle. "
    "If this spell was bargained, you may cast the exiled card without paying its mana cost "
    "if that spell's mana value is 4 or less. Put the exiled card into your hand if it wasn't cast this way."
)
_CAPABILITY = "library.search.private_face_down_cast_or_hand"


class BargainPrivateSearchCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, body, *, bargain=True):
        prefix = "Bargain\n" if bargain else ""
        source = replace(record(prefix + body, "Sorcery"), keywords=("Bargain",) if bargain else ())
        return compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")

    def test_private_search_source_preserves_bargain_and_fixed_mana_limit(self):
        ir = self.compile(_BODY)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        ordinary, resolution = ir.faces[0].nodes
        self.assertEqual("printed-bargain-declaration-v1", ordinary.template_id)
        self.assertIn("casting.additional_cost.bargain", ordinary.capability_dependencies)
        self.assertEqual("bargained-private-search-cast-v24", resolution.template_id)
        self.assertEqual((len("Bargain\n"), len("Bargain\n" + _BODY), 2),
                         (resolution.span.start, resolution.span.end, resolution.span.line))
        self.assertIsNone(resolution.target_schema)
        self.assertIn(_CAPABILITY, resolution.capability_dependencies)
        effect, = resolution.effects
        self.assertEqual(24, effect["schema_version"])
        self.assertEqual({"minimum": 1, "maximum": 1}, effect["count"])
        self.assertEqual({}, effect["selector"])
        self.assertEqual(4, bargain_search_limit(effect).maximum)
        self.assertIn(_CAPABILITY, library_result_payload_dependencies(effect))

    def test_incomplete_printed_and_malformed_search_shapes_fail_closed(self):
        for body in (
            _BODY.replace("for a card", "for a creature card"),
            _BODY.replace("exile it face down", "exile it"),
            _BODY.replace(", then shuffle", ""),
            _BODY.replace("if it wasn't cast this way", "if it was cast this way"),
            _BODY.replace("4 or less", "its original mana cost"),
        ):
            with self.subTest(body=body):
                self.assertNotEqual("exact", self.compile(body).status)
        self.assertNotEqual("exact", self.compile(_BODY, bargain=False).status)
        effect, = self.compile(_BODY).faces[0].nodes[1].effects
        for changed in (
            {**effect, "schema_version": 24.0},
            {**effect, "reveal": True},
            {**effect, "shuffle_after": False},
            {**effect, "count": {"minimum": 0, "maximum": 1}},
            {**effect, "cast_limit": {"schema_version": 1, "maximum": True}},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                library_result_payload_dependencies(changed)


class BargainPrivateSearchNativeTests(BargainFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-search.sqlite3"
        fixture_root = ROOT / "tests/fixtures"
        build_fixture_database([
            fixture_root / "bound-effect-program-cards.json",
            fixture_root / "bargain-cards.json",
            fixture_root / "discover-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private Bargain search", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def begin_search(self, *, chosen, candidate="Hill Giant", seed=702166300):
        session = self.session(seed)
        spell = self.add(session.engine, "Beseech the Mirror", zone="hand")
        price = self.add(session.engine, "Sol Ring")
        result = self.add(session.engine, candidate, zone="library", ref="private-search-result")
        self.assert_closed(session, spell)
        self.ready(session, {"C": 1, "B": 3})
        self.cast_bargain(session, spell, price, chosen=chosen)
        self.assertEqual("semantic.search", self.resolve(session).kind)
        self.assertIn(candidate, str(session.packet("pilot:A", full=True)["decision"]["ctx"]["search_cards"]))
        for seat in "BCD":
            self.assertNotIn(candidate, str(session.packet(f"pilot:{seat}", full=True)))
        return session, result.object_id, price.object_id

    def shuffles(self, session):
        return [event for event in session.state.events if event.code == "library.shuffle"]

    def select(self, session, object_id):
        ref = session.state.cards[object_id].ref
        self.choose_as(session, "A", search_cards=[ref])

    def test_unbargained_search_requires_one_hit_shuffles_and_returns_it_privately(self):
        session, result, price = self.begin_search(chosen=False)
        before = authoritative_state_hash(session.state)
        refused = session.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertFalse(refused.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        prior = len(self.shuffles(session))
        session = self.replay(session, load=True)
        self.select(session, result)
        self.assertEqual(prior + 1, len(self.shuffles(session)))
        self.assertEqual("hand", session.state.cards[result].zone)
        self.assertEqual("battlefield", session.state.cards[price].zone)
        self.assertNotIn("Hill Giant", str(session.packet("pilot:B", full=True)))
        self.settle(session)
        self.replay(session, load=True)

    def test_paid_search_casts_the_exact_limit_for_free_after_private_save_and_shuffle(self):
        session, result, price = self.begin_search(chosen=True, seed=702166301)
        before = len(self.shuffles(session))
        self.select(session, result)
        self.assertEqual(before + 1, len(self.shuffles(session)))
        card = session.state.cards[result]
        self.assertEqual(("exile", True, ["A"]), (card.zone, card.face_down, card.known_to))
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        for seat in "BCD":
            packet = str(session.packet(f"pilot:{seat}", full=True))
            self.assertNotIn("Hill Giant", packet)
            self.assertNotIn("bargain-search:", packet)
        session = self.replay(session, load=True)
        option, = session.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]
        self.assertEqual(4, option["mana_value"])
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.choose_as(session, "A", choice="cast", cast={"cost_option": option["id"]})
        self.assertEqual("stack", session.state.cards[result].zone)
        self.assertFalse(session.state.cards[result].face_down)
        self.assertEqual("graveyard", session.state.cards[price].zone)
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.assertFalse(any(event.code == "library.discover" for event in session.state.events))
        self.settle(session)
        self.assertEqual("battlefield", session.state.cards[result].zone)
        self.replay(session, load=True)

    def test_declined_and_over_limit_results_go_to_hand_after_the_shuffle(self):
        for index, candidate in enumerate(("Hill Giant", "Gigantosaurus")):
            with self.subTest(candidate=candidate):
                session, result, price = self.begin_search(chosen=True, candidate=candidate, seed=702166310 + index)
                prior = len(self.shuffles(session))
                self.select(session, result)
                self.assertEqual(prior + 1, len(self.shuffles(session)))
                if candidate == "Hill Giant":
                    self.assertEqual("semantic.choice", session.state.pending_decision.kind)
                    self.choose_as(session, "A", choice="decline")
                else:
                    self.assertNotEqual("semantic.choice", session.state.pending_decision.kind)
                self.assertEqual("hand", session.state.cards[result].zone)
                self.assertEqual("graveyard", session.state.cards[price].zone)
                self.assertNotIn(candidate, str(session.packet("pilot:B", full=True)))
                self.settle(session)
                self.replay(session, load=True)

    def test_removing_the_sealed_bargain_fact_loses_the_paid_cast_offer(self):
        session, result, _price = self.begin_search(chosen=True, seed=702166320)
        with patch("quorune.discover_casting.was_search_spell_bargained", return_value=False):
            self.select(session, result)
        self.assertEqual("hand", session.state.cards[result].zone)
        self.assertNotEqual("semantic.choice", session.state.pending_decision.kind)

    def test_replacement_resume_hides_private_result_and_shuffles_once(self):
        from quorune.replacement.model import ReplacementClass, ReplacementEffect
        from quorune.replacement.operations import SetField
        from quorune.semantic_runtime import zone_replacements

        collect = zone_replacements.collect_zone_change_replacement_effects
        alternatives = tuple(
            ReplacementEffect(
                effect_id=f"fixture:bargain-redirect-{zone}",
                source_id="fixture:search-redirect",
                event_kind="zone.change",
                replacement_class=ReplacementClass.OTHER,
                conditions={"destination": {"eq": "exile"}},
                operations=(SetField("destination", zone),),
            )
            for zone in ("hand", "graveyard")
        )

        def redirects(host, **kwargs):
            return (*collect(host, **kwargs), *alternatives)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", side_effect=redirects):
            session, result, _price = self.begin_search(chosen=True, seed=702166330)
            ref = session.state.cards[result].ref
            prior = len(self.shuffles(session))
            self.select(session, result)
            self.assertEqual("replacement.order", session.state.pending_decision.kind)
            self.assertEqual(["pilot:A"], session.pending_principals())
            self.assertEqual("library", session.state.cards[result].zone)
            self.assertEqual(prior, len(self.shuffles(session)))
            session = self.replay(session, load=True)
            ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
            redirect, = [row["id"] for row in ctx["options"] if "redirect-hand" in row["id"]]
            wrong_actor = session.act("pilot:B", {"action_id": "choose", "replacement": redirect})
            self.assertFalse(wrong_actor.ok)
            resumed = session.act("pilot:A", {"action_id": "choose", "replacement": redirect})
            self.assertTrue(resumed.ok, resumed.summary)
            self.assertEqual("hand", session.state.cards[result].zone)
            self.assertEqual(prior + 1, len(self.shuffles(session)))
            journal, = [event for event in session.state.events
                        if event.code == "replacement.apply" and event.details.get("object") == ref]
            self.assertEqual(["A", "analyst"], journal.visibility)
            for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
                packet = str(session.packet(principal, full=True))
                self.assertNotIn(ref, packet)
                self.assertNotIn("Hill Giant", packet)
            self.settle(session)
            self.replay(session, load=True)
