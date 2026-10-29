"""Cast spell mana value includes X and survives payment changes and departure."""

from copy import deepcopy
from dataclasses import replace
import json,math,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_event_player_fixed_damage as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin
from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
from quorune.cast_event_amounts import CAST_MANA_VALUE_CAPABILITY as CAPABILITY,committed_cast_mana_value_facts
from quorune.query_effect_amount_model import PublicQueryAmountError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


def cast_amount(**changes):
    return ScalarEffectAmountSpec(ScalarAmountOrigin.CAST_SPELL_MANA_VALUE,characteristic='mana_value',schema_version=8,**changes)


def cast_item(**changes):
    context={'event':'spell.cast','schema_version':7,'logical_object_id':'original-spell@1','stack':'S1','player':'B','mana_value':4.0,'mana_spent_total':3}
    context.update(changes)
    return SimpleNamespace(controller='A',context=context)


class CastSpellManaValueCompilerTests(unittest.TestCase):
    def test_new_scalar_schema_round_trips_and_legacy_schema_is_unchanged(self):
        spec=cast_amount();self.assertEqual(spec,ScalarEffectAmountSpec.from_dict(spec.to_dict()))
        old=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,characteristic='power')
        self.assertEqual({'kind':'scalar_effect_amount','schema_version':1,'origin':'source_characteristic','characteristic':'power','history_fact':None,'coefficient':1,'binding_id':None},old.to_dict())
        for change in ({'schema_version':True},{'schema_version':1},{'characteristic':'power'},{'history_fact':'spells_cast'},{'extra':1}):
            with self.assertRaises(ValueError):ScalarEffectAmountSpec.from_dict({**spec.to_dict(),**change})

    def test_reader_uses_committed_spell_value_not_actual_spend_or_a_current_object(self):
        host=SimpleNamespace(state=SimpleNamespace(cards={}))
        self.assertEqual(4,resolve_scalar_effect_amount(host,cast_amount().to_dict(),cast_item()))
        self.assertEqual(8,resolve_scalar_effect_amount(host,cast_amount(coefficient=2).to_dict(),cast_item()))
        self.assertEqual(0,resolve_scalar_effect_amount(host,cast_amount().to_dict(),cast_item(mana_value=0.0)))
        item=cast_item();raw=dict(item.context);raw.pop('event');item.context={'event':'spell.cast','event_context':raw}
        self.assertEqual(4,resolve_scalar_effect_amount(host,cast_amount().to_dict(),item))

    def test_missing_bad_and_non_cast_facts_are_unavailable_without_creating_a_cache(self):
        for changes in ({'event':'spell.cast_or_copy'},{'schema_version':2},{'schema_version':True},
                {'logical_object_id':None},{'player':''},{'stack':None},{'mana_value':None},{'mana_value':True},
                {'mana_value':-1},{'mana_value':1.5},{'mana_value':math.nan},{'mana_value':math.inf}):
            item=cast_item(**changes);before=deepcopy(item.context)
            with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(None,cast_amount(binding_id='value').to_dict(),item)
            self.assertEqual(before,item.context)

    def test_declared_cast_scalar_cache_is_bound_to_the_cast_identity_and_value(self):
        item=cast_item();spec=cast_amount(binding_id='declared-x').to_dict()
        self.assertEqual(4,resolve_scalar_effect_amount(None,spec,item));self.assertEqual(4,resolve_scalar_effect_amount(None,spec,item))
        item.context['mana_value']=1
        with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(None,spec,item)
        item.context['mana_value']=4;item.context['logical_object_id']='returned-card@2'
        with self.assertRaises(PublicQueryAmountError):resolve_scalar_effect_amount(None,spec,item)

    def test_using_actual_spend_or_current_graveyard_value_mutants_are_killed(self):
        import quorune.cast_event_amounts as owner
        item=cast_item()
        def witness():self.assertEqual(4,resolve_scalar_effect_amount(None,cast_amount().to_dict(),item))
        witness();original=owner.committed_cast_mana_value_facts
        for wrong in (3,1):
            with patch.object(owner,'committed_cast_mana_value_facts',return_value={**original(item),'mana_value':wrong}),self.assertRaises(AssertionError):witness()

    def test_cast_values_compose_with_independent_result_and_player_owners(self):
        registry=load_default_capability_registry()
        bodies=("draw cards equal to that spell's mana value.","that player loses life equal to that spell's mana value.",
            "this enchantment deals damage equal to that spell's mana value to that player.",
            "this enchantment deals damage equal to that spell's mana value to target opponent.")
        for body in bodies:
            record=replace(query_record('Whenever an opponent casts a spell, '+body),type_line='Enchantment')
            ir=compile_oracle_card(record,capability_registry=registry)
            self.assertEqual('exact',ir.status,(body,ir.material_residuals));self.assertIn(CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
        for text in ("Whenever you cast or copy an instant or sorcery spell, draw cards equal to that spell's mana value.",
                "Counter target spell. Draw cards equal to that spell's mana value.",
                "Whenever you cast a spell, draw cards equal to that spell's power."):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_verified_cast_value_withdrawal_preserves_existing_source_scalar(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        text="Whenever you cast a spell, draw cards equal to that spell's mana value."
        self.assertEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['committed cast value withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record('When this creature dies, draw cards equal to its power.'),capability_registry=registry).status)


class CastSpellManaValueNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.EventPlayerFixedDamageNativeTests.session
    add=native.EventPlayerFixedDamageNativeTests.add
    prepare=native.EventPlayerFixedDamageNativeTests.prepare
    cast=native.EventPlayerFixedDamageNativeTests.cast
    cast_for=native.EventPlayerFixedDamageNativeTests.cast_for
    action=native.EventPlayerFixedDamageNativeTests.action
    priority=native.EventPlayerFixedDamageNativeTests.priority
    checkpoint=native.EventPlayerFixedDamageNativeTests.checkpoint
    resolve=native.EventPlayerFixedDamageNativeTests.resolve
    replay=native.EventPlayerFixedDamageNativeTests.replay
    assert_closed=native.EventPlayerFixedDamageNativeTests.assert_closed
    until=native.EventPlayerFixedDamageNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'cast-spell-values.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/cast-spell-mana-value-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Cast spell values',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def cast_x(self,session,seat,card,x,targets,*,reserve=None):
        self.priority(session,seat);offered=self.action(session,seat,session.state.cards[card.object_id])
        command={'action_id':offered['id'],'x':x,'targets':targets,'pay':'auto','reserve':reserve or {}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_runtime_cast_value_dependency_is_inferred_without_capability_or_marker(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603636001);source=self.add(session.engine,'The Frightful Four');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,coverage=tuple(c for c in program.coverage if c!='cast-spell-mana-value-amount'),
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def test_actual_frightful_counts_chosen_x_after_reduction_and_countering(self):
        session=self.session(603636010);source=self.add(session.engine,'The Frightful Four',zone='hand');spell=self.add(session.engine,'Blaze',seat='B',zone='hand')
        self.add(session.engine,'Ruby Medallion',seat='B');self.add(session.engine,'Vedalken Orrery',seat='B');counter=self.add(session.engine,'Counterspell',zone='hand')
        private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        self.assert_closed(session,source);self.assert_closed(session,spell)
        self.prepare(session,mana={'C':3,'B':1,'U':2});session.state.players['B'].mana_pool.update({'C':2,'R':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'U':2});self.assertIsNone(self.resolve(session));life={s:session.state.players[s].life for s in 'ABCD'}
        self.cast_x(session,'B',spell,3,['C'])
        original=next(i for i in session.state.stack if i.card_object_id==spell.object_id);self.assertEqual(3,original.context['mana_spent_total'])
        triggered=next(i for i in session.state.stack if i.source_object_id==source.object_id);self.assertEqual(4.0,triggered.context['mana_value'])
        self.cast(session,'A',counter,[original.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone);self.assertEqual(1.0,session.engine._effective_card_data(session.state.cards[spell.object_id])['mana_value'])
        self.assertEqual(life['B']-4,session.state.players['B'].life)
        for seat in 'ACD':self.assertEqual(life[seat],session.state.players[seat].life)
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_zero_value_cast_is_known_zero_and_consumes_first_noncreature(self):
        session=self.session(603636020);source=self.add(session.engine,'The Frightful Four',zone='hand');zero=self.add(session.engine,'Darksteel Relic',seat='B',zone='hand')
        later=self.add(session.engine,'Short Sword',seat='B',zone='hand');self.add(session.engine,'Vedalken Orrery',seat='B')
        self.assert_closed(session,source);self.assert_closed(session,zero);self.prepare(session,mana={'C':3,'B':1});session.state.players['B'].mana_pool['C']=1;self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));life=session.state.players['B'].life
        self.cast_for(session,'B',zero,reserve={'C':1});self.assertTrue(any(i.source_object_id==source.object_id for i in session.state.stack))
        self.assertIsNone(self.resolve(session));self.assertEqual(life,session.state.players['B'].life)
        self.cast(session,'B',later);self.assertFalse(any(i.source_object_id==source.object_id for i in session.state.stack));self.assertIsNone(self.resolve(session))
        self.assertEqual(life,session.state.players['B'].life);self.replay(session,load=True)

    def test_actual_cyclops_declared_amount_uses_countered_cast_value_for_source_buff(self):
        session=self.session(603636030);source=self.add(session.engine,'Erratic Cyclops',zone='hand');spell=self.add(session.engine,'Blaze',zone='hand')
        self.add(session.engine,'Ruby Medallion');counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'R':2});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':2,'R':1});self.assertIsNone(self.resolve(session));self.assertEqual(0,session.engine._numeric_stat(source.object_id,'power'))
        self.cast_x(session,'A',spell,3,['C']);original=next(i for i in session.state.stack if i.card_object_id==spell.object_id)
        self.cast(session,'B',counter,[original.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'));self.assertEqual('graveyard',session.state.cards[spell.object_id].zone);self.replay(session,load=True)

    def test_actual_keeper_uses_referenced_graveyard_cast_and_preserves_target_choice(self):
        session=self.session(603636040);source=self.add(session.engine,'Keeper of Secrets',zone='hand');spell=self.add(session.engine,'Squee, the Immortal',zone='graveyard')
        private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        self.assert_closed(session,source);self.assert_closed(session,spell);self.prepare(session,mana={'C':6,'R':3});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'R':2});self.assertIsNone(self.resolve(session));life=session.state.players['B'].life
        self.cast(session,'A',spell);self.assertIsNotNone(session.state.pending_decision);self.assertNotEqual('priority',session.state.pending_decision.kind)
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':'choose','targets':['A']}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','targets':['B']});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(life-3,session.state.players['B'].life)
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_background_grant_reads_cast_value_and_keeps_commander_damage_source(self):
        session=self.session(603636050);background=self.add(session.engine,'Passionate Archaeologist',zone='hand')
        commander=next(session.state.cards[oid] for oid in session.state.players['A'].zones['command'])
        aura=self.add(session.engine,'Lifelink',zone='hand');spell=self.add(session.engine,'Squee, the Immortal',zone='exile')
        destroy=self.add(session.engine,'Disenchant',seat='B',zone='hand');private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        for card in (background,aura,spell):self.assert_closed(session,card)
        self.prepare(session,mana={'C':2,'R':3,'W':2});session.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(session)
        self.cast_for(session,'A',commander,reserve={'C':2,'R':3,'W':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',background,reserve={'C':1,'R':2,'W':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',aura,[commander.ref],reserve={'C':1,'R':2});self.assertIsNone(self.resolve(session))
        self.assertIn('lifelink',session.engine._combat_keywords(session.state.cards[commander.object_id]))
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        self.cast(session,'A',spell);self.assertIsNotNone(session.state.pending_decision);self.assertNotEqual('priority',session.state.pending_decision.kind)
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'choose','targets':['B']});self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',destroy,[background.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[background.object_id].zone)
        self.assertEqual(life['B']-3,session.state.players['B'].life);self.assertEqual(life['A']+3,session.state.players['A'].life)
        for seat in 'CD':self.assertEqual(life[seat],session.state.players[seat].life)
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)
