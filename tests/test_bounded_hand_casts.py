"""Pinned CR 601.3e/608.2g: one current hand spell, with its actual costs."""
from dataclasses import FrozenInstanceError
import json, unittest
from common import ROOT
from quorune.bounded_hand_cast_model import (
    BoundedHandCastSpec, ResolveBoundedHandCastIntent, validate_bounded_hand_cast_identity,
    bounded_hand_cast_payload_dependencies, bounded_hand_cast_node_capabilities,
    BOUNDED_HAND_CAST_CAPABILITY as CAP, BOUNDED_HAND_CAST_FIELD as FIELD,
)
from quorune.compiler.bounded_hand_cast_templates import bounded_hand_cast_template
from quorune.mana_value_limit import ManaValueLimitSpec
from quorune.replacement.immutable import FrozenMap

FIXTURE = ROOT / 'tests/fixtures/bounded-hand-cast-cards.json'
CANDIDATES = ("Rishkar's Expertise", "Sram's Expertise", "Yahenni's Expertise", "Kari Zev's Expertise", "Baral's Expertise")

class BoundedHandCastModelTests(unittest.TestCase):
    def test_fixed_optional_controller_hand_spell_has_lossless_closed_codec(self):
        for maximum in (0, 2, 3, 4, 5, 13):
            spec = BoundedHandCastSpec(ManaValueLimitSpec(maximum));effect = spec.effect()
            self.assertEqual(effect, BoundedHandCastSpec.from_effect(json.loads(json.dumps(effect))).effect())
            self.assertTrue(spec.mana_value_limit.permits(maximum));self.assertFalse(spec.mana_value_limit.permits(maximum+1))
            self.assertEqual((CAP,), bounded_hand_cast_payload_dependencies(effect))
            self.assertEqual((CAP,), bounded_hand_cast_node_capabilities(effects=(effect,), target_schema=None))
            with self.assertRaises(FrozenInstanceError):spec.schema_version = 2
    def test_only_complete_fixed_spell_wording_lowers(self):
        text='You may cast a spell with mana value 3 or less from your hand without paying its mana cost.'
        self.assertEqual(3, bounded_hand_cast_template(text).mana_value_limit.maximum)
        for bad in (text.replace('3', 'X'), text.replace('3', '03'), text.replace('3', '-1'),
                text.replace('your hand', 'your graveyard'),text.replace('a spell', 'a creature spell'),
                text.replace('a spell', 'a card'),text.replace('You may', 'Target player may'),
                text+' Draw a card.',text.replace('a spell', 'two spells')):
            self.assertIsNone(bounded_hand_cast_template(bad))
    def test_unowned_scope_price_or_literal_fields_fail_closed(self):
        effect=BoundedHandCastSpec(ManaValueLimitSpec(3)).effect()
        for change in ({'player':'B'}, {'op':'cast'}, {'from':'exile'}, {'free':False},
                {FIELD:{'schema_version':True,'mana_value_limit':{'schema_version':1,'maximum':3}}},
                {FIELD:{'schema_version':1,'mana_value_limit':{'schema_version':1,'maximum':True}}}):
            with self.assertRaises((ValueError,TypeError)):BoundedHandCastSpec.from_effect({**effect,**change})
        self.assertEqual((),bounded_hand_cast_node_capabilities(effects=(effect,),target_schema={'kind':'player'}))
    def test_pending_cast_identity_preserves_permission_and_rejects_unknown_or_internal_response_data(self):
        spec=BoundedHandCastSpec(ManaValueLimitSpec(3))
        for response in (None,FrozenMap({'card':'private-card','cost_option':'issued-price','targets':['public-target']})):
            intent=ResolveBoundedHandCastIntent('A','STACK',spec,'a'*64,response)
            raw=json.loads(json.dumps(intent.identity()));self.assertEqual(raw,validate_bounded_hand_cast_identity(raw))
            for change in ({'actor':''},{'stack_ref':''},{'options_fingerprint':'Z'*64},
                    {'response':{'_mana_payment_id':'forged'}},{'response':False},{'extra':1}):
                with self.assertRaises((ValueError,TypeError)):validate_bounded_hand_cast_identity({**raw,**change})


from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from quorune.errors import GameRuleError
from quorune.discover_model import discover_options_fingerprint
import quorune.rules.bounded_hand_cast as cast_owner

