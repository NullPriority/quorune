from __future__ import annotations

"""CR 601.2c/608.2b: a replacement retains one selected target incarnation."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.resolution_conditions import SAME_TARGET_SELF_REPLACEMENT_CAPABILITY as CAP
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_compound_damage_recipients as controls


class SameTargetKickedCompilerTests(unittest.TestCase):
    def compile(self,body,registry=None):
        return compile_oracle_card(replace(record('Kicker {1}\n'+body),keywords=('Kicker',)),
            capability_registry=registry or load_default_capability_registry(),capability_profile='commander_review')

    def test_anaphor_has_one_complete_shared_target_and_independent_branch_owners(self):
        ir=self.compile('Target creature gets +3/+3 until end of turn. If this spell was kicked, that creature gets +5/+5 until end of turn instead.')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=next(n for n in ir.faces[0].nodes if n.effects)
        self.assertEqual(1,node.target_schema['count']);self.assertIn(CAP,node.capability_dependencies)
        self.assertEqual(5,node.effects[0]['schema_version'])
        self.assertEqual('$target.0',node.effects[0]['effects'][0]['card'])
        self.assertEqual('$target.0',node.effects[0]['otherwise_effects'][0]['card'])
        from quorune.rules.resolution_condition_shapes import resolution_condition_node_capabilities
        for changed in ({**node.target_schema,'count':2},None):
            self.assertFalse(resolution_condition_node_capabilities(effects=node.effects,target_schema=changed,mechanic_ids=node.mechanics))
        effects=deepcopy(node.effects);effects[0]['effects'][0]['card']='$target.1'
        self.assertFalse(resolution_condition_node_capabilities(effects=effects,target_schema=node.target_schema,mechanic_ids=node.mechanics))

    def test_new_target_occurrences_changed_domains_and_unrepresented_results_stay_residual(self):
        for body in (
            'Target creature gets +3/+3 until end of turn. If this spell was kicked, target creature gets +5/+5 until end of turn instead.',
            'Target creature gets +3/+3 until end of turn. If this spell was kicked, any number of target creatures get +5/+5 until end of turn instead.',
            'Target creature you control gets +3/+3 until end of turn. If this spell was kicked, that creature gets +5/+5 until end of turn instead.',
            'Target creature gets +3/+3 until end of turn. If this spell was kicked, that creature gets +X/+X until end of turn instead.',
            'Target player discards a card. If this spell was kicked, that player discards three cards instead.',
            'Generic Bound Compiler Fixture deals 2 damage to any target. If this spell was kicked, it deals 4 damage to that creature instead.',
            'Generic Bound Compiler Fixture deals 2 damage to any target. If this spell was kicked, it deals 4 damage divided as you choose among any number of targets instead.'):
            self.assertNotEqual('exact',self.compile(body).status,body)

    def test_each_required_authority_withdrawal_prevents_same_target_admission(self):
        body='Target creature gets +3/+3 until end of turn. If this spell was kicked, that creature gets +5/+5 until end of turn instead.'
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in (CAP,'resolution.effect.fixed_cast_fact','resolution.effect.fixed_public_self_replacement','target.revalidate_resolution'):
            changed=deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn'])
            self.assertNotEqual('exact',self.compile(body,CapabilityRegistry(changed)).status,cap)


class SameTargetKickedRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundDamageRuntimeTests.session
    add=controls.CompoundDamageRuntimeTests.add
    prepare=controls.CompoundDamageRuntimeTests.prepare
    checkpoint=controls.CompoundDamageRuntimeTests.checkpoint
    cast=controls.CompoundDamageRuntimeTests.cast
    resolve=controls.CompoundDamageRuntimeTests.resolve
    replay=controls.CompoundDamageRuntimeTests.replay
    binding=controls.CompoundDamageRuntimeTests.binding
    actions=controls.CompoundDamageRuntimeTests.actions
    action=controls.CompoundDamageRuntimeTests.action
    private_replay=controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'same-target-kicked.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/kicked-same-target-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Same target Kicker',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_stat_replacements_keep_one_creature_and_exact_selected_keywords(self):
        for name,normal,kicked_amount,base,kicker in (
                ('Might of Murasa',3,5,{'G':1,'C':1},{'G':2,'C':3}),
                ('Explosive Growth',2,5,{'G':1},{'G':1,'C':5}),
                ('Colossal Growth',3,4,{'G':1,'C':1},{'G':1,'C':1,'R':1})):
            for kicked in (False,True):
                session=self.session(729801+len(name)+int(kicked))
                source=self.add(session.engine,name,zone='hand')
                target=self.add(session.engine,'Generic Bound Body',seat='B',ref='same-target-body')
                other=self.add(session.engine,'Generic Bound Body',seat='D',ref='same-target-untouched')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.prepare(session,mana=kicker if kicked else base);self.checkpoint(session)
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                action=self.action(session,source);before=authoritative_state_hash(session.state)
                command={'action_id':action['id'],'targets':[target.ref],'cost_option':'kicked' if kicked else 'normal','pay':'auto'}
                denied=session.act('pilot:B',command);self.assertFalse(denied.ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual([target.ref],session.state.stack[-1].targets)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual(2+(kicked_amount if kicked else normal),session.engine._numeric_stat(target.object_id,'power'))
                self.assertEqual(2,session.engine._numeric_stat(other.object_id,'power'))
                for keyword in ('haste','trample'):
                    self.assertEqual(name=='Colossal Growth' and kicked,keyword in session.engine._combat_keywords(session.state.cards[target.object_id]))
                self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
                self.private_replay(session,private)

    def test_original_damage_replacements_retain_same_target_and_literal_amount(self):
        for name,normal,boosted,base,paid in (
                ('Burst Lightning',2,4,{'R':1},{'R':1,'C':4}),
                ('Shivan Fire',2,4,{'R':1},{'R':1,'C':4}),
                ('Roil Eruption',3,5,{'R':1,'C':1},{'R':1,'C':6}),
                ('Firebending Lesson',2,5,{'R':1},{'R':1,'C':4})):
            for kicked in (False,True):
                session=self.session(729820+len(name)+int(kicked))
                source=self.add(session.engine,name,zone='hand')
                target=self.add(session.engine,'Generic Bound Body',seat='B',ref='same-damage-target')
                target.counters['+1/+1']=10
                other=self.add(session.engine,'Generic Bound Body',seat='D',ref='same-damage-untouched')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.prepare(session,mana=paid if kicked else base);self.checkpoint(session)
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':[target.ref],
                    'cost_option':'kicked' if kicked else 'normal','pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual(boosted if kicked else normal,session.state.cards[target.object_id].marked_damage)
                self.assertEqual(0,session.state.cards[other.object_id].marked_damage)
                self.assertEqual([40]*4,[session.state.players[s].life for s in 'ABCD'])
                self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
                self.private_replay(session,private)

    def test_runtime_cannot_omit_same_target_or_child_damage_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(729840);source=self.add(session.engine,'Burst Lightning',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        for cap in (CAP,'damage.source.stack_current_or_lki'):
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            changed=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_seven_originals_have_whole_card_admission_and_fully_playable_preflight(self):
        from quorune.preflight import card_semantic_status
        session=self.session(729844)
        for name in ('Might of Murasa','Explosive Growth','Colossal Growth','Burst Lightning',
                'Shivan Fire','Roil Eruption','Firebending Lesson'):
            card=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,card)['strict_capability_ready'],name)
            status=card_semantic_status(self.db.lookup(name),session.engine.semantics,db=self.db,
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],(name,status['unresolved']))

    def test_actual_bounce_invalidates_selected_incarnation_and_keeps_paid_cost(self):
        session=self.session(729841)
        source=self.add(session.engine,'Might of Murasa',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B',ref='kicked-bounce-target')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1})
        self.prepare(session,mana={'G':2,'C':3});self.checkpoint(session)
        action=self.action(session,source);before=authoritative_state_hash(session.state)
        for targets in ([],[target.ref,target.ref]):
            denied=session.act('pilot:A',{'action_id':action['id'],'targets':targets,'cost_option':'kicked','pay':'auto'})
            self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);old=target.logical_object_id
        self.cast(session,'B',bounce,[target.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        current=session.state.cards[target.object_id]
        self.assertEqual('hand',current.zone);self.assertNotEqual(old,current.logical_object_id)
        self.assertEqual(2,session.engine._numeric_stat(current.object_id,'power'))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_actual_countered_kicked_burst_has_no_damage_and_no_refund(self):
        session=self.session(729842)
        source=self.add(session.engine,'Burst Lightning',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='D',ref='countered-burst-target')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'R':1,'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':[target.ref],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0,session.state.cards[target.object_id].marked_damage)
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_actual_copy_retains_paid_choice_and_retargets_only_the_copy(self):
        session=self.session(729843)
        source=self.add(session.engine,'Burst Lightning',zone='hand')
        first=self.add(session.engine,'Generic Bound Body',seat='B',ref='copied-burst-original-target')
        second=self.add(session.engine,'Generic Bound Body',seat='D',ref='copied-burst-new-target')
        for card in (first,second):card.counters['+1/+1']=10
        copy=self.add(session.engine,'Reverberate',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'R':2})
        self.prepare(session,mana={'R':1,'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'targets':[first.ref],'cost_option':'kicked','pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);original=session.state.stack[-1]
        self.cast(session,'B',copy,[original.ref]);pending=self.resolve(session)
        self.assertEqual('semantic.choice',pending.kind)
        before=authoritative_state_hash(session.state)
        denied=session.act('pilot:A',{'action_id':'choose','targets':[second.ref]})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        denied=session.act('pilot:B',{'action_id':'choose','targets':[first.ref,second.ref]})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        accepted=session.act('pilot:B',{'action_id':'choose','targets':[second.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        copied=next(item for item in session.state.stack if item.kind=='spell_copy')
        self.assertIs(True,copied.context['kicked']);self.assertEqual('B',copied.controller)
        self.assertEqual([second.ref],copied.targets)
        self.assertEqual([first.ref],next(item for item in session.state.stack if item.ref==original.ref).targets)
        self.assertIsNone(self.resolve(session))
        for card in (first,second):self.assertEqual(4,session.state.cards[card.object_id].marked_damage)
        self.assertEqual(2,len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(0,sum(session.state.players['B'].mana_pool.values()))
        self.private_replay(session,private)



    def test_original_copy_replacement_has_two_closed_counts_and_one_target(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        from test_controller_creature_keyword_absence import register_card

        original = self.db.lookup("Rite of Replication", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, original.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program = next(p for p in semantics.programs_for_oracle(original.oracle_id) if p.effects)
        self.assertEqual(
            {"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]},
            program.target_schema,
        )
        condition, = program.effects
        self.assertEqual(5, condition["schema_version"])
        self.assertEqual("$context.kicked", condition["cast_fact"])
        ordinary, = condition["otherwise_effects"]
        kicked, = condition["effects"]
        recipe = TokenCopyRecipeSpec("target").to_dict()
        self.assertEqual({"op": "create_token", "controller": "$controller", "quantity": 1, "copy_spec": recipe}, ordinary)
        self.assertEqual({**ordinary, "quantity": 5}, kicked)
        self.assertTrue({CAP, "resolution.effect.fixed_cast_fact", "target.revalidate_resolution",
                         "token.creation.fixed_copy"} <= set(program.capability_dependencies))

        for invalid in (
            {**kicked, "quantity": True},
            {**kicked, "quantity": 6},
            {**kicked, "copy_spec": TokenCopyRecipeSpec("source").to_dict()},
            {**kicked, "tapped": True},
            {**kicked, "controller": "$target.0"},
        ):
            with self.subTest(invalid=invalid):
                changed = replace(program, effects=({**condition, "effects": [invalid]},))
                bound = bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(bound["strict"], bound)

    def test_kicked_copy_scope_keeps_unrepresented_text_and_omission_residual(self):
        from test_controller_creature_keyword_absence import register_card

        original = self.db.lookup("Rite of Replication", fuzzy=False)
        for changed in (
            original.oracle_text.replace("five of those tokens", "six of those tokens"),
            original.oracle_text.replace("five of those tokens", "X of those tokens"),
            original.oracle_text.replace("five of those tokens", "five tokens that are copies of target creature"),
            original.oracle_text.replace("copy of target creature.", "copy of target artifact."),
            original.oracle_text.replace("copy of target creature.", "copy of target creature, except it has flying."),
        ):
            with self.subTest(oracle=changed):
                ir = compile_oracle_card(replace(original, oracle_text=changed), capability_registry=self.registry,
                                         capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status, changed)
        with patch("quorune.compiler.kicked_self_replacement_templates._kicked_target_copy_count", return_value=None):
            report, _ = register_card(self.db, self.registry, original.name)
            self.assertFalse(report["strict_capability_ready"], report)

    def test_real_kicker_selects_one_creation_batch_and_uses_copiable_values(self):
        for kicked, quantity, remaining in ((False, 1, 5), (True, 5, 0)):
            with self.subTest(kicked=kicked):
                session = self.session(729850 + int(kicked))
                spell = self.add(session.engine, "Rite of Replication", zone="hand")
                target = self.add(session.engine, "Grizzly Bears", seat="B", ref="rite-model")
                private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand", ref="rite-private")
                target.counters["+1/+1"] = 2
                target.tapped = True
                session.state.config.semantic_policy = "trusted_only"
                self.prepare(session, mana={"U": 2, "C": 7})
                self.checkpoint(session)
                binding = self.binding(session, spell)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                action = self.action(session, spell)
                result = session.act("pilot:A", {
                    "action_id": action["id"], "targets": [target.ref],
                    "cost_option": "kicked" if kicked else "normal", "pay": "auto",
                })
                self.assertTrue(result.ok, result.summary)
                self.assertIs(kicked, session.state.stack[-1].context["kicked"])
                self.assertEqual(remaining, sum(session.state.players["A"].mana_pool.values()))
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
                self.assertEqual(quantity, len(tokens))
                for token in tokens:
                    self.assertEqual(("A", "A"), (token.owner, token.controller))
                    self.assertEqual("Grizzly Bears", session.engine._effective_card_data(token)["name"])
                    self.assertEqual({}, token.counters)
                    self.assertFalse(token.tapped)
                event, = (event for event in session.state.events if event.code == "token.create")
                self.assertEqual(quantity, event.details["base_quantity"])
                self.assertCountEqual([token.ref for token in tokens], event.details["objects"])
                self.assertEqual(2, session.state.cards[target.object_id].counters["+1/+1"])
                self.private_replay(session, private)

    def test_actual_spell_copy_keeps_kicker_and_uses_the_copied_spells_own_target(self):
        session = self.session(729852)
        spell = self.add(session.engine, "Rite of Replication", zone="hand")
        first = self.add(session.engine, "Grizzly Bears", seat="C", ref="rite-original-model")
        second = self.add(session.engine, "Young Wolf", seat="D", ref="rite-copy-model")
        copier = self.add(session.engine, "Reverberate", seat="B", zone="hand")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand", ref="ritual-private")
        session.state.config.semantic_policy = "trusted_only"
        session.state.players["B"].mana_pool.update({"R": 2})
        self.prepare(session, mana={"U": 2, "C": 7})
        self.checkpoint(session)
        for source in (spell, copier):
            binding = self.binding(session, source)
            self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        accepted = session.act("pilot:A", {
            "action_id": self.action(session, spell)["id"], "targets": [first.ref],
            "cost_option": "kicked", "pay": "auto",
        })
        self.assertTrue(accepted.ok, accepted.summary)
        original_ref = session.state.stack[-1].ref
        self.cast(session, "B", copier, [original_ref])
        pending = self.resolve(session)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:A", {"action_id": "choose", "targets": [second.ref]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        chosen = session.act("pilot:B", {"action_id": "choose", "targets": [second.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        copy, = (item for item in session.state.stack if item.kind == "spell_copy")
        original = next(item for item in session.state.stack if item.ref == original_ref)
        self.assertIs(True, copy.context["kicked"])
        self.assertEqual(("B", [second.ref], [first.ref]), (copy.controller, copy.targets, original.targets))
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        actual = [(card.owner, card.controller, session.engine._effective_card_data(card)["name"]) for card in tokens]
        self.assertCountEqual([("A", "A", "Grizzly Bears")] * 5 + [("B", "B", "Young Wolf")] * 5, actual)
        self.assertEqual(2, len(session.engine._current_turn_history("spell_cast")))
        for seat in "AB":
            self.assertEqual(0, sum(session.state.players[seat].mana_pool.values()))
        self.private_replay(session, private)

    def test_paid_copy_target_loss_skips_the_whole_batch_without_refund(self):
        session = self.session(729853)
        spell = self.add(session.engine, "Rite of Replication", zone="hand")
        model = self.add(session.engine, "Grizzly Bears", seat="B", ref="rite-departing-model")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        session.state.config.semantic_policy = "trusted_only"
        session.state.players["B"].mana_pool.update({"U": 1})
        self.prepare(session, mana={"U": 2, "C": 7})
        self.checkpoint(session)
        binding = self.binding(session, spell)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        command = {"action_id": self.action(session, spell)["id"], "cost_option": "kicked", "pay": "auto"}
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:A", {**command, "targets": [model.ref, model.ref]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {**command, "targets": [model.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast(session, "B", bounce, [model.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[model.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.assertFalse(any(event.code == "token.create" for event in session.state.events))
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.replay(session, load=True)

    def test_verified_fixed_copy_withdrawal_preserves_other_kicked_target_results(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_controller_creature_keyword_absence import register_card

        capability = "token.creation.fixed_copy"
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == capability).update(
            status="blocked", blockers=["Scoped fixed copy withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((capability,), profile="commander_review")
        self.assertIn("status:" + capability + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, "Rite of Replication")
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Might of Murasa")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_fresh_original_modifier_kills_same_target_producer_omission(self):
        method='test_original_stat_replacements_keep_one_creature_and_exact_selected_keywords'
        with patch('quorune.compiler.kicked_same_target_replacements.same_target_kicked_replacement',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_fresh_original_modifier_kills_unpaid_branch_mutation(self):
        from quorune.semantic_choices.resolution_condition import PublicResolutionConditionHandler
        original=PublicResolutionConditionHandler.prepare
        def unpaid(handler,effect,context):
            if effect.get('schema_version')==5:effect={**effect,'cast_fact':False}
            return original(handler,effect,context)
        method='test_original_stat_replacements_keep_one_creature_and_exact_selected_keywords'
        with patch.object(PublicResolutionConditionHandler,'prepare',unpaid):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
