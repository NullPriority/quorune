from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.object_predicate import ObjectQuerySpec
from quorune.semantic_runtime.conditional_continuous import _validate_target
from test_fixed_direct_target_predicates import record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from common import ROOT
from scripts.build_test_database import build_fixture_database
import test_mixed_permanent_targets as native_mixed
import test_qualified_attack_subjects as qualified_attacks
import test_ring_native_actions as turns


class ConditionalReceiverUnionModelTests(unittest.TestCase):
    def descriptor(self):
        source=record('As long as there are four or more card types among cards in your graveyard, '
            'Insects and Spiders you control get +1/+1 and have deathtouch.',suffix=596001,type_line='Enchantment')
        ir=compile_oracle_card(source,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertFalse(ir.material_residuals)
        node=ir.faces[0].nodes[0]
        self.assertIn('state_query.permanent.receiver_union',node.capability_dependencies)
        return node.handlers[0]['target']

    def test_compiler_produced_conditional_union_is_accepted_by_its_registered_owner(self):
        target=self.descriptor()
        result=_validate_target(target)
        self.assertIsNotNone(result)

    def test_conditional_union_keeps_reserved_scope_and_template_references_closed(self):
        target=self.descriptor();query=ObjectQuerySpec.from_dict(target['predicate'])
        for fields in ({'owner':'A'},{'controller':'A'},{'excluded_controllers':('B',)},{'exclude_ref':'physical:foreign'}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                _validate_target({**target,'predicate':replace(query,**fields).to_dict()})
        branch=replace(query.any_of[0],exclude_ref='physical:foreign')
        with self.assertRaises(ValueError):
            _validate_target({**target,'predicate':replace(query,any_of=(branch,query.any_of[1])).to_dict()})

    def test_branch_source_placeholder_uses_existing_live_union_binding(self):
        from quorune.semantic_runtime.continuous_components import _fixed_query_effect_predicate
        target=self.descriptor();query=ObjectQuerySpec.from_dict(target['predicate'])
        query=replace(query,any_of=(replace(query.any_of[0],exclude_ref='$source'),query.any_of[1]))
        _validate_target({**target,'predicate':query.to_dict()})
        bound=_fixed_query_effect_predicate(query,target_controller='source_controller',exclude_source=False,
            context=SimpleNamespace(source_ref='original-source',source_controller='B'))
        self.assertEqual('B',bound.controller)
        self.assertEqual(('original-source',None),tuple(part.exclude_ref for part in bound.any_of))

    def test_omitting_template_reference_validation_is_detected(self):
        method='test_conditional_union_keeps_reserved_scope_and_template_references_closed'
        with patch('quorune.object_query_union.validate_receiver_union_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_mount_vehicle_anthem_has_a_closed_mixed_subtype_scope(self):
        from quorune.semantic_runtime.components import validate_runtime_handler_descriptors
        from quorune.semantic_runtime.context import SemanticNodeError

        source = record("Mounts and Vehicles you control get +1/+1.", suffix=596002, type_line="Creature")
        registry = load_default_capability_registry()
        ir = compile_oracle_card(source, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        handler, = node.handlers
        condition = handler["condition"]
        self.assertEqual("source_controller", condition["target_controller"])
        predicate = ObjectQuerySpec.from_dict(condition["predicate"])
        self.assertEqual(ObjectQuerySpec(zones=("battlefield",), subtypes_any=("mount", "vehicle")), predicate)
        validate_runtime_handler_descriptors(node.handlers)
        for invalid in (
            {**condition, "predicate": replace(predicate, subtypes_any=("equipment", "vehicle")).to_dict()},
            {**condition, "target_controller": "any"},
        ):
            with self.subTest(condition=invalid), self.assertRaises(SemanticNodeError):
                validate_runtime_handler_descriptors(({**handler, "condition": invalid},))


class ConditionalReceiverUnionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native_mixed.MixedPermanentTargetRuntimeTests.session
    add=native_mixed.MixedPermanentTargetRuntimeTests.add
    prepare=native_mixed.MixedPermanentTargetRuntimeTests.prepare
    checkpoint=native_mixed.MixedPermanentTargetRuntimeTests.checkpoint
    resolve=native_mixed.MixedPermanentTargetRuntimeTests.resolve
    replay=native_mixed.MixedPermanentTargetRuntimeTests.replay
    private_replay=native_mixed.MixedPermanentTargetRuntimeTests.private_replay
    priority=native_mixed.MixedPermanentTargetRuntimeTests.priority
    actions=native_mixed.MixedPermanentTargetRuntimeTests.actions
    reject=native_mixed.MixedPermanentTargetRuntimeTests.reject
    binding=native_mixed.MixedPermanentTargetRuntimeTests.binding
    resume=native_mixed.MixedPermanentTargetRuntimeTests.resume
    settle=native_mixed.MixedPermanentTargetRuntimeTests.settle
    pay_cast=native_mixed.MixedPermanentTargetRuntimeTests.pay_cast
    private_draws=native_mixed.MixedPermanentTargetRuntimeTests.private_draws
    crew_vehicle=native_mixed.MixedPermanentTargetRuntimeTests.crew_vehicle
    until=turns.RingNativeActionTests.until
    attack=qualified_attacks.QualifiedAttackNativeTests.attack
    trigger_count=qualified_attacks.QualifiedAttackNativeTests.trigger_count

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'conditional-unions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/conditional-receiver-union-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Conditional receiver unions',[
            DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def stats(self,s,card):
        card=s.state.cards[card.object_id]
        return tuple(s.engine._numeric_stat(card.object_id,field) for field in ('power','toughness'))

    def deathtouch(self,s,card):
        return 'deathtouch' in s.engine._combat_keywords(s.state.cards[card.object_id])

    def test_cloudspire_captain_buffs_the_mount_and_crewed_vehicle(self):
        session = self.session(596120)
        engine = session.engine
        captain = self.add(engine, "Cloudspire Captain")
        mount = self.add(engine, "Brightfield Glider")
        vehicle = self.add(engine, "Cultivator's Caravan")
        opposing_mount = self.add(engine, "Brightfield Glider", seat="B", ref="opposing-mount")
        self.assertTrue(self.binding(session, captain)["strict_capability_ready"])
        self.prepare(session)
        self.checkpoint(session)
        self.assertEqual((2, 2), self.stats(session, mount))
        self.assertEqual((1, 1), self.stats(session, opposing_mount))
        self.assertEqual((2, 3), self.stats(session, captain))
        self.crew_vehicle(session, vehicle, captain)
        self.assertEqual((6, 6), self.stats(session, vehicle))
        self.assertEqual((2, 3), self.stats(session, captain))
        self.replay(session, load=True)

    def test_miriam_uses_turn_scope_and_counts_each_declared_attacker_once(self):
        session = self.session(596121)
        engine = session.engine
        miriam = self.add(engine, "Miriam, Herd Whisperer")
        mount = self.add(engine, "Brightfield Glider")
        dual = self.add(engine, "Cultivator's Caravan", ref="dual-subtype-attacker")
        dual.annotations["copy_overrides"] = {"type_line": "Artifact Creature — Vehicle Mount"}
        ordinary = self.add(engine, "Generic Bound Body")
        for attacker in (mount, dual, ordinary):
            attacker.acquired_control_turn_count = -1
        vehicle = self.add(engine, "Cultivator's Caravan")
        self.add(engine, "Sol Ring")
        removal = self.add(engine, "Disenchant", seat="B", zone="hand")
        for index in range(2):
            self.add(engine, "Generic Bound Plains", seat="B", ref=f"miriam-removal-mana-{index}")
        self.assertTrue(self.binding(session, miriam)["strict_capability_ready"])
        self.prepare(session)
        self.checkpoint(session)

        self.priority(session, "B")
        offered = next(action for action in self.actions(session, "B") if action["id"] == f"cast:{removal.ref}")
        command = {"action_id": offered["id"], "targets": [vehicle.ref], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:B", command)
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

        self.attack(session, ((mount, "B"), (dual, "C"), (ordinary, "D")))
        self.assertEqual(2, self.trigger_count(session, miriam))
        self.assertIsNone(self.settle(session))
        for target, expected in ((mount, 1), (dual, 1), (ordinary, 0), (vehicle, 0)):
            self.assertEqual(expected, session.state.cards[target.object_id].counters.get("+1/+1", 0))

        self.until(session, lambda game: game.state.active_player == "B" and game.state.phase == "precombat_main")
        self.priority(session, "B")
        offered = next(action for action in self.actions(session, "B") if action["id"] == f"cast:{removal.ref}")
        result = session.act("pilot:B", {**command, "action_id": offered["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.settle(session))
        self.assertEqual("graveyard", session.state.cards[vehicle.object_id].zone)
        self.replay(session, load=True)

    def test_lagorin_saddled_attack_targets_a_mount_and_an_opposing_vehicle(self):
        session = self.session(596122)
        engine = session.engine
        lagorin = self.add(engine, "Lagorin, Soul of Alacria")
        lagorin.acquired_control_turn_count = -1
        mount = self.add(engine, "Brightfield Glider")
        vehicle = self.add(engine, "Cultivator's Caravan", seat="B")
        helper = self.add(engine, "Generic Bound Body")
        self.assertTrue(self.binding(session, lagorin)["strict_capability_ready"])
        self.prepare(session)
        self.checkpoint(session)
        saddle = next(action for action in self.actions(session, "A")
            if action["id"].startswith(f"activate:{lagorin.ref}:"))
        paid = session.act("pilot:A", {"action_id": saddle["id"], "cost_cards": [helper.ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.settle(session))
        self.attack(session, ((lagorin, "B"),))
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        before = authoritative_state_hash(session.state)
        invalid = session.act("pilot:A", {"action_id": "choose", "targets": [vehicle.ref, helper.ref]})
        self.assertFalse(invalid.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": "choose", "targets": [mount.ref, vehicle.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.settle(session))
        self.assertEqual(1, session.state.cards[mount.object_id].counters["+1/+1"])
        self.assertEqual(1, session.state.cards[vehicle.object_id].counters["+1/+1"])
        self.replay(session, load=True)

    def test_real_swarmweaver_condition_overlap_control_and_graveyard_changes(self):
        s=self.session(596100)
        source=self.add(s.engine,'The Swarmweaver',zone='hand')
        spider=self.add(s.engine,'Giant Spider');overlap=self.add(s.engine,'Universal Automaton')
        opposing=self.add(s.engine,'Giant Spider',seat='B',ref='opposing-spider')
        fodder=self.add(s.engine,'Memnite')
        rites=self.add(s.engine,'Village Rites',zone='hand');exile=self.add(s.engine,'Return to Nature',zone='hand')
        control=self.add(s.engine,'Control Magic',seat='B',zone='hand');self.add(s.engine,'Vedalken Orrery',seat='B')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for seat,names in (('A',('Shock','Divination')),('B',('Shock','Divination','Sol Ring','Llanowar Elves'))):
            for name in names:self.add(s.engine,name,seat=seat,zone='graveyard',ref=seat+'-grave-'+name.casefold().replace(' ','-'))
        victim=next(c for c in s.state.cards.values() if c.ref=='B-grave-sol-ring')
        s.state.players['B'].mana_pool.update({'C':2,'U':2})
        self.prepare(s,mana={'C':3,'B':2,'G':2});self.checkpoint(s)
        self.pay_cast(s,source,payment={'C':2,'B':1,'G':1})
        s=self.resume(s);self.assertIsNone(self.settle(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.controller=='A' and 'insect' in
            s.engine._type_parts(s.engine._effective_card_data(c)['type_line'])[1]]
        self.assertEqual(2,len(tokens))
        for token in tokens:
            self.assertEqual((1,1),self.stats(s,token));self.assertFalse(self.deathtouch(s,token))
            self.assertIn('flying',s.engine._combat_keywords(token))
            self.assertEqual({'B','G'},set(s.engine._effective_card_data(token)['colors']))
        self.assertEqual((2,4),self.stats(s,spider));self.assertEqual((1,1),self.stats(s,overlap))
        self.pay_cast(s,rites,payment={'B':1},sacrifice_cards=[fodder.ref])
        self.assertEqual('graveyard',s.state.cards[fodder.object_id].zone)
        self.assertEqual((3,5),self.stats(s,spider));self.assertEqual((2,2),self.stats(s,overlap))
        self.assertTrue(self.deathtouch(s,overlap));self.assertFalse(self.deathtouch(s,opposing))
        before=set(s.state.players['A'].zones['hand'])
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(2,len(self.private_draws(s,before)))
        for token in tokens:self.assertEqual((2,2),self.stats(s,token));self.assertTrue(self.deathtouch(s,token))
        self.pay_cast(s,control,[source.ref],payment={'C':2,'U':2},seat='B')
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual('B',s.state.cards[source.object_id].controller)
        self.assertEqual((2,4),self.stats(s,spider));self.assertEqual((1,1),self.stats(s,overlap))
        self.assertFalse(self.deathtouch(s,spider));self.assertEqual((3,5),self.stats(s,opposing))
        self.assertTrue(self.deathtouch(s,opposing))
        self.pay_cast(s,exile,[victim.ref],payment={'C':1,'G':1},modes=['mode_3'])
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual('exile',s.state.cards[victim.object_id].zone)
        self.assertEqual((2,4),self.stats(s,opposing));self.assertFalse(self.deathtouch(s,opposing))
        self.assertEqual((2,3),self.stats(s,source))
        self.private_replay(s,private)

    def test_conditional_receiver_union_authorities_fail_closed_with_verified_indices(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        tests=set(discover_tests(ROOT).values())
        for cap in ('state_query.permanent.receiver_union','continuous.characteristics.fixed_public_state'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',
                blockers=['withdrawn conditional receiver-union authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=source,discovered_test_ids=tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup('The Swarmweaver'),capability_registry=registry,capability_profile='commander_review')
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)

    def test_conditional_union_requires_its_implicit_runtime_query_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(596110);source=self.add(s.engine,'The Swarmweaver',zone='hand')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(h.get('target',{}).get('predicate',{}).get('any_of') for h in p.handlers))
        cap='state_query.permanent.receiver_union'
        dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
        self.assertNotIn(cap,self.registry.closure(dependencies,profile='commander_review').reachable)
        missing=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertTrue(any(cap in b for b in bound['blockers']),bound['blockers'])


if __name__=='__main__':unittest.main()
