"""CR109.2/118/205.3/400.7: current subtypes, owner hand and locked prices."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.semantics import SemanticRegistry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.compiler.qualified_return_costs import qualified_return_cost_query
from quorune.compiler.spell_additional_cost_templates import fixed_zone_change_additional_cost_template
from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from scripts.build_test_database import build_fixture_database
from test_declared_effect_amounts import generic_spell
import test_target_recipient_results as recipients

FIXTURE=ROOT/'tests/fixtures/qualified-return-cost-cards.json'
NAMES=('Flooded Shoreline','Wirewood Symbiote')


def cost_record(price):
    return replace(generic_spell(price+': Untap target creature.',mana_cost='{1}{G}'),
        type_line='Creature — Insect',power='1',toughness='1')


class QualifiedReturnCostCompilerTests(unittest.TestCase):
    def test_explicit_price_version_is_closed_and_legacy_choice_wire_keeps_old_interpretation(self):
        from quorune.activation_cost_choice import CostChoice
        from quorune.qualified_return_cost_model import qualified_return_cost_payload_dependencies as dependencies
        query=qualified_return_cost_query('Elf')
        old=CostChoice(kind='return_one_to_owner_hand',predicate=query.to_dict())
        old_raw=old.to_dict();self.assertNotIn('return_schema_version',old_raw)
        self.assertEqual(old_raw,CostChoice.from_dict(old_raw).to_dict());self.assertEqual((),dependencies(old_raw))
        self.assertEqual(1,old.fixed_zone_change_cost().schema_version)
        new=replace(old,return_schema_version=7);raw=new.to_dict()
        self.assertEqual(raw,CostChoice.from_dict(raw).to_dict());self.assertEqual(7,new.fixed_zone_change_cost().schema_version)
        self.assertEqual(('activation.selected_zone_change.fixed',),dependencies(raw))
        self.assertEqual(dependencies(raw),dependencies(new.compact()))
        for change in ({'return_schema_version':None},{'return_schema_version':True},
                {'return_schema_version':6},{'return_schema_version':8},{'another':True},{'unknown':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):CostChoice.from_dict({**raw,**change})
        for change in ({'subtypes_all':['elf','goblin']},{'owner':'$actor'},
                {'types_all':['creature']},{'include_phased_out':True},{'known_to_actor':False}):
            broken={**new.compact(),'q':{**query.to_dict(),**change}}
            with self.subTest(change=change),self.assertRaises(ValueError):dependencies(broken)

    def test_basic_land_and_creature_subtypes_lower_to_existing_public_permanent_query(self):
        registry=load_default_capability_registry()
        for price,subtype,count in (("Return two Islands you control to their owner's hand",'island',2),
                ("Return an Elf you control to its owner's hand",'elf',1),
                ("Return a Time Lord you control to its owner's hand",'time lord',1),
                ("Return an Assembly-Worker you control to its owner's hand",'assembly-worker',1)):
            with self.subTest(price=price):
                ir=compile_oracle_card(cost_record(price),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                choice=ir.faces[0].nodes[0].cost['choices'][0]
                self.assertEqual(count,choice['n']);self.assertEqual('return_one_to_owner_hand',choice['k'])
                self.assertEqual([subtype],choice['q']['subtypes_all']);self.assertEqual([],choice['q']['types_all'])
                self.assertEqual('$actor',choice['q']['controller']);self.assertIsNone(choice['q']['owner'])

    def test_subtype_does_not_add_creature_type_and_legacy_price_wire_round_trips(self):
        query=qualified_return_cost_query('Elf');self.assertEqual((),query.types_all)
        for clause in ("As an additional cost to cast this spell, return an Elf you control to its owner's hand.",
                "As an additional cost to cast this spell, return two Islands you control to their owner's hand.",
                "As an additional cost to cast this spell, return a creature you control to its owner's hand."):
            template=fixed_zone_change_additional_cost_template(clause,allow_counted=True)
            self.assertIsNotNone(template);raw=dict(template.descriptor)
            self.assertEqual(7 if template.predicate.subtypes_all else 1,raw['schema_version'])
            self.assertEqual(raw,FixedZoneChangeAdditionalCost.from_descriptor(raw).to_descriptor())

    def test_unknown_qualities_partial_groups_and_extra_price_riders_stay_residual(self):
        registry=load_default_capability_registry()
        for price in ("Return an unrepresented subtype you control to its owner's hand",
                "Return a tapped Elf you control to its owner's hand",
                "Return two Islands or a Forest you control to their owner's hand",
                "Return any number of Elves you control to their owner's hand",
                "Return two target Islands you control to their owner's hand",
                "Return an Elf you control to its owner's hand, Perform an unrepresented action"):
            with self.subTest(price=price):
                self.assertTrue(compile_oracle_card(cost_record(price),capability_registry=registry,
                    capability_profile='commander_review').material_residuals)
        for quality in ('tapped elf','basic island','elf or goblin','nonexistent subtype'):
            self.assertIsNone(qualified_return_cost_query(quality))


class QualifiedReturnCostNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[c for c in base['cards'] if c['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=folder/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,FIXTURE],folder/'qualified-return.sqlite3')
        cls.db=CardDatabase(folder/'qualified-return.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified returns',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def register(self,name):
        card=self.db.lookup(name,fuzzy=False);semantics=SemanticRegistry()
        register_generated_programs(self.db,semantics,[card],trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'],bound['blockers']);return card,semantics

    def activation_offer(self,s,source):
        self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        return next(a for a in actions if a['id'].startswith('activate:'+source.ref+':'))

    def activate(self,s,source,target,paid):
        offer=self.activation_offer(s,s.state.cards[source.object_id])
        command={'action_id':offer['id'],'targets':[target.ref],'cost_cards':[c.ref for c in paid],'pay':'auto'}
        self.reject(s,'B',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        return offer

    def tap_islands_for_mana(self,s,lands):
        for land in lands:
            self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            mana=next(a for a in actions if a['id']=='activate:'+land.ref+':intrinsic_island')
            self.reject(s,'B',{'action_id':mana['id']})
            result=s.act('pilot:A',{'action_id':mana['id']});self.assertTrue(result.ok,result.summary)

    def test_originals_close_normally_and_generic_land_forest_and_counted_controls_retain(self):
        for name in (*NAMES,'Quirion Ranger','Meloku the Clouded Mirror','Soratami Mirror-Mage'):
            with self.subTest(name=name):self.register(name)
        for name in ('Ovinomancer','Icebreaker Kraken'):
            card=self.db.lookup(name,fuzzy=False)
            self.assertTrue(compile_oracle_card(card,capability_registry=self.registry,
                capability_profile='commander_review').material_residuals)

    def test_registered_price_and_result_owners_cannot_be_dropped_without_coverage(self):
        for name in NAMES:
            card,semantics=self.register(name)
            ability=next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event=='activate')
            result_cap='permanent.return.owner_hand' if name==NAMES[0] else 'permanent.untap.effect'
            for missing in ('activation.selected_zone_change.fixed',result_cap):
                with self.subTest(name=name,missing=missing):
                    self.assertIn(missing,ability.capability_dependencies)
                    deps=tuple(c for c in ability.capability_dependencies if c!=missing)
                    altered=replace(ability,coverage=(),capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                    bound=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review')
                    self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+missing,bound['blockers'])

    def test_actual_two_island_cost_pays_before_response_and_returns_each_owner_hand(self):
        s=self.session(713000);source=self.add(s.engine,NAMES[0])
        first=self.add(s.engine,'Island',ref='qualified-first-island')
        second=self.add(s.engine,'Tropical Island',seat='B',ref='qualified-second-island')
        second.controller='A'
        s.state.players['B'].zones['battlefield'].remove(second.object_id);s.state.players['A'].zones['battlefield'].append(second.object_id)
        mountain=self.add(s.engine,'Mountain',ref='qualified-wrong-mountain')
        target=self.add(s.engine,'Grizzly Bears',seat='D')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-private')
        self.prepare(s,mana={'U':2});self.checkpoint(s);offer=self.activation_offer(s,source)
        for refs in ([first.ref],[first.ref,first.ref],[first.ref,mountain.ref]):
            self.reject(s,'A',{'action_id':offer['id'],'targets':[target.ref],'cost_cards':refs,'pay':'auto'})
        self.activate(s,source,target,[first,second])
        self.assertEqual('hand',s.state.cards[first.object_id].zone);self.assertEqual('hand',s.state.cards[second.object_id].zone)
        self.assertIn(second.object_id,s.state.players['B'].zones['hand'])
        self.assertNotIn(second.object_id,s.state.players['A'].zones['hand'])
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual('battlefield',s.state.cards[target.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertIn(target.object_id,s.state.players['D'].zones['hand']);self.finish(s,private)

    def test_actual_selected_islands_can_fund_their_mana_price_before_return(self):
        s=self.session(713010);source=self.add(s.engine,NAMES[0])
        lands=[self.add(s.engine,'Island',ref='qualified-mana-island-'+str(i)) for i in range(2)]
        target=self.add(s.engine,'Grizzly Bears',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-mana-private')
        self.prepare(s);self.checkpoint(s)
        # The existing conservative auto planner excludes cost-reserved cards.
        # Take their real, offered mana actions before paying the return cost.
        self.tap_islands_for_mana(s,lands)
        self.assertEqual(2,s.state.players['A'].mana_pool.get('U',0));self.activate(s,source,target,lands)
        self.assertTrue(all(s.state.cards[c.object_id].zone=='hand' for c in lands))
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[target.object_id].zone);self.finish(s,private)

    def test_actual_kindred_elf_and_changeling_pay_a_nontargeted_return_with_any_creature_target(self):
        for i,name in enumerate(('Prowess of the Fair','Woodland Changeling')):
            with self.subTest(name=name):
                s=self.session(713020+i);source=self.add(s.engine,NAMES[1]);paid=self.add(s.engine,name)
                target=self.add(s.engine,'Grizzly Bears',seat='B');target.tapped=True
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-elf-private')
                self.assert_closed(s,paid)
                self.prepare(s,active='B');self.checkpoint(s);self.activate(s,source,target,[paid])
                self.assertEqual('hand',s.state.cards[paid.object_id].zone)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.cards[target.object_id].tapped)
                self.finish(s,private)

    def test_actual_once_each_turn_limits_source_and_reentry_starts_a_new_incarnation(self):
        s=self.session(713030);source=self.add(s.engine,NAMES[1]);equipment=self.add(s.engine,'Runed Stalactite')
        elf=self.add(s.engine,'Llanowar Elves');target=self.add(s.engine,'Grizzly Bears');target.tapped=True
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-usage-private')
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s);old=source.logical_object_id
        self.assert_closed(s,equipment);equip=self.activation_offer(s,equipment)
        result=s.act('pilot:A',{'action_id':equip['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.activate(s,source,target,[source]);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.cards[target.object_id].tapped)
        self.cast(s,'A',s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s))
        fresh=s.state.cards[source.object_id];self.assertNotEqual(old,fresh.logical_object_id)
        self.activate(s,fresh,target,[elf]);self.assertIsNone(self.resolve(s))
        self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a['id'].startswith('activate:'+fresh.ref+':') for a in actions));self.finish(s,private)

    def test_actual_changed_island_type_is_used_and_an_unchanged_mountain_is_excluded(self):
        s=self.session(713040);source=self.add(s.engine,NAMES[0])
        island=self.add(s.engine,'Island');changed=self.add(s.engine,'Mountain',ref='qualified-becomes-island')
        wrong=self.add(s.engine,'Mountain',ref='qualified-still-mountain');target=self.add(s.engine,'Grizzly Bears',seat='B')
        seas=self.add(s.engine,'Spreading Seas',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-type-private')
        self.prepare(s,mana={'C':1,'U':1});self.checkpoint(s);self.cast(s,'A',seas,[changed.ref])
        self.assertIsNone(self.resolve(s));offer=self.activation_offer(s,source)
        legal=offer['cost_summary']['choose_cost'][0]['legal_refs']
        self.assertIn(changed.ref,legal);self.assertIn(island.ref,legal);self.assertNotIn(wrong.ref,legal)
        self.tap_islands_for_mana(s,[island,changed])
        self.activate(s,source,target,[island,changed]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[changed.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[wrong.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[seas.object_id].zone);self.finish(s,private)

    def test_actual_shrouded_elf_is_eligible_for_the_nontargeted_price(self):
        s=self.session(713050);source=self.add(s.engine,NAMES[1]);elf=self.add(s.engine,'Llanowar Elves')
        greaves=self.add(s.engine,'Lightning Greaves');target=self.add(s.engine,'Grizzly Bears',seat='B');target.tapped=True
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-shroud-private')
        self.prepare(s);self.checkpoint(s)
        self.priority(s,'A');equip=self.activation_offer(s,greaves)
        result=s.act('pilot:A',{'action_id':equip['id'],'targets':[elf.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertIn('shroud',s.engine._combat_keywords(s.state.cards[elf.object_id]))
        self.activate(s,source,target,[elf]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[elf.object_id].zone);self.assertFalse(s.state.cards[target.object_id].tapped)
        self.assertEqual('battlefield',s.state.cards[greaves.object_id].zone);self.finish(s,private)

    def test_actual_returned_target_new_incarnation_is_not_untapped_by_old_ability(self):
        s=self.session(713060);source=self.add(s.engine,NAMES[1]);elf=self.add(s.engine,'Llanowar Elves')
        self.add(s.engine,'Vedalken Orrery');icy=self.add(s.engine,'Icy Manipulator',seat='B')
        self.add(s.engine,'Island',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-incarnation-private')
        self.prepare(s,mana={'G':1});self.checkpoint(s);old=elf.logical_object_id
        self.activate(s,source,elf,[elf]);self.assertEqual('hand',s.state.cards[elf.object_id].zone)
        self.cast(s,'A',s.state.cards[elf.object_id]);self.resolve_top(s)
        fresh=s.state.cards[elf.object_id];self.assertNotEqual(old,fresh.logical_object_id)
        self.priority(s,'B');actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        tap=next(a for a in actions if a['id'].startswith('activate:'+icy.ref+':'))
        result=s.act('pilot:B',{'action_id':tap['id'],'targets':[fresh.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.resolve_top(s);self.assertTrue(s.state.cards[elf.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertTrue(s.state.cards[elf.object_id].tapped);self.finish(s,private)

    def test_actual_paid_return_cost_rolls_back_after_commit_fault_and_retries(self):
        import quorune.rules.activation_costs as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.session(713070);source=self.add(s.engine,NAMES[0])
        lands=[self.add(s.engine,'Island',ref='qualified-fault-island-'+str(i)) for i in range(2)]
        target=self.add(s.engine,'Grizzly Bears',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-fault-private')
        self.prepare(s,mana={'U':2});self.checkpoint(s);offer=self.activation_offer(s,source)
        command={'action_id':offer['id'],'targets':[target.ref],'cost_cards':[c.ref for c in lands],'pay':'auto'}
        original=owner.pay_counted_zone_change_activation_cost;observed=[]
        def fault(*args,**kwargs):
            result=original(*args,**kwargs)
            observed.append([s.state.cards[c.object_id].zone for c in lands])
            raise GameRuleError('Injected return-price postcommit failure')
        before=authoritative_state_hash(s.state)
        with patch.object(owner,'pay_counted_zone_change_activation_cost',side_effect=fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok)
        self.assertEqual([['hand','hand']],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual(2,s.state.players['A'].mana_pool.get('U',0))
        s=self.replay(s,load=True);command['action_id']=self.activation_offer(s,source)['id']
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[target.object_id].zone);self.finish(s,private)

    def test_actual_token_elf_can_pay_and_ceases_after_return(self):
        s=self.session(713080);source=self.add(s.engine,NAMES[1]);elf=self.add(s.engine,'Llanowar Elves')
        target=self.add(s.engine,'Grizzly Bears',seat='B');target.tapped=True
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='qualified-token-private')
        # A canonical token is prepared before the checkpoint; only the original
        # Symbiote activation is claimed as a supported gameplay action here.
        created=s.engine.create_token('A',name='Prepared Elf token',copy_of=elf.ref)
        token=next(c for c in s.state.cards.values() if c.ref==created[0])
        self.prepare(s);self.checkpoint(s);self.activate(s,source,target,[token])
        self.assertNotIn(token.object_id,s.state.players['A'].zones['hand'])
        self.assertEqual('outside',s.state.cards[token.object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.cards[target.object_id].tapped);self.finish(s,private)

    def test_fresh_omitted_subtype_mutant_is_killed_after_positive_current_type_action(self):
        import quorune.rules.casting_additional_costs as owner
        original=owner.query_objects
        def mutant(rows,query):
            return original(rows,replace(query,subtypes_all=()))
        with patch.object(owner,'query_objects',side_effect=mutant):
            with self.assertRaises(AssertionError):
                self.test_actual_changed_island_type_is_used_and_an_unchanged_mountain_is_excluded()
