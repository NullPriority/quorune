from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_cast_grants as controls
from quorune.rules.evidence_storage import read_evidence_document


class AdditionalTokenStatusTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCastGrantTests.session
    add=controls.TurnCastGrantTests.add
    prepare=controls.TurnCastGrantTests.prepare
    checkpoint=controls.TurnCastGrantTests.checkpoint
    resolve=controls.TurnCastGrantTests.resolve
    replay=controls.TurnCastGrantTests.replay
    binding=controls.TurnCastGrantTests.binding
    actions=controls.TurnCastGrantTests.actions
    cast=controls.TurnCastGrantTests.cast
    reject=controls.TurnCastGrantTests.reject
    private_replay=controls.TurnCastGrantTests.private_replay
    priority=controls.TurnCastGrantTests.priority
    options=controls.TurnCastGrantTests.options
    use=controls.TurnCastGrantTests.use

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'additional-status.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/additional-token-status-cards.json',ROOT/'tests/fixtures/token-article-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Additional token creation status',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_normal_registered_tapped_creation_applies_to_added_map_and_replays(self):
        s=self.session(807000);observer=self.add(s.engine,'Generic Token Status Observer')
        spell=self.add(s.engine,'Generic Tapped Artifact Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for card in (observer,spell):self.assertTrue(self.binding(s,card)['strict_capability_ready'])
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.cast(s,'A',spell);self.assertIsNone(self.resolve(s))
        tokens={c.printed_name:c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'}
        self.assertEqual({'Servo','Map'},set(tokens));self.assertTrue(tokens['Servo'].tapped)
        self.assertTrue(tokens['Map'].tapped,{name:card.tapped for name,card in tokens.items()})
        self.private_replay(s,private)

    def test_intrinsic_keywords_stay_with_their_definition_and_plain_status_stays_plain(self):
        s=self.session(807010);self.add(s.engine,'Generic Token Status Observer')
        spell=self.add(s.engine,'Generic Intrinsic Artifact Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(s,spell)['strict_capability_ready']);self.prepare(s,mana={'G':1});self.checkpoint(s)
        self.cast(s,'A',spell);self.assertIsNone(self.resolve(s));tokens={c.printed_name:c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'}
        self.assertEqual({'Servo','Map'},set(tokens));self.assertFalse(any(c.tapped for c in tokens.values()))
        self.assertIn('deathtouch',s.engine._combat_keywords(tokens['Servo']))
        self.assertNotIn('Deathtouch',s.engine._copyable_characteristics(tokens['Map']).get('keywords',[]));self.private_replay(s,private)

    def test_nonmatching_controller_and_type_do_not_receive_additional_tokens(self):
        for i,(owner,name) in enumerate((('B','Generic Tapped Artifact Subject'),('A','Generic Tapped Creature Subject'))):
            s=self.session(807020+i);self.add(s.engine,'Generic Token Status Observer',seat=owner)
            spell=self.add(s.engine,name,zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(s,spell)['strict_capability_ready']);self.prepare(s,mana={'G':1});self.checkpoint(s)
            self.cast(s,'A',spell);self.assertIsNone(self.resolve(s));tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
            self.assertEqual(1,len(tokens));self.assertNotEqual('Map',tokens[0].printed_name);self.assertTrue(tokens[0].tapped);self.private_replay(s,private)

    def test_status_survives_both_legal_quantity_replacement_orders_and_pending_load(self):
        for map_first in (True,False):
            s=self.session(807030+map_first);self.add(s.engine,'Generic Token Status Observer');multiplier=self.add(s.engine,'Parallel Lives')
            spell=self.add(s.engine,'Generic Tapped Artifact Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(s,multiplier)['strict_capability_ready']);self.prepare(s,mana={'G':1});self.checkpoint(s)
            self.cast(s,'A',spell);pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
            s=self.replay(s,load=True);options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
            selected=next((row for row in options if ('Map' in row['label'])==map_first),None);self.assertIsNotNone(selected,options)
            command={'action_id':'choose','replacement':selected['id']};self.reject(s,command,'pilot:D')
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            for _ in range(4):
                pending=self.resolve(s)
                if pending is None:break
                self.assertEqual('replacement.order',pending.kind);row=s.packet('pilot:A',full=True)['decision']['ctx']['options'][0]
                result=s.act('pilot:A',{'action_id':'choose','replacement':row['id']});self.assertTrue(result.ok,result.summary)
            else:self.fail('Token replacement order did not finish')
            tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertTrue(tokens)
            self.assertTrue(all(c.tapped for c in tokens));self.assertEqual(2,sum(c.printed_name=='Servo' for c in tokens))
            self.assertEqual(2 if map_first else 1,sum(c.printed_name=='Map' for c in tokens));self.private_replay(s,private)

    def test_owner_api_temporary_status_is_separate_from_intrinsic_characteristics(self):
        s=self.session(807040);self.add(s.engine,'Generic Token Status Observer');self.prepare(s)
        with s.engine.transaction():
            refs=s.engine.create_token('A',name='Servo',tapped=True,temporary_keywords=('Haste',),
                characteristics={'type_line':'Token Artifact Creature — Servo','power':'1','toughness':'1','colors':[], 'keywords':['Deathtouch']})
        tokens={s.engine._resolve_object('A',ref).printed_name:s.engine._resolve_object('A',ref) for ref in refs}
        self.assertEqual({'Servo','Map'},set(tokens))
        for card in tokens.values():self.assertTrue(card.tapped);self.assertIn('Haste',card.temporary_keywords)
        self.assertIn('Deathtouch',s.engine._copyable_characteristics(tokens['Servo'])['keywords'])
        self.assertNotIn('Deathtouch',s.engine._copyable_characteristics(tokens['Map']).get('keywords',[]))

    def test_ambiguous_mixed_status_rejects_before_creation_and_rolls_back(self):
        from quorune.token_creation import create_token_batch
        from quorune.replacement.model import ReplacementEffectError
        from quorune.record import authoritative_state_hash
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        s=self.session(807050);self.add(s.engine,'Generic Token Status Observer');self.prepare(s)
        compiled=fixed_token_creation_effect_template('Create a tapped 1/1 colorless Servo artifact creature token and a 2/2 colorless Construct artifact creature token.')
        self.assertIsNotNone(compiled);before=authoritative_state_hash(s.state)
        with self.assertRaises(ReplacementEffectError),s.engine.transaction():create_token_batch(s.engine,'A',tokens=compiled.effect['tokens'])
        self.assertEqual(before,authoritative_state_hash(s.state));self.assertFalse(any(c.is_token for c in s.state.cards.values()))

    def test_verified_withdrawal_and_fresh_missing_status_are_rejected(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        cap='token.creation.additional_replacement';raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn status replacement'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Generic Token Status Observer'),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.replacement import application
        method='test_normal_registered_tapped_creation_applies_to_added_map_and_replays'
        with patch.object(application,'_shared_token_creation_status',return_value={}):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
