from __future__ import annotations

"""Pinned CR 205.4/302.6/602.2/605.3: selected taps are not tap symbols."""

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.activation_cost_choice import CostChoice
from quorune.compiler.activated_tap_costs import fixed_tap_activation_cost_choice
from quorune.rules.activation_costs import FixedTapActivationCost
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_attachment_followup_bindings as controls

FIXTURE = ROOT / "tests/fixtures/legendary-tap-activation-costs.json"
CAPABILITY = "activation.selected_tap.fixed_legendary"
CANDIDATES = ("Honor-Worn Shaku", "Relic of Legends")


class LegendaryTapModelTests(unittest.TestCase):
    def test_literal_legendary_grammar_has_an_explicit_closed_version(self):
        for noun, types in (("permanent", ()), ("creature", ("creature",))):
            choice = fixed_tap_activation_cost_choice("Tap an untapped legendary " + noun + " you control")
            self.assertIsNotNone(choice)
            self.assertEqual(3, choice.tap_schema_version)
            self.assertEqual(3, choice.fixed_tap_cost().schema_version)
            self.assertEqual(types, choice.fixed_tap_cost().predicate.types_all)
            self.assertEqual(("legendary",), choice.fixed_tap_cost().predicate.supertypes_all)
            self.assertEqual(choice, CostChoice.from_dict(choice.to_dict()))
        for text in ("Tap two untapped legendary creatures you control", "Tap another untapped legendary creature you control",
            "Tap an untapped blue legendary creature you control", "Tap an untapped legendary artifact or creature you control",
            "Tap an untapped nonlegendary creature you control", "Tap an untapped legendary creature an opponent controls"):
            with self.subTest(text=text):self.assertIsNone(fixed_tap_activation_cost_choice(text))
        from quorune.abilities import parse_activated_abilities
        from quorune.compiler.activated_tap_costs import fixed_activated_tap_cost
        combined = parse_activated_abilities(card_name="Boundary",
            oracle_text="{T}, Tap an untapped legendary creature you control: Draw a card.")[0]
        self.assertIs(combined, fixed_activated_tap_cost(combined))
        self.assertTrue(combined.uncompiled_costs)

    def test_historical_cost_bytes_and_rejection_meaning_are_preserved(self):
        for text, version in (("Tap an untapped creature you control", 1), ("Tap an untapped blue creature you control", 2)):
            choice = fixed_tap_activation_cost_choice(text)
            wire = choice.to_dict()
            self.assertNotIn("tap_schema_version", wire)
            self.assertNotIn("tap_schema_version", choice.compact())
            self.assertEqual(wire, CostChoice.from_dict(wire).to_dict())
            self.assertEqual(version, choice.fixed_tap_cost().schema_version)
        choice = fixed_tap_activation_cost_choice("Tap an untapped legendary creature you control")
        wire = choice.to_dict()
        missing = {k:v for k,v in wire.items() if k != "tap_schema_version"}
        with self.assertRaises(ValueError):CostChoice.from_dict(missing)
        with self.assertRaises(ValueError):CostChoice.from_dict({**wire,"card_type":"artifact"})
        with self.assertRaises(ValueError):CostChoice.from_dict({**wire,"unknown":True})
        for version in (None, True, 1, 2, 4):
            with self.subTest(version=version), self.assertRaises(ValueError):
                CostChoice.from_dict({**wire,"tap_schema_version":version})
        for version in (1, 2):
            with self.subTest(version=version), self.assertRaises(ValueError):
                replace(choice.fixed_tap_cost(),schema_version=version)
        for changes in ({"count":2}, {"another":True}, {"predicate":replace(choice.fixed_tap_cost().predicate,colors_any=("U",))}):
            with self.subTest(changes=str(changes)), self.assertRaises(ValueError):replace(choice.fixed_tap_cost(),**changes)

    def test_effective_supertype_and_creature_type_are_independent_requirements(self):
        from quorune.object_query import ObjectQueryResult, query_objects
        rows = (ObjectQueryResult("land","land","Legendary land","A","A","battlefield",types=("land",),supertypes=("legendary",),known_to_actor=True),
            ObjectQueryResult("creature","creature","Legendary creature","B","A","battlefield",types=("creature",),supertypes=("legendary",),known_to_actor=True),
            ObjectQueryResult("ordinary","ordinary","Ordinary creature","A","A","battlefield",types=("creature",),known_to_actor=True))
        for noun, expected in (("permanent",{"land","creature"}), ("creature",{"creature"})):
            cost = fixed_tap_activation_cost_choice("Tap an untapped legendary "+noun+" you control").fixed_tap_cost()
            self.assertEqual(expected,{r.ref for r in query_objects(rows,cost.bound_predicate("A","source"))})
            changed = tuple(replace(r,controller="B") if r.ref=="creature" else r for r in rows)
            self.assertEqual(expected-{"creature"},{r.ref for r in query_objects(changed,cost.bound_predicate("A","source"))})


