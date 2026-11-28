from __future__ import annotations

"""CR101.2/601.2: resolution-created can't rules govern offers and acceptance."""

from pathlib import Path
import json
import os
from copy import deepcopy
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_self_exile_return_spells as controls
from quorune.rules.evidence_storage import read_evidence_document


class TemporaryCastRestrictionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.SelfExileReturnSpellTests.session
    add=controls.SelfExileReturnSpellTests.add
    prepare=controls.SelfExileReturnSpellTests.prepare
    checkpoint=controls.SelfExileReturnSpellTests.checkpoint
    resolve=controls.SelfExileReturnSpellTests.resolve
    replay=controls.SelfExileReturnSpellTests.replay
    binding=controls.SelfExileReturnSpellTests.binding
    priority=controls.SelfExileReturnSpellTests.priority
    cast=controls.SelfExileReturnSpellTests.cast
    top=controls.SelfExileReturnSpellTests.top
    private_replay=controls.SelfExileReturnSpellTests.private_replay
    advance=controls.SelfExileReturnSpellTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'temporary-cast.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/announced-x-additional-cost-cards.json',ROOT/'tests/fixtures/temporary-cast-restriction-cards.json',
            ROOT/'tests/fixtures/announced-x-activation-cards.json',ROOT/'tests/fixtures/flashback-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Temporary casts',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def offered(self,session,seat):return session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']

    def blocked(self,session,seat,card,command):
        self.priority(session,seat)
        self.assertFalse(any(a['id']=='cast:'+card.ref for a in self.offered(session,seat)))
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:'+seat,{'action_id':'cast:'+card.ref,**command});self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_actual_silence_preserves_response_spell_then_blocks_opponents_and_expires(self):
        session=self.session(751300);source=self.add(session.engine,'Silence',zone='hand')
        first=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='before-silence-bolt')
        second=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='after-silence-bolt')
        third=self.add(session.engine,'Lightning Bolt',seat='D',zone='hand',ref='other-opponent-bolt')
        own=self.add(session.engine,'Lightning Bolt',zone='hand',ref='silence-controller-bolt')
        self.add(session.engine,'Mountain',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Silence must admit')
        self.prepare(session,mana={'W':1,'R':1});session.state.players['B'].mana_pool.update({'R':2});session.state.players['D'].mana_pool.update({'R':1})
        self.checkpoint(session);self.cast(session,'A',source,payment={'W':1})
        self.cast(session,'B',first,['A'],{'R':1});self.top(session)
        self.assertEqual(37,session.state.players['A'].life);self.assertEqual(1,len(session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',own,['B'],{'R':1})
        self.blocked(session,'B',second,{'targets':['A'],'pay':'manual','payment':{'R':1}})
        self.blocked(session,'D',third,{'targets':['A'],'pay':'manual','payment':{'R':1}})
        self.assertIsNone(self.resolve(session));self.assertEqual(37,session.state.players['B'].life)
        session=self.replay(session,load=True)
        self.advance(session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main')
        self.assertTrue(any(a['id']=='cast:'+second.ref for a in self.offered(session,'B')))
        self.private_replay(session,private)

    def test_actual_cease_fire_blocks_only_target_creatures_and_draws_for_its_controller(self):
        session=self.session(751301);source=self.add(session.engine,'Cease-Fire',zone='hand')
        creature=self.add(session.engine,'Grizzly Bears',seat='B',zone='hand')
        instant=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        foreign=self.add(session.engine,'Grizzly Bears',seat='D',zone='hand',ref='unrestricted-creature')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Cease-Fire must admit')
        self.prepare(session,actor='B',active='B',mana={'G':1,'C':1,'R':1});session.state.players['A'].mana_pool.update({'W':1,'C':2})
        self.checkpoint(session);hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,'A',source,['B'],{'W':1,'C':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.blocked(session,'B',creature,{'pay':'manual','payment':{'G':1,'C':1}})
        self.cast(session,'B',instant,['A'],{'R':1});self.assertIsNone(self.resolve(session));self.assertEqual(37,session.state.players['A'].life)
        self.assertEqual('hand',session.state.cards[foreign.object_id].zone);self.private_replay(session,private)

    def test_actual_permission_denied_counters_then_blocks_every_opponent_noncreature(self):
        session=self.session(751303);source=self.add(session.engine,'Permission Denied',zone='hand')
        target=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='denied-bolt')
        later=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='later-denied-bolt')
        third=self.add(session.engine,'Lightning Bolt',seat='D',zone='hand',ref='third-denied-bolt')
        body=self.add(session.engine,'Grizzly Bears',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Permission Denied must admit')
        self.prepare(session,actor='B',active='B',mana={'R':2,'G':1,'C':1})
        session.state.players['A'].mana_pool.update({'W':1,'U':1});session.state.players['D'].mana_pool.update({'R':1})
        self.checkpoint(session);self.cast(session,'B',target,['A'],{'R':1})
        self.cast(session,'A',source,[session.state.stack[-1].ref],{'W':1,'U':1})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.assertEqual(40,session.state.players['A'].life)
        self.cast(session,'B',body,payment={'G':1,'C':1})
        self.blocked(session,'B',later,{'targets':['A'],'pay':'manual','payment':{'R':1}})
        self.blocked(session,'D',third,{'targets':['A'],'pay':'manual','payment':{'R':1}})
        self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
        self.private_replay(session,private)

    def test_actual_calamitys_wake_exiles_graveyards_and_itself_but_allows_creatures_lands_and_abilities(self):
        session=self.session(751304);source=self.add(session.engine,"Calamity's Wake",zone='hand')
        graves=[self.add(session.engine,'Grizzly Bears',seat=seat,zone='graveyard',ref='wake-grave-'+seat) for seat in 'ABCD']
        body=self.add(session.engine,'Grizzly Bears',zone='hand',ref='wake-creature-cast')
        own=self.add(session.engine,'Lightning Bolt',zone='hand',ref='wake-controller-bolt')
        foreign=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='wake-opponent-bolt')
        land=self.add(session.engine,'Mountain',zone='hand',ref='wake-land-play')
        ring=self.add(session.engine,'Mountain',ref='wake-mana-mountain');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],"Complete original Calamity's Wake must admit")
        self.prepare(session,mana={'W':1,'C':2,'G':1,'R':1});session.state.players['B'].mana_pool.update({'R':1})
        self.checkpoint(session);self.cast(session,'A',source,payment={'W':1,'C':1})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in (*graves,source)))
        self.blocked(session,'A',own,{'targets':['B'],'pay':'manual','payment':{'R':1}})
        actions=self.offered(session,'A');self.assertTrue(any(a['id']=='play-land:'+land.ref for a in actions))
        mana_actions=[a for a in actions if a['id'].startswith('activate:'+ring.ref+':')];self.assertEqual(1,len(mana_actions))
        result=session.act('pilot:A',{'action_id':mana_actions[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[ring.object_id].tapped)
        result=session.act('pilot:A',{'action_id':'play-land:'+land.ref});self.assertTrue(result.ok,result.summary)
        self.cast(session,'A',body,payment={'G':1,'C':1})
        self.blocked(session,'B',foreign,{'targets':['A'],'pay':'manual','payment':{'R':1}})
        self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
        self.private_replay(session,private)

    def test_actual_orims_chant_preserves_target_and_kicked_attack_scope(self):
        for kicked in (False,True):
            with self.subTest(kicked=kicked):
                session=self.session(751305+int(kicked));source=self.add(session.engine,"Orim's Chant",zone='hand')
                body=self.add(session.engine,'Grizzly Bears',seat='B')
                body.acquired_control_turn_count=-1
                bolt=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.assertTrue(self.binding(session,source)['strict_capability_ready'],"Complete original Orim's Chant must admit")
                self.prepare(session,actor='B',active='B',mana={'R':1});session.state.players['A'].mana_pool.update({'W':1+int(kicked)})
                self.checkpoint(session);self.priority(session,'A')
                command={'action_id':'cast:'+source.ref,'targets':['B'],'cost_option':'kicked' if kicked else 'normal',
                    'pay':'manual','payment':{'W':1+int(kicked)}}
                before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:D',command).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.blocked(session,'B',bolt,{'targets':['A'],'pay':'manual','payment':{'R':1}})
                turn=session.state.turn_sequence;domains=session.engine._attack_declaration_problem('B').domains
                if kicked:
                    self.assertNotIn('A',domains.get(body.ref,()))
                else:
                    self.assertIn('A',domains.get(body.ref,()))
                    self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
                    result=session.act('pilot:B',{'action_id':'attack','attacks':{body.ref:'A'}});self.assertTrue(result.ok,result.summary)
                self.advance(session,lambda s:s.state.phase=='postcombat_main')
                self.assertEqual(turn,session.state.turn_sequence)
                self.assertEqual(40 if kicked else 38,session.state.players['A'].life);self.private_replay(session,private)

    def test_silence_blocks_existing_flashback_and_commander_permissions_without_paying(self):
        session=self.session(751308);source=self.add(session.engine,'Silence',seat='B',zone='hand')
        flashback=self.add(session.engine,'Think Twice',zone='graveyard')
        commanders=[session.state.cards[oid] for oid in session.state.players['A'].zones['command']]
        self.assertEqual(1,len(commanders));commander=commanders[0]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,flashback)['strict_capability_ready'])
        self.prepare(session,mana={'W':1,'U':1,'C':2});session.state.players['B'].mana_pool.update({'W':1})
        before={a['id'] for a in self.offered(session,'A')}
        self.assertIn('cast:'+flashback.ref,before);self.assertIn('cast:'+commander.ref,before)
        self.checkpoint(session);self.cast(session,'B',source,payment={'W':1});self.assertIsNone(self.resolve(session))
        session=self.replay(session,load=True)
        self.blocked(session,'A',flashback,{'from':'graveyard','cost_option':'flashback','pay':'manual','payment':{'U':1,'C':2}})
        self.blocked(session,'A',commander,{'from':'command','pay':'manual','payment':{'W':1}})
        self.assertEqual('graveyard',session.state.cards[flashback.object_id].zone)
        self.assertEqual('command',session.state.cards[commander.object_id].zone);self.private_replay(session,private)

    def test_permission_denied_legal_uncounterable_target_still_creates_restriction(self):
        session=self.session(751309);source=self.add(session.engine,'Permission Denied',zone='hand')
        veto=self.add(session.engine,"Dovin's Veto",seat='B',zone='hand')
        target=self.add(session.engine,'Lightning Bolt',zone='hand',ref='veto-target-bolt')
        later=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='veto-later-bolt')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,veto)['strict_capability_ready'])
        self.prepare(session,mana={'W':1,'U':1,'R':1});session.state.players['B'].mana_pool.update({'W':1,'U':1,'R':1})
        self.checkpoint(session);self.cast(session,'A',target,['B'],{'R':1})
        self.cast(session,'B',veto,[session.state.stack[-1].ref],{'W':1,'U':1})
        self.assertTrue(session.state.stack[-1].context['cant_be_countered'])
        self.cast(session,'A',source,[session.state.stack[-1].ref],{'W':1,'U':1})
        session=self.replay(session,load=True);self.top(session,remaining=2)
        self.assertEqual('stack',session.state.cards[veto.object_id].zone)
        self.blocked(session,'B',later,{'targets':['A'],'pay':'manual','payment':{'R':1}})
        self.assertIsNone(self.resolve(session));self.assertEqual(40,session.state.players['B'].life)
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.private_replay(session,private)

    def test_permission_denied_all_illegal_target_creates_no_rule_and_later_cast_remains_legal(self):
        session=self.session(751310);source=self.add(session.engine,'Permission Denied',zone='hand')
        target=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='withdrawn-target-bolt')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        later=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='legal-after-illegal-target')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',active='B',mana={'R':2,'U':2});session.state.players['A'].mana_pool.update({'W':1,'U':1})
        self.checkpoint(session);self.cast(session,'B',target,['A'],{'R':1});ref=session.state.stack[-1].ref
        self.cast(session,'A',source,[ref],{'W':1,'U':1});self.cast(session,'B',counter,[ref],{'U':2})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        from quorune.temporary_cast_rule_model import ResolvedCastRestriction
        self.assertFalse(any(isinstance(e,ResolvedCastRestriction) for e in session.state.continuous_effects))
        self.assertTrue(any(a['id']=='cast:'+later.ref for a in self.offered(session,'B')))
        self.cast(session,'B',later,['A'],{'R':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(37,session.state.players['A'].life);self.private_replay(session,private)

    def test_canonically_verified_restriction_owner_withdrawal_keeps_all_six_originals_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        cap='casting.prohibition.resolution_until_end_of_turn'
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn temporary casting restriction owner'])
        registry=CapabilityRegistry(changed);declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Silence','Cease-Fire','Permission Denied',"Calamity's Wake","Orim's Chant",'Ranger-Captain of Eos'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers))
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_actual_ranger_captain_searches_then_sacrifices_before_restriction_resolves(self):
        session=self.session(751307);source=self.add(session.engine,'Ranger-Captain of Eos',zone='hand')
        eligible=self.add(session.engine,'Generic Bound Commander',zone='library',ref='ranger-search-creature')
        response=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='ranger-response')
        later=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='ranger-blocked')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Ranger-Captain must admit')
        self.prepare(session,mana={'W':2,'C':1});session.state.players['B'].mana_pool.update({'R':2})
        self.checkpoint(session);self.cast(session,'A',source,payment={'W':2,'C':1})
        decision=self.resolve(session);self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice',decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);packet=session.packet('pilot:A',full=True)
        self.assertIn(eligible.ref,{row['id'] for row in packet['decision']['ctx'].get('search_cards',())})
        result=session.act('pilot:A',{'action_id':'choose','search_cards':[eligible.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('hand',session.state.cards[eligible.object_id].zone)
        self.assertEqual(1,session.state.players['A'].stats.get('shuffle_count',0))
        self.priority(session,'A');actions=[a for a in self.offered(session,'A') if a['id'].startswith('activate:'+source.ref+':')]
        self.assertEqual(1,len(actions));result=session.act('pilot:A',{'action_id':actions[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.cast(session,'B',response,['A'],{'R':1});self.top(session)
        self.assertEqual(37,session.state.players['A'].life)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.blocked(session,'B',later,{'targets':['A'],'pay':'manual','payment':{'R':1}});self.private_replay(session,private)

    def test_cast_rule_spec_and_journal_reject_malformed_scope_subject_and_duration(self):
        from quorune.temporary_cast_rule_model import TemporaryCastRestrictionSpec,ResolvedCastRestriction
        from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
        spec=TemporaryCastRestrictionSpec('target','creature');rule=ResolvedCastRestriction('CR-test','S-test',1,'A',spec,'B')
        self.assertEqual(rule,continuous_journal_effect_from_dict(rule.to_dict()))
        for data in ({**spec.to_dict(),'schema_version':True},{**spec.to_dict(),'player_scope':'unowned'},
                {**spec.to_dict(),'spell_kind':'activated abilities'},{**spec.to_dict(),'extra':True}):
            with self.assertRaises((ValueError,TypeError)):TemporaryCastRestrictionSpec.from_dict(data)
        for data in ({**rule.to_dict(),'target_player':None},{**rule.to_dict(),'duration':'while_source_present'},
                {**rule.to_dict(),'extra':True},{**rule.to_dict(),'timestamp':True}):
            with self.assertRaises((ValueError,TypeError)):continuous_journal_effect_from_dict(data)

    def test_emitted_cast_restriction_requires_its_owner_independently_at_runtime(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        cap='casting.prohibition.resolution_until_end_of_turn'
        session=self.session(751302);source=self.add(session.engine,'Silence',zone='hand')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies]
        self.assertEqual(1,len(programs));program=programs[0]
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        effect=program.effects[0]
        for changed in ({**effect,'restriction':{**effect['restriction'],'spell_kind':'abilities'}},
                {**effect,'player':'$target.0'},{**effect,'unexpected':1}):
            malformed=replace(program,effects=(changed,))
            bound=bind_semantic_program_runtime(malformed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertTrue(any(b.startswith('runtime_temporary_cast_restriction:invalid:') for b in bound['blockers']))

    def test_actual_silence_kills_producer_and_journal_commit_omissions(self):
        method='test_actual_silence_preserves_response_spell_then_blocks_opponents_and_expires'
        for owner,omission in (('quorune.compiler.temporary_player_rule_templates.temporary_cast_restriction_template',lambda *a:None),
                ('quorune.rules.temporary_cast_restrictions.commit_continuous_effect',lambda *a:None)):
            with patch(owner,omission):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_shared_cast_query_and_descriptor_validator_omissions_are_killed(self):
        for owner,omission,method in (
                ('quorune.rules.temporary_cast_restrictions.active_temporary_cast_restrictions',lambda *a:(),
                 'test_actual_silence_preserves_response_spell_then_blocks_opponents_and_expires'),
                ('quorune.card_programs.runtime_requirements.temporary_cast_runtime_dependencies',lambda *a:(),
                 'test_emitted_cast_restriction_requires_its_owner_independently_at_runtime')):
            with patch(owner,omission):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture


AVATAR_WRATH = "Avatar's Wrath"


APPA = "Appa, Steadfast Guardian"


class AirbendCastRestrictionNativeTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "temporary-cast-restriction-cards.json": {AVATAR_WRATH, APPA},
            "event-spell-copy-cards.json": {"Ambush Viper", "Grizzly Bears"},
            "activation-restriction-material-cards.json": {"Unsummon", "Island"},
            "ability-source-counter-cards.json": {"Vedalken Orrery"},
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains"},
            "awaken-cards.json": {"Dryad Arbor"},
        }
        fixtures = []
        for filename, names in support.items():
            doc = json.loads((ROOT / "tests" / "fixtures" / filename).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert names == {row["name"] for row in doc["cards"]}, filename
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / filename
            part.write_text(json.dumps(doc), encoding="utf-8")
            fixtures.append(part)
        path = folder / "airbend-wrath.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Airbend and next-turn rule", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def source(self, game):
        card = self.add(game.engine, AVATAR_WRATH, zone="hand", ref="wrath-original-spell")
        self.assert_closed(game, card)
        return card

    def next_turn(self, game, expected):
        self.finish_turn(game)
        self.assertEqual(expected, game.state.active_player)

    def test_actual_airbend_keeps_target_and_exile_grant_waits_for_original_controllers_next_turn(self):
        from quorune.rules.airbend import airbend_cast_option, current_airbend_cast_permission
        from quorune.rules.casting.proposal import build_cast_offer
        from quorune.rules.temporary_cast_restrictions import active_temporary_cast_restrictions

        game = self.session(7016501)
        source = self.source(game)
        flash = self.add(game.engine, "Vedalken Orrery")
        saved = self.add(game.engine, "Grizzly Bears", ref="wrath-saved-creature")
        airbent = self.add(game.engine, "Ambush Viper", seat="B", ref="wrath-b-owned-a-controlled")
        hand_spell = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="wrath-b-hand-cast")
        private = self.add(game.engine, "Grizzly Bears", seat="C", zone="hand", ref="wrath-c-private-card")
        game.engine.change_control(airbent.object_id, "A")
        self.assert_closed(game, flash)
        self.assert_closed(game, airbent)
        self.assert_closed(game, hand_spell)
        for index in range(4):
            self.add(game.engine, "Generic Bound Plains", ref=f"wrath-white-mana-{index}")
        for index in range(3):
            self.add(game.engine, "Island", seat="B", ref=f"wrath-b-island-{index}")
        self.prepare(game)
        self.checkpoint(game)
        self.next_turn(game, "B")
        self.until(game, lambda g: g.state.phase == "precombat_main")
        self.cast_for(game, "A", source, [saved.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[saved.object_id].zone)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        current = game.state.cards[airbent.object_id]
        self.assertEqual("exile", current.zone)
        permission = current_airbend_cast_permission(current, "B")
        self.assertEqual(current.logical_object_id, permission.logical_object_id)
        self.assertIsNone(current_airbend_cast_permission(current, "A"))
        restriction, = active_temporary_cast_restrictions(game.engine, "B")
        self.assertEqual(("A", "A", "until_next_turn"), (restriction.controller, restriction.expires_on_next_turn_of, restriction.duration.value))
        self.assertIsNone(build_cast_offer(game.engine, "B", current).offer)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.priority(game, "B")
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:B", {"action_id": "cast:" + current.ref, "cost_option": "airbend", "pay": "auto"})
        self.assertFalse(bad.ok, bad.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "B", hand_spell, [saved.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[saved.object_id].zone)
        game = self.replay(game, load=True)
        for seat in "CD":
            self.next_turn(game, seat)
            self.assertTrue(active_temporary_cast_restrictions(game.engine, "B"))
        self.next_turn(game, "A")
        self.assertFalse(active_temporary_cast_restrictions(game.engine, "B"))
        self.priority(game, "B")
        current = game.state.cards[airbent.object_id]
        quotes = build_cast_offer(game.engine, "B", current).offer.to_dict()["cost_options"]
        quote, = quotes
        self.assertEqual(("airbend", "alternate"), (quote["id"], quote["kind"]))
        self.assertEqual("zero", airbend_cast_option(current, "B")["x_value_policy"])
        self.assertEqual({"GENERIC": 2}, {key: amount for key, amount in quote["requirements"].items() if amount})
        bad = game.act("pilot:B", {"action_id": "cast:" + current.ref, "cost_option": "normal", "pay": "auto"})
        self.assertFalse(bad.ok, bad.summary)
        chosen = game.act("pilot:B", {"action_id": "cast:" + current.ref, "cost_option": "airbend", "pay": "auto"})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("stack", game.state.cards[airbent.object_id].zone)
        self.assertIsNone(self.resolve(game))
        landed = game.state.cards[airbent.object_id]
        self.assertEqual(("battlefield", "B"), (landed.zone, landed.controller))
        self.assertIsNone(current_airbend_cast_permission(landed, "B"))
        self.replay(game, load=True)

    def test_intentionally_empty_target_airbends_every_creature_and_never_play_lands_or_tokens(self):
        from quorune.rules.airbend import AIRBEND_EVENT, current_airbend_cast_permission
        from quorune.rules.casting.proposal import build_cast_offer
        from quorune.rules.temporary_cast_restrictions import active_temporary_cast_restrictions
        from quorune.rules.zone_action_permissions import compiled_land_play_permission

        game = self.session(7016502)
        source = self.source(game)
        owner_card = self.add(game.engine, "Grizzly Bears", ref="wrath-empty-own")
        other_card = self.add(game.engine, "Grizzly Bears", seat="B", ref="wrath-empty-opponent")
        dryad = self.add(game.engine, "Dryad Arbor", ref="wrath-land-creature")
        land = self.add(game.engine, "Generic Bound Plains", ref="wrath-noncreature-land")
        token_ref, = game.engine.create_token("C", name="Soldier Token", quantity=1,
            characteristics={"type_line": "Creature — Soldier", "power": "1", "toughness": "1"})
        self.prepare(game, mana={"C": 2, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [])
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in (source, owner_card, other_card, dryad)))
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.assertIsNone(current_airbend_cast_permission(game.state.cards[source.object_id], "A"))
        self.assertIsNotNone(current_airbend_cast_permission(game.state.cards[owner_card.object_id], "A"))
        self.assertFalse(compiled_land_play_permission(game.engine, "A", game.state.cards[dryad.object_id]))
        self.assertIsNone(build_cast_offer(game.engine, "A", game.state.cards[dryad.object_id]).offer)
        self.assertFalse(any(card.ref == token_ref and card.zone in {"battlefield", "exile"} for card in game.state.cards.values()))
        event, = (row for row in game.state.events if row.code == AIRBEND_EVENT)
        self.assertEqual({owner_card.ref, other_card.ref, dryad.ref, token_ref}, set(event.details["objects"]))
        self.assertTrue(active_temporary_cast_restrictions(game.engine, "D"))
        self.replay(game, load=True)

    def test_illegal_sole_target_skips_airbend_cast_restriction_and_terminal_exile(self):
        from quorune.rules.airbend import AIRBEND_EVENT, current_airbend_cast_permission
        from quorune.rules.temporary_cast_restrictions import active_temporary_cast_restrictions

        game = self.session(7016503)
        source = self.source(game)
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="wrath-sole-target")
        other = self.add(game.engine, "Ambush Viper", seat="C", ref="wrath-unmoved")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        game.state.players["B"].mana_pool["U"] = 1
        self.prepare(game, mana={"C": 2, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [target.ref])
        self.cast_for(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.assertIsNone(current_airbend_cast_permission(game.state.cards[other.object_id], "C"))
        self.assertFalse(any(row.code == AIRBEND_EVENT for row in game.state.events))
        self.assertFalse(active_temporary_cast_restrictions(game.engine, "B"))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_airbend_whole_span_and_independent_permission_duration_and_terminal_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.airbend import CAPABILITY, CAST_CAPABILITY, airbend_node_capabilities
        from quorune.rules.spell_self_exile import SELF_EXILE
        from quorune.temporary_cast_rule_model import NEXT_TURN_NONHAND_CAPABILITY

        card = self.db.lookup(AVATAR_WRATH, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
        required = {CAPABILITY, CAST_CAPABILITY, NEXT_TURN_NONHAND_CAPABILITY, SELF_EXILE.capability}
        self.assertTrue(required <= set(node.capability_dependencies))
        self.assertEqual((), airbend_node_capabilities(tuple(reversed(node.effects)), node.target_schema, None))
        for original, changed in (("Choose up to one target", "Choose one target"),
                                  ("Until your next turn", "Until end of turn"),
                                  ("airbend all other creatures", "airbend all other nonland creatures")):
            text = card.oracle_text.replace(original, changed)
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry).status)
        game = self.session(7016504)
        original = self.source(game)
        program, = game.engine.semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual("exile", program.destination)
        for cap in required:
            deps = tuple(value for value in program.capability_dependencies if value != cap)
            mutant = replace(program, capability_dependencies=deps,
                             capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", binding["blockers"])


    def _appa_enter(self, game, card):
        self.assert_closed(game, card)
        self.cast_for(game, "A", card)
        decision = self.resolve(game)
        self.assertEqual(("semantic.target", ("A",)), (decision.kind, tuple(decision.actors)))
        offer, = [row for row in game.packet("pilot:A", full=True)["decision"]["legal_actions"] if row["id"] == "choose"]
        return offer["target_schema"]

    def _appa_send(self, game, seat, command, *, accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def _appa_record(self, game, comment):
        from hashlib import sha256
        from quorune.record import replay_record

        folder = os.environ.get("QUORUNE_APPA_RECORD_PARENT")
        if folder is None:
            self.replay(game, load=True)
            return
        record = Path(folder) / comment
        game.save(record)
        replay = replay_record(record, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        state_hash = authoritative_state_hash(game.state)
        self.assertEqual(state_hash, replay["final_state_hash"])
        record.with_suffix(".receipt.json").write_text(json.dumps({
            "manifest_sha256": sha256((record / "manifest.json").read_bytes()).hexdigest(),
            "final_state_sha256": state_hash, "commands": len(game.commands),
        }, indent=2) + "\n", encoding="utf-8")

    def _appa_allies(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and not card.is_card_object
                and "Ally" in game.engine._effective_card_data(card)["type_line"]]

    def _appa_cast_exile(self, game, seat, card):
        self.priority(game, seat)
        target = game.state.cards[card.object_id]
        offer = self.action(game, seat, target)
        option, = offer["cost_options"]
        self.assertEqual(("airbend", "alternate"), (option["id"], option["kind"]))
        self.assertEqual({"GENERIC": 2}, {symbol: size for symbol, size in option["requirements"].items() if size})
        self._appa_send(game, seat, {"action_id": offer["id"], "cost_option": "airbend", "pay": "auto"})
        cast = next(item for item in game.state.stack if item.card_object_id == target.object_id)
        self.assertEqual("exile", cast.context["cast_origin"])

    def test_appa_locks_any_number_airbends_legal_arrivals_and_tracks_real_exile_casts(self):
        from quorune.rules.airbend import AIRBEND_EVENT, current_airbend_cast_permission

        game = self.session(70165101)
        appa = self.add(game.engine, APPA, zone="hand")
        own = self.add(game.engine, "Grizzly Bears", ref="appa-own-bear")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="appa-other-seat")
        departed = self.add(game.engine, "Grizzly Bears", ref="appa-departing")
        artifact = self.add(game.engine, "Vedalken Orrery", ref="appa-artifact")
        borrowed = self.add(game.engine, "Ambush Viper", seat="B", ref="appa-borrowed-viper")
        land = self.add(game.engine, "Dryad Arbor", ref="appa-land-creature")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="appa-hand-response")
        game.engine.change_control(borrowed.object_id, "A")
        token_ref, = game.engine.create_token("A", name="Soldier Token", quantity=1,
            characteristics={"type_line": "Creature — Soldier", "power": "1", "toughness": "1"})
        self.prepare(game, mana={"C": 4, "W": 2})
        game.state.players["B"].mana_pool.update({"C": 2, "U": 1})
        self.checkpoint(game)
        targets = self._appa_enter(game, appa)
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertIsNone(game.packet(principal, full=True)["decision"])
        self.assertEqual({own.ref, departed.ref, artifact.ref, borrowed.ref, token_ref}, set(targets["legal_refs"]))
        for seat, items in (("B", [own.ref]), ("A", [own.ref, own.ref]), ("A", [appa.ref]),
                            ("A", [other.ref]), ("A", [land.ref])):
            self._appa_send(game, seat, {"action_id": "choose", "targets": items}, accepted=False)
        self._appa_record(game, "appa-target-choice-pending-game-record")
        self._appa_send(game, "A", {"action_id": "choose", "targets": [
            own.ref, artifact.ref, borrowed.ref, token_ref, departed.ref]})
        self.cast_for(game, "B", bounce, [departed.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[departed.object_id].zone)
        self.assertFalse(self._appa_allies(game))
        self._appa_record(game, "appa-partial-targets-pending-game-record")
        self.settle(game)
        event, = [row for row in game.state.events if row.code == AIRBEND_EVENT]
        self.assertEqual({own.ref, artifact.ref, borrowed.ref, token_ref}, set(event.details["objects"]))
        self.assertFalse(any(card.ref == token_ref and card.zone in {"battlefield", "exile"} for card in game.state.cards.values()))
        for original, seat in ((own, "A"), (artifact, "A"), (borrowed, "B")):
            current = game.state.cards[original.object_id]
            self.assertEqual("exile", current.zone)
            permission = current_airbend_cast_permission(current, seat)
            self.assertEqual(current.logical_object_id, permission.logical_object_id)
            self.assertIsNone(current_airbend_cast_permission(current, "D"))
        self.assertIsNone(current_airbend_cast_permission(game.state.cards[borrowed.object_id], "A"))
        self.assertEqual("battlefield", game.state.cards[appa.object_id].zone)
        self.assertFalse(self._appa_allies(game))
        self.priority(game, "A")
        self._appa_send(game, "A", {"action_id": "cast:" + game.state.cards[borrowed.object_id].ref,
                                  "cost_option": "airbend", "pay": "auto"}, accepted=False)
        self._appa_cast_exile(game, "B", borrowed)
        self.settle(game)
        self.assertFalse(self._appa_allies(game))
        self.assertEqual("B", game.state.cards[borrowed.object_id].controller)
        self._appa_cast_exile(game, "A", own)
        self._appa_record(game, "appa-own-exile-cast-pending-game-record")
        self.settle(game)
        ally, = self._appa_allies(game)
        actual = game.engine._effective_card_data(ally)
        self.assertEqual(("A", 1, 1, ["W"]), (ally.controller, int(actual["power"]),
                                              int(actual["toughness"]), actual["colors"]))
        self.assertEqual(("battlefield", "A"), (game.state.cards[own.object_id].zone, game.state.cards[own.object_id].controller))
        self.assertIsNone(current_airbend_cast_permission(game.state.cards[own.object_id], "A"))
        self._appa_record(game, "appa-exile-cast-ally-resolved-game-record")

    def test_appa_flash_and_empty_choice_are_distinct_from_illegal_sole_target(self):
        from quorune.rules.airbend import AIRBEND_EVENT

        for choose in (False, True):
            with self.subTest(choose=choose):
                game = self.session(70165102 + int(choose))
                appa = self.add(game.engine, APPA, zone="hand")
                candidate = self.add(game.engine, "Grizzly Bears", ref="appa-one-candidate")
                still = self.add(game.engine, "Grizzly Bears", ref="appa-unselected")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.prepare(game, mana={"C": 2, "W": 2}, active="B", actor="A")
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                self.assertEqual({candidate.ref, still.ref}, set(self._appa_enter(game, appa)["legal_refs"]))
                self._appa_send(game, "A", {"action_id": "choose", "targets": [candidate.ref] if choose else []})
                if choose:
                    self.cast_for(game, "B", bounce, [candidate.ref])
                    self.resolve_top(game)
                self.settle(game)
                self.assertEqual("hand" if choose else "battlefield", game.state.cards[candidate.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[still.object_id].zone)
                self.assertFalse(any(row.code == AIRBEND_EVENT for row in game.state.events))
                self.assertEqual(choose, any(row.code == "target.illegal" for row in game.state.events))
                self._appa_record(game, f"appa-{'sole-illegal' if choose else 'empty'}-game-record")

    def test_appa_exact_target_type_controller_and_self_entry_require_all_dependencies(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.airbend import TARGETED_CAPABILITY, airbend_targeted_nonland_schema
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup(APPA, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, APPA)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        entry, = [program for program in semantics.programs_for_oracle(card.oracle_id) if program.event == "permanent.enter.self"]
        self.assertEqual(airbend_targeted_nonland_schema(), entry.target_schema)
        deps = tuple(cap for cap in entry.capability_dependencies if cap != TARGETED_CAPABILITY)
        mutant = replace(entry, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{TARGETED_CAPABILITY}", binding["blockers"])
        for wrong in (replace(entry, event="permanent.leave.self"),
                      replace(entry, target_schema={**entry.target_schema, "source_exclusion": False}),
                      replace(entry, target_schema={**entry.target_schema, "controller_relation": "opponent"})):
            report = bind_semantic_program_runtime(wrong, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(report["strict"], report)
        for wrong in ("other target lands you control", "other target nonland permanents an opponent controls",
                      "target nonland permanents you control"):
            changed = replace(card, oracle_text=card.oracle_text.replace("other target nonland permanents you control", wrong))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)
