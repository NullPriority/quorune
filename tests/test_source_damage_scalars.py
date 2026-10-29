"""A represented source-event pronoun and committed amount keep separate roles."""
from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_source_attack_alone as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry

CAPABILITY='quantity_expression.scalar_effect_amount'
TEXT='Whenever this creature is dealt damage, it deals that much damage to target opponent or planeswalker.'


class SourceDamageScalarCompilerTests(unittest.TestCase):
    def test_source_event_scalar_keeps_source_amount_and_target_roles_separate(self):
        registry=load_default_capability_registry();ir=compile_oracle_card(query_record(TEXT),capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0];effect=node.effects[0]
        self.assertEqual('damage',effect['op']);self.assertEqual('$source',effect['source']);self.assertEqual('$target.0',effect['target'])
        self.assertEqual('committed_event_amount',effect['amount']['origin']);self.assertIn(CAPABILITY,node.capability_dependencies)
        self.assertEqual({'field':'target','op':'eq','value':'$source.ref'},node.event_condition)
        for text in (TEXT.replace('this creature','a creature',1),TEXT+' Rewrite history.',TEXT.replace('that much','an unrepresented amount')):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_omitting_source_pronoun_leaf_mutant_is_killed(self):
        import quorune.compiler.damage_templates as owner
        def witness():self.assertEqual('exact',compile_oracle_card(query_record(TEXT),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch.object(owner,'source_pronoun_damage_effect_template',return_value=None),self.assertRaises(AssertionError):witness()

    def test_scalar_counter_pronoun_uses_the_fixed_incarnation_pinned_source_reference(self):
        text='Whenever this creature is dealt damage, put that many +1/+1 counters on it.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry());self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual('$source.zone_object',ir.faces[0].nodes[0].effects[0]['card'])

    def test_verified_scalar_withdrawal_keeps_fixed_source_damage(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['committed scalar withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(query_record(TEXT),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record(TEXT.replace('that much','2')),capability_registry=registry).status)


class SourceDamageScalarNativeTests(unittest.TestCase):
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
    replay=native.SourceAttackAloneNativeTests.replay
    assert_closed=native.SourceAttackAloneNativeTests.assert_closed
    until=native.SourceAttackAloneNativeTests.until
    attack=native.SourceAttackAloneNativeTests.attack
    resolve_top=native.SourceAttackAloneNativeTests.resolve_top

    @classmethod
    def setUpClass(cls):
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-damage-scalar.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/source-damage-scalar-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source damage scalar',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def choose_target(self,session,target,*,actor='A'):
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        command={'action_id':'choose','targets':[target]};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:'+actor,command);self.assertTrue(result.ok,result.summary)

    def test_actual_lethal_mogg_damage_uses_departed_source_and_exact_committed_amount(self):
        session=self.session(120642001);source=self.add(session.engine,'Mogg Maniac',zone='hand');shock=self.add(session.engine,'Shock',seat='B',zone='hand')
        private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'R':1});session.state.players['B'].mana_pool['R']=1;self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));identity=session.state.cards[source.object_id].logical_object_id;life={s:session.state.players[s].life for s in 'ABCD'}
        self.cast(session,'B',shock,[source.ref]);self.assertIsNotNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        session=self.replay(session,load=True);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'choose','targets':['A']}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.choose_target(session,'C');self.assertIsNone(self.resolve(session));self.assertEqual(life['C']-2,session.state.players['C'].life)
        for seat in 'ABD':self.assertEqual(life[seat],session.state.players[seat].life)
        self.assertNotEqual(identity,session.state.cards[source.object_id].logical_object_id)
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_reckoner_distinguishes_two_damage_occurrences_from_marked_damage_total(self):
        session=self.session(120642010);source=self.add(session.engine,'Boros Reckoner',zone='hand');shock=self.add(session.engine,'Shock',seat='B',zone='hand');second=self.add(session.engine,'Zap',seat='B',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,second);self.prepare(session,mana={'R':3});session.state.players['B'].mana_pool.update({'R':2,'C':2});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));life={s:session.state.players[s].life for s in 'CD'}
        self.cast(session,'B',shock,[source.ref]);self.assertIsNotNone(self.resolve(session));self.choose_target(session,'C')
        self.cast(session,'B',second,[source.ref]);self.assertIsNotNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.choose_target(session,'D')
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(life['C']-2,session.state.players['C'].life);self.assertEqual(life['D']-1,session.state.players['D'].life);self.replay(session,load=True)

    def test_actual_prevented_damage_creates_no_source_damage_trigger(self):
        session=self.session(120642020);source=self.add(session.engine,'Mogg Maniac',zone='hand');prevent=self.add(session.engine,'Mending Hands',zone='hand');shock=self.add(session.engine,'Shock',seat='B',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,prevent);self.prepare(session,mana={'C':1,'R':1,'W':1});session.state.players['B'].mana_pool['R']=1;self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'W':1});self.assertIsNone(self.resolve(session));self.cast(session,'A',prevent,[source.ref]);self.assertIsNone(self.resolve(session))
        self.cast(session,'B',shock,[source.ref]);self.assertIsNone(self.resolve(session));self.assertFalse(session.state.stack)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone);self.assertEqual(0,session.state.cards[source.object_id].marked_damage);self.replay(session,load=True)

    def test_runtime_scalar_dependency_is_inferred_without_declaration(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(120642030);source=self.add(session.engine,'Mogg Maniac');found=0
        for p in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in p.capability_dependencies:continue
            found+=1;deps=tuple(c for c in p.capability_dependencies if c!=CAPABILITY);changed=replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(found,0)

    def test_actual_fixed_hunters_result_is_not_incoming_damage_or_source_power(self):
        session=self.session(120642040);source=self.add(session.engine,'Sun-Crowned Hunters',zone='hand');shock=self.add(session.engine,'Shock',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'R':2});session.state.players['B'].mana_pool['R']=1;self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));life=session.state.players['C'].life
        self.cast(session,'B',shock,[source.ref]);self.assertIsNotNone(self.resolve(session));self.assertEqual(2,session.state.cards[source.object_id].marked_damage);self.assertEqual(5,session.engine._numeric_stat(source.object_id,'power'))
        self.choose_target(session,'C');session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(life-3,session.state.players['C'].life);self.replay(session,load=True)

    def test_actual_drake_counter_trigger_does_not_follow_a_blinked_incarnation(self):
        session=self.session(120642050);source=self.add(session.engine,'Drake Hatcher',zone='hand');blink=self.add(session.engine,'Flicker of Fate',zone='hand');self.add(session.engine,'Fervor')
        for i in range(3):self.add(session.engine,'Plains',ref='counter-blink-plains-'+str(i))
        self.assert_closed(session,source);self.assert_closed(session,blink);self.prepare(session,mana={'U':1});self.checkpoint(session);self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.attack(session,[(source,'B')]);self.until(session,lambda s:bool(s.state.stack));self.assertEqual({},session.state.cards[source.object_id].counters)
        old=session.state.cards[source.object_id].logical_object_id;self.cast(session,'A',blink,[source.ref])
        # The blink spell also triggers the original Hatcher's Prowess.
        # Resolve each top item until the selected blink spell leaves the stack.
        for _ in range(4):
            if session.state.cards[blink.object_id].zone!='stack':break
            self.resolve_top(session)
        self.assertNotEqual('stack',session.state.cards[blink.object_id].zone)
        self.assertNotEqual(old,session.state.cards[source.object_id].logical_object_id);self.assertEqual({},session.state.cards[source.object_id].counters)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual({},session.state.cards[source.object_id].counters);self.replay(session,load=True)

    def test_actual_borrowed_sliver_grant_keeps_recipient_source_and_trigger_controller_after_departures(self):
        session=self.session(120642060);provider=self.add(session.engine,'Spiteful Sliver');body=self.add(session.engine,'Universal Automaton',seat='B')
        theft=self.add(session.engine,'Act of Treason',seat='B',zone='hand');shock=self.add(session.engine,'Shock',zone='hand');bounce=self.add(session.engine,'Unsummon',zone='hand')
        self.assert_closed(session,provider);self.prepare(session,actor='B',active='B',mana={'C':2,'R':1});session.state.players['A'].mana_pool.update({'R':1,'U':1});self.checkpoint(session)
        self.cast(session,'B',theft,[provider.ref]);self.assertIsNone(self.resolve(session));self.assertEqual('B',session.state.cards[provider.object_id].controller)
        life=session.state.players['C'].life;self.cast(session,'A',shock,[body.ref]);self.assertIsNotNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        trigger=next(i for i in session.state.stack if i.kind=='triggered_ability');self.assertEqual('B',trigger.controller);self.assertEqual(body.object_id,trigger.source_object_id)
        self.choose_target(session,'C',actor='B');self.cast(session,'A',bounce,[provider.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[provider.object_id].zone);self.assertEqual(life-2,session.state.players['C'].life);self.replay(session,load=True)

    def test_actual_drake_counter_result_places_the_committed_damage_amount(self):
        session=self.session(120642070);source=self.add(session.engine,'Drake Hatcher',zone='hand');self.add(session.engine,'Fervor')
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session);self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        life=session.state.players['B'].life;self.attack(session,[(source,'B')]);self.until(session,lambda s:bool(s.state.stack));self.assertEqual(life-1,session.state.players['B'].life)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual({'incubation':1},session.state.cards[source.object_id].counters);self.replay(session,load=True)

    def test_actual_coalhauler_uses_one_committed_amount_for_every_player(self):
        session=self.session(120642080);source=self.add(session.engine,'Coalhauler Swine',zone='hand');shock=self.add(session.engine,'Shock',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'R':2});session.state.players['B'].mana_pool['R']=1;self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));life={s:session.state.players[s].life for s in 'ABCD'}
        self.cast(session,'B',shock,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone);self.assertEqual(2,session.state.cards[source.object_id].marked_damage)
        for seat in 'ABCD':self.assertEqual(life[seat]-2,session.state.players[seat].life)
        self.replay(session,load=True)