class LegendaryTapCompilerTests(unittest.TestCase):
    def test_complete_originals_close_without_discarding_the_channel_sibling(self):
        registry = load_default_capability_registry();semantics = SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"legendary-originals.sqlite3";build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,"Moonsnare Prototype"):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False)
                        register_generated_programs(db,semantics,(card,),trust_level="provisional",capability_registry=registry,
                            capability_profile="commander_review",promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile="commander_review")
                        bound=bind_card_program_runtime(program,capability_registry=registry,profile="commander_review")
                        self.assertEqual(name in CANDIDATES,bool(bound["strict_capability_ready"] and bound["trust_basis"]=="capability_closed"),bound["blockers"])

    def test_both_ordinary_and_mana_activations_require_the_new_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        registry=load_default_capability_registry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"legendary-compiler.sqlite3";build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in CANDIDATES:
                    card=db.lookup(name,fuzzy=False);ir=compile_oracle_card(card,capability_registry=registry,capability_profile="commander_review")
                    self.assertEqual("exact",ir.status,ir.material_residuals)
                    node=next(n for f in ir.faces for n in f.nodes if CAPABILITY in n.capability_dependencies)
                    self.assertEqual(3,node.cost["choices"][0]["tap_schema_version"])
                    self.assertEqual(node.text,card.oracle_text[node.span.start:node.span.end])
                    self.assertEqual("mana_ability" if name=="Relic of Legends" else "activated_ability",node.kind)
                    raw=json.loads((ROOT/"quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
                    next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status="blocked",blockers=["new qualifier withdrawn"])
                    blocked=CapabilityRegistry(raw);blocked.mark_evidence_verified(registry.evidence_fingerprint)
                    self.assertNotEqual("exact",compile_oracle_card(card,capability_registry=blocked,capability_profile="commander_review").status)


class LegendaryTapRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachmentFollowupRuntimeTests.session
    add=controls.AttachmentFollowupRuntimeTests.add
    prepare=controls.AttachmentFollowupRuntimeTests.prepare
    closed=controls.AttachmentFollowupRuntimeTests.closed
    actions=controls.AttachmentFollowupRuntimeTests.actions
    cast_offered=controls.AttachmentFollowupRuntimeTests.cast_offered
    checkpoint=controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve=controls.AttachmentFollowupRuntimeTests.resolve
    replay=controls.AttachmentFollowupRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/"legendary-tap.sqlite3"
        build_fixture_database([ROOT/"tests/fixtures/bound-effect-program-cards.json",FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition("Legendary selected taps",[DeckEntry("Generic Bound Commander",1,"commander"),DeckEntry("Generic Bound Plains",30)],["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def world(self,seed,name,*,mana=None,source_tapped=False):
        session=self.session(seed);source=self.add(session.engine,name)
        creature=self.add(session.engine,"Isamaru, Hound of Konda",ref="legendary-creature")
        creature.acquired_control_turn_count=session.state.players["A"].turns_begun
        land=self.add(session.engine,"Mikokoro, Center of the Sea",ref="legendary-land")
        ordinary=self.add(session.engine,"Ornithopter",ref="ordinary-cost-object")
        private=self.add(session.engine,"Unsummon",seat="D",zone="hand",ref="legendary-private-hand")
        source.tapped=source_tapped;self.prepare(session,source,mana or {})
        return session,source,creature,land,ordinary,private

    def offered(self,session,source):
        return next(row for row in self.actions(session) if row['id'].startswith("activate:"+source.ref+":") and row.get('cost_summary',{}).get('choose_cost'))

    def activate(self,session,source,selected,**values):
        row=self.offered(session,source)
        result=session.act("pilot:A",{'action_id':row['id'],'cost_cards':[selected.ref],'pay':'auto',**values})
        self.assertTrue(result.ok,result.summary)

    def reject(self,session,command,seat="A"):
        before=authoritative_state_hash(session.state);result=session.act("pilot:"+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(session.state))

    def private_replay(self,session,private):
        for seat in "ABC":self.assertNotIn(private.ref,str(session.packet("pilot:"+seat,full=True)))
        return self.replay(session,load=True)

    def test_shaku_pays_a_new_creature_or_land_while_the_source_is_tapped(self):
        for i,use_land in enumerate((False,True)):
            session,source,creature,land,_,private=self.world(298100+i,"Honor-Worn Shaku",source_tapped=True)
            self.checkpoint(session);chosen=land if use_land else creature
            self.assertEqual({creature.ref,land.ref},set(self.offered(session,source)['cost_summary']['choose_cost'][0]['legal_refs']))
            self.activate(session,source,chosen)
            self.assertTrue(session.state.cards[chosen.object_id].tapped)
            self.assertTrue(session.state.cards[source.object_id].tapped);self.assertTrue(session.state.stack)
            session=self.private_replay(session,private);self.assertIsNone(self.resolve(session))
            self.assertFalse(session.state.cards[source.object_id].tapped);self.private_replay(session,private)

    def test_ineligible_costs_and_wrong_actor_reject_before_tap_or_stack_mutation(self):
        session,source,creature,land,ordinary,private=self.world(298110,"Honor-Worn Shaku",source_tapped=True)
        enemy=self.add(session.engine,"Isamaru, Hound of Konda",seat="B",ref="enemy-legendary")
        phased=self.add(session.engine,"Mikokoro, Center of the Sea",ref="phased-legendary");phased.phased_out=True
        self.prepare(session,source,{});self.checkpoint(session);row=self.offered(session,source)
        self.assertEqual({creature.ref,land.ref},set(row['cost_summary']['choose_cost'][0]['legal_refs']))
        for selected in ([],[ordinary.ref],[enemy.ref],[phased.ref],[creature.ref,creature.ref],[private.ref]):
            self.reject(session,{'action_id':row['id'],'cost_cards':selected})
        self.reject(session,{'action_id':row['id'],'cost_cards':[creature.ref]},"B")
        self.activate(session,source,creature);self.assertIsNone(self.resolve(session));self.private_replay(session,private)

    def test_actual_response_moves_shaku_but_does_not_refund_its_paid_tap(self):
        session,source,creature,_,_,private=self.world(298120,"Honor-Worn Shaku",source_tapped=True,mana={'C':2,'U':1})
        response=self.add(session.engine,"Disperse",zone="hand");self.closed(session,response)
        self.checkpoint(session);self.activate(session,source,creature)
        self.cast_offered(session,response,targets=(source.ref,));self.assertIsNone(self.resolve(session))
        self.assertEqual("hand",session.state.cards[source.object_id].zone)
        self.assertTrue(session.state.cards[creature.object_id].tapped);self.private_replay(session,private)

    def test_relic_immediately_adds_the_chosen_color_and_funds_a_real_spell(self):
        session,source,creature,_,ordinary,private=self.world(298130,"Relic of Legends")
        spell=self.add(session.engine,"Unsummon",zone="hand");self.closed(session,spell)
        self.checkpoint(session);self.activate(session,source,creature,mana_output={'U':1})
        self.assertFalse(session.state.stack);self.assertEqual(1,session.state.players['A'].mana_pool['U'])
        self.assertTrue(session.state.cards[creature.object_id].tapped);self.assertFalse(session.state.cards[source.object_id].tapped)
        session=self.private_replay(session,private);spell=session.state.cards[spell.object_id]
        self.cast_offered(session,spell,targets=(ordinary.ref,));self.assertEqual(0,session.state.players['A'].mana_pool['U'])
        self.assertIsNone(self.resolve(session));self.assertEqual('hand',session.state.cards[ordinary.object_id].zone)
        self.private_replay(session,private)

    def test_relic_rejects_land_and_noncolor_mana_but_works_while_tapped(self):
        session,source,creature,land,_,private=self.world(298140,"Relic of Legends",source_tapped=True)
        self.checkpoint(session);row=self.offered(session,source)
        self.assertEqual({creature.ref},set(row['cost_summary']['choose_cost'][0]['legal_refs']))
        self.reject(session,{'action_id':row['id'],'cost_cards':[land.ref],'mana_output':{'G':1}})
        self.reject(session,{'action_id':row['id'],'cost_cards':[creature.ref],'mana_output':{'C':1}})
        self.assertFalse(session.state.cards[creature.object_id].tapped)
        self.activate(session,source,creature,mana_output={'G':1});self.assertFalse(session.state.stack)
        self.assertTrue(session.state.cards[source.object_id].tapped);self.assertEqual(1,session.state.players['A'].mana_pool['G'])
        self.private_replay(session,private)

    def test_payload_minimum_is_required_for_catalog_and_fixed_mana_without_coverage(self):
        for i,name in enumerate(CANDIDATES):
            session,source,*_=self.world(298150+i,name)
            program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
            deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            handlers=tuple(h for h in program.handlers if name!='Relic of Legends' or h.get('handler_id')!='activation.catalog.pinned.v1')
            changed=replace(program,coverage=(),handlers=handlers,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])

    def test_legendary_query_omission_is_killed_by_an_actual_original_activation(self):
        from quorune.rules.activation_costs import query_objects
        session,source,_,_,ordinary,_=self.world(298160,"Honor-Worn Shaku",source_tapped=True)
        def omit(rows,query):return query_objects(rows,replace(query,supertypes_all=()))
        with patch('quorune.rules.activation_costs.query_objects',side_effect=omit):
            self.prepare(session,source,{})
            self.checkpoint(session)
            self.activate(session,source,ordinary)
            with self.assertRaises(AssertionError):self.assertFalse(session.state.cards[ordinary.object_id].tapped)


if __name__=="__main__":unittest.main()
