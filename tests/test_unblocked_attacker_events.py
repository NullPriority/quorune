"""CR508.3f/509.3g: unblocked triggers are a declare-blockers occurrence."""
import unittest
import json
from copy import deepcopy
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock,patch
from common import ROOT
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class UnblockedAttackerCompilerTests(unittest.TestCase):
    def test_grouped_unblocked_opponent_has_one_shared_recipient_and_complete_source_result(self):
        from quorune.rules.unblocked_attackers import unblocked_attacker_runtime_dependencies
        from quorune.unblocked_attacker_model import (
            UNBLOCKED_PLAYER_ATTACK_EVENT, unblocked_player_attack_condition, unblocked_source_control_effects,
        )

        caps = load_default_capability_registry()
        profile = "commander_review"
        text = ("Whenever one or more creatures an opponent controls attack you and aren't blocked, "
                "that player draws two cards and gains control of this artifact. Untap it.")
        def compile_fixture(oracle):
            return compile_oracle_card(record(oracle, "Artifact"), capability_registry=caps, capability_profile=profile)

        ir = compile_fixture(text)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(UNBLOCKED_PLAYER_ATTACK_EVENT, node.event)
        self.assertEqual(unblocked_player_attack_condition(), node.event_condition)
        self.assertEqual(unblocked_source_control_effects(2), tuple(node.effects))
        self.assertIsNone(node.target_schema)
        shape = dict(event=node.event, active_zone=node.active_zone, event_condition=node.event_condition,
                     effects=node.effects, target_schema=None, handlers=(), cost_schema=None, coverage=node.mechanics)
        required = set(unblocked_attacker_runtime_dependencies(SimpleNamespace(**shape)))
        self.assertTrue(required and required <= set(node.capability_dependencies))
        for mutated in (
            {"event_condition": {"field": "attacking_player", "op": "eq", "value": "$source.controller"}},
            {"effects": tuple(reversed(node.effects))},
            {"effects": ({**node.effects[0], "player": "$controller"}, *node.effects[1:])},
            {"effects": ({**node.effects[0], "private": False}, *node.effects[1:])},
        ):
            with self.subTest(mutated=mutated), self.assertRaises(ValueError):
                unblocked_attacker_runtime_dependencies(SimpleNamespace(**{**shape, **mutated}))
        for before, after in (("attack you", "attack a planeswalker you control"),
                              ("that player draws", "you draw"),
                              ("two cards", "X cards"),
                              ("Untap it.", "You may untap it.")):
            with self.subTest(excluded=after):
                self.assertTrue(compile_fixture(text.replace(before, after)).material_residuals)
        with patch("quorune.compiler.unblocked_attacker_bindings.unblocked_source_control_result", return_value=None):
            self.assertTrue(compile_fixture(text).material_residuals)

    def test_verified_unblocked_event_withdrawal_preserves_ordinary_attack(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id']=='trigger.event.unblocked_attacker').update(status='blocked',blockers=['post-declaration event withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record("Whenever this creature attacks and isn't blocked, draw a card.",'Creature — Human'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record('Whenever this creature attacks, draw a card.','Creature — Human'),capability_registry=registry).status)

    def test_unblocked_source_heads_use_the_post_declaration_event(self):
        registry=load_default_capability_registry()
        for body in ('draw a card.','you gain 2 life.'):
            ir=compile_oracle_card(record("Whenever this creature attacks and isn't blocked, "+body,'Creature — Human'),
                capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual('creature.unblocked',node.event)
            self.assertIn({'field':'card','op':'eq','value':'$source.ref'},node.event_condition['all'])
            self.assertIn({'field':'card_object_identity','op':'eq','value':'$source.logical_object_id'},node.event_condition['all'])


class UnblockedAttackerOwnerTests(unittest.TestCase):
    def test_grouped_unblocked_players_separate_attacker_and_recipient_and_keep_legacy_events(self):
        from quorune.rules.unblocked_attackers import unblocked_attacker_trigger_items, unblocked_player_attack_pairs
        from quorune.unblocked_attacker_model import (
            UNBLOCKED_ATTACKER_EVENT, UNBLOCKED_PLAYER_ATTACK_EVENT, UnblockedAttackerOccurrence,
        )

        rows = tuple(UnblockedAttackerOccurrence(ref, ref, ref + "@0", owner, defender) for ref, owner, defender in (
            ("first", "A", "B"), ("second", "A", "B"), ("walker", "A", "B"),
            ("battle", "A", "B"), ("later", "C", "B"), ("other", "A", "D"),
        ))
        targets = {"first": "B", "second": "B", "walker": "walker-B", "battle": "siege-B", "later": "B", "other": "D"}
        self.assertEqual((("A","B"), ("A","D"), ("C","B")), unblocked_player_attack_pairs(rows, targets))
        for version in (1, 2):
            dispatch = Mock(return_value=())
            state = SimpleNamespace(unblocked_attacker_event_version=version, combat=SimpleNamespace(attackers=targets))
            host = SimpleNamespace(state=state, _dispatch_semantic_event=dispatch, _semantic_event_sources=Mock(return_value=[]))
            self.assertEqual(([], ()), unblocked_attacker_trigger_items(host, rows=rows))
            events = [(call.args[0], call.args[1]) for call in dispatch.call_args_list]
            self.assertEqual([(UNBLOCKED_ATTACKER_EVENT, row.semantic_context()) for row in rows], events[:len(rows)])
            extra = [(UNBLOCKED_PLAYER_ATTACK_EVENT, {"attacking_player": a, "defender": d})
                     for a, d in (("A","B"), ("A","D"), ("C","B"))] if version == 2 else []
            self.assertEqual(extra, events[len(rows):])

    def test_unblocked_event_checkpoint_format_agreement_and_historical_absence_remain_strict(self):
        from quorune.model import GameState,GameConfig
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        state=GameState(game_id='unblocked-version',config=GameConfig(),players={},cards={},deck_names={},
            commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)
        raw=state.to_dict();self.assertEqual(2,raw['unblocked_attacker_event_version'])
        self.assertEqual(raw,GameState.from_dict(raw).to_dict())
        old_version=GameState.from_dict({**raw,'unblocked_attacker_event_version':1})
        self.assertEqual(1,old_version.to_dict()['unblocked_attacker_event_version'])
        validate_state_versions({'format':format_state_versions(old_version)},old_version)
        old=deepcopy(raw);old.pop('unblocked_attacker_event_version');legacy=GameState.from_dict(old)
        self.assertIsNone(legacy.unblocked_attacker_event_version);self.assertEqual(old,legacy.to_dict())
        validate_state_versions({'format':format_state_versions(state)},state)
        with self.assertRaises(ValueError):validate_state_versions({'format':{**format_state_versions(state),'unblocked_attacker_event_version':0}},state)
        for value in (True,0,3,'1'):
            with self.assertRaises(ValueError):GameState.from_dict({**raw,'unblocked_attacker_event_version':value})
    def test_forgetting_historical_blocked_membership_is_killed(self):
        import quorune.rules.unblocked_attackers as owner
        original=owner.unblocked_attacker_occurrences
        def omitted(host):
            prior=host.state.combat.blockers
            host.state.combat.blockers={k:v for k,v in prior.items() if v}
            try:return original(host)
            finally:host.state.combat.blockers=prior
        with patch.object(owner,'unblocked_attacker_occurrences',side_effect=omitted):
            result=unittest.TestResult();UnblockedAttackerOwnerTests('test_later_empty_blocker_list_does_not_become_a_new_unblocked_occurrence').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_declaration_membership_excludes_historical_blocks_removed_attackers_and_wrong_timing(self):
        from quorune.rules.unblocked_attackers import unblocked_attacker_occurrences
        def card(ref,**changes):return SimpleNamespace(ref=ref,object_id=ref,logical_object_id=ref+'@0',controller='A',zone='battlefield',phased_out=False,**changes)
        cards={k:card(k) for k in ('declared','entered-attacking','blocked','removed-blocker','gone')};cards['gone'].zone='hand'
        combat=SimpleNamespace(blockers_declared=True,attackers={k:'B' for k in cards},blockers={'blocked':['blocker'],'removed-blocker':[]})
        host=SimpleNamespace(state=SimpleNamespace(unblocked_attacker_event_version=1,combat=combat,cards=cards),active_seats=('A','B','C','D'),
            _effective_card_data=lambda card:{'type_line':'Creature'},_type_parts=lambda line:({'creature'},set(),set()),
            _defending_player_for_attacker=lambda oid,target:target)
        rows=unblocked_attacker_occurrences(host)
        self.assertEqual({'declared','entered-attacking'},{r.reference for r in rows})
        cards['declared'].logical_object_id='declared@1';self.assertEqual('declared@0',next(r.logical_object_id for r in rows if r.reference=='declared'))
        combat.blockers_declared=False
        with self.assertRaises(ValueError):unblocked_attacker_occurrences(host)
        host.state.unblocked_attacker_event_version=None
        self.assertEqual((),unblocked_attacker_occurrences(host))

    def test_unblocked_occurrence_version_and_roundtrip_have_closed_identities(self):
        from quorune.unblocked_attacker_model import UnblockedAttackerOccurrence,unblocked_attacker_event_version
        row=UnblockedAttackerOccurrence('source','source-id','source@0','A','C')
        self.assertEqual(('source','source@0','C'),tuple(row.semantic_context()[k] for k in ('card','card_object_identity','defending_player')))
        with self.assertRaises(ValueError):replace(row,logical_object_id='')
        for value in (True,0,3,'1'):
            with self.assertRaises(ValueError):unblocked_attacker_event_version(value)

    def test_later_empty_blocker_list_does_not_become_a_new_unblocked_occurrence(self):
        from quorune.rules.unblocked_attackers import unblocked_attacker_occurrences
        source=SimpleNamespace(ref='source',object_id='source',logical_object_id='source@0',controller='A',zone='battlefield',phased_out=False)
        combat=SimpleNamespace(blockers_declared=True,attackers={'source':'B'},blockers={'source':[]})
        host=SimpleNamespace(state=SimpleNamespace(unblocked_attacker_event_version=1,combat=combat,cards={'source':source}),active_seats=('A','B'),
            _effective_card_data=lambda card:{'type_line':'Creature'},_type_parts=lambda line:({'creature'},set(),set()),_defending_player_for_attacker=lambda oid,target:target)
        self.assertEqual((),unblocked_attacker_occurrences(host))


class UnblockedDefenderPoisonCompilerTests(unittest.TestCase):
    def test_poison_result_is_an_untargeted_intrinsic_post_declaration_trigger(self):
        from quorune.unblocked_attacker_model import (
            UNBLOCKED_ATTACKER_CAPABILITY, UNBLOCKED_DEFENDER_POISON_CAPABILITY,
            intrinsic_unblocked_condition, unblocked_defender_poison_effect,
        )

        registry = load_default_capability_registry()
        text = "Whenever this creature attacks and isn't blocked, defending player gets a poison counter."
        ir = compile_oracle_card(record(text, "Creature — Human"), capability_registry=registry,
                                 capability_profile="commander_review")
        effect = unblocked_defender_poison_effect()
        node, = [node for face in ir.faces for node in face.nodes if effect in node.effects]
        self.assertEqual("creature.unblocked", node.event)
        self.assertEqual(intrinsic_unblocked_condition(), node.event_condition)
        self.assertIsNone(node.target_schema)
        self.assertIn(UNBLOCKED_ATTACKER_CAPABILITY, node.capability_dependencies)
        self.assertIn(UNBLOCKED_DEFENDER_POISON_CAPABILITY, node.capability_dependencies)
        self.assertIn("counter.producer.fixed_player_effect", node.capability_dependencies)
        self.assertEqual(registry.closure(node.capability_dependencies, profile="commander_review").trusted,
                         node.exact)

    def test_raw_poison_cannot_rebind_the_trigger_or_add_a_target(self):
        from quorune.rules.combat_defending_players import combat_defending_player_runtime_dependencies
        from quorune.unblocked_attacker_model import (
            UNBLOCKED_ATTACKER_CAPABILITY, UNBLOCKED_DEFENDER_POISON_CAPABILITY,
            intrinsic_unblocked_condition, unblocked_defender_poison_effect,
        )

        effect = unblocked_defender_poison_effect()
        packet = {
            "effects": (effect,), "target_schema": None, "active_zone": "battlefield",
            "event": "creature.unblocked", "event_condition": intrinsic_unblocked_condition(),
            "coverage": (),
        }
        self.assertEqual({
            UNBLOCKED_ATTACKER_CAPABILITY, UNBLOCKED_DEFENDER_POISON_CAPABILITY,
            "counter.producer.fixed_player_effect",
        }, set(combat_defending_player_runtime_dependencies(SimpleNamespace(**packet))))
        for invalid in (
            {"event": "creature.attacks"},
            {"event_condition": {"field": "card", "op": "eq", "value": "$source.ref"}},
            {"active_zone": "stack"},
            {"target_schema": {"count": 1}},
            {"effects": ({**effect, "amount": True},)},
            {"effects": ({**effect, "amount": 2},)},
            {"effects": ({"op": "offer_optional_effect", "effects": [effect]},)},
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "exact intrinsic"):
                combat_defending_player_runtime_dependencies(SimpleNamespace(**{**packet, **invalid}))

    def test_omitting_the_compiler_result_loses_the_complete_poison_instruction(self):
        from quorune.unblocked_attacker_model import unblocked_defender_poison_effect

        text = "Whenever this creature attacks and isn't blocked, defending player gets a poison counter."
        card = record(text, "Creature — Human")
        registry = load_default_capability_registry()
        effect = unblocked_defender_poison_effect()
        baseline = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertTrue(any(effect in node.effects for face in baseline.faces for node in face.nodes))
        with patch("quorune.compiler.event_player_results.fixed_event_player_result", return_value=None):
            changed = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertFalse(any(effect in node.effects for face in changed.faces for node in face.nodes))
        self.assertTrue(any(
            residual.reason == "trigger effect has no exact generic template"
            for face in changed.faces for residual in face.residuals
        ))

    def test_genuinely_withdrawn_poison_or_counter_owner_preserves_unblocked_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.unblocked_attacker_model import UNBLOCKED_DEFENDER_POISON_CAPABILITY
        from scripts.update_capability_evidence import build_index, discover_tests

        raw=json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered=set(discover_tests(ROOT).values())
        profile="commander_review"
        prefix="Whenever this creature attacks and isn't blocked, "
        poison=record(prefix+"defending player gets a poison counter.","Creature — Human")
        draw=record(prefix+"draw a card.","Creature — Human")
        self.assertEqual("exact",compile_oracle_card(poison,capability_registry=load_default_capability_registry(),
                             capability_profile=profile).status)
        for capability in (UNBLOCKED_DEFENDER_POISON_CAPABILITY,"counter.producer.fixed_player_effect"):
            with self.subTest(withdrawn=capability):
                blocked=deepcopy(raw)
                next(row for row in blocked["capabilities"] if row["id"]==capability).update(
                    status="blocked",blockers=["Test removal of the actual operation owner"],
                )
                registry=CapabilityRegistry(blocked)
                index=build_index(registry_value=blocked,declaration_source=declarations,
                                  discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
                self.assertNotEqual("exact",compile_oracle_card(poison,capability_registry=registry,
                                      capability_profile=profile).status)
                self.assertEqual("exact",compile_oracle_card(draw,capability_registry=registry,
                                  capability_profile=profile).status)
