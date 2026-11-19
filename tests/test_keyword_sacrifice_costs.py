"""Current keyword-qualified payment uses effective creature abilities."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from quorune.keyword_sacrifice_cost_model import (KEYWORD_SACRIFICE_COST_VERSION,
    keyword_sacrifice_predicate,validate_keyword_sacrifice_cost)


class KeywordSacrificeCostModelTests(unittest.TestCase):
    def cost(self,**changes):
        return SimpleNamespace(**{'schema_version':8,'operation':'sacrifice_one','count':1,
            'unblocked_attacker':False,'count_parameter':None,'predicate_union':None,
            'predicate':keyword_sacrifice_predicate('defender'),**changes})

    def test_predicate_requires_current_owned_battlefield_creature_and_keyword_without_target_or_source_exclusion(self):
        value=keyword_sacrifice_predicate('defender')
        self.assertEqual(('battlefield',),value.zones);self.assertEqual('$actor',value.controller)
        self.assertEqual(('creature',),value.types_all);self.assertEqual(('defender',),value.keywords_all)
        self.assertIsNone(value.owner);self.assertIsNone(value.exclude_ref);self.assertTrue(value.known_to_actor)
        self.assertEqual(value,type(value).from_dict(value.to_dict()));validate_keyword_sacrifice_cost(self.cost())
        self.assertEqual(8,KEYWORD_SACRIFICE_COST_VERSION)

    def test_new_variant_rejects_old_wire_and_noncanonical_owner_keyword_count_or_extra_semantics(self):
        query=keyword_sacrifice_predicate('defender')
        changes=({'schema_version':1},{'schema_version':True},{'schema_version':7},{'count':True},
            {'count':2},{'operation':'discard_one'},{'unblocked_attacker':True},{'count_parameter':'X'},
            {'predicate_union':object()},{'predicate':replace(query,keywords_all=())},
            {'predicate':replace(query,keywords_all=('flying',))},{'predicate':replace(query,controller='B')},
            {'predicate':replace(query,types_all=('artifact',))},{'predicate':replace(query,token=True)})
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):validate_keyword_sacrifice_cost(self.cost(**change))
        for keyword in ('Defender','flying','defender or flying','',None,True):
            with self.subTest(keyword=keyword),self.assertRaises(ValueError):keyword_sacrifice_predicate(keyword)

    def test_fresh_omitted_keyword_mutant_is_killed_by_independent_payment_quality(self):
        from unittest.mock import patch
        import quorune.keyword_sacrifice_cost_model as owner
        original=owner.keyword_sacrifice_predicate
        wrong=lambda keyword:replace(original(keyword),keywords_all=())
        result=unittest.TestResult()
        import sys
        module=sys.modules[__name__]
        witness=type(self)('test_predicate_requires_current_owned_battlefield_creature_and_keyword_without_target_or_source_exclusion')
        with patch.object(module,'keyword_sacrifice_predicate',wrong):witness.run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


class KeywordSacrificeChoiceCodecTests(unittest.TestCase):
    def choice(self,**changes):
        from quorune.activation_cost_choice import CostChoice
        return CostChoice(kind='sacrifice_one',predicate=keyword_sacrifice_predicate('defender').to_dict(),
            sacrifice_schema_version=8,**changes)

    def test_explicit_choice_and_cost_round_trip_preserves_unmarked_legacy_shape(self):
        from quorune.activation_cost_choice import CostChoice
        from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
        for another in (False,True):
            value=self.choice(another=another);cost=value.fixed_zone_change_cost()
            self.assertEqual(8,cost.schema_version);self.assertEqual(('defender',),cost.predicate.keywords_all)
            self.assertEqual(value,CostChoice.from_dict(value.to_dict()))
            self.assertEqual(cost,FixedZoneChangeAdditionalCost.from_descriptor(cost.to_descriptor()))
            self.assertEqual(8,value.compact()['sacrifice_schema_version'])
        legacy=CostChoice(kind='sacrifice',card_type='creature')
        self.assertEqual({'kind':'sacrifice','count':1,'zone':'battlefield','card_type':'creature','another':False},legacy.to_dict())
        self.assertEqual({'k':'sacrifice','n':1,'z':'battlefield','t':'creature'},legacy.compact())

    def test_old_versions_and_stripped_or_malformed_choice_markers_cannot_admit_keyword_prices(self):
        from quorune.activation_cost_choice import CostChoice
        from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
        wire=self.choice().to_dict();cost=self.choice().fixed_zone_change_cost().to_descriptor()
        for version in range(1,8):
            with self.subTest(version=version),self.assertRaises(ValueError):
                FixedZoneChangeAdditionalCost.from_descriptor({**cost,'schema_version':version})
        missing=dict(wire);missing.pop('sacrifice_schema_version')
        with self.assertRaises(ValueError):CostChoice.from_dict(missing)
        for changes in ({'sacrifice_schema_version':None},{'sacrifice_schema_version':True},{'sacrifice_schema_version':7},
                {'kind':'discard_one'},{'zone':'hand'},{'count':2},{'return_schema_version':7}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):CostChoice.from_dict({**wire,**changes})

    def test_raw_full_and_compact_prices_infer_their_independent_runtime_owner_without_coverage(self):
        from quorune.rules.keyword_sacrifice_costs import keyword_sacrifice_payload_dependencies
        from quorune.keyword_sacrifice_cost_model import KEYWORD_SACRIFICE_CAPABILITY
        for value in (self.choice().to_dict(),self.choice().compact(),self.choice(another=True).compact(),
                self.choice().fixed_zone_change_cost().to_descriptor()):
            self.assertIn(KEYWORD_SACRIFICE_CAPABILITY,keyword_sacrifice_payload_dependencies(value))
        value=self.choice().compact()
        for changes in ({'sacrifice_schema_version':True},{'other':True},{'other':0},{'extra':None},{'q':None}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                keyword_sacrifice_payload_dependencies({**value,**changes})
        self.assertEqual((),keyword_sacrifice_payload_dependencies({'k':'sacrifice','n':1,'z':'battlefield'}))


class KeywordSacrificeCompilerTests(unittest.TestCase):
    def test_existing_activation_cost_owner_lowers_complete_keyword_choice_and_all_other_costs(self):
        from quorune.abilities import parse_activated_abilities
        from quorune.compiler.activated_zone_change_costs import fixed_activated_zone_change_cost
        from quorune.compiler.activated_costs import activated_ability_cost_capabilities
        from quorune.keyword_sacrifice_cost_model import KEYWORD_SACRIFICE_CAPABILITY
        for prefix,another in (('{T}, Sacrifice a creature with defender',False),('{2}, {T}, Sacrifice another creature with defender',True)):
            ability,=parse_activated_abilities(card_name='Generic Source',oracle_text=prefix+': Draw a card.',keywords=())
            lowered=fixed_activated_zone_change_cost(ability)
            self.assertEqual((),lowered.uncompiled_costs);self.assertEqual(1,len(lowered.choices))
            choice=lowered.choices[0];self.assertEqual(another,choice.another)
            self.assertEqual(('defender',),choice.fixed_zone_change_cost().predicate.keywords_all)
            self.assertEqual(8,choice.sacrifice_schema_version);self.assertTrue(lowered.tap_source)
            self.assertEqual(ability.mana,lowered.mana)
            self.assertIn(KEYWORD_SACRIFICE_CAPABILITY,activated_ability_cost_capabilities(lowered))

    def test_unsupported_keyword_disjunction_multiple_costs_and_unrelated_prices_remain_uncompiled(self):
        from quorune.compiler.activated_zone_change_costs import _zone_change_descriptor
        for text in ('Sacrifice a creature with flying','Sacrifice a creature with defender or flying',
                'Sacrifice two creatures with defender','Sacrifice a creature with defender that entered this turn',
                'Discard a creature with defender','Sacrifice an artifact with defender'):
            with self.subTest(text=text):self.assertIsNone(_zone_change_descriptor(text))


from inherited_composition_fixture import InheritedNativeFixture


class KeywordSacrificeNativeFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        import json,tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();root=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=root/'base.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,ROOT/'tests/fixtures/keyword-sacrifice-cards.json'],root/'keyword.sqlite3')
        cls.db=CardDatabase(root/'keyword.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Keyword sacrifice',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def action_for_cost(self,s,source):
        self.priority(s,'A')
        current=s.state.cards[source.object_id]
        ability=next(a for a in s.engine._activated_abilities(current) if any(c.sacrifice_schema_version==8 for c in a.choices))
        action_id='activate:'+current.ref+':'+ability.ability_id
        return next((a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']==action_id),None)


class KeywordSacrificeNativeTests(KeywordSacrificeNativeFixture,unittest.TestCase):
    def test_actual_paid_originals_use_controlled_defender_cost_and_keep_private_packets_and_replay(self):
        import json
        from quorune.record import authoritative_state_hash
        for index,name in enumerate(('Flamewright','Portcullis Vine')):
            with self.subTest(name=name):
                s=self.session(602217001+index);source=self.add(s.engine,name,zone='hand')
                self.add(s.engine,'Fervor')
                wall=self.add(s.engine,'Wall of Air',ref='own-defender')
                wrong=self.add(s.engine,'Grizzly Bears',ref='nondefender')
                foreign=self.add(s.engine,'Wall of Air',seat='B',ref='opposing-defender')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='keyword-sacrifice-private')
                self.assert_closed(s,source)
                self.prepare(s,mana={'R':1,'W':1} if index==0 else {'G':1,'C':2});self.checkpoint(s)
                self.cast_for(s,'A',source,reserve={} if index==0 else {'C':2});self.assertIsNone(self.resolve(s))
                current=s.state.cards[source.object_id];self.assertEqual('battlefield',current.zone)
                chosen=s.state.cards[wall.object_id] if index==0 else current
                action=self.action_for_cost(s,current);self.assertIsNotNone(action)
                command={'action_id':action['id'],'cost_cards':[chosen.ref],'targets':['B'] if index==0 else [],'pay':'auto'}
                before=authoritative_state_hash(s.state)
                for principal,payment in (('pilot:B',chosen.ref),('pilot:A',wrong.ref),('pilot:A',foreign.ref)):
                    result=s.act(principal,{**command,'cost_cards':[payment]});self.assertFalse(result.ok,result.summary)
                    self.assertEqual(before,authoritative_state_hash(s.state))
                hand=len(s.state.players['A'].zones['hand'])
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual('graveyard',s.state.cards[chosen.object_id].zone)
                self.assertEqual(1,len(s.state.stack))
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                if index==0:
                    self.assertEqual(39,s.state.players['B'].life);self.assertTrue(s.state.cards[source.object_id].tapped)
                else:
                    self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
                    self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
                for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.replay(s,load=True)


class KeywordSacrificeInteractionTests(KeywordSacrificeNativeFixture,unittest.TestCase):
    def cost_command(self,s,source,payment,targets=()):
        action=self.action_for_cost(s,s.state.cards[source.object_id]);self.assertIsNotNone(action)
        return {'action_id':action['id'],'cost_cards':[s.state.cards[payment.object_id].ref],
            'targets':list(targets),'pay':'auto'}

    def test_actual_added_and_removed_defender_controls_payment_instead_of_printed_creature_type(self):
        from quorune.record import authoritative_state_hash
        s=self.session(602217010);source=self.add(s.engine,'Flamewright')
        source.acquired_control_turn_count=-1
        wall=self.add(s.engine,'Wall of Air');bear=self.add(s.engine,'Grizzly Bears')
        grant=self.add(s.engine,'Guard Duty',zone='hand');remove=self.add(s.engine,'Humble',zone='hand')
        for c in (source,grant,remove):self.assert_closed(s,c)
        self.prepare(s,mana={'W':2,'C':1});self.checkpoint(s)
        self.assertIsNotNone(self.action_for_cost(s,source))
        self.cast_for(s,'A',grant,[bear.ref],reserve={'W':1,'C':1});self.assertIsNone(self.resolve(s))
        self.cast_for(s,'A',remove,[wall.ref]);self.assertIsNone(self.resolve(s))
        current=s.state.cards[wall.object_id];self.assertEqual('battlefield',current.zone)
        self.assertIn('wall',s.engine._type_parts(s.engine._effective_card_data(current)['type_line'])[1])
        command=self.cost_command(s,source,bear,['B']);before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:A',{**command,'cost_cards':[current.ref]});self.assertFalse(rejected.ok,rejected.summary)
        self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',s.state.cards[bear.object_id].zone);self.assertIsNone(self.resolve(s))
        self.assertEqual(39,s.state.players['B'].life);self.assertEqual('battlefield',s.state.cards[wall.object_id].zone)
        self.replay(s,load=True)

    def test_actual_first_ability_token_is_eligible_for_second_ability_after_real_untap(self):
        s=self.session(602217011);source=self.add(s.engine,'Flamewright');source.acquired_control_turn_count=-1
        untap=self.add(s.engine,'Vitalize',zone='hand');self.assert_closed(s,source);self.assert_closed(s,untap)
        self.prepare(s,mana={'C':1,'G':1});self.checkpoint(s)
        self.assertIsNone(self.action_for_cost(s,source))
        first=next(a for a in s.engine._activated_abilities(source) if not a.choices)
        command={'action_id':'activate:'+source.ref+':'+first.ability_id,'pay':'auto','reserve':{'G':1}}
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token']
        self.assertEqual(1,len(tokens));token=tokens[0]
        self.assertIn('defender',s.engine._combat_keywords(token));self.assertTrue(s.state.cards[source.object_id].tapped)
        self.cast_for(s,'A',untap);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.cards[source.object_id].tapped)
        result=s.act('pilot:A',self.cost_command(s,source,token,['B']));self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(39,s.state.players['B'].life)
        self.assertFalse(any(c.object_id==token.object_id and c.zone=='battlefield' for c in s.state.cards.values()))
        self.replay(s,load=True)

    def test_post_sacrifice_fault_rolls_back_source_tap_mana_zone_events_and_retries_once(self):
        from unittest.mock import patch
        import quorune.rules.activation.payment_commit as payment_owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.session(602217012);source=self.add(s.engine,'Portcullis Vine');source.acquired_control_turn_count=-1
        wall=self.add(s.engine,'Wall of Air');self.prepare(s,mana={'C':2});self.checkpoint(s)
        command=self.cost_command(s,source,wall);before=authoritative_state_hash(s.state);original=payment_owner.commit_activation_payments;observed=[]
        def fault(*args,**kwargs):
            result=original(*args,**kwargs)
            observed.append((s.state.cards[wall.object_id].zone,s.state.cards[source.object_id].tapped,sum(s.state.players['A'].mana_pool.values())))
            raise GameRuleError('Injected selected defender post-sacrifice fault')
        with patch.object(payment_owner,'commit_activation_payments',fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok,result.summary)
            self.assertIn('Injected selected defender post-sacrifice fault',result.summary)
        self.assertEqual([('graveyard',True,0)],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);hand=len(s.state.players['A'].zones['hand'])
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[wall.object_id].zone);self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']));self.replay(s)


class KeywordSacrificeResponseTests(KeywordSacrificeNativeFixture,unittest.TestCase):
    def test_actual_countered_ability_keeps_self_sacrifice_and_mana_without_drawing(self):
        s=self.session(602217020);source=self.add(s.engine,'Portcullis Vine');source.acquired_control_turn_count=-1
        counter=self.add(s.engine,'Stifle',seat='B',zone='hand');self.assert_closed(s,counter)
        s.state.players['B'].mana_pool.update({'U':1});self.prepare(s,mana={'C':2});self.checkpoint(s)
        action=self.action_for_cost(s,source);self.assertIsNotNone(action);hand=len(s.state.players['A'].zones['hand'])
        command={'action_id':action['id'],'cost_cards':[source.ref],'pay':'auto'}
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        ability=s.state.stack[-1].ref;self.cast_for(s,'B',counter,[ability])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual(hand,len(s.state.players['A'].zones['hand']));self.replay(s)

    def test_actual_exile_replacement_pays_sacrifice_without_a_creature_dies_occurrence(self):
        s=self.session(602217021);source=self.add(s.engine,'Portcullis Vine');source.acquired_control_turn_count=-1
        replacement=self.add(s.engine,'Rest in Peace',seat='B');self.assert_closed(s,replacement)
        self.prepare(s,mana={'C':2});self.checkpoint(s);hand=len(s.state.players['A'].zones['hand'])
        action=self.action_for_cost(s,source);self.assertIsNotNone(action)
        result=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[source.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertFalse(any(e.kind=='permanent_died' for e in s.state.turn_history.events))
        self.assertIsNone(self.resolve(s));self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)


class KeywordSacrificeBindingTests(KeywordSacrificeNativeFixture,unittest.TestCase):
    def test_complete_originals_bind_with_independent_price_owner_and_never_stripped_dependency(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import compile_oracle_card
        from quorune.keyword_sacrifice_cost_model import KEYWORD_SACRIFICE_CAPABILITY
        for name in ('Flamewright','Portcullis Vine'):
            card=self.db.lookup(name);ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
            binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
            ability=next(a for a in program.abilities if KEYWORD_SACRIFICE_CAPABILITY in a.capability_dependencies)
            for cap in (KEYWORD_SACRIFICE_CAPABILITY,'activation.selected_zone_change.fixed'):
                deps=tuple(c for c in ability.capability_dependencies if c!=cap)
                changed=replace(ability,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict'],(name,cap));self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_genuinely_verified_keyword_and_selected_cost_withdrawals_preserve_unrelated_support(self):
        from pathlib import Path
        import json
        from copy import deepcopy
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        document=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in ('activation.selected_zone_change.fixed_keyword','activation.selected_zone_change.fixed'):
            value=deepcopy(raw);next(c for c in value['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn selected keyword cost authority'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=document,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ('Flamewright','Portcullis Vine'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,(cap,name))
                blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)
            ir=compile_oracle_card(self.db.lookup('Grizzly Bears'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)


class KeywordSacrificeNativeMutationTests(KeywordSacrificeNativeFixture,unittest.TestCase):
    def test_fresh_native_ignored_keyword_mutant_is_killed_after_real_paid_originals(self):
        from unittest.mock import patch
        import quorune.object_query as owner
        witness=lambda:KeywordSacrificeNativeTests.test_actual_paid_originals_use_controlled_defender_cost_and_keep_private_packets_and_replay(self)
        witness();original=owner.keyword_type_terms
        native=KeywordSacrificeNativeTests('test_actual_paid_originals_use_controlled_defender_cost_and_keep_private_packets_and_replay')
        native.db=self.db;native.registry=self.registry;native.deck=self.deck
        result=unittest.TestResult()
        with patch.object(owner,'keyword_type_terms',side_effect=lambda words:original(words)|{'defender'}):native.run(result)
        self.assertEqual([],result.errors);self.assertGreaterEqual(len(result.failures),1)
