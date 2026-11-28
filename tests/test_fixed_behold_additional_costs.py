"""CR701.4/701.20: choosing a controlled permanent and revealing a hand card differ."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quorune.model import CardInstance

from quorune.rules.behold_additional_cost import FixedBeholdAdditionalCost, BEHOLD_ADDITIONAL_COST_CAPABILITY
from quorune.compiler.behold_cost_templates import fixed_behold_cost_leaf, without_fixed_behold_reminder
from quorune.compiler.spell_additional_cost_templates import fixed_alternative_additional_cost_template
from quorune.rules.casting_additional_cost_groups import FixedAlternativeAdditionalCost, fixed_alternative_additional_cost_node_capabilities, selected_zone_cost_payload_dependencies
from quorune.rules.casting.behold_costs import behold_cost_candidates, behold_cost_selection, commit_behold_additional_costs
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


CLAUSE='As an additional cost to cast this spell, behold a Dragon or pay {1}. (To behold a Dragon, choose a Dragon you control or reveal a Dragon card from your hand.)'
NAMES=['Caustic Exhale','Lys Alana Dignitary','Silvergill Mentor','Kinsbaile Aspirant','Mudbutton Cursetosser']


class FixedBeholdCostModelTests(unittest.TestCase):
    def test_behold_leaf_and_exact_reminder_keep_both_nonmoving_zones_and_mana_alternative(self):
        template=fixed_alternative_additional_cost_template(CLAUSE)
        descriptor=template.descriptor;cost=FixedAlternativeAdditionalCost.from_descriptor(descriptor)
        self.assertEqual(FixedBeholdAdditionalCost('dragon'),cost.options[0])
        self.assertEqual(1,cost.options[1].requirements_dict['GENERIC'])
        self.assertEqual(CLAUSE.split(' (')[0],without_fixed_behold_reminder(CLAUSE))
        self.assertIn(BEHOLD_ADDITIONAL_COST_CAPABILITY,fixed_alternative_additional_cost_node_capabilities(cost_schema=template.cost_schema))
        self.assertIn(BEHOLD_ADDITIONAL_COST_CAPABILITY,selected_zone_cost_payload_dependencies({'additional_costs':[cost.options[0].to_descriptor()]}))
        from quorune.choice_forms import build_action_form,delegated_choice_fields
        action={'id':'cast:SOURCE','action':'cast','cost_options':[{'id':'behold-price','label':'Behold a Dragon',
            'choice_schema':{'behold_cards':{'type':'object_ref_array','legal_refs':['HAND','PERMANENT'],'minimum':1,'maximum':1}}}]}
        form=build_action_form(action,decision_kind='priority',context={})
        field=next(f for f in form['variants']['options'][0]['fields'] if f['name']=='behold_cards')
        self.assertEqual('refs',field['control']);self.assertEqual(1,field['minimum']);self.assertEqual(1,field['maximum'])
        self.assertIn('behold_cards',delegated_choice_fields(action,decision_kind='priority',context={}))

    def test_malformed_behold_and_wrong_reminder_or_open_qualities_stay_closed(self):
        raw=FixedBeholdAdditionalCost('dragon').to_descriptor()
        for change in ({'schema_version':True},{'schema_version':2},{'kind':'reveal_from_hand'},
                {'subtype':'Dragon'},{'subtype':'unrepresented quality'},{'count':2}):
            with self.assertRaises(ValueError):FixedBeholdAdditionalCost.from_descriptor({**raw,**change})
        for phrase in ['behold two Dragons','behold a Dragon creature','behold an Dragon','you may behold a Dragon','behold a Dragon and exile it']:
            self.assertIsNone(fixed_behold_cost_leaf(phrase))
        for clause in [CLAUSE.replace('from your hand.)','from your graveyard.)'),
                CLAUSE.replace('choose a Dragon','choose an Elf'),CLAUSE.replace('pay {1}','pay {X}'),
                CLAUSE.replace('behold a Dragon or','you may behold a Dragon or')]:
            self.assertIsNone(fixed_alternative_additional_cost_template(clause))

    def test_linked_exile_and_owner_hand_return_keep_disjoint_closed_descriptors(self):
        from quorune.linked_exile_return_model import (
            LINKED_ABILITY_BINDING_PREFIX, LINKED_ABILITY_TIMING, LINKED_BEHOLD_HAND_RETURN_VERSION,
            LINKED_EXILE_RETURN_OPERATION, LinkedExileReturnSpec, validate_linked_instruction,
        )
        from quorune.rules.behold_additional_cost import LINKED_BEHOLD_SCHEMA_VERSION
        from quorune.rules.casting_additional_cost_groups import fixed_additional_cost_leaf

        pair = LINKED_ABILITY_BINDING_PREFIX + '1' * 24
        old_cost = FixedBeholdAdditionalCost('dragon')
        linked_cost = FixedBeholdAdditionalCost('dragon', schema_version=LINKED_BEHOLD_SCHEMA_VERSION,
                                                linked_exile_pair=pair)
        self.assertEqual(old_cost, FixedBeholdAdditionalCost.from_descriptor(old_cost.to_descriptor()))
        self.assertEqual(linked_cost, FixedBeholdAdditionalCost.from_descriptor(linked_cost.to_descriptor()))
        with self.assertRaises(ValueError):
            fixed_additional_cost_leaf(linked_cost.to_descriptor())
        with self.assertRaises(ValueError):
            FixedBeholdAdditionalCost.from_descriptor({**old_cost.to_descriptor(), 'linked_exile_pair': pair})
        spec = LinkedExileReturnSpec(timing=LINKED_ABILITY_TIMING, schema_version=LINKED_BEHOLD_HAND_RETURN_VERSION)
        instruction = {'op': LINKED_EXILE_RETURN_OPERATION, 'binding_id': pair, 'phase': 'return',
                       'spec': spec.to_dict()}
        validate_linked_instruction(instruction)
        with self.assertRaises(ValueError):
            validate_linked_instruction({**instruction, 'phase': 'exile', 'cards': '$targets'})
        with self.assertRaises(ValueError):
            validate_linked_instruction({**instruction, '_aura_target_refs': {'one': 'two'}})

    def test_original_receipt_rejects_crossed_identity_and_stays_off_spell_copies(self):
        from quorune.beheld_exile_state import PAID_BEHELD_EXILE_FIELD, PaidBeheldExile, mark_resolving_beheld_exile
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.errors import GameRuleError
        from quorune.linked_exile_return_model import ExiledObjectIdentity, LINKED_ABILITY_BINDING_PREFIX
        from quorune.selection.copy_targets import stack_copy_context

        pair = LINKED_ABILITY_BINDING_PREFIX + '1' * 24
        expected = PaidBeheldExile(ContinuousObjectIdentity('spell', 'spell@3'), pair,
                                  ExiledObjectIdentity('paid', 'paid-ref', 'paid@2')).to_dict()
        card = SimpleNamespace(object_id='spell', logical_object_id='spell@3', zone='stack',
                               is_spell_copy=False, annotations={})
        descriptor = FixedBeholdAdditionalCost('dragon', schema_version=2, linked_exile_pair=pair).to_descriptor()
        program = SimpleNamespace(cost_schema={'additional_costs': [descriptor]})
        host = SimpleNamespace(semantics={'spell-program': program})
        item = SimpleNamespace(kind='spell', card_object_id='spell', semantic_key='spell-program', ref='stack-ref',
                               context={'retained': [1]})
        for change, error in (
            ({'schema_version': True}, ValueError),
            ({'spell': {'object_id': 'spell', 'logical_object_id': 'spell@2'}}, GameRuleError),
            ({'ability_pair': LINKED_ABILITY_BINDING_PREFIX + '2' * 24}, GameRuleError),
        ):
            item.context[PAID_BEHELD_EXILE_FIELD] = {**expected, **change}
            with self.assertRaises(error):
                mark_resolving_beheld_exile(host, card, item)
            self.assertEqual({}, card.annotations)
        item.context[PAID_BEHELD_EXILE_FIELD] = expected
        mark_resolving_beheld_exile(host, card, item)
        self.assertEqual(expected, card.annotations[PAID_BEHELD_EXILE_FIELD])
        copy = stack_copy_context(item, {}, {}, permanent_spell=True, characteristics={'name': 'Witness'})
        self.assertNotIn(PAID_BEHELD_EXILE_FIELD, copy)
        self.assertEqual([1], copy['retained'])
        self.assertIsNot(copy['retained'], item.context['retained'])

    def test_linked_behold_records_only_the_actual_nontoken_exile_arrival(self):
        from quorune.linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
        from quorune.rules.casting.behold_costs import commit_linked_behold_cost

        pair = LINKED_ABILITY_BINDING_PREFIX + '1' * 24
        cost = FixedBeholdAdditionalCost('dragon', schema_version=2, linked_exile_pair=pair)
        for token, destination, linked in ((False, 'exile', True), (True, 'exile', False),
                                           (False, 'graveyard', False)):
            with self.subTest(token=token, destination=destination):
                host, source, _, _, chosen, _, _ = self.fixture()
                chosen.is_token = token
                ok, selected = behold_cost_selection(host, seat='A', source=source, cost=cost,
                    response={'behold_cards': [chosen.ref]}, hint=False, choice_schema={}, cost_position=0)
                self.assertTrue(ok)
                result = SimpleNamespace(beheld_linked_exile=None)

                def commit_zone_change(host, proposal, source, result, changes, **kwargs):
                    self.assertIs(changes[0][0], chosen)
                    self.assertEqual('exile', changes[0][7])
                    chosen.zone = destination
                    chosen.zone_change_counter += 1

                with patch('quorune.rules.casting.zone_cost_commit.commit_card_cost_changes', commit_zone_change):
                    commit_linked_behold_cost(host, SimpleNamespace(seat='A'), source, result, selected,
                                              {'additional_costs': [cost.to_descriptor()]}, {})
                if linked:
                    linked_pair, identity = result.beheld_linked_exile
                    self.assertEqual(pair, linked_pair)
                    self.assertTrue(identity.matches_exiled_card(chosen))
                else:
                    self.assertIsNone(result.beheld_linked_exile)

    def fixture(self):
        def card(oid,zone,owner='A',controller='A',subtype='Dragon',*,phased=False):
            return CardInstance(object_id=oid,ref=oid+'-ref',
                printed_name='Generic '+subtype,oracle_id=oid+'-oracle',zone=zone,owner=owner,controller=controller,
                phased_out=phased,annotations={'fixture_subtype':subtype})
        source=card('spell','hand');hidden=card('hidden','hand');wrong=card('wrong','hand',subtype='Elf')
        borrowed=card('borrowed','battlefield',owner='B');foreign=card('foreign','battlefield',controller='B')
        phased=card('phased','battlefield',phased=True)
        cards={c.object_id:c for c in [source,hidden,wrong,borrowed,foreign,phased]}
        host=SimpleNamespace(state=SimpleNamespace(cards=cards,players={'A':SimpleNamespace(zones={'hand':['spell','hidden','wrong']})}),
            _effective_card_data=lambda c:{'type_line':'Kindred Enchantment — '+c.annotations['fixture_subtype']},
            _type_parts=lambda text:(['kindred','enchantment'],[text.split(' — ')[1]],[]))
        host._log=lambda *a,**kw:None
        return host,source,hidden,wrong,borrowed,foreign,phased

    def test_candidates_use_control_not_owner_exclude_source_and_allow_noncreature_kindred(self):
        host,source,hidden,wrong,borrowed,foreign,phased=self.fixture()
        cards=behold_cost_candidates(host,'A',source,FixedBeholdAdditionalCost('dragon'))
        self.assertEqual([hidden.object_id,borrowed.object_id],[c.object_id for c in cards])
        choice={};self.assertEqual((True,None),behold_cost_selection(host,seat='A',source=source,
            cost=FixedBeholdAdditionalCost('dragon'),response={},hint=True,choice_schema=choice,cost_position=0))
        self.assertEqual([hidden.ref,borrowed.ref],choice['behold_cards']['legal_refs'])
        hand_token=CardInstance(object_id='hand-token',ref='hand-token',oracle_id='token-oracle',printed_name='Dragon Token',
            owner='A',controller='A',zone='hand',is_token=True,annotations={'fixture_subtype':'Dragon'})
        host.state.cards[hand_token.object_id]=hand_token;host.state.players['A'].zones['hand'].append(hand_token.object_id)
        self.assertNotIn(hand_token,behold_cost_candidates(host,'A',source,FixedBeholdAdditionalCost('dragon')))
        hand_token.zone='battlefield';host.state.players['A'].zones['hand'].remove(hand_token.object_id)
        self.assertIn(hand_token,behold_cost_candidates(host,'A',source,FixedBeholdAdditionalCost('dragon')))
        other=CardInstance(object_id='another-permanent',ref='another-permanent',oracle_id='another-oracle',printed_name='Generic Dragon',
            owner='A',controller='A',zone='battlefield',zone_timestamp=3,annotations={'fixture_subtype':'Dragon'})
        host.state.cards[other.object_id]=other
        before=[c.ref for c in behold_cost_candidates(host,'A',source,FixedBeholdAdditionalCost('dragon'))]
        host.state.cards=dict(reversed(tuple(host.state.cards.items())))
        self.assertEqual(before,[c.ref for c in behold_cost_candidates(host,'A',source,FixedBeholdAdditionalCost('dragon'))])

    def test_choice_shape_and_current_sealed_identity_reject_wrong_duplicate_and_stale_objects(self):
        host,source,hidden,wrong,borrowed,foreign,phased=self.fixture();cost=FixedBeholdAdditionalCost('dragon')
        for refs in ([],[wrong.ref],[foreign.ref],[source.ref],[borrowed.ref,borrowed.ref]):
            self.assertFalse(behold_cost_selection(host,seat='A',source=source,cost=cost,response={'behold_cards':refs},hint=False,choice_schema={},cost_position=0)[0])
        ok,selected=behold_cost_selection(host,seat='A',source=source,cost=cost,response={'behold_cards':[borrowed.ref]},hint=False,choice_schema={},cost_position=0)
        self.assertTrue(ok);self.assertIsNone(selected['selection']['reveal'])
        option={'additional_costs':[cost.to_descriptor()],'selected_additional_costs':[selected]}
        borrowed.zone_change_counter+=1
        with self.assertRaises(ValueError):commit_behold_additional_costs(host,SimpleNamespace(seat='A'),source,SimpleNamespace(),option)

    def test_permanent_commit_records_quality_without_moving_tapping_or_disclosing_hand(self):
        host,source,hidden,wrong,borrowed,foreign,phased=self.fixture();cost=FixedBeholdAdditionalCost('dragon')
        _,selected=behold_cost_selection(host,seat='A',source=source,cost=cost,response={'behold_cards':[borrowed.ref]},hint=False,choice_schema={},cost_position=0)
        with patch('quorune.rules.casting.behold_costs.begin_stack_cost_hand_reveal') as reveal:
            info=commit_behold_additional_costs(host,SimpleNamespace(seat='A'),source,SimpleNamespace(),{'additional_costs':[cost.to_descriptor()],'selected_additional_costs':[selected]})
        reveal.assert_not_called();self.assertEqual('battlefield',borrowed.zone);self.assertFalse(borrowed.tapped)
        self.assertEqual('dragon',info['payments'][0]['quality']);self.assertEqual(borrowed.logical_object_id,info['payments'][0]['logical_object_id'])

    def test_control_omission_mutant_is_killed_by_a_fresh_query(self):
        from quorune.rules.casting import behold_costs as owner
        original=owner.object_matches_query
        def witness():
            host,source,hidden,wrong,borrowed,foreign,phased=self.fixture()
            self.assertNotIn(foreign,owner.behold_cost_candidates(host,'A',source,FixedBeholdAdditionalCost('dragon')))
        witness()
        def omit(row,query):
            from dataclasses import replace
            return original(row,replace(query,controller=None))
        with patch.object(owner,'object_matches_query',side_effect=omit),self.assertRaises(AssertionError):witness()


class FixedBeholdCostCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'behold-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/fixed-behold-additional-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_all_five_originals_have_complete_normal_admission_and_source_spans(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        semantics=SemanticRegistry();cards=[self.db.lookup(n,fuzzy=False) for n in NAMES]
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(name=card.name):
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                for face in ir.faces:
                    for node in face.nodes:self.assertEqual(node.text,card.oracle_text[node.span.start:node.span.end])
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],(card.name,bound['blockers']));self.assertEqual('capability_closed',bound['trust_basis'])

    def test_verified_behold_capability_withdrawal_blocks_originals_and_keeps_old_reveal(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_granted_skulk import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id']==BEHOLD_ADDITIONAL_COST_CAPABILITY).update(status='blocked',blockers=['Behold casting cost owner withdrawn'])
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)
        old='As an additional cost to cast this spell, reveal a Merfolk card from your hand or pay {3}.\nDraw a card.'
        self.assertEqual('exact',compile_oracle_card(record(old,'Sorcery'),capability_registry=registry,capability_profile='commander_review').status)


class FixedBeholdCostNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'behold-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/fixed-behold-additional-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Behold costs',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def offer(self,s,card):
        self.priority(s,'A');return self.action(s,'A',card)

    def behold_command(self,s,card,chosen,*,targets=(),reserve=None):
        offer=self.offer(s,card)
        option=next(o for o in offer['cost_options'] if 'behold_cards' in o.get('choice_schema',{}))
        self.assertIn(chosen.ref,option['choice_schema']['behold_cards']['legal_refs'])
        return {'action_id':offer['id'],'cost_option':option['id'],'behold_cards':[chosen.ref],
            'targets':list(targets),'pay':'auto','reserve':reserve or {}},option

    def cast_beholding(self,s,card,chosen,*,targets=(),reserve=None):
        command,option=self.behold_command(s,card,chosen,targets=targets,reserve=reserve)
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        return option

    def caustic_witness(self,seed,mode):
        from quorune.attachments import attach_objects
        from quorune.rules.behold_additional_cost import BEHOLD_COST_INFORMATION_FIELD
        s=self.session(seed);source=self.add(s.engine,'Caustic Exhale',zone='hand')
        chosen=self.add(s.engine,'Shivan Dragon',ref='controlled-behold-dragon')
        hand=self.add(s.engine,'Shivan Dragon',zone='hand',ref='concealed-dragon-choice-A')
        foreign=self.add(s.engine,'Shivan Dragon',seat='B',ref='foreign-behold-dragon')
        target=self.add(s.engine,'Colossal Dreadmaw',seat='B');greaves=self.add(s.engine,'Lightning Greaves')
        attach_objects(s.state.cards,greaves,chosen,source_timestamp=greaves.zone_timestamp,players=s.state.players)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-caustic-card-C')
        self.assert_closed(s,source);self.prepare(s,mana={'B':1,'C':1});prior_reveal=set(hand.revealed_to);self.checkpoint(s)
        offer=self.offer(s,source);behold=next(o for o in offer['cost_options'] if 'behold_cards' in o.get('choice_schema',{}))
        refs=behold['choice_schema']['behold_cards']['legal_refs'];self.assertIn(chosen.ref,refs);self.assertIn(hand.ref,refs);self.assertNotIn(foreign.ref,refs)
        self.assertIn('shroud',s.engine._combat_keywords(s.state.cards[chosen.object_id]))
        for bad in [[],[foreign.ref],[source.ref],[chosen.ref,chosen.ref]]:
            self.reject(s,'A',{'action_id':offer['id'],'cost_option':behold['id'],'behold_cards':bad,'targets':[target.ref],'pay':'auto'})
        if mode=='mana':
            option=next(o for o in offer['cost_options'] if 'behold_cards' not in o.get('choice_schema',{}))
            command={'action_id':offer['id'],'cost_option':option['id'],'targets':[target.ref],'pay':'auto'}
            self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        else:self.cast_beholding(s,source,hand if mode=='hand' else chosen,targets=[target.ref])
        item=s.state.stack[-1]
        self.assertEqual(set('ABCD') if mode=='hand' else prior_reveal,set(s.state.cards[hand.object_id].revealed_to))
        self.assertEqual(0 if mode=='mana' else 1,sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual(mode!='mana',BEHOLD_COST_INFORMATION_FIELD in item.context)
        self.assertEqual('battlefield',s.state.cards[chosen.object_id].zone);self.assertFalse(s.state.cards[chosen.object_id].tapped)
        if mode=='permanent':self.assertNotIn(hand.ref,json.dumps(s.packet('pilot:B',full=True)))
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((3,3),tuple(s.engine._numeric_stat(target.object_id,k) for k in ('power','toughness')))
        self.assertEqual(prior_reveal,set(s.state.cards[hand.object_id].revealed_to));self.assertEqual('hand',s.state.cards[hand.object_id].zone)
        self.finish(s,private)

    def test_actual_caustic_hand_permanent_and_mana_prices_keep_one_targeted_result(self):
        for i,mode in enumerate(['hand','permanent','mana']):
            with self.subTest(mode=mode):self.caustic_witness(1116300+i,mode)

    def test_actual_lys_chooses_borrowed_noncreature_kindred_and_keeps_conditional_green_mana(self):
        s=self.session(1116310);source=self.add(s.engine,'Lys Alana Dignitary',zone='hand')
        chosen=self.add(s.engine,'Prowess of the Fair',seat='B');s.engine.change_control(chosen.object_id,'A')
        elf=self.add(s.engine,'Llanowar Elves');shock=self.add(s.engine,'Shock',seat='B',zone='hand');self.add(s.engine,'Fervor')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-lys-card-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':1,'G':1});s.state.players['B'].mana_pool.update({'R':1});self.checkpoint(s)
        self.cast_beholding(s,source,chosen);self.assertIsNone(self.resolve(s))
        self.assertEqual(('B','A','battlefield'),(s.state.cards[chosen.object_id].owner,s.state.cards[chosen.object_id].controller,s.state.cards[chosen.object_id].zone))
        self.priority(s,'A');ability,=s.engine._activated_abilities(s.state.cards[source.object_id]);action='activate:'+source.ref+':'+ability.ability_id
        self.assertFalse(any(a['id']==action for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        self.cast(s,'B',shock,[elf.ref]);decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        self.replay(s,load=True);command={'action_id':'choose','choice':'apply'};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[elf.object_id].zone)
        self.priority(s,'A');self.assertTrue(any(a['id']==action for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        self.reject(s,'D',{'action_id':action,'pay':'auto'});result=s.act('pilot:A',{'action_id':action,'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertTrue(s.state.cards[source.object_id].tapped);self.assertEqual(2,s.state.players['A'].mana_pool['G']);self.finish(s,private)

    def test_actual_silvergill_reveals_changeling_and_creates_complete_merfolk_without_drawing(self):
        s=self.session(1116320);source=self.add(s.engine,'Silvergill Mentor',zone='hand')
        chosen=self.add(s.engine,'Changeling Outcast',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-mentor-card-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':1,'U':1});prior_reveal=set(chosen.revealed_to);self.checkpoint(s)
        library=len(s.state.players['A'].zones['library']);hand=len(s.state.players['A'].zones['hand'])
        self.cast_beholding(s,source,chosen);self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens));token=tokens[0];effective=s.engine._effective_card_data(token)
        self.assertIn('merfolk',s.engine._type_parts(effective['type_line'])[1]);self.assertEqual({'W','U'},set(effective['colors']))
        self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
        self.assertEqual(library,len(s.state.players['A'].zones['library']));self.assertEqual(hand-1,len(s.state.players['A'].zones['hand']))
        self.assertEqual('hand',s.state.cards[chosen.object_id].zone);self.assertEqual(prior_reveal,set(s.state.cards[chosen.object_id].revealed_to));self.finish(s,private)

    def test_actual_silvergill_cannot_behold_itself_and_free_base_still_pays_additional_mana(self):
        from quorune.rules.behold_additional_cost import BEHOLD_COST_INFORMATION_FIELD
        s=self.session(1116321);source=self.add(s.engine,'Silvergill Mentor',zone='hand');omni=self.add(s.engine,'Omniscience')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-free-cost-card-C')
        self.assert_closed(s,source);self.assert_closed(s,omni);self.prepare(s,mana={'C':2});self.checkpoint(s)
        offer=self.offer(s,source);self.assertTrue(offer['cost_options'])
        self.assertTrue(all('behold_cards' not in o.get('choice_schema',{}) for o in offer['cost_options']))
        option=next(o for o in offer['cost_options'] if o['requirements']['U']==0 and o['requirements']['GENERIC']==2)
        result=s.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertNotIn(BEHOLD_COST_INFORMATION_FIELD,s.state.stack[-1].context)
        self.assertIsNone(self.resolve(s));self.assertEqual(1,len([c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']));self.finish(s,private)

    def test_actual_kinsbaile_other_entry_keeps_its_fixed_until_end_of_turn_sibling(self):
        s=self.session(1116330);source=self.add(s.engine,'Kinsbaile Aspirant',zone='hand')
        chosen=self.add(s.engine,'Goldmeadow Stalwart');bear=self.add(s.engine,'Grizzly Bears',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-aspirant-card-C')
        self.assert_closed(s,source);self.prepare(s,mana={'W':1,'C':1,'G':1});self.checkpoint(s)
        self.cast_beholding(s,source,chosen);self.assertIsNone(self.resolve(s));self.assertEqual(2,s.engine._numeric_stat(source.object_id,'power'))
        self.cast(s,'A',bear);self.assertIsNone(self.resolve(s));self.assertEqual((3,2),tuple(s.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        self.replay(s,load=True);self.until(s,lambda s:s.state.active_player=='B' and s.state.step=='upkeep')
        self.assertEqual((2,1),tuple(s.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')));self.finish(s,private)

    def test_actual_mudbutton_cannot_block_and_dies_destroy_targets_only_low_power_opposing_creatures(self):
        s=self.session(1116340);source=self.add(s.engine,'Mudbutton Cursetosser',zone='hand')
        chosen=self.add(s.engine,'Raging Goblin',zone='hand');wall=self.add(s.engine,'Wall of Omens')
        body=self.add(s.engine,'Grizzly Bears',seat='B');big=self.add(s.engine,'Colossal Dreadmaw',seat='B')
        shock=self.add(s.engine,'Shock',seat='B',zone='hand');self.add(s.engine,'Vedalken Orrery');self.add(s.engine,'Fervor',seat='B')
        self.add(s.engine,'Mountain',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-mudbutton-card-C')
        self.assert_closed(s,source);self.prepare(s,actor='A',active='B',mana={'B':1});s.state.players['B'].mana_pool.update({'R':1});self.checkpoint(s)
        self.cast_beholding(s,source,chosen);self.assertIsNone(self.resolve(s))
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=s.act('pilot:B',{'action_id':'attack','attackers':{body.ref:'A'}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        self.reject(s,'A',{'action_id':'block','blocks':{source.ref:[body.ref]}})
        result=s.act('pilot:A',{'action_id':'block','blocks':{wall.ref:[body.ref]}});self.assertTrue(result.ok,result.summary)
        self.cast(s,'B',shock,[source.ref]);self.resolve_top(s)
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        self.reject(s,'A',{'action_id':'choose','targets':[big.ref]});self.target(s,'A',[body.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('graveyard',s.state.cards[body.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[big.object_id].zone);self.finish(s,private)

    def test_actual_overlapping_behold_spells_keep_shared_kindred_hand_card_revealed_after_lower_spell_is_countered(self):
        s=self.session(1116350);first=self.add(s.engine,'Lys Alana Dignitary',zone='hand',ref='first-behold-spell')
        second=self.add(s.engine,'Lys Alana Dignitary',zone='hand',ref='second-behold-spell');chosen=self.add(s.engine,'Elvish Promenade',zone='hand')
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand');self.add(s.engine,'Vedalken Orrery')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-overlap-card-C')
        self.assert_closed(s,first);self.assert_closed(s,second);self.prepare(s,mana={'C':2,'G':2});s.state.players['B'].mana_pool.update({'U':2});prior_reveal=set(chosen.revealed_to);self.checkpoint(s)
        self.cast_beholding(s,first,chosen,reserve={'G':1});lower=s.state.stack[-1].ref
        self.cast_beholding(s,second,chosen);self.assertEqual(set('ABCD'),set(s.state.cards[chosen.object_id].revealed_to));self.replay(s,load=True)
        self.cast(s,'B',counter,[lower]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone);self.assertEqual(set('ABCD'),set(s.state.cards[chosen.object_id].revealed_to))
        self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[second.object_id].zone)
        self.assertEqual(prior_reveal,set(s.state.cards[chosen.object_id].revealed_to));self.finish(s,private)

    def test_immutable_behold_plan_rejects_changed_permanent_control_before_partial_payment(self):
        from quorune.rules.casting.proposal import build_cast_proposal
        from quorune.rules.casting.model import CastProposalRequest
        from quorune.rules.casting.commit import commit_cast
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        s=self.session(1116360);source=self.add(s.engine,'Caustic Exhale',zone='hand');chosen=self.add(s.engine,'Shivan Dragon')
        target=self.add(s.engine,'Colossal Dreadmaw',seat='B');self.assert_closed(s,source);self.prepare(s,mana={'B':1});self.checkpoint(s)
        command,_=self.behold_command(s,source,chosen,targets=[target.ref]);response={**command,'card':source.ref};response.pop('action_id')
        proposal=build_cast_proposal(s.engine,CastProposalRequest.from_submission('A',response));s.engine.change_control(chosen.object_id,'B')
        before=authoritative_state_hash(s.state)
        with self.assertRaises((GameRuleError,ValueError)),s.engine.transaction():commit_cast(s.engine,proposal,response)
        self.assertEqual(before,authoritative_state_hash(s.state));self.assertEqual('hand',s.state.cards[source.object_id].zone)

    def test_actual_failed_disclosure_rolls_back_and_retry_preserves_principal_and_private_projections(self):
        from quorune.rules.casting import behold_costs as owner
        from quorune.errors import GameRuleError
        s=self.session(1116361);source=self.add(s.engine,'Caustic Exhale',zone='hand');chosen=self.add(s.engine,'Shivan Dragon',zone='hand')
        target=self.add(s.engine,'Colossal Dreadmaw',seat='B');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unseen-rollback-card-C')
        self.assert_closed(s,source);self.prepare(s,mana={'B':1});prior_reveal=set(chosen.revealed_to);self.checkpoint(s);command,_=self.behold_command(s,source,chosen,targets=[target.ref])
        original=owner.begin_stack_cost_hand_reveal
        def failed(*args,**kwargs):original(*args,**kwargs);raise GameRuleError('Injected post-disclosure failure')
        with patch.object(owner,'begin_stack_cost_hand_reveal',side_effect=failed):self.reject(s,'A',command)
        self.assertEqual(prior_reveal,set(s.state.cards[chosen.object_id].revealed_to));self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.cast_beholding(s,source,chosen,targets=[target.ref]);self.assertIsNone(self.resolve(s));self.finish(s,private)

    def test_runtime_behold_authority_is_required_independently_of_caller_annotations(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(1116362);source=self.add(s.engine,'Silvergill Mentor');self.assert_closed(s,source)
        programs=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if BEHOLD_ADDITIONAL_COST_CAPABILITY in p.capability_dependencies]
        self.assertTrue(programs)
        for program in programs:
            deps=tuple(c for c in program.capability_dependencies if c!=BEHOLD_ADDITIONAL_COST_CAPABILITY)
            changed=replace(program,capability_dependencies=deps,coverage=(),capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+BEHOLD_ADDITIONAL_COST_CAPABILITY,bound['blockers'])

    def test_fresh_original_offer_kills_foreign_permanent_control_omission_mutant(self):
        from quorune.rules.casting import behold_costs as owner
        original=owner.object_matches_query
        def witness(seed):
            s=self.session(seed);source=self.add(s.engine,'Caustic Exhale',zone='hand')
            self.add(s.engine,'Shivan Dragon',seat='B');target=self.add(s.engine,'Colossal Dreadmaw',seat='B')
            self.assert_closed(s,source);self.prepare(s,mana={'B':1,'C':1});self.checkpoint(s)
            offer=self.offer(s,source);self.assertTrue(all('behold_cards' not in o.get('choice_schema',{}) for o in offer['cost_options']))
            option=offer['cost_options'][0];result=s.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'targets':[target.ref],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s));self.replay(s,load=True)
        witness(1116370)
        def omitted(row,query):return original(row,replace(query,controller=None))
        with patch.object(owner,'object_matches_query',side_effect=omitted),self.assertRaises(AssertionError):witness(1116371)
