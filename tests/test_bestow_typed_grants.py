"""Pinned CR702.103: a represented Bestow face can supply an Aura-mode quote."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_enchanted_player_statics as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry

from scripts.build_test_database import build_fixture_database

CAPABILITY='casting.bestow.fixed_mana'
QUOTE='Enchanted creature gets +4/+4 and has "When this creature becomes the target of a spell or ability, sacrifice it."'
TEXT='Bestow {3}{U}{U}\nWhen this permanent becomes the target of a spell or ability, sacrifice it.\n'+QUOTE


def record(text,*,keywords=('Bestow',)):
    return replace(query_record(text),type_line='Enchantment Creature — Nautilus',power='4',toughness='4',keywords=keywords)


class BestowTypedGrantCompilerTests(unittest.TestCase):
    def test_validated_face_mode_read_ahead_admits_quote_independent_of_line_order(self):
        registry=load_default_capability_registry()
        for text in (TEXT,QUOTE+'\nBestow {3}{U}{U}',TEXT.replace('Bestow {3}{U}{U}','Bestow {0}')):
            ir=compile_oracle_card(record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            parents=[n for n in ir.faces[0].nodes if n.text==QUOTE and n.kind=='static_ability']
            self.assertEqual(1,len(parents));self.assertEqual(QUOTE,text[parents[0].span.start:parents[0].span.end])
            self.assertTrue(any(n.kind=='granted_triggered_ability' for n in ir.faces[0].nodes))
        for text in (QUOTE,TEXT.replace('Bestow {3}{U}{U}','Bestow {X}{U}'),
                TEXT.replace('Bestow {3}{U}{U}','Bestow {U/P}'),TEXT.replace('Bestow {3}{U}{U}','Bestow chosen cost')):
            self.assertNotEqual('exact',compile_oracle_card(record(text),capability_registry=registry).status,text)

    def test_verified_parent_mode_withdrawal_blocks_complete_card_and_keeps_unrelated_keyword(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['Bestow parent withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record(TEXT),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record('Flying'),capability_registry=registry).status)

    def test_omitted_validated_mode_read_ahead_mutant_is_killed(self):
        registry=load_default_capability_registry()
        def witness():self.assertEqual('exact',compile_oracle_card(record(TEXT),capability_registry=registry).status)
        witness()
        with patch('quorune.compiler.bestow_nodes.face_has_fixed_bestow',return_value=False),self.assertRaises(AssertionError):witness()


class BestowTypedGrantNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.EnchantedPlayerStaticNativeTests.session
    add=native.EnchantedPlayerStaticNativeTests.add
    prepare=native.EnchantedPlayerStaticNativeTests.prepare
    cast=native.EnchantedPlayerStaticNativeTests.cast
    cast_for=native.EnchantedPlayerStaticNativeTests.cast_for
    action=native.EnchantedPlayerStaticNativeTests.action
    priority=native.EnchantedPlayerStaticNativeTests.priority
    checkpoint=native.EnchantedPlayerStaticNativeTests.checkpoint
    resolve=native.EnchantedPlayerStaticNativeTests.resolve
    replay=native.EnchantedPlayerStaticNativeTests.replay
    assert_closed=native.EnchantedPlayerStaticNativeTests.assert_closed
    stats=native.EnchantedPlayerStaticNativeTests.stats

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'bestow-grants.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/bestow-typed-grant-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Bestow typed grants',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def bestow(self,session,source,target,*,reserve=None):
        self.priority(session,'A');offered=self.action(session,'A',session.state.cards[source.object_id])
        self.assertIn('bestow',[row['id'] for row in offered['cost_options']])
        before=authoritative_state_hash(session.state)
        command={'action_id':offered['id'],'cost_option':'bestow','targets':[target.ref],'pay':'auto','reserve':reserve or {}}
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def source_fixture(self,seed):
        session=self.session(seed);source=self.add(session.engine,'Crystalline Nautilus',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',seat='B',ref='bestow-receiver')
        self.assert_closed(session,source)
        return session,source,body

    def test_actual_normal_creature_mode_has_only_its_own_targeted_sacrifice(self):
        session,source,body=self.source_fixture(702627001);spell=self.add(session.engine,'Twiddle',seat='C',zone='hand')
        self.prepare(session,mana={'C':2,'U':1});session.state.players['C'].mana_pool['U']=1;self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.assertIn('creature',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[source.object_id])['type_line'])[0])
        self.assertIsNone(session.state.cards[source.object_id].attached_to);self.assertEqual((2,2),self.stats(session,body));self.assertEqual((4,4),self.stats(session,source))
        self.cast(session,'C',spell,[source.ref]);self.assertTrue(any(s.kind=='triggered_ability' and s.controller=='A' for s in session.state.stack))
        self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual('battlefield',session.state.cards[body.object_id].zone);self.replay(session,load=True)

    def test_actual_bestowed_grant_is_controlled_by_receiver_and_unattachment_restores_creature(self):
        session,source,body=self.source_fixture(702627010);spell=self.add(session.engine,'Twiddle',seat='C',zone='hand');private=self.add(session.engine,'Twiddle',seat='D',zone='hand',ref='bestow-private')
        self.prepare(session,mana={'C':3,'U':2});session.state.players['C'].mana_pool['U']=1;self.checkpoint(session)
        self.bestow(session,source,body);self.assertIsNone(self.resolve(session))
        self.assertEqual(body.object_id,session.state.cards[source.object_id].attached_to);self.assertEqual((6,6),self.stats(session,body))
        self.assertEqual({'enchantment'},set(session.engine._type_parts(session.engine._effective_card_data(session.state.cards[source.object_id])['type_line'])[0]))
        self.cast(session,'C',spell,[body.ref]);self.assertTrue(any(s.kind=='triggered_ability' and s.controller=='B' for s in session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[body.object_id].zone);self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertIsNone(session.state.cards[source.object_id].attached_to);self.assertEqual((4,4),self.stats(session,source))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_queued_receiver_trigger_survives_aura_source_sacrificing_to_its_own_target_trigger(self):
        session,source,body=self.source_fixture(702627020);spell=self.add(session.engine,'Twiddle',seat='C',zone='hand');removal=self.add(session.engine,'Disenchant',zone='hand')
        self.prepare(session,mana={'C':4,'U':2,'W':1});session.state.players['C'].mana_pool['U']=1;self.checkpoint(session)
        self.bestow(session,source,body,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'C',spell,[body.ref]);self.assertTrue(any(s.kind=='triggered_ability' and s.controller=='B' for s in session.state.stack))
        self.cast(session,'A',removal,[source.ref]);self.assertTrue(any(s.kind=='triggered_ability' and s.controller=='A' for s in session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual('graveyard',session.state.cards[body.object_id].zone);self.replay(session,load=True)

    def test_actual_bestow_target_loss_resolves_as_creature_without_granting_to_departed_receiver(self):
        session,source,body=self.source_fixture(702627030);bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        self.prepare(session,mana={'C':3,'U':2});session.state.players['B'].mana_pool['U']=1;self.checkpoint(session)
        self.bestow(session,source,body);self.cast(session,'B',bounce,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[body.object_id].zone);self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertIn('creature',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[source.object_id])['type_line'])[0]);self.assertEqual((4,4),self.stats(session,source))
        self.assertIsNone(session.state.cards[source.object_id].attached_to);self.replay(session,load=True)
