from __future__ import annotations

"""CR118.7a/702: Equip and Cycling selectors retain complete nonmana prices."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_static_land_animation as controls
from quorune.rules.evidence_storage import read_evidence_document


class AbilityKindCostModifierTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.StaticLandAnimationTests.session
    add=controls.StaticLandAnimationTests.add
    prepare=controls.StaticLandAnimationTests.prepare
    checkpoint=controls.StaticLandAnimationTests.checkpoint
    resolve=controls.StaticLandAnimationTests.resolve
    replay=controls.StaticLandAnimationTests.replay
    binding=controls.StaticLandAnimationTests.binding
    priority=controls.StaticLandAnimationTests.priority
    cast=controls.StaticLandAnimationTests.cast
    offered_cast=controls.StaticLandAnimationTests.offered_cast
    private_replay=controls.StaticLandAnimationTests.private_replay
    offered=controls.StaticLandAnimationTests.offered
    advance=controls.StaticLandAnimationTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'ability-kind-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/ability-kind-cost-modifier-cards.json',ROOT/'tests/fixtures/attached-occurrence-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ability kind costs',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_complete_fluctuator_registers_before_actual_cycling(self):
        session=self.session(752200);source=self.add(session.engine,'Fluctuator',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Fluctuator must admit')

    def test_complete_headmaster_and_steelshaper_register_their_independent_siblings(self):
        session=self.session(752201)
        for name in ('Bureau Headmaster','Auriok Steelshaper'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')

    def activation(self,session,card,seat='A'):
        self.priority(session,seat);actions=[a for a in self.offered(session,seat) if a['id'].startswith('activate:'+card.ref+':')]
        self.assertEqual(1,len(actions),actions);return actions[0]

    def test_actual_fluctuator_makes_generic_cycling_zero_but_preserves_discard_and_private_draw(self):
        from quorune.record import authoritative_state_hash
        session=self.session(752202);source=self.add(session.engine,'Fluctuator',zone='hand')
        cycle=self.add(session.engine,'Drifting Meadow',zone='hand');foreign=self.add(session.engine,'Drifting Meadow',seat='B',zone='hand',ref='opponent-cycle')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'C':2});self.checkpoint(session)
        self.offered_cast(session,source,{'C':2});self.assertIsNone(self.resolve(session))
        action=self.activation(session,cycle);before_hand=len(session.state.players['A'].zones['hand']);before=authoritative_state_hash(session.state)
        command={'action_id':action['id'],'pay':'manual','payment':{}}
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[cycle.object_id].zone);self.assertEqual(before_hand-1,len(session.state.players['A'].zones['hand']))
        self.assertEqual('hand',session.state.cards[foreign.object_id].zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(before_hand,len(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_actual_fluctuator_does_not_remove_colored_cycling_price(self):
        from quorune.record import authoritative_state_hash
        session=self.session(752203);self.add(session.engine,'Fluctuator');cycle=self.add(session.engine,'Hieroglyphic Illumination',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'U':1});self.checkpoint(session)
        action=self.activation(session,cycle);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'U':1}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.players['A'].mana_pool.get('U',0));self.assertEqual('graveyard',session.state.cards[cycle.object_id].zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.private_replay(session,private)

    def test_actual_headmaster_discounts_equipment_cast_then_equip_only(self):
        from quorune.record import authoritative_state_hash
        session=self.session(752204);source=self.add(session.engine,'Bureau Headmaster',zone='hand')
        equipment=self.add(session.engine,'Bonesplitter',zone='hand');body=self.add(session.engine,'Generic Bound Body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'R':1,'W':1});self.checkpoint(session)
        self.offered_cast(session,source,{'R':1,'W':1});self.assertIsNone(self.resolve(session))
        self.offered_cast(session,equipment,{});self.assertIsNone(self.resolve(session))
        action=self.activation(session,equipment);command={'action_id':action['id'],'targets':[body.ref],'pay':'manual','payment':{}}
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(session.state.cards[equipment.object_id].attached_to)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(body.object_id,session.state.cards[equipment.object_id].attached_to)
        self.assertEqual((4,6),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.private_replay(session,private)

    def test_actual_steelshaper_equip_discount_exists_before_its_equipped_soldier_anthem(self):
        session=self.session(752205);source=self.add(session.engine,'Auriok Steelshaper',zone='hand')
        equipment=self.add(session.engine,'Bonesplitter');other=self.add(session.engine,'Grizzly Bears')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'W':1,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'W':1,'C':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((1,1),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        action=self.activation(session,equipment);result=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'manual','payment':{}})
        self.assertTrue(result.ok,result.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(source.object_id,session.state.cards[equipment.object_id].attached_to)
        self.assertEqual((4,2),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        self.assertEqual((2,2),(session.engine._numeric_stat(other.object_id,'power'),session.engine._numeric_stat(other.object_id,'toughness')))
        self.private_replay(session,private)

    def test_fluctuator_reduces_typecycling_but_does_not_discount_another_seat(self):
        from quorune.record import authoritative_state_hash
        session=self.session(752206);self.add(session.engine,'Fluctuator')
        source=self.add(session.engine,'Ash Barrens',zone='hand');foreign=self.add(session.engine,'Drifting Meadow',seat='B',zone='hand',ref='unsubsidized-cycle')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session);self.checkpoint(session)
        self.priority(session,'B');self.assertFalse(any(a['id'].startswith('activate:'+foreign.ref+':') for a in self.offered(session,'B')))
        before=authoritative_state_hash(session.state);abilities=[a for a in session.engine._activated_abilities(session.state.cards[foreign.object_id]) if a.discard_source]
        self.assertEqual(1,len(abilities));result=session.act('pilot:B',{'action_id':'activate:'+foreign.ref+':'+abilities[0].ability_id,'pay':'manual','payment':{}})
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(session.state))
        action=self.activation(session,source);result=session.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{}});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);session=self.replay(session,load=True)
        decision=self.resolve(session);self.assertIsNotNone(decision);self.assertNotEqual('priority',decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','search_cards':[]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.private_replay(session,private)

    def test_equip_discount_leaves_unrelated_equipment_and_creature_activation_prices(self):
        from quorune.rules.activation.cost_modifiers import current_activation_cost_modifiers
        from quorune.rules.activation.ability_kinds import activation_matches_cost_scope
        session=self.session(752207);self.add(session.engine,'Bureau Headmaster')
        mentor=self.add(session.engine,'Generic Bound Mentor');mentor.acquired_control_turn_count=-1
        body=self.add(session.engine,'Grizzly Bears');self.prepare(session,mana={'C':1});self.checkpoint(session)
        abilities=[a for a in session.engine._activated_abilities(mentor) if not a.mana_ability]
        self.assertEqual(1,len(abilities));self.assertFalse(activation_matches_cost_scope(session.engine,mentor,abilities[0],'equip'))
        self.assertEqual((),current_activation_cost_modifiers(session.engine,'A',mentor,abilities[0]))
        action=self.activation(session,mentor);result=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'manual','payment':{}})
        self.assertFalse(result.ok,result.summary);result=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'manual','payment':{'C':1}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session));self.assertIn('flying',session.engine._combat_keywords(session.state.cards[body.object_id]));self.replay(session,load=True)

    def test_kind_scope_model_and_whole_sentence_grammar_are_closed(self):
        from dataclasses import replace
        from quorune.compiler.activation_cost_modifier_templates import static_activation_cost_modifier_handler
        from quorune.semantic_runtime.activation_costs import FixedActivationCostModifierHandler
        from quorune.activation_cost_modifiers import FixedActivationCostModifierSpec
        for text,kind,amount in (('Cycling abilities you activate cost {2} less to activate.','cycling',-2),
                ('Equip abilities you activate cost {1} less to activate.','equip',-1),('Equip costs you pay cost {1} less.','equip',-1)):
            compiled=static_activation_cost_modifier_handler(text);self.assertIsNotNone(compiled)
            spec=FixedActivationCostModifierHandler().validate(compiled[1]);self.assertEqual((kind,amount),(spec.ability_scope,spec.generic_adjustment))
            self.assertEqual(spec,FixedActivationCostModifierSpec.from_dict(spec.to_dict()))
            with self.assertRaises(ValueError):replace(spec,ability_scope='keyword_named_by_player')
        for text in ('Cycling abilities you activate cost {U} less to activate.','Equip abilities you activate that target this creature cost {1} less to activate.',
                'Equip abilities you activate of other Equipment cost {1} less to activate.','Cycling abilities you activate cost {2} less to activate. Draw a card.'):
            self.assertIsNone(static_activation_cost_modifier_handler(text),text)

    def test_cycling_kind_requires_exact_trusted_descriptor_not_similar_hand_activation(self):
        from dataclasses import replace
        from quorune.rules.activation.ability_kinds import activation_matches_cost_scope
        session=self.session(752208);source=self.add(session.engine,'Drifting Meadow',zone='hand')
        abilities=[a for a in session.engine._activated_abilities(source) if a.discard_source]
        self.assertEqual(1,len(abilities));ability=abilities[0]
        self.assertTrue(activation_matches_cost_scope(session.engine,source,ability,'cycling'))
        self.assertFalse(activation_matches_cost_scope(session.engine,source,replace(ability,discard_source=False),'cycling'))
        self.assertFalse(activation_matches_cost_scope(session.engine,source,replace(ability,builtin_semantic_key='builtin:equip'),'cycling'))

    def test_current_source_departure_revalidates_zero_cost_cycling_before_discard(self):
        from quorune.record import authoritative_state_hash
        session=self.session(752209);modifier=self.add(session.engine,'Fluctuator');source=self.add(session.engine,'Drifting Meadow',zone='hand')
        self.prepare(session);self.checkpoint(session);action=self.activation(session,source)
        session.engine.move_card(modifier.object_id,'graveyard',log=False);self.checkpoint(session)
        before=authoritative_state_hash(session.state);result=session.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{}})
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual('hand',session.state.cards[source.object_id].zone)

    def test_static_cost_owner_is_an_independent_runtime_minimum_for_each_original(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name in ('Fluctuator','Bureau Headmaster','Auriok Steelshaper'):
            session=self.session(752210);source=self.add(session.engine,name,zone='hand')
            programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(d.get('handler_id')=='modification.activation-cost.fixed-generic.v1' for d in p.handlers)]
            self.assertEqual(1,len(programs));program=programs[0];cap='activation.mana_cost.static_generic'
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            binding=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict'],name);self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])

    def test_canonically_verified_cost_owner_withdrawal_blocks_all_three_originals(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw);cap='activation.mana_cost.static_generic'
        next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn ability-kind cost authority'])
        registry=CapabilityRegistry(changed);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Fluctuator','Bureau Headmaster','Auriok Steelshaper'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_whole_original_producer_kind_filter_and_current_modifier_omissions_are_killed(self):
        from unittest.mock import patch
        import quorune.rules.activation.ability_kinds as kinds
        original=kinds.activation_matches_cost_scope
        def no_cycling(host,source,ability,scope):return False if scope=='cycling' else original(host,source,ability,scope)
        cases=(('quorune.compiler.runtime_templates.static_activation_cost_modifier_handler',lambda *a:None,
                'test_complete_fluctuator_registers_before_actual_cycling'),
            ('quorune.rules.activation.cost_modifiers.activation_matches_cost_scope',no_cycling,
                'test_actual_fluctuator_makes_generic_cycling_zero_but_preserves_discard_and_private_draw'),
            ('quorune.rules.activation.pricing.current_activation_cost_modifiers',lambda *a:(),
                'test_actual_headmaster_discounts_equipment_cast_then_equip_only'))
        for target,replacement,method in cases:
            with patch(target,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
