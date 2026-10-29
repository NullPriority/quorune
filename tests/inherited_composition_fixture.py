"""Shared fixture/actions; deliberately not a unittest TestCase."""
import json,tempfile
from pathlib import Path
from common import ROOT
import test_source_attack_alone as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

class InheritedNativeFixture:
    register_fixture_cards_on_add=True

    session=native.SourceAttackAloneNativeTests.session

    add=native.SourceAttackAloneNativeTests.add

    prepare=native.SourceAttackAloneNativeTests.prepare

    cast=native.SourceAttackAloneNativeTests.cast

    cast_for=native.SourceAttackAloneNativeTests.cast_for

    action=native.SourceAttackAloneNativeTests.action

    priority=native.SourceAttackAloneNativeTests.priority

    checkpoint=native.SourceAttackAloneNativeTests.checkpoint

    resolve=native.SourceAttackAloneNativeTests.resolve

    resolve_top=native.SourceAttackAloneNativeTests.resolve_top

    replay=native.SourceAttackAloneNativeTests.replay

    assert_closed=native.SourceAttackAloneNativeTests.assert_closed

    until=native.SourceAttackAloneNativeTests.until

    attack=native.SourceAttackAloneNativeTests.attack

    trigger_count=native.SourceAttackAloneNativeTests.trigger_count

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'inherited-compositions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/inherited-rule-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Inherited compositions',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def settle(self,session):
        self.until(session,lambda s:not s.state.stack and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority')

    def target(self,session,seat,refs):
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        command={'action_id':'choose','targets':list(refs)};before=authoritative_state_hash(session.state)
        foreign='D' if seat!='D' else 'C';self.assertFalse(session.act('pilot:'+foreign,command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
