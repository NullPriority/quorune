from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.spell_additional_cost_templates import fixed_zone_change_additional_cost_template,fixed_alternative_additional_cost_template
from scripts.build_test_database import build_fixture_database
import test_colorless_nonland_targets as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Skaab Goliath','Skaab Ruinator','Soaring Stoneglider')
EXILE='casting.additional_cost.zone_change.fixed_exile'
ALTERNATIVE='casting.additional_cost.fixed_alternative'


class CountedPermanentCostTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ColorlessNonlandTargetTests.session
    add=controls.ColorlessNonlandTargetTests.add
    prepare=controls.ColorlessNonlandTargetTests.prepare
    checkpoint=controls.ColorlessNonlandTargetTests.checkpoint
    resolve=controls.ColorlessNonlandTargetTests.resolve
    replay=controls.ColorlessNonlandTargetTests.replay
    binding=controls.ColorlessNonlandTargetTests.binding
    actions=controls.ColorlessNonlandTargetTests.actions
    cast=controls.ColorlessNonlandTargetTests.cast
    reject=controls.ColorlessNonlandTargetTests.reject
    private_replay=controls.ColorlessNonlandTargetTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'counted-permanent.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/counted-permanent-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Counted permanent prices',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def action(self,s,source):return next((a for a in self.actions(s) if a['id']=='cast:'+source.ref),None)

    def test_counted_leaf_default_and_explicit_boundary_and_alternative_price_are_closed(self):
        text='As an additional cost to cast this spell, exile two creature cards from your graveyard.'
        self.assertIsNone(fixed_zone_change_additional_cost_template(text))
        template=fixed_zone_change_additional_cost_template(text,allow_counted=True);self.assertIsNotNone(template)
        cost=template.cost_schema['additional_costs'][0];self.assertEqual(2,cost['count']);self.assertEqual('exile_cards',cost['choice_field'])
        self.assertEqual(['graveyard'],cost['predicate']['zones']);self.assertEqual(['creature'],cost['predicate']['types_all'])
        alternative=fixed_alternative_additional_cost_template('As an additional cost to cast this spell, exile two cards from your graveyard or pay {1}{W}.')
        self.assertIsNotNone(alternative)
        for text in ('As an additional cost to cast this spell, exile half the cards from your graveyard.',
                'As an additional cost to cast this spell, exile two different-name creature cards from your graveyard.',
                'As an additional cost to cast this spell, exile two creature cards and a land card from your graveyard.'):
            self.assertIsNone(fixed_zone_change_additional_cost_template(text,allow_counted=True),text)

    def test_three_originals_have_normal_complete_admission_and_cost_capabilities(self):
        s=self.session(799000)
        for name in NAMES:
            source=self.add(s.engine,name,zone='hand');b=self.binding(s,source);self.assertTrue(b['strict_capability_ready'],(name,b['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            self.assertTrue(any(EXILE in p.capability_dependencies for p in s.engine.semantics.programs_for_oracle(source.oracle_id)))

    def test_goliath_exact_two_owned_creatures_pay_before_entry_and_invalid_refs_roll_back(self):
        s=self.session(799010);source=self.add(s.engine,'Skaab Goliath',zone='hand')
        cards=[self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='goliath-price-'+str(i)) for i in range(3)]
        foreign=self.add(s.engine,'Generic Bound Body',seat='B',zone='graveyard',ref='goliath-foreign')
        wrong=self.add(s.engine,'Generic Bound Growth',zone='graveyard',ref='goliath-instant')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':5,'U':1});self.checkpoint(s);action=self.action(s,source);self.assertIsNotNone(action)
        command={'action_id':action['id'],'pay':'auto','exile_cards':[c.ref for c in cards[:2]]}
        for refs in ([],[cards[0].ref],[cards[0].ref]*2,[cards[0].ref,foreign.ref],[cards[0].ref,wrong.ref],[c.ref for c in cards]):
            self.reject(s,{**command,'exile_cards':refs})
        self.reject(s,command,'pilot:D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('stack',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        for card in cards[:2]:self.assertEqual('exile',s.state.cards[card.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertIn('trample',s.engine._combat_keywords(s.state.cards[source.object_id]))
        for card in (cards[2],foreign,wrong):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.private_replay(s,private)

    def test_ruinator_casts_from_hand_or_graveyard_and_cannot_exile_its_own_cast_source(self):
        for origin in ('hand','graveyard'):
            s=self.session(799020+(origin=='graveyard'));source=self.add(s.engine,'Skaab Ruinator',zone=origin)
            cards=[self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='ruinator-price-'+str(i)) for i in range(3)]
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':1,'U':2});self.checkpoint(s);action=self.action(s,source);self.assertIsNotNone(action)
            if origin=='graveyard':
                schema=action['cost_options'][0]['choice_schema']['exile_cards'];self.assertNotIn(source.ref,schema['legal_refs'])
                self.reject(s,{'action_id':action['id'],'pay':'auto','exile_cards':[source.ref,cards[0].ref,cards[1].ref]})
            result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto','exile_cards':[c.ref for c in cards]});self.assertTrue(result.ok,result.summary)
            self.assertEqual('stack',s.state.cards[source.object_id].zone)
            self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in cards))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
            self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]));self.private_replay(s,private)

    def test_stoneglider_selects_exactly_one_exile_or_colored_mana_branch(self):
        for mana_branch in (False,True):
            s=self.session(799030+mana_branch);source=self.add(s.engine,'Soaring Stoneglider',zone='hand')
            cards=[self.add(s.engine,name,zone='graveyard',ref='stoneglider-price-'+str(i)) for i,name in enumerate(('Generic Bound Body','Generic Bound Growth'))]
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':3 if mana_branch else 2,'W':2 if mana_branch else 1});self.checkpoint(s)
            action=self.action(s,source);self.assertIsNotNone(action)
            option=next((o for o in action['cost_options'] if ('choice_schema' not in o)==mana_branch),None);self.assertIsNotNone(option)
            command={'action_id':action['id'],'cost_option':option['id'],'pay':'auto',**({} if mana_branch else {'exile_cards':[c.ref for c in cards]})}
            self.reject(s,command,'pilot:B');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            for card in cards:self.assertEqual('graveyard' if mana_branch else 'exile',s.state.cards[card.object_id].zone)
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
            self.assertTrue({'flying','vigilance'}<=s.engine._combat_keywords(s.state.cards[source.object_id]));self.private_replay(s,private)

    def test_verified_exile_withdrawal_and_fresh_counted_price_omission_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==EXILE).update(status='blocked',blockers=['withdrawn owned graveyard exile'],dependency_fail_closed_status='not_run')
        reg=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        reg.mark_evidence_verified(validate_capability_evidence_index(index,registry=reg))
        ir=compile_oracle_card(self.db.lookup('Skaab Goliath'),capability_registry=reg,capability_profile='commander_review');blocks=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+EXILE+':blocked' in b for b in blocks),blocks);self.assertFalse(any('evidence_index:unverified' in b for b in blocks))
        from quorune.compiler import permanent_additional_cost_nodes as owner
        actual=owner.fixed_zone_change_additional_cost_template
        method='test_three_originals_have_normal_complete_admission_and_cost_capabilities'
        with patch.object(owner,'fixed_zone_change_additional_cost_template',side_effect=lambda text,**kwargs:actual(text,allow_counted=False)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_alternative_payload_requires_every_leaf_owner_without_annotations(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(799035);source=self.add(s.engine,'Soaring Stoneglider',zone='hand')
        program=next((p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.cost_schema),None)
        self.assertIsNotNone(program)
        original=bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(original['strict'],original['blockers'])
        for capability in (EXILE,ALTERNATIVE):
            deps=tuple(d for d in program.capability_dependencies if d!=capability)
            mutant=replace(program,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(mutant,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+capability,bound['blockers'])
        from quorune.replacement.immutable import freeze_value,thaw_value
        from quorune.rules.casting_additional_cost_groups import selected_zone_cost_payload_dependencies
        schema=thaw_value(freeze_value(program.cost_schema));before=deepcopy(schema)
        self.assertEqual((ALTERNATIVE,EXILE),selected_zone_cost_payload_dependencies(freeze_value(schema)))
        self.assertEqual(before,schema)
        invalid=deepcopy(schema);invalid['additional_costs'][0]['options'][0]['count']=0
        unknown=deepcopy(schema);unknown['additional_costs'][0]['options'][0]['kind']='unknown_cost'
        for value in (invalid,unknown):
            with self.assertRaises(ValueError):selected_zone_cost_payload_dependencies(value)
        self.assertEqual((),selected_zone_cost_payload_dependencies({'effect':{'kind':'zone_change','count':0}}))

    def test_fresh_cost_payload_dependency_omission_is_killed(self):
        from quorune.rules import casting_additional_cost_groups as owner
        method='test_alternative_payload_requires_every_leaf_owner_without_annotations'
        with patch.object(owner,'selected_zone_cost_payload_dependencies',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_insufficient_count_or_wrong_mana_withholds_only_unpayable_branches(self):
        s=self.session(799040);source=self.add(s.engine,'Skaab Ruinator',zone='graveyard')
        for i in range(2):self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='ruinator-short-'+str(i))
        self.prepare(s,mana={'U':2,'C':1});self.checkpoint(s)
        self.assertIsNone(self.action(s,source),'Two other creatures plus the cast source cannot pay three')
        for mana_branch in (False,True):
            s=self.session(799041+mana_branch);source=self.add(s.engine,'Soaring Stoneglider',zone='hand')
            if not mana_branch:
                for i in range(2):self.add(s.engine,'Generic Bound Growth',zone='graveyard',ref='stoneglider-only-price-'+str(i))
            self.prepare(s,mana={'C':3 if mana_branch else 2,'W':2 if mana_branch else 1});self.checkpoint(s)
            action=self.action(s,source);self.assertIsNotNone(action);self.assertEqual(1,len(action['cost_options']))
            self.assertEqual(mana_branch,'choice_schema' not in action['cost_options'][0])

    def test_countered_original_keeps_all_exiled_payment_cards_and_mana_spent(self):
        s=self.session(799050);source=self.add(s.engine,'Skaab Goliath',zone='hand');counter=self.add(s.engine,'Counterspell',seat='B',zone='hand')
        cards=[self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='goliath-counter-price-'+str(i)) for i in range(2)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['U']=2;self.prepare(s,mana={'C':5,'U':1});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':self.action(s,source)['id'],'pay':'auto','exile_cards':[c.ref for c in cards]})
        self.assertTrue(result.ok,result.summary);self.cast(s,'B',counter,[s.state.stack[-1].ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        for card in cards:self.assertEqual('exile',s.state.cards[card.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_actual_cascade_waives_base_mana_but_retains_three_card_exile_price(self):
        s=self.session(799060);party=self.add(s.engine,'Boarding Party',zone='hand');source=self.add(s.engine,'Skaab Ruinator',zone='library')
        library=s.state.players['A'].zones['library'];library.remove(source.object_id);library.append(source.object_id)
        cards=[self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='ruinator-free-price-'+str(i)) for i in range(3)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':5,'R':1});self.checkpoint(s);self.cast(s,'A',party)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('selection.exile_cast',pending.kind)
        self.reject(s,{'action_id':'cast','exile_cards':[c.ref for c in cards[:2]]})
        result=s.act('pilot:A',{'action_id':'cast','exile_cards':[c.ref for c in cards],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        for card in cards:self.assertEqual('exile',s.state.cards[card.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)
