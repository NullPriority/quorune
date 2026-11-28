"""Capability-bound activated public permanent return and its suspended draw."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.next_draw_replacement_model import PUBLIC_PERMANENT_RETURN_RESULT, ResolvedNextDrawReplacement
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.next_draw_replacement_shapes import PUBLIC_PERMANENT_RETURN_CAPABILITY
from scripts.build_test_database import build_fixture_database
import test_scheduled_draw_outcome_native as scheduled_draw


class ScheduledPublicReturnTests(InheritedNativeFixture, unittest.TestCase):
    source = 'Harbor Assessments'
    prepare_draw = scheduled_draw.ScheduledDrawOutcomeNativeTests.prepare_draw
    use_database = classmethod(scheduled_draw.ScheduledDrawOutcomeNativeTests._use_database.__func__)

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / 'scheduled-public-return.sqlite3'
        fixtures = ROOT / 'tests' / 'fixtures'
        build_fixture_database([
            fixtures / 'draw-discard-replacement-cards.json', fixtures / 'bound-effect-program-cards.json',
            fixtures / 'scheduled-public-return-cards.json',
        ], database)
        cls.use_database(database)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_public_return_resolves_every_seat_before_moving_the_selected_permanents(self):
        game, draw, hands = self.prepare_draw(53116229, ())
        enchantment = self.add(game.engine, self.source)
        self.assert_closed(game, enchantment)
        self.add(game.engine, 'Island', ref='return-activation-payment')
        stolen = self.add(game.engine, 'Grizzly Bears', seat='B', ref='stolen-public-return')
        game.engine.change_control(stolen.object_id, 'A', reason='Prepared current controller for ownership test')
        own = {seat: self.add(game.engine, 'Grizzly Bears', seat=seat, ref=f'public-return-{seat}') for seat in ('B', 'D')}
        objects = {'A': stolen, **own}
        self.prepare(game)
        self.checkpoint(game)
        act = game.act('pilot:A', {'action_id': f'activate:{enchantment.ref}:ab1', 'pay': 'auto'})
        self.assertTrue(act.ok, act.summary)
        self.assertIsNone(self.resolve(game))
        grant, = [item for item in game.state.continuous_effects if isinstance(item, ResolvedNextDrawReplacement)]
        self.assertEqual((PUBLIC_PERMANENT_RETURN_RESULT, enchantment.ref), (grant.result.kind, grant.source_ref))
        before = tuple(game.state.players['A'].zones['library'])
        self.cast_for(game, 'A', draw)
        self.assertIsNotNone(self.resolve(game))
        prior = {}
        for seat, card in objects.items():
            offer = game.packet('pilot:' + seat, full=True)['decision']
            self.assertEqual(('choice.apnap', seat), (offer['kind'], offer['actor']))
            for completed, refs in prior.items():
                self.assertEqual(refs, offer['ctx']['prior_public_choices'][completed])
            self.assertTrue(all(other.ref not in str(offer) for owner, other in hands.items() if owner != seat))
            self.assertEqual(before, tuple(game.state.players['A'].zones['library']))
            self.assertTrue(all(game.state.cards[row.object_id].zone == 'battlefield' for row in objects.values()))
            if seat == 'A':
                snapshot = authoritative_state_hash(game.state)
                invalid = game.act('pilot:A', {'action_id': 'choose', 'cards': [own['B'].ref]})
                self.assertFalse(invalid.ok)
                self.assertEqual(snapshot, authoritative_state_hash(game.state))
            reply = game.act('pilot:' + seat, {'action_id': 'choose', 'cards': [card.ref]})
            self.assertTrue(reply.ok, reply.summary)
            prior[seat] = [card.ref]
            if seat == 'A':
                game = self.replay(game, load=True)
        for card in objects.values():
            self.assertEqual('hand', game.state.cards[card.object_id].zone)
            self.assertIn(card.object_id, game.state.players[card.owner].zones['hand'])
        self.assertEqual(before[:-1], tuple(game.state.players['A'].zones['library']))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_public_return_does_not_admit_without_its_distinct_capability(self):
        card = self.db.lookup(self.source, fuzzy=False)
        source = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', source.status)
        for replacement in ('each opponent', 'target player'):
            variant = replace(card, oracle_text=card.oracle_text.replace('each player', replacement))
            self.assertNotEqual('exact', compile_oracle_card(variant, capability_registry=self.registry,
                                                           capability_profile='commander_review').status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_bytes())
        next(row for row in raw['capabilities'] if row['id'] == PUBLIC_PERMANENT_RETURN_CAPABILITY).update(
            status='blocked', blockers=['public return authority withdrawn'])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=registry,
                                                       capability_profile='commander_review').status)

    def test_omitting_the_public_zone_commit_fails_the_native_witness(self):
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin

        with patch.object(SemanticChoiceIntentHostMixin, 'move_objects_simultaneously_intent', return_value=()):
            with self.assertRaisesRegex(AssertionError, "'hand' != 'battlefield'"):
                self.test_public_return_resolves_every_seat_before_moving_the_selected_permanents()