class BoundedHandCastOfferModelTests(unittest.TestCase):
    def world(self):
        card=SimpleNamespace(object_id='object',ref='private-card',logical_object_id='object@1',owner='A',zone='hand',printed_name='Split choice')
        item=SimpleNamespace(ref='STACK',controller='A',context={'currently_resolving':True})
        host=SimpleNamespace(state=SimpleNamespace(cards={'object':card},players={'A':SimpleNamespace(zones={'hand':['object']})},stack=[item]),
            active_seats=('A','B'),card_record=lambda c:object(),_effective_card_data=lambda view:{'mana_value':{'cheap':2,'dear':6,'fuse':8}[view.face]})
        def offer(host,actor,c,**kwargs):
            self.assertEqual('free',kwargs['resolving_payment']);self.assertEqual('hand',kwargs['resolving_origin'])
            return SimpleNamespace(offer=SimpleNamespace(to_dict=lambda **ignored:{'face':kwargs['face_name'],'label':'Canonical offer',
                'cost_options':[{'id':'base','label':'Pay additional price','mana':{'G':7},'additional_costs':[]}]}))
        self.enterContext(patch.object(cast_owner,'offered_cast_methods',return_value=((None,'cheap'),(None,'dear'),('fuse',None),('prototype',None))))
        self.enterContext(patch.object(cast_owner,'build_cast_offer',side_effect=offer))
        self.enterContext(patch.object(cast_owner,'characteristic_cast_view',side_effect=lambda h,c,r,m:(SimpleNamespace(face='fuse' if m=='fuse' else None),r)))
        self.enterContext(patch.object(cast_owner,'prospective_cast_card',side_effect=lambda h,v,f:SimpleNamespace(face=f or v.face)))
        return host,item,card
    def test_proposed_half_and_fused_values_are_separate_from_hand_card_or_total_price(self):
        host,item,card=self.world()
        spec=BoundedHandCastSpec(ManaValueLimitSpec(2));effect={**spec.effect(),'player':'A'}
        options=cast_owner.bounded_hand_cast_options(host,item=item,actor='A',effect=effect)
        self.assertEqual(['cheap'],[x['face'] for x in options]);self.assertEqual({'G':7},options[0]['mana'])
        pairs=cast_owner._option_pairs(host,'A',BoundedHandCastSpec(ManaValueLimitSpec(8)))
        self.assertEqual(['cheap','dear',None],[p[0]['face'] for p in pairs]);self.assertEqual([None,None,'fuse'],[p[0]['cast_method'] for p in pairs])
    def test_logical_incarnation_change_invalidates_the_previously_issued_offer_before_any_cast(self):
        host,item,card=self.world();spec=BoundedHandCastSpec(ManaValueLimitSpec(2));effect={**spec.effect(),'player':'A'}
        options=cast_owner.bounded_hand_cast_options(host,item=item,actor='A',effect=effect)
        intent=ResolveBoundedHandCastIntent('A','STACK',spec,discover_options_fingerprint(options),None)
        card.logical_object_id='object@2'
        with patch.object(cast_owner,'commit_resolving_cast') as commit:
            with self.assertRaises(GameRuleError):cast_owner.execute_bounded_hand_cast(host,intent)
            commit.assert_not_called()
    def test_original_controller_resolution_and_own_hand_are_required(self):
        host,item,card=self.world();effect={**BoundedHandCastSpec(ManaValueLimitSpec(2)).effect(),'player':'A'}
        with self.assertRaises(GameRuleError):cast_owner.bounded_hand_cast_options(host,item=item,actor='B',effect=effect)
        item.context.clear()
        with self.assertRaises(GameRuleError):cast_owner.bounded_hand_cast_options(host,item=item,actor='A',effect=effect)
        item.context['currently_resolving']=True;card.owner='B'
        with self.assertRaises(GameRuleError):cast_owner.bounded_hand_cast_options(host,item=item,actor='A',effect=effect)


from pathlib import Path
import tempfile
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry, CapabilityRegistry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as compiler_controls
import test_public_condition_self_replacements as controls

class BoundedHandCastCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()
    def test_complete_fixed_free_hand_spell_lowers_and_unknown_siblings_do_not(self):
        text='You may cast a spell with mana value 3 or less from your hand without paying its mana cost.'
        for body in (text,'Create three 1/1 colorless Servo artifact creature tokens. '+text):
            ir=compile_oracle_card(compiler_controls.record(body),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals);self.assertIn(CAP,ir.faces[0].nodes[0].capability_dependencies)
        for body in (text.replace('3','X'),text+' Exile the top ten cards of your library.'):
            self.assertNotEqual('exact',compile_oracle_card(compiler_controls.record(body),capability_registry=self.registry).status)
    def test_fine_permission_and_exact_lowering_withdrawals_fail_closed(self):
        text='You may cast a spell with mana value 3 or less from your hand without paying its mana cost.'
        with patch('quorune.compiler.bounded_hand_cast_templates.bounded_hand_cast_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(compiler_controls.record(text),capability_registry=self.registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(x for x in raw['capabilities'] if x['id']==CAP).update(status='blocked',blockers=['Hand cast permission withdrawn'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual('exact',compile_oracle_card(compiler_controls.record(text),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(compiler_controls.record('Draw two cards.'),capability_registry=registry).status)

class LegendaryWizardHandCastContractTests(unittest.TestCase):
    def test_legendary_wizard_hand_cast_rejects_forged_limits(self):
        from copy import deepcopy

        from quorune.bounded_hand_cast_model import (
            LEGENDARY_WIZARD_HAND_CAST_CAPABILITY, legendary_wizard_hand_cast_effect,
        )
        from quorune.characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.query_effect_amount_model import PublicQueryAmountSpec

        effect = legendary_wizard_hand_cast_effect()
        self.assertIn(LEGENDARY_WIZARD_HAND_CAST_CAPABILITY, bounded_hand_cast_payload_dependencies(effect))
        all_wizards = CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=("battlefield",), subtypes_any=("wizard",)),
        )
        legendary_wizards = CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=("battlefield",), subtypes_any=("wizard",), supertypes_all=("legendary",)),
        )
        for maximum in (
            4,
            PublicQueryAmountSpec(all_wizards, coefficient=2).to_dict(),
            PublicQueryAmountSpec(legendary_wizards, coefficient=3).to_dict(),
        ):
            with self.subTest(maximum=maximum):
                forged = deepcopy(effect)
                forged[FIELD]["mana_value_limit"]["maximum"] = maximum
                with self.assertRaisesRegex(ValueError, "exact live public mana-value limit"):
                    bounded_hand_cast_payload_dependencies(forged)

    def test_legendary_wizard_hand_cast_requires_own_combat_and_public_count_authority(self):
        from dataclasses import replace

        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        supported = (
            "At the beginning of combat on your turn, you may cast an instant or sorcery spell with mana "
            "value X or less from your hand without paying its mana cost, where X is twice the number of "
            "legendary Wizards you control."
        )
        creature = replace(compiler_controls.record(supported), type_line="Legendary Creature — Wizard",
                           power="1", toughness="1")
        registry = load_default_capability_registry()
        admitted = compile_oracle_card(creature, capability_registry=registry)
        self.assertEqual("exact", admitted.status, admitted.material_residuals)
        for unexpected in ("At the beginning of combat,", "At the beginning of the end step on your turn,"):
            wrong_time = replace(creature, oracle_text=supported.replace(
                "At the beginning of combat on your turn,", unexpected,
            ))
            self.assertNotEqual("exact", compile_oracle_card(wrong_time, capability_registry=registry).status)

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        count = next(row for row in raw["capabilities"] if row["id"] == "quantity_expression.public_query_effect_amount")
        count.update(status="blocked", blockers=["public count authority withdrawn"])
        registry = CapabilityRegistry(raw)
        evidence = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(creature, capability_registry=registry).status)
        fixed = compiler_controls.record(
            "You may cast a spell with mana value 3 or less from your hand without paying its mana cost.",
        )
        self.assertEqual("exact", compile_oracle_card(fixed, capability_registry=registry).status)


class BoundedHandCastRegistrationTests(unittest.TestCase):
    def test_all_five_originals_close_normally_and_variable_neighbor_keeps_its_complete_source(self):
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'original-expertises.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,'Electrodominance'):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False)
                        register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=registry,
                            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                        binding=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                        self.assertEqual(name!='Electrodominance',bool(binding['strict_capability_ready'] and binding['trust_basis']=='capability_closed'),binding['blockers'])

class BoundedHandCastRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicConditionSelfReplacementTests.session
    start=controls.PublicConditionSelfReplacementTests.start
    add=controls.PublicConditionSelfReplacementTests.add
    prepare=controls.PublicConditionSelfReplacementTests.prepare
    checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
    resolve=controls.PublicConditionSelfReplacementTests.resolve
    replay=controls.PublicConditionSelfReplacementTests.replay
    binding=controls.PublicConditionSelfReplacementTests.binding
    priority=controls.PublicConditionSelfReplacementTests.priority
    cast=controls.PublicConditionSelfReplacementTests.cast
    reject=controls.PublicConditionSelfReplacementTests.reject
    advance=controls.PublicConditionSelfReplacementTests.advance
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'bounded-hand-casts.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Bounded free hand spells',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def test_actual_sram_creates_three_servos_then_casts_offered_blue_spell_for_no_mana_privately(self):
        s=self.start(928010);source=self.add(s.engine,"Sram's Expertise",zone='hand')
        child=self.add(s.engine,'Wind Drake',zone='hand');above=self.add(s.engine,'Hill Giant',zone='hand')
        land=self.add(s.engine,'Plains',zone='hand');foreign=self.add(s.engine,'Lightning Bolt',seat='C',zone='hand')
        self.assertTrue(self.binding(s,source)['strict_capability_ready'],self.binding(s,source)['blockers'])
        mana={k:0 for k in 'CWUBRG'};mana.update(C=2,W=2)
        self.prepare(s,mana=mana);self.checkpoint(s);self.cast(s,source);decision=self.resolve(s)
        self.assertEqual('semantic.choice',decision.kind)
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='A']
        self.assertEqual(3,len(tokens))
        for token in tokens:
            self.assertEqual(1,s.engine._numeric_stat(token.object_id,'power'));self.assertEqual(1,s.engine._numeric_stat(token.object_id,'toughness'))
            data=s.engine._effective_card_data(token);self.assertIn('Artifact Creature',data['type_line']);self.assertEqual([],list(data['colors']))
        self.assertEqual('stack',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];options=ctx['cast_options']
        option=next(x for x in options if x['card']==child.ref)
        self.assertFalse(any(x['card'] in (above.ref,land.ref,foreign.ref) for x in options))
        for seat in 'BCD':self.assertNotIn(child.ref,str(s.packet('pilot:'+seat,full=True)))
        self.assertNotIn(foreign.ref,str(s.packet('pilot:A',full=True)))
        command={'action_id':'choose','choice':'cast','cast':{'card':child.ref,'cost_option':option['id']}}
        self.reject(s,command,'D');self.reject(s,{'action_id':'choose','choice':'cast','cast':{'card':above.ref}},'A')
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('stack',s.state.cards[child.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[child.object_id].zone);self.replay(s,load=True)

    def ready_with(self,s,mana=None):
        self.prepare(s,mana=mana or {k:30 for k in 'CWUBRG'});self.checkpoint(s)
    def hand_offer(self,s):
        d=self.resolve(s);self.assertIsNotNone(d);self.assertEqual('semantic.choice',d.kind)
        return s.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
    def choose_hand(self,s,option,*,targets=(),extra=None):
        card=next(c for c in s.state.cards.values() if c.ref==option['card'])
        binding=self.binding(s,card);self.assertTrue(binding['strict_capability_ready'],binding['blockers']);self.assertEqual('capability_closed',binding['trust_basis'])
        payload={'card':option['card'],'cost_option':option['id'],'targets':list(targets)}
        if extra:payload.update(extra)
        command={'action_id':'choose','choice':'cast','cast':payload};self.reject(s,command,'D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
    def test_actual_rishkar_draws_before_current_hand_cast_and_zero_draw_still_allows_decline(self):
        for count in (0,2):
            with self.subTest(count=count):
                s=self.start(928020+count);source=self.add(s.engine,"Rishkar's Expertise",zone='hand')
                old=self.add(s.engine,'Llanowar Elves',zone='hand')
                if count:self.add(s.engine,'Grizzly Bears')
                land=self.add(s.engine,'Plains',zone='library',ref='expertise-drawn-land')
                drawn=self.add(s.engine,'Wind Drake',zone='library',ref='expertise-drawn-spell')
                self.ready_with(s);before=len(s.state.players['A'].draw_history);self.cast(s,source);options=self.hand_offer(s)
                self.assertEqual(before+count,len(s.state.players['A'].draw_history));self.assertTrue(any(o['card']==old.ref for o in options))
                self.assertEqual('stack',s.state.cards[source.object_id].zone)
                if count:
                    option=next(o for o in options if o['card']==drawn.ref);self.assertEqual('hand',s.state.cards[drawn.object_id].zone)
                    s=self.replay(s,load=True);self.choose_hand(s,option)
                    self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('stack',s.state.cards[drawn.object_id].zone)
                    self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[drawn.object_id].zone)
                else:
                    self.assertEqual('library',s.state.cards[drawn.object_id].zone)
                    result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[old.object_id].zone)
                self.replay(s,load=True)
    def test_actual_yahenni_defers_state_based_death_through_paid_sacrifice_or_cast_trigger(self):
        for child_name in ('Village Rites','Giant Growth'):
            with self.subTest(child=child_name):
                s=self.start(928030+(child_name=='Giant Growth'));source=self.add(s.engine,"Yahenni's Expertise",zone='hand')
                child=self.add(s.engine,child_name,zone='hand');body=self.add(s.engine,'Grizzly Bears',ref='yahenni-own-cost-body')
                foreign=self.add(s.engine,'Grizzly Bears',seat='B',ref='yahenni-foreign-body')
                if child_name=='Giant Growth':pyromancer=self.add(s.engine,'Young Pyromancer')
                else:pyromancer=None
                self.ready_with(s);self.cast(s,source);options=self.hand_offer(s)
                self.assertEqual('battlefield',s.state.cards[body.object_id].zone);self.assertLessEqual(s.engine._numeric_stat(body.object_id,'toughness'),0)
                self.assertEqual('battlefield',s.state.cards[foreign.object_id].zone)
                option=next(o for o in options if o['card']==child.ref)
                s=self.replay(s,load=True)
                if child_name=='Village Rites':
                    self.assertIn(body.ref,option['choice_schema']['sacrifice_cards']['legal_refs'])
                    self.reject(s,{'action_id':'choose','choice':'cast','cast':{'card':child.ref,'cost_option':option['id'],'sacrifice_cards':[foreign.ref]}},'A')
                    self.choose_hand(s,option,extra={'sacrifice_cards':[body.ref]})
                else:self.choose_hand(s,option,targets=[body.ref])
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('stack',s.state.cards[child.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[body.object_id].zone);self.assertEqual('graveyard',s.state.cards[foreign.object_id].zone)
                before=len(s.state.players['A'].draw_history);self.assertIsNone(self.resolve(s))
                if child_name=='Village Rites':self.assertEqual(before+2,len(s.state.players['A'].draw_history))
                else:
                    self.assertEqual('graveyard',s.state.cards[pyromancer.object_id].zone)
                    tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='A']
                    self.assertEqual(1,len(tokens));self.assertEqual(1,s.engine._numeric_stat(tokens[0].object_id,'toughness'))
                self.replay(s,load=True)
    def test_actual_kari_zev_keeps_creature_and_vehicle_control_untap_and_haste_after_decline(self):
        for name in ('Grizzly Bears',"Smuggler's Copter"):
            with self.subTest(target=name):
                s=self.start(928040+(name!="Grizzly Bears"));source=self.add(s.engine,"Kari Zev's Expertise",zone='hand')
                target=self.add(s.engine,name,seat='B',ref='kari-borrowed-target');target.tapped=True;child=self.add(s.engine,'Grizzly Bears',zone='hand',ref='kari-hand-child')
                self.ready_with(s);self.cast(s,source,[target.ref]);options=self.hand_offer(s)
                self.assertTrue(any(o['card']==child.ref for o in options))
                current=s.state.cards[target.object_id];self.assertEqual('A',current.controller);self.assertFalse(current.tapped);self.assertIn('haste',s.engine._combat_keywords(current))
                s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s));self.advance(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
                current=s.state.cards[target.object_id];self.assertEqual('B',current.controller);self.assertNotIn('haste',s.engine._combat_keywords(current));self.replay(s,load=True)
    def test_actual_baral_zero_targets_and_partial_target_loss_can_cast_current_or_returned_hand_spell(self):
        for zero in (True,False):
            with self.subTest(zero=zero):
                s=self.start(928050+zero);source=self.add(s.engine,"Baral's Expertise",zone='hand')
                child=self.add(s.engine,'Hill Giant' if zero else 'Wind Drake',zone='hand' if zero else 'battlefield',ref='baral-free-child')
                artifact=self.add(s.engine,'Sol Ring');foreign=self.add(s.engine,'Grizzly Bears',seat='B');bounce=self.add(s.engine,'Unsummon',zone='hand')
                self.ready_with(s);self.cast(s,source,[] if zero else [child.ref,artifact.ref,foreign.ref])
                if not zero:self.cast(s,bounce,[foreign.ref])
                options=self.hand_offer(s);self.assertEqual('hand',s.state.cards[child.object_id].zone)
                self.assertEqual('battlefield' if zero else 'hand',s.state.cards[artifact.object_id].zone)
                self.assertEqual('battlefield' if zero else 'hand',s.state.cards[foreign.object_id].zone)
                self.assertFalse(any(o['card']==foreign.ref for o in options));option=next(o for o in options if o['card']==child.ref)
                s=self.replay(s,load=True);self.choose_hand(s,option);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual('stack',s.state.cards[child.object_id].zone);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[child.object_id].zone);self.replay(s,load=True)

    def test_actual_no_eligible_spell_and_rule_of_law_finish_without_a_future_cast_permission(self):
        for prohibited in (False,True):
            with self.subTest(prohibited=prohibited):
                s=self.start(928060+prohibited);source=self.add(s.engine,"Sram's Expertise",zone='hand')
                above=self.add(s.engine,'Hill Giant',zone='hand');land=self.add(s.engine,'Plains',zone='hand')
                if prohibited:
                    self.add(s.engine,'Rule of Law');child=self.add(s.engine,'Wind Drake',zone='hand')
                else:child=above
                mana={k:0 for k in 'CWUBRG'};mana.update(C=2,W=2);self.ready_with(s,mana)
                self.cast(s,source);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('hand',s.state.cards[child.object_id].zone)
                self.assertEqual(3,len([c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='A']))
                self.reject(s,{'action_id':'choose','choice':'cast','cast':{'card':child.ref}},'A')
                self.reject(s,{'action_id':'cast:'+child.ref,'pay':'auto'},'A');self.replay(s,load=True)
    def test_actual_all_primary_targets_illegal_prevents_both_expertise_free_casts(self):
        for name in ("Kari Zev's Expertise","Baral's Expertise"):
            with self.subTest(source=name):
                s=self.start(928070+(name=="Baral's Expertise"));source=self.add(s.engine,name,zone='hand')
                victim=self.add(s.engine,'Grizzly Bears',seat='B',ref='expertise-illegal-target')
                child=self.add(s.engine,'Grizzly Bears',zone='hand',ref='expertise-waiting-child')
                bounce=self.add(s.engine,'Unsummon',zone='hand')
                self.ready_with(s);self.cast(s,source,[victim.ref]);self.cast(s,bounce,[victim.ref])
                self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual('hand',s.state.cards[victim.object_id].zone);self.assertEqual('hand',s.state.cards[child.object_id].zone)
                self.replay(s,load=True)
    def test_actual_mandatory_discard_is_affordable_offered_and_paid_as_two_private_hand_cards(self):
        for count in (1,2):
            with self.subTest(spares=count):
                s=self.start(928080+count);source=self.add(s.engine,"Sram's Expertise",zone='hand')
                child=self.add(s.engine,'Cathartic Reunion',zone='hand')
                spares=[self.add(s.engine,'Plains',zone='hand',ref='reunion-cost-'+str(i)) for i in range(count)]
                foreign=self.add(s.engine,'Plains',seat='C',zone='hand',ref='reunion-foreign-cost')
                mana={k:0 for k in 'CWUBRG'};mana.update(C=2,W=2);self.ready_with(s,mana);self.cast(s,source)
                if count==1:
                    self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[child.object_id].zone)
                else:
                    options=self.hand_offer(s);option=next(o for o in options if o['card']==child.ref)
                    self.reject(s,{'action_id':'choose','choice':'cast','cast':{'card':child.ref,'cost_option':option['id'],'discard_cards':[spares[0].ref,foreign.ref]}},'A')
                    s=self.replay(s,load=True);self.choose_hand(s,option,extra={'discard_cards':[c.ref for c in spares]})
                    self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in spares))
                    self.assertEqual('hand',s.state.cards[foreign.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
                    before=len(s.state.players['A'].draw_history);self.assertIsNone(self.resolve(s));self.assertEqual(before+3,len(s.state.players['A'].draw_history))
                self.replay(s,load=True)
    def test_actual_optional_kicker_is_paid_in_addition_to_the_free_mana_price(self):
        s=self.start(928090);source=self.add(s.engine,"Sram's Expertise",zone='hand');child=self.add(s.engine,'Into the Roil',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',seat='B');mana={k:0 for k in 'CWUBRG'};mana.update(C=3,W=2,U=1)
        self.ready_with(s,mana);self.cast(s,source);options=self.hand_offer(s)
        option=next(o for o in options if o['card']==child.ref and o['kind']=='kicker')
        self.assertEqual(2,sum(option['requirements'].values()));self.assertEqual(2,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);self.choose_hand(s,option,targets=[target.ref]);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        before=len(s.state.players['A'].draw_history);self.assertIsNone(self.resolve(s));self.assertEqual(before+1,len(s.state.players['A'].draw_history))
        self.assertEqual('hand',s.state.cards[target.object_id].zone);self.replay(s,load=True)
    def test_actual_cost_increase_is_required_for_both_advertisement_and_accepted_free_spell(self):
        for spare in (0,1):
            with self.subTest(spare=spare):
                s=self.start(928100+spare);source=self.add(s.engine,"Sram's Expertise",zone='hand')
                child=self.add(s.engine,'Giant Growth',zone='hand');self.add(s.engine,'Thalia, Guardian of Thraben')
                mana={k:0 for k in 'CWUBRG'};mana.update(C=3+spare,W=2);self.ready_with(s,mana);self.cast(s,source)
                if not spare:self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[child.object_id].zone)
                else:
                    option=next(o for o in self.hand_offer(s) if o['card']==child.ref);self.assertEqual(1,sum(option['requirements'].values()))
                    token=next(c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='A')
                    self.choose_hand(s,option,targets=[token.ref]);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
                    self.assertIsNone(self.resolve(s));self.assertEqual(4,s.engine._numeric_stat(token.object_id,'power'))
                self.replay(s,load=True)
    def test_actual_free_x_is_zero_and_a_card_with_no_mana_cost_can_be_cast(self):
        for name in ('Stonecoil Serpent','Ancestral Vision'):
            with self.subTest(child=name):
                s=self.start(928110+(name=='Ancestral Vision'));source=self.add(s.engine,"Sram's Expertise",zone='hand');child=self.add(s.engine,name,zone='hand')
                mana={k:0 for k in 'CWUBRG'};mana.update(C=2,W=2);self.ready_with(s,mana);self.cast(s,source)
                option=next(o for o in self.hand_offer(s) if o['card']==child.ref)
                if name=='Stonecoil Serpent':
                    self.reject(s,{'action_id':'choose','choice':'cast','cast':{'card':child.ref,'cost_option':option['id'],'x':1}},'A')
                    self.choose_hand(s,option,extra={'x':0});stack=next(x for x in s.state.stack if x.card_object_id==child.object_id);self.assertEqual(0,stack.x_value)
                else:self.choose_hand(s,option,targets=['A'])
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));before=len(s.state.players['A'].draw_history);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[child.object_id].zone)
                self.assertEqual(before+(3 if name=='Ancestral Vision' else 0),len(s.state.players['A'].draw_history));self.replay(s,load=True)
    def test_actual_modal_land_face_is_unavailable_and_free_counter_can_target_its_resolving_parent(self):
        s=self.start(928120);source=self.add(s.engine,"Sram's Expertise",zone='hand');child=self.add(s.engine,'Jwari Disruption // Jwari Ruins',zone='hand')
        self.ready_with(s);self.cast(s,source);parent=next(x for x in s.state.stack if x.card_object_id==source.object_id)
        options=[o for o in self.hand_offer(s) if o['card']==child.ref];self.assertTrue(options);self.assertEqual({'Jwari Disruption'},{o['face'] for o in options})
        s=self.replay(s,load=True);self.choose_hand(s,options[0],targets=[parent.ref])
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('stack',s.state.cards[child.object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[child.object_id].zone);self.replay(s,load=True)
    def test_fault_after_discard_price_and_child_stack_creation_restores_the_private_offer_before_retry(self):
        from quorune.record import authoritative_state_hash
        s=self.start(928130);source=self.add(s.engine,"Sram's Expertise",zone='hand');child=self.add(s.engine,'Cathartic Reunion',zone='hand')
        spares=[self.add(s.engine,'Plains',zone='hand',ref='rollback-discard-'+str(i)) for i in range(2)]
        self.ready_with(s);self.cast(s,source);option=next(o for o in self.hand_offer(s) if o['card']==child.ref)
        before=authoritative_state_hash(s.state);original=cast_owner.commit_resolving_cast;partial=[]
        def fault(host,**kwargs):
            original(host,**kwargs);partial.append((s.state.cards[child.object_id].zone,tuple(s.state.cards[c.object_id].zone for c in spares),s.state.cards[source.object_id].zone))
            raise GameRuleError('Injected hand free cast after payment failure')
        command={'action_id':'choose','choice':'cast','cast':{'card':child.ref,'cost_option':option['id'],'discard_cards':[c.ref for c in spares]}}
        with patch.object(cast_owner,'commit_resolving_cast',side_effect=fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok,result.summary)
        self.assertEqual([('stack',('graveyard','graveyard'),'stack')],partial);self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.choose_hand(s,option,extra={'discard_cards':[c.ref for c in spares]});self.assertIsNone(self.resolve(s))
        self.assertEqual(3,len([c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='A']));self.replay(s,load=True)
    def test_fresh_missing_mana_value_limit_mutation_is_killed_by_the_actual_offered_hand(self):
        original=cast_owner._option_pairs
        def mutant(host,actor,spec):return original(host,actor,BoundedHandCastSpec(ManaValueLimitSpec(99)))
        with patch.object(cast_owner,'_option_pairs',side_effect=mutant):
            with self.assertRaises(AssertionError):self.test_actual_sram_creates_three_servos_then_casts_offered_blue_spell_for_no_mana_privately()
    def test_executable_hand_permission_still_requires_its_fine_capability_after_coverage_is_removed(self):
        from dataclasses import replace
        s=self.start(928140);source=self.add(s.engine,"Sram's Expertise",zone='hand')
        p=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        deps=tuple(c for c in p.capability_dependencies if c!=CAP)
        changed=replace(p,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_actual_split_half_is_eligible_below_combined_card_value_and_fuse_needs_combined_value(self):
        for fused in (False,True):
            with self.subTest(fused=fused):
                s=self.start(928150+fused);source=self.add(s.engine,"Sram's Expertise" if fused else "Kari Zev's Expertise",zone='hand')
                child=self.add(s.engine,'Wear // Tear',zone='hand')
                artifact=self.add(s.engine,'Sol Ring',seat='B');enchantment=self.add(s.engine,'Impact Tremors',seat='B')
                victim=self.add(s.engine,'Grizzly Bears',seat='B')
                self.ready_with(s);self.cast(s,source,[] if fused else [victim.ref])
                options=[o for o in self.hand_offer(s) if o['card']==child.ref]
                self.assertEqual({'Wear','Tear'},{o['face'] for o in options if o['cast_method'] is None})
                self.assertEqual(fused,any(o['cast_method']=='fuse' for o in options))
                option=next(o for o in options if o['cast_method']=='fuse') if fused else next(o for o in options if o['face']=='Wear' and o['cast_method'] is None)
                s=self.replay(s,load=True);self.choose_hand(s,option,targets=[artifact.ref,enchantment.ref] if fused else [artifact.ref])
                self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[artifact.object_id].zone)
                self.assertEqual('graveyard' if fused else 'battlefield',s.state.cards[enchantment.object_id].zone);self.replay(s,load=True)
    def test_actual_adventure_uses_the_proposed_spell_value_and_keeps_its_normal_exile_lifecycle(self):
        s=self.start(928160);source=self.add(s.engine,"Kari Zev's Expertise",zone='hand')
        child=self.add(s.engine,'Brazen Borrower // Petty Theft',zone='hand')
        victim=self.add(s.engine,'Grizzly Bears',seat='B');other=self.add(s.engine,'Sol Ring',seat='B')
        self.ready_with(s);self.cast(s,source,[victim.ref]);options=[o for o in self.hand_offer(s) if o['card']==child.ref]
        self.assertEqual({'Petty Theft'},{o['face'] for o in options});s=self.replay(s,load=True)
        self.choose_hand(s,options[0],targets=[other.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[other.object_id].zone);self.assertEqual('exile',s.state.cards[child.object_id].zone)
        self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(o.get('card')==child.ref and o.get('face')=='Brazen Borrower' for o in actions));self.replay(s,load=True)
    def test_actual_overload_is_not_a_second_price_for_a_free_cast(self):
        s=self.start(928170);source=self.add(s.engine,"Sram's Expertise",zone='hand');child=self.add(s.engine,'Cyclonic Rift',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',seat='B');other=self.add(s.engine,'Wind Drake',seat='B')
        self.ready_with(s);self.cast(s,source);options=[o for o in self.hand_offer(s) if o['card']==child.ref]
        self.assertTrue(options);self.assertFalse(any(o['kind']=='overload' for o in options))
        self.reject(s,{'action_id':'choose','choice':'cast','cast':{'card':child.ref,'cost_option':'overload','targets':[target.ref]}},'A')
        self.choose_hand(s,options[0],targets=[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[target.object_id].zone);self.assertEqual('battlefield',s.state.cards[other.object_id].zone);self.replay(s,load=True)
    def test_actual_commander_return_cost_replacement_resumes_exact_free_cast_and_parent_after_reload(self):
        self.deck=DeckDefinition('Commander return cost',[DeckEntry('Ashaya, Soul of the Wild',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Ashaya, Soul of the Wild'])
        for redirect in (False,True):
            with self.subTest(redirect=redirect):
                s=self.start(928180+redirect);source=self.add(s.engine,"Sram's Expertise",zone='hand');child=self.add(s.engine,'Deprive',zone='hand')
                commander=next(c for c in s.state.cards.values() if c.owner=='A' and c.is_commander)
                p=s.state.players['A'];p.zones['command'].remove(commander.object_id);p.zones['battlefield'].append(commander.object_id);commander.zone='battlefield'
                register_generated_programs(self.db,s.engine.semantics,(self.db.lookup('Ashaya, Soul of the Wild'),),trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',
                    promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                self.assertTrue(self.binding(s,commander)['strict_capability_ready'],self.binding(s,commander)['blockers'])
                self.ready_with(s);self.cast(s,source);parent=next(x for x in s.state.stack if x.card_object_id==source.object_id)
                option=next(o for o in self.hand_offer(s) if o['card']==child.ref);self.assertIn(commander.ref,option['choice_schema']['return_cards']['legal_refs'])
                self.choose_hand(s,option,targets=[parent.ref],extra={'return_cards':[commander.ref]})
                self.assertEqual('replacement.order',s.state.pending_decision.kind);self.assertEqual('battlefield',s.state.cards[commander.object_id].zone)
                self.assertEqual('hand',s.state.cards[child.object_id].zone);s=self.replay(s,load=True)
                options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                selected=next(o['id'] for o in options if o['id'].startswith('decline:') != redirect)
                command={'action_id':'choose','replacement':selected};self.reject(s,command,'D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual('command' if redirect else 'hand',s.state.cards[commander.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('stack',s.state.cards[child.object_id].zone)
                self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[child.object_id].zone)
                self.assertEqual(3,len([c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='A']));self.replay(s,load=True)
