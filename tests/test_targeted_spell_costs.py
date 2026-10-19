from __future__ import annotations

from pathlib import Path
import json,tempfile,unittest
from common import ROOT
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.targeted_spell_costs import TargetedSpellCostSpec,TargetedCostSubject
from test_granted_skulk import record
import test_fixed_reveal_additional_costs as reveal


class TargetedSpellCostCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def test_self_and_commander_targeted_costs_have_closed_source_spanned_components(self):
        for subject in ('this creature','one or more commanders you control'):
            text=f'Spells your opponents cast that target {subject} cost {{2}} more to cast.'
            ir=compile_oracle_card(record(text,'Creature'),capability_registry=self.registry)
            self.assertEqual('exact',ir.status,ir.material_residuals);node,=ir.faces[0].nodes
            self.assertEqual(text,node.text);self.assertIn('casting.cost.targeted_fixed_generic',node.capability_dependencies)
            self.assertEqual(2,node.handlers[0]['modifier']['generic'])

    def test_unknown_target_taxes_and_malformed_cost_specs_remain_closed(self):
        text='Spells your opponents cast that target this creature cost {2} more to cast.'
        for variant in (text.replace('this creature','a Merfolk you control'),text.replace('{2}','{U}'),
                text.replace('Spells','Abilities').replace('cast','activate'),text.replace('more','less')):
            self.assertNotEqual('exact',compile_oracle_card(record(variant,'Creature'),capability_registry=self.registry).status)
        raw=TargetedSpellCostSpec(TargetedCostSubject.SOURCE,2).to_dict()
        for change in ({'schema_version':True},{'generic':True},{'generic':0},{'subject':'all_creatures'},{'extra':1}):
            with self.assertRaises(ValueError):TargetedSpellCostSpec.from_dict({**raw,**change})
        from quorune.targeted_spell_costs import validate_targeted_spell_cost_fact
        value={'schema_version':1,'generic':2,'sources':[{'component_id':'source','source_ref':'A01','source_identity':'A01@1','controller':'A','generic':2}]}
        self.assertEqual(2,validate_targeted_spell_cost_fact(value))
        for change in ({'schema_version':True},{'generic':True},{'generic':1},{'sources':value['sources']*2},{'extra':1}):
            with self.assertRaises(ValueError):validate_targeted_spell_cost_fact({**value,**change})


class TargetedSpellCostRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=reveal.FixedRevealCostRuntimeTests.session
    add=reveal.FixedRevealCostRuntimeTests.add
    fund=reveal.FixedRevealCostRuntimeTests.fund
    checkpoint=reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve=reveal.FixedRevealCostRuntimeTests.resolve
    replay=reveal.FixedRevealCostRuntimeTests.replay
    reject=reveal.FixedRevealCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'targeted-cost.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/targeted-spell-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Targeted cost witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_boreal_tax_rejects_unpaid_target_and_preserves_an_untaxed_legal_offer(self):
        session=self.session(6012);target=self.add(session.engine,'Boreal Elemental',ref='actual-boreal')
        other=self.add(session.engine,'Generic Bound Body',ref='untaxed-target')
        spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='opponent-unsummon')
        self.fund(session,{'U':1},actor='B')
        action=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
        self.assertIn(other.ref,action['target_schema']['legal_refs']);self.assertNotIn(target.ref,action['target_schema']['legal_refs'])
        self.reject(session,'B',action_id=action['id'],targets=[target.ref],pay='auto')
        result=session.act('pilot:B',{'action_id':action['id'],'targets':[other.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('hand',session.state.cards[other.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone);self.replay(session,load=True)

    def test_paid_boreal_tax_is_a_casting_cost_and_has_no_ward_choice(self):
        session=self.session(6013);target=self.add(session.engine,'Boreal Elemental',ref='paid-boreal')
        spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='paid-unsummon')
        private=self.add(session.engine,'Generic Bound Growth',seat='B',zone='hand',ref='tax-private')
        self.fund(session,{'U':1,'C':2},actor='B')
        result=session.act('pilot:B',{'action_id':'cast:'+spell.ref,'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.players['B'].mana_pool['C']);self.assertEqual(1,len(session.state.stack))
        for viewer in 'ACD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[target.object_id].zone);self.replay(session,load=True)

    def test_esior_taxes_one_or_more_commanders_once_and_independent_sources_stack(self):
        for copies in (1,2):
            with self.subTest(copies=copies):
                session=self.session(6014+copies)
                for i in range(copies):self.add(session.engine,'Esior, Wardwing Familiar' if i==0 else 'Generic Commander Target Tax',ref='esior-'+str(i))
                commanders=[]
                for seat in 'AB':
                    oid=session.state.players[seat].zones['command'][0]
                    if seat=='B':
                        record=self.db.lookup('Isamaru, Hound of Konda');card=session.state.cards[oid];card.oracle_id=record.oracle_id;card.printed_name=record.name
                    session.engine.move_card(oid,'battlefield',controller='A',log=False)
                    commanders.append(session.state.cards[oid])
                # The foreign-owned commander is taxed by its current controller's Esior.
                spell=self.add(session.engine,'Generic Tax Two Targets',seat='C',zone='hand',ref='two-commanders')
                self.fund(session,{'U':1,'C':3*copies},actor='C')
                result=session.act('pilot:C',{'action_id':'cast:'+spell.ref,'targets':[c.ref for c in commanders],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertEqual(0,session.state.players['C'].mana_pool['C'])
                session=self.replay(session,load=True)
                while (decision:=self.resolve(session)) is not None:
                    self.assertEqual('replacement.order',decision.kind)
                    principal=session.pending_principals()[0];ctx=session.packet(principal,full=True)['decision']['ctx']
                    option=next(row['id'] for row in ctx['options'] if row['decline'])
                    result=session.act(principal,{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
                self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in commanders));self.replay(session,load=True)

    def test_current_controller_phase_and_ability_removal_determine_future_taxes(self):
        for boundary in ('own','phased','humility'):
            with self.subTest(boundary=boundary):
                session=self.session(6020);target=self.add(session.engine,'Boreal Elemental',ref='current-tax-source')
                spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='current-tax-spell')
                if boundary=='own':session.engine.change_control(target.object_id,'B',reason='Targeted cost source controller boundary')
                elif boundary=='phased':target.phased_out=True
                else:self.add(session.engine,'Humility',ref='tax-humility')
                other=self.add(session.engine,'Generic Bound Body',ref='current-untaxed')
                self.fund(session,{'U':1},actor='B')
                action=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
                chosen=other if boundary=='phased' else target
                result=session.act('pilot:B',{'action_id':action['id'],'targets':[chosen.ref],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_increases_precede_ordinary_reductions_and_free_base_retains_the_tax(self):
        from quorune.rules.casting.costs import build_cast_cost_options
        from quorune.rules.casting.proposal import _spell_semantic_key
        session=self.session(6021);target=self.add(session.engine,'Boreal Elemental',ref='reduced-tax-target')
        spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='reduced-tax-spell')
        self.add(session.engine,'Generic Casting Reveal Reducer',seat='B',ref='tax-reducer');self.fund(session,{'U':1},actor='B')
        result=session.act('pilot:B',{'action_id':'cast:'+spell.ref,'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)
        session=self.session(6022);target=self.add(session.engine,'Boreal Elemental',ref='free-tax-target')
        spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='free-tax-spell');self.fund(session,{'C':2},actor='B')
        program=session.engine.semantics.get(_spell_semantic_key(self.db.lookup('Unsummon'),None))
        options=build_cast_cost_options(session.engine,'B',spell,program,response={'targets':[target.ref]},hint=False,force_without_mana_cost=True)
        self.assertEqual(1,len(options));self.assertEqual(2,options[0].to_dict()['requirements']['GENERIC'])
        self.assertEqual(0,options[0].to_dict()['requirements']['U'])

    def test_all_five_original_tax_cards_have_complete_capability_bindings(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        for name in ('Sphinx of New Prahv','Boreal Elemental','Syr Elenora, the Discerning','Charix, the Raging Isle','Esior, Wardwing Familiar'):
            with self.subTest(name=name):
                program=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_paid_target_tax_survives_pending_mana_replacement_with_private_loaded_replay(self):
        from quorune.damage_prevention_creation import PreventionShieldCreationRequest,PreventionSubjectAllocation,plan_prevention_shield_creation,commit_prevention_shield_creation
        from quorune.damage_modifier_state import PreventionMode,DamageModifierDuration
        session=self.session(6023);target=self.add(session.engine,'Boreal Elemental',ref='pending-tax-target')
        spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='pending-tax-spell')
        reef=self.add(session.engine,'Shivan Reef',seat='B',ref='pending-tax-reef')
        for label in ('one','two'):
            request=PreventionShieldCreationRequest(source_id='fixture:tax-prevent:'+label,controller='B',mode=PreventionMode.AMOUNT,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,subjects=(PreventionSubjectAllocation('B',1),),label='Prevent '+label)
            commit_prevention_shield_creation(session.engine,plan_prevention_shield_creation(session.engine,request))
        self.fund(session,{'C':2},actor='B')
        result=session.act('pilot:B',{'action_id':'cast:'+spell.ref,'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual(2,session.state.players['B'].mana_pool['C']);self.assertFalse(session.state.cards[reef.object_id].tapped)
        session=self.replay(session,load=True);ctx=session.packet('pilot:B',full=True)['decision']['ctx']
        self.reject(session,'A',action_id='choose',replacement=ctx['options'][0]['id'])
        result=session.act('pilot:B',{'action_id':'choose','replacement':ctx['options'][0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.players['B'].mana_pool['C']);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[target.object_id].zone);self.replay(session,load=True)

    def test_tax_cost_locks_before_mana_payment_changes_its_source(self):
        session=self.session(6024);target=self.add(session.engine,'Boreal Elemental',ref='locked-tax-source')
        spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='locked-tax-spell')
        self.fund(session,{'U':1,'C':2},actor='B');original=session.engine._pay_for_cost
        def pay_then_lose_tax(*args,**kwargs):
            result=original(*args,**kwargs);session.engine.move_card(target.object_id,'exile',log=False);return result
        session.engine._pay_for_cost=pay_then_lose_tax
        result=session.act('pilot:B',{'action_id':'cast:'+spell.ref,'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(0,session.state.players['B'].mana_pool['C'])
        self.assertEqual('exile',session.state.cards[target.object_id].zone)

    def test_modal_untargeted_branch_and_aura_target_share_the_same_tax_contract(self):
        session=self.session(6025);target=self.add(session.engine,'Boreal Elemental',ref='modal-tax-source')
        spell=self.add(session.engine,'Generic Tax Modal',seat='B',zone='hand',ref='modal-tax-spell')
        self.fund(session,{'U':1},actor='B')
        action=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
        schema=action['target_schema'];mode=next(mode for mode in schema['legal_modes'] if not schema['mode_schemas'][mode]['groups'])
        hand=len(session.state.players['B'].zones['hand'])
        result=session.act('pilot:B',{'action_id':action['id'],'modes':[mode],'targets':[],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand,len(session.state.players['B'].zones['hand']));self.replay(session,load=True)
        session=self.session(6026);target=self.add(session.engine,'Boreal Elemental',ref='aura-tax-source')
        aura=self.add(session.engine,'Pacifism',seat='B',zone='hand',ref='taxed-aura')
        self.fund(session,{'W':1,'C':3},actor='B')
        session.engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.active_player='B'
        session.engine._grant_priority('B');session.engine.pump();self.checkpoint(session)
        result=session.act('pilot:B',{'action_id':'cast:'+aura.ref,'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(0,session.state.players['B'].mana_pool['C'])
        self.assertIsNone(self.resolve(session));self.assertEqual(target.object_id,session.state.cards[aura.object_id].attached_to)
        self.replay(session,load=True)

    def test_two_target_price_table_describes_combined_sources_and_reduced_requirements(self):
        session=self.session(6027)
        targets=[self.add(session.engine,name,ref='priced-'+str(i)) for i,name in enumerate(('Boreal Elemental','Sphinx of New Prahv'))]
        spell=self.add(session.engine,'Generic Tax Two Targets',seat='B',zone='hand',ref='priced-two-targets')
        self.fund(session,{'U':1,'C':4},actor='B')
        action=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
        option,=action['cost_options'];costs=option['target_costs']
        self.assertEqual({0,2,4},{row['requirements']['GENERIC'] for row in costs['prices']})
        self.assertIn('Pay {4}{U}',{row['label'] for row in costs['prices']})
        from quorune.choice_forms import build_action_form
        form=build_action_form(action,decision_kind='priority',context={})
        field=next(field for field in form['variants']['options'][0]['fields'] if field['control']=='targets')
        self.assertEqual(costs,field['target_costs'])
        result=session.act('pilot:B',{'action_id':action['id'],'targets':[card.ref for card in targets],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(0,session.state.players['B'].mana_pool['C'])
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_original_tax_cards_keep_native_entry_characteristics_and_charix_activation(self):
        for index,name in enumerate(('Syr Elenora, the Discerning','Sphinx of New Prahv','Esior, Wardwing Familiar')):
            with self.subTest(name=name):
                session=self.session(6030+index);source=self.add(session.engine,name,zone='hand',ref='original-tax-body')
                self.fund(session,{'W':4,'U':4,'C':5});hand=len(session.state.players['A'].zones['hand'])
                result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session));card=session.state.cards[source.object_id]
                self.assertEqual('battlefield',card.zone)
                if index==0:
                    self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
                    self.assertEqual(hand,session.engine._numeric_stat(card.object_id,'power'))
                else:
                    self.assertTrue(({'flying','vigilance'} if index==1 else {'flying'}).issubset(session.engine._combat_keywords(card)))
                self.replay(session,load=True)
        session=self.session(6033);source=self.add(session.engine,'Charix, the Raging Isle',ref='original-charix')
        for i in range(2):self.add(session.engine,'Island',ref='charix-island-'+str(i))
        self.fund(session,{'C':3})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual((2,15),tuple(session.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
        self.replay(session,load=True)

    def test_targeted_spell_cost_omission_and_dependency_mutants_are_killed(self):
        from unittest.mock import patch
        from quorune.rules.capabilities import CapabilityRegistry
        text='Spells your opponents cast that target this creature cost {2} more to cast.'
        with patch('quorune.compiler.targeted_spell_costs.targeted_spell_cost_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),capability_registry=self.registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in ('casting.cost.targeted_fixed_generic','casting.cost.modifier.fixed_query','target.revalidate_resolution'):
            changed=json.loads(json.dumps(raw));next(c for c in changed['capabilities'] if c['id']==dependency).update(status='blocked',blockers=['Targetedspellcostdependencyomitted'])
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),capability_registry=CapabilityRegistry(changed)).status)
        for target,method in (('quorune.rules.casting.targeted_costs.apply_target_cost_increase','test_paid_boreal_tax_is_a_casting_cost_and_has_no_ward_choice'),
                ('quorune.rules.casting.targeted_costs.public_payable_target_schema','test_actual_boreal_tax_rejects_unpaid_target_and_preserves_an_untaxed_legal_offer')):
            if target.endswith('public_payable_target_schema'):
                replacement=lambda schema,option:schema
            else:replacement=lambda option:None
            with patch(target,side_effect=replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_copied_tax_source_uses_copied_ability_but_its_current_controller_and_identity(self):
        session=self.session(6034);original=self.add(session.engine,'Boreal Elemental',seat='C',ref='copy-tax-original')
        copier=self.add(session.engine,'Clone',zone='hand',ref='copy-tax-clone');self.fund(session,{'U':1,'C':3})
        result=session.act('pilot:A',{'action_id':'cast:'+copier.ref,'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',self.resolve(session).kind)
        ctx=session.packet('pilot:A',full=True)['decision']['ctx'];option=next(row['id'] for row in ctx['options'] if original.ref in row['id'] and not row['decline'])
        result=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)
        spell=self.add(session.engine,'Unsummon',seat='B',zone='hand',ref='copy-tax-spell');self.fund(session,{'U':1},actor='B')
        self.reject(session,'B',action_id='cast:'+spell.ref,targets=[copier.ref],pay='auto')
        self.fund(session,{'U':1,'C':2},actor='B')
        result=session.act('pilot:B',{'action_id':'cast:'+spell.ref,'targets':[copier.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.players['B'].mana_pool['C']);self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_opponent_activation_targeting_tax_source_keeps_its_ordinary_price(self):
        session=self.session(6035);target=self.add(session.engine,'Boreal Elemental',ref='activation-tax-target')
        source=self.add(session.engine,'Generic Targeted Tax Activation Control',seat='B',ref='untaxed-activation-source')
        self.fund(session,{'U':1},actor='B')
        action=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:B',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('hand',session.state.cards[target.object_id].zone);self.replay(session,load=True)

    def test_same_printed_tax_sources_keep_distinct_physical_cost_components(self):
        session=self.session(6036)
        targets=[self.add(session.engine,'Boreal Elemental',ref='same-tax-'+str(i)) for i in range(2)]
        spell=self.add(session.engine,'Generic Tax Two Targets',seat='B',zone='hand',ref='same-tax-spell')
        self.fund(session,{'U':1,'C':4},actor='B')
        action=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
        clauses=action['cost_options'][0]['target_costs']['clauses'];self.assertEqual(2,len({row['component_id'] for row in clauses}))
        result=session.act('pilot:B',{'action_id':action['id'],'targets':[card.ref for card in targets],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(0,session.state.players['B'].mana_pool['C'])
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)
