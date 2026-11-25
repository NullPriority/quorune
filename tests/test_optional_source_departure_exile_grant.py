"""Optional linked exile remains owned by the original granted Sliver."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.optional_effect_templates import _optional_source_departure_pair_is_closed
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Constricting Sliver"


class OptionalSourceDepartureExileGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "optional-source-departure-exile-grant.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "optional-source-departure-exile-grant-cards.json",
            fixtures / "combat-prevention-source-power-ceiling-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional exile owned by the Sliver", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_grants_one_optional_coupled_source_departure_pair(self):
        bound, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = {program.event: program for program in
                    semantics.programs_for_oracle(self.db.lookup(ORIGINAL, fuzzy=False).oracle_id)}
        grant = programs["characteristics.evaluate"]
        trigger = programs["permanent.enter.self"]
        self.assertIn("continuous.ability.fixed_query_grant", grant.capability_dependencies)
        self.assertIn("zone.exile_until_source_departure.fixed", trigger.capability_dependencies)
        self.assertEqual("opponent", trigger.target_schema["controller_relation"])
        wrapper, = trigger.effects
        self.assertEqual(("offer_optional_effect", "$controller"), (wrapper["op"], wrapper["player"]))
        exile, returning = wrapper["effects"]
        self.assertEqual(("exile", "return"), (exile["phase"], returning["phase"]))
        self.assertEqual(exile["binding_id"], returning["binding_id"])
        self.assertEqual("source_departure", exile["spec"]["timing"])
        self.assertTrue(_optional_source_departure_pair_is_closed(wrapper["effects"], trigger.target_schema, trigger.coverage))
        self.assertFalse(_optional_source_departure_pair_is_closed(
            (exile, {**returning, "binding_id": "blink:other"}), trigger.target_schema, trigger.coverage,
        ))
        self.assertFalse(_optional_source_departure_pair_is_closed(
            (exile, returning), {**trigger.target_schema, "count": 2}, trigger.coverage,
        ))

    def choose_target(self, game, target, *, forbidden=None):
        self.until(game, lambda current: getattr(current.state.pending_decision, "kind", None) == "semantic.target")
        decision = game.packet("pilot:A", full=True)["decision"]
        legal = decision["ctx"]["target_schema"]["legal_refs"]
        self.assertIn(target.ref, legal)
        if forbidden is not None:
            self.assertNotIn(forbidden.ref, legal)
            before = authoritative_state_hash(game.state)
            refused = game.act("pilot:A", {"action_id": "choose", "targets": [forbidden.ref]})
            self.assertFalse(refused.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(accepted.ok, accepted.summary)

    def decide_exile(self, game, *, choice, reload=False):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        command = {"action_id": "choose", "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        if reload:
            game = self.replay(game, load=True)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        return game

    def test_actual_grantee_controls_target_protection_and_each_sliver_owns_its_own_return(self):
        game = self.session(61030011)
        provider = self.add(game.engine, ORIGINAL, zone="hand")
        grantee = self.add(game.engine, "Sliver Construct", zone="hand")
        first = self.add(game.engine, "Centaur Courser", seat="C")
        protected = self.add(game.engine, "Black Knight", seat="B")
        self.add(game.engine, "Elvish Mystic", seat="D")
        bounces = [self.add(game.engine, "Unsummon", zone="hand", ref=f"sliver-exile-bounce-{i}") for i in range(2)]
        for card in (provider, grantee, first, protected, *bounces):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "W": 1, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", provider, reserve={"C": 3, "U": 2})
        self.resolve_top(game)
        self.choose_target(game, first, forbidden=protected)
        game = self.decide_exile(game, choice="apply", reload=True)
        self.assertEqual("exile", game.state.cards[first.object_id].zone)

        self.cast_for(game, "A", grantee, reserve={"U": 2})
        self.resolve_top(game)
        self.choose_target(game, protected)
        game = self.decide_exile(game, choice="apply", reload=True)
        self.assertEqual("exile", game.state.cards[protected.object_id].zone)

        self.cast_for(game, "A", bounces[0], [provider.ref])
        self.resolve_top(game)
        self.assertEqual(("battlefield", "C"), (game.state.cards[first.object_id].zone,
                                                 game.state.cards[first.object_id].controller))
        self.assertEqual("exile", game.state.cards[protected.object_id].zone)
        self.cast_for(game, "A", bounces[1], [grantee.ref])
        self.resolve_top(game)
        self.assertEqual(("battlefield", "B"), (game.state.cards[protected.object_id].zone,
                                                 game.state.cards[protected.object_id].controller))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_decline_and_grantee_leaving_before_resolution_each_leave_the_target_in_play(self):
        game = self.session(61030012)
        provider = self.add(game.engine, ORIGINAL, zone="hand")
        grantee = self.add(game.engine, "Sliver Construct", zone="hand")
        target = self.add(game.engine, "Centaur Courser", seat="B")
        self.add(game.engine, "Elvish Mystic", seat="C")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        for card in (provider, grantee, target, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "W": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", provider, reserve={"C": 3, "U": 1})
        self.resolve_top(game)
        self.choose_target(game, target)
        game = self.decide_exile(game, choice="decline")
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)

        self.cast_for(game, "A", grantee, reserve={"U": 1})
        self.resolve_top(game)
        self.choose_target(game, target)
        self.cast_for(game, "A", bounce, [grantee.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[grantee.object_id].zone)
        pending = self.resolve(game)
        if pending is not None:
            game = self.decide_exile(game, choice="apply", reload=True)
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)
