from __future__ import annotations

"""CR 107.3a/601.2b-h: one declared X links hand price and target count."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_attached_protection_exceptions as controls
import test_qualified_hexproof as target_controls
import test_random_discard_activation_costs as privacy_controls
from quorune.rules.evidence_storage import read_evidence_document


class AnnouncedHandDiscardCostTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachedProtectionExceptionTests.session
    add=controls.AttachedProtectionExceptionTests.add
    prepare=controls.AttachedProtectionExceptionTests.prepare
    checkpoint=controls.AttachedProtectionExceptionTests.checkpoint
    resolve=controls.AttachedProtectionExceptionTests.resolve
    replay=controls.AttachedProtectionExceptionTests.replay
    binding=controls.AttachedProtectionExceptionTests.binding
    priority=controls.AttachedProtectionExceptionTests.priority
    cast=controls.AttachedProtectionExceptionTests.cast
    actions=controls.AttachedProtectionExceptionTests.actions
    private_replay=controls.AttachedProtectionExceptionTests.private_replay
    top=target_controls.QualifiedHexproofTests.top
    assert_hand_private=privacy_controls.RandomDiscardActivationRuntimeTests.assert_hand_private

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'announced-hand-discard.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/announced-hand-discard-cards.json',ROOT/'tests/fixtures/announced-x-additional-cost-cards.json',
            ROOT/'tests/fixtures/fixed-resolution-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Announced hand price',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_aether_tide_pays_two_creatures_and_returns_two_distinct_opponent_targets(self):
        session=self.session(750700);source=self.add(session.engine,'Aether Tide',zone='hand')
        costs=[self.add(session.engine,'Grizzly Bears',zone='hand',ref='creature-price-'+str(i)) for i in range(2)]
        targets=[self.add(session.engine,'Grizzly Bears',seat=seat,ref='returned-target-'+seat) for seat in 'BC']
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-announced-hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Aether Tide must admit')
        self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        original=len(session.state.players['A'].zones['hand'])
        command={'action_id':'cast:'+source.ref,'x':2,'discard_cards':[c.ref for c in costs],
            'targets':[c.ref for c in targets],'pay':'manual','payment':{'U':1,'C':2}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.stack[-1].x_value)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
        self.assertEqual(original-3,len(session.state.players['A'].zones['hand']))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in targets))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in targets))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def arrange(self,name,seed,*,count=2,replacements=False):
        color,kind={'Aether Tide':('U','Grizzly Bears'),'Scorched Earth':('R','Generic Bound Plains')}[name]
        session=self.session(seed);source=self.add(session.engine,name,zone='hand')
        if count==0:
            for oid in tuple(session.state.players['A'].zones['hand']):
                if oid!=source.object_id:session.engine.move_card(oid,'library',reason='fixture zero-cost hand boundary')
        costs=[self.add(session.engine,kind,zone='hand',ref='declared-price-'+str(i)) for i in range(count)]
        targets=[self.add(session.engine,kind,seat=seat,ref='declared-target-'+seat) for seat in 'BC'[:count]]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='declared-private-hand')
        if replacements:
            for seat,label in (('B','Generic Payment Destination Replacement'),('C','Generic Payment Competing Replacement')):
                control=self.add(session.engine,label,seat=seat);self.assertTrue(self.binding(session,control)['strict_capability_ready'])
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        mana={color:1,**({'C':count} if count else {})};self.prepare(session,mana=mana);self.checkpoint(session)
        command={'action_id':'cast:'+source.ref,'x':count,'discard_cards':[c.ref for c in costs],
            'targets':[c.ref for c in targets],'pay':'manual','payment':mana}
        return session,source,costs,targets,private,command

    def test_actual_scorched_earth_pays_land_cards_before_destroying_original_land_targets(self):
        session,source,costs,targets,private,command=self.arrange('Scorched Earth',750710)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.stack[-1].x_value)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in targets))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in targets))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.private_replay(session,private)

    def test_wrong_types_owners_cardinalities_and_malformed_x_reject_before_payment(self):
        session,source,costs,targets,private,command=self.arrange('Aether Tide',750720)
        own_land=next(session.state.cards[oid] for oid in session.state.players['A'].zones['hand'] if oid not in {c.object_id for c in (source,*costs)})
        foreign=self.add(session.engine,'Grizzly Bears',seat='C',zone='hand',ref='foreign-declared-cost')
        self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        for change in ({'x':True},{'x':-1},{'x':3},{'discard_cards':[costs[0].ref]},
                {'discard_cards':[costs[0].ref,costs[0].ref]},{'discard_cards':[costs[0].ref,own_land.ref]},
                {'discard_cards':[costs[0].ref,foreign.ref]},{'discard_cards':[costs[0].ref,source.ref]},
                {'targets':[targets[0].ref]},{'targets':[targets[0].ref,targets[0].ref]},{'payment':{'U':1}}):
            result=session.act('pilot:A',{**command,**change});self.assertFalse(result.ok,change)
            self.assertEqual(before,authoritative_state_hash(session.state))
        action=next(row for row in self.actions(session) if row['id']==command['action_id'])
        option=next(row for row in action['cost_options'] if row['id']=='normal')
        field=option['choice_schema']['discard_cards'];self.assertEqual('x',field['count_from'])
        self.assertTrue({c.ref for c in costs}<=set(field['legal_refs']))
        self.assertNotIn(own_land.ref,field['legal_refs']);self.assertNotIn(foreign.ref,field['legal_refs'])
        self.private_replay(session,private)

    def test_both_original_zero_x_casts_need_neither_cost_cards_nor_targets(self):
        for index,name in enumerate(('Aether Tide','Scorched Earth')):
            with self.subTest(name=name):
                session,source,costs,targets,private,command=self.arrange(name,750730+index,count=0)
                command.pop('discard_cards');command.pop('targets')
                self.assertTrue(any(r['id']==command['action_id'] for r in self.actions(session)))
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual(0,session.state.stack[-1].x_value);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.private_replay(session,private)

    def test_countering_keeps_the_declared_discards_and_mana_paid(self):
        session,source,costs,targets,private,command=self.arrange('Aether Tide',750740,count=1)
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        session.state.players['B'].mana_pool['U']=2;self.prepare(session,mana={'U':1,'C':1});self.checkpoint(session)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref],{'U':2})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(('graveyard','graveyard','battlefield'),tuple(session.state.cards[c.object_id].zone for c in (source,costs[0],targets[0])))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_partial_and_all_target_departure_do_not_change_or_refund_the_price(self):
        for index,(name,count) in enumerate((('Scorched Earth',2),('Aether Tide',1))):
            with self.subTest(name=name):
                session,source,costs,targets,private,command=self.arrange(name,750750+index,count=count)
                bounce=self.add(session.engine,'Boomerang',seat='B',zone='hand')
                session.state.players['B'].mana_pool['U']=2;self.prepare(session,mana=command['payment']);self.checkpoint(session)
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.cast(session,'B',bounce,[targets[0].ref],{'U':2});self.top(session)
                self.assertEqual('hand',session.state.cards[targets[0].object_id].zone)
                self.assertEqual(count,session.state.stack[-1].x_value)
                if count==2:self.assertEqual('battlefield',session.state.cards[targets[1].object_id].zone)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual('hand',session.state.cards[targets[0].object_id].zone)
                if count==2:self.assertEqual('graveyard',session.state.cards[targets[1].object_id].zone)
                self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
                self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_two_declared_discards_keep_prior_destination_choices_across_pending_load(self):
        session,source,costs,targets,private,command=self.arrange('Aether Tide',750760,replacements=True)
        original=set(session.state.players['A'].zones['hand']);result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        choices=0
        while session.state.pending_decision is not None and session.state.pending_decision.kind=='replacement.order':
            self.assertLess(choices,6);decision=session.state.pending_decision
            self.assertEqual('priority_action_cost',decision.continuation['replacement_resume_kind'])
            self.assertEqual(2,decision.continuation['priority_response']['x'])
            self.assertEqual([c.ref for c in costs],decision.continuation['priority_response']['discard_cards'])
            self.assertEqual(original,set(session.state.players['A'].zones['hand']))
            self.assertEqual(3,sum(session.state.players['A'].mana_pool.values()))
            session=self.replay(session,load=True);option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
            before=authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:B',{'action_id':'choose','replacement':option}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            result=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary);choices+=1
        self.assertEqual(2,choices)
        for cost in costs:
            self.assertEqual('exile',session.state.cards[cost.object_id].zone)
            self.assertEqual(1,sum(session.state.cards[cost.object_id].counters.values()))
        self.assertEqual(2,session.state.stack[-1].x_value);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('replacement.order',self.resolve(session).kind)
        option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        result=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in targets))
        self.private_replay(session,private)

    def test_closed_discard_parameter_keeps_old_payloads_and_rejects_unowned_shapes(self):
        from dataclasses import replace
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost,announced_zone_cost_capabilities,ANNOUNCED_X_DISCARD_CAPABILITY
        from quorune.compiler.spell_additional_cost_templates import announced_x_additional_cost_template,fixed_zone_change_additional_cost_template
        for types in ((),('creature',),('land',)):
            cost=FixedZoneChangeAdditionalCost(operation='discard_one',choice_field='discard_cards',
                predicate=ObjectQuerySpec(zones=('hand',),owner='$actor',known_to_actor=True,types_all=types),schema_version=3,count=None,count_parameter='x')
            self.assertEqual(cost,FixedZoneChangeAdditionalCost.from_descriptor(cost.to_descriptor()))
            self.assertEqual((ANNOUNCED_X_DISCARD_CAPABILITY,),announced_zone_cost_capabilities(cost))
            self.assertEqual(0,cost.required_count(0));self.assertEqual(9,cost.required_count(9))
        for change in ({'count':1},{'count_parameter':'mana_spent'},{'schema_version':True},{'operation':'sacrifice_one'},
                       {'predicate':ObjectQuerySpec(zones=('hand',),owner='$actor',known_to_actor=True,colors_all=('U',))}):
            with self.assertRaises(ValueError):replace(cost,**change)
        for value in (True,-1,1.5,'2'):
            with self.assertRaises(ValueError):cost.required_count(value)
        text='As an additional cost to cast this spell, discard X creature cards.'
        self.assertIsNotNone(announced_x_additional_cost_template(text));self.assertIsNone(fixed_zone_change_additional_cost_template(text))
        for bad in (text.replace('X creature cards','X cards at random'),text.replace('X creature cards','X cards with different names'),
                    text.replace('discard','you may discard'),text.replace('discard X creature cards','sacrifice X creatures')):
            self.assertIsNone(announced_x_additional_cost_template(bad))
        old=FixedZoneChangeAdditionalCost(operation='exile_one_from_graveyard',choice_field='exile_cards',
            predicate=ObjectQuerySpec(zones=('graveyard',),owner='$actor',known_to_actor=True),schema_version=3,count=None,count_parameter='x')
        self.assertEqual({'schema_version','kind','operation','choice_field','predicate','count_parameter'},set(old.to_descriptor()))
        self.assertEqual(('casting.additional_cost.announced_x_graveyard_exile',),announced_zone_cost_capabilities(old))

    def test_unrestricted_hand_control_excludes_the_source_and_declares_x_without_mana_x(self):
        for index,has_other in enumerate((False,True)):
            with self.subTest(has_other=has_other):
                session=self.session(750780+index);source=self.add(session.engine,'Generic Announced Hand Witness',zone='hand')
                for oid in tuple(session.state.players['A'].zones['hand']):
                    if oid!=source.object_id:session.engine.move_card(oid,'library',reason='fixture unrestricted hand boundary')
                other=self.add(session.engine,'Grizzly Bears',zone='hand') if has_other else None
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                self.prepare(session,mana={'U':1});self.checkpoint(session)
                action=next(r for r in self.actions(session) if r['id']=='cast:'+source.ref)
                option=next(r for r in action['cost_options'] if r['id']=='normal')
                self.assertEqual(int(has_other),option['choice_schema']['x']['maximum'])
                self.assertNotIn(source.ref,option['choice_schema']['discard_cards']['legal_refs'])
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:A',{'action_id':action['id'],'x':1,'discard_cards':[source.ref],'pay':'auto'}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                command={'action_id':action['id'],'x':int(has_other),'discard_cards':[other.ref] if other else [],
                    'pay':'manual','payment':{'U':1}}
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual(int(has_other),session.state.stack[-1].x_value)
                if other:self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual(1,len(session.state.players['A'].zones['hand']))
                self.assert_hand_private(session,'A',session.state.players['A'].zones['hand']);self.private_replay(session,private)

    def test_verified_declared_discard_and_result_owner_withdrawals_and_runtime_minimum(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.casting_additional_costs import ANNOUNCED_X_DISCARD_CAPABILITY as cap
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Aether Tide'),capability_registry=verified(raw),capability_profile='commander_review').status)
        for dependency in (cap,'casting.additional_cost.zone_change.fixed_discard','target.cardinality.mana_announced_x'):
            value=json.loads(json.dumps(raw));next(r for r in value['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['withdrawn linked hand price owner'])
            ir=compile_oracle_card(self.db.lookup('Aether Tide'),capability_registry=verified(value),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);blockers=[b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+dependency+':blocked' in b for b in blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(750790);source=self.add(session.engine,'Aether Tide',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        result=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_fresh_actual_producer_count_binding_and_discard_commit_omissions_are_killed(self):
        from quorune.compiler.spell_additional_cost_nodes import announced_x_additional_cost_template
        from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
        original_count=FixedZoneChangeAdditionalCost.required_count
        def omitted(text):return None if 'discard X' in text else announced_x_additional_cost_template(text)
        def wrong_count(cost,x=None):return 1 if cost.operation=='discard_one' and cost.count_parameter=='x' else original_count(cost,x)
        method='test_actual_aether_tide_pays_two_creatures_and_returns_two_distinct_opponent_targets'
        for owner,mutation in (('quorune.compiler.spell_additional_cost_nodes.announced_x_additional_cost_template',omitted),
                               ('quorune.rules.casting_additional_costs.FixedZoneChangeAdditionalCost.required_count',wrong_count),
                               ('quorune.rules.casting.commit._fixed_zone_change_commit_entries',lambda *a,**k:[])):
            with patch(owner,mutation):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
