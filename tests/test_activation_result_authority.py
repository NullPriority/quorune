"""A structural activated-cost catalogue cannot authorize an unknown result."""

import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
import test_restricted_mana_grants as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class ActivationResultAuthorityTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.RestrictedManaGrantNativeTests.session
    add=native.RestrictedManaGrantNativeTests.add
    prepare=native.RestrictedManaGrantNativeTests.prepare
    priority=native.RestrictedManaGrantNativeTests.priority
    checkpoint=native.RestrictedManaGrantNativeTests.checkpoint
    resolve=native.RestrictedManaGrantNativeTests.resolve
    replay=native.RestrictedManaGrantNativeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'activation-results.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-attack-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Result authority',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def activate(self,session,card,targets=()):
        self.priority(session,'A')
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id'].startswith('activate:'+card.ref+':'))
        result=session.act('pilot:A',{'action_id':offered['id'],'targets':[c.ref for c in targets],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_catalogue_without_result_has_no_offer_and_rejects_before_payment(self):
        session=self.session(602629060);source=self.add(session.engine,'Cloudchaser Kestrel')
        session.state.config.semantic_policy='trusted_only'
        self.prepare(session,mana={'W':1});self.checkpoint(session)
        ability=next(a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if not a.mana_ability)
        self.assertTrue(ability.compiled_cost)
        self.assertIsNone(session.engine.semantics.get(session.engine._semantic_key_for_ability(source,ability)))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r['id'].startswith('activate:'+source.ref+':') for r in actions))
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'activate:'+source.ref+':'+ability.ability_id,'pay':'auto'})
        self.assertFalse(result.ok);self.assertIn('stale action_id',result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state));self.assertFalse(session.state.stack)
        from quorune.rules.activation.proposal import build_activation_proposal
        from quorune.rules.activation.model import ActivationProposalRequest,ActivationProposalError
        request=ActivationProposalRequest(actor='A',source_ref=source.ref,zones=('battlefield',),ability_selector=ability.ability_id)
        with self.assertRaises(ActivationProposalError) as caught:build_activation_proposal(session.engine,request)
        self.assertEqual('unresolved_result_semantics',caught.exception.reason)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_result_authority_keeps_mana_builtin_equip_and_represented_results(self):
        session=self.session(602629070);ring=self.add(session.engine,'Sol Ring');equipment=self.add(session.engine,'Short Sword')
        session.state.config.semantic_policy='trusted_only'
        remover=self.add(session.engine,'Vampire Hexmage');body=self.add(session.engine,'Grizzly Bears');body.counters['charge']=1
        self.prepare(session);self.checkpoint(session)
        self.activate(session,ring);self.assertEqual(2,session.state.players['A'].mana_pool['C'])
        self.activate(session,equipment,[body]);self.assertIsNone(self.resolve(session))
        self.assertEqual(body.object_id,session.state.cards[equipment.object_id].attached_to)
        self.activate(session,remover,[body]);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[body.object_id].counters);self.assertEqual('graveyard',session.state.cards[remover.object_id].zone)
        self.replay(session,load=True)

    def test_missing_result_authority_mutant_is_killed_by_the_invalid_offer(self):
        session=self.session(602629080);source=self.add(session.engine,'Cloudchaser Kestrel')
        session.state.config.semantic_policy='trusted_only'
        self.prepare(session,mana={'W':1});self.checkpoint(session)
        from quorune.rules.activation.proposal import build_activation_offer
        ability=next(a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if not a.mana_ability)
        def witness():self.assertIsNone(build_activation_offer(session.engine,'A',session.state.cards[source.object_id],ability).offer)
        witness()
        with patch('quorune.rules.activation.proposal.activation_result_is_represented',return_value=True),self.assertRaises(AssertionError):witness()

    def test_explicit_arbitration_policy_retains_its_unresolved_proposal_workflow(self):
        from quorune.rules.activation.proposal import build_activation_offer,build_activation_proposal
        from quorune.rules.activation.model import ActivationProposalRequest
        session=self.session(602630090);source=self.add(session.engine,'Cloudchaser Kestrel')
        self.assertEqual('arbitrate_or_pause',session.state.config.semantic_policy)
        self.prepare(session,mana={'W':1});self.checkpoint(session)
        ability=next(a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if not a.mana_ability)
        before=authoritative_state_hash(session.state)
        self.assertIsNotNone(build_activation_offer(session.engine,'A',source,ability).offer)
        request=ActivationProposalRequest(actor='A',source_ref=source.ref,zones=('battlefield',),ability_selector=ability.ability_id)
        proposal=build_activation_proposal(session.engine,request)
        self.assertIsNotNone(proposal);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_shared_result_guard_preserves_explicit_typed_execution_owners(self):
        from quorune.rules.activation.proposal import activation_result_is_represented
        source=object();ability=SimpleNamespace(mana_ability=False,library_search_types=())
        host=SimpleNamespace(_semantic_key_for_ability=lambda *args:'absent',semantics=SimpleNamespace(get=lambda key:None),_crew_threshold=lambda ability:None)
        with patch('quorune.rules.activation.proposal.station_cost_choice',return_value=None):
            self.assertFalse(activation_result_is_represented(host,source,ability))
            for key in ('builtin:equip','builtin:draw:1'):
                host._semantic_key_for_ability=lambda *args,key=key:key
                self.assertTrue(activation_result_is_represented(host,source,ability))
            host._semantic_key_for_ability=lambda *args:'absent';ability.mana_ability=True
            self.assertTrue(activation_result_is_represented(host,source,ability));ability.mana_ability=False
            host._crew_threshold=lambda ability:0
            self.assertTrue(activation_result_is_represented(host,source,ability));host._crew_threshold=lambda ability:None
            ability.library_search_types=('Plains',)
            self.assertTrue(activation_result_is_represented(host,source,ability));ability.library_search_types=()
            host.semantics.get=lambda key:object()
            self.assertTrue(activation_result_is_represented(host,source,ability))
        host.semantics.get=lambda key:None
        with patch('quorune.rules.activation.proposal.station_cost_choice',return_value=object()):
            self.assertTrue(activation_result_is_represented(host,source,ability))
