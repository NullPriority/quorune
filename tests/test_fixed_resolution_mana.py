from __future__ import annotations

from dataclasses import replace
import json,tempfile,unittest
import os
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.rules.fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class FixedResolutionManaCompilerTests(unittest.TestCase):
    def test_fixed_symbol_bundles_are_closed_separate_outputs(self):
        registry=load_default_capability_registry()
        for text in ('Add {B}{B}{B}.','Add {W}{U}{B}{R}{G}.','Add {C}{C}.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry)
            self.assertEqual('exact',ir.status,text)
            node=ir.faces[0].nodes[0];self.assertEqual(('mana.production.fixed_resolution',),fixed_resolution_mana_capabilities(effects=node.effects,target_schema=None,mechanic_ids=node.mechanics))
        ir=compile_oracle_card(replace(query_record('Add {W}{U}{B}{R}{G}.'),type_line='Sorcery'),capability_registry=registry)
        self.assertEqual(5,len(ir.faces[0].nodes[0].effects))

    def test_dynamic_restricted_and_malformed_mana_remain_closed(self):
        registry=load_default_capability_registry()
        for text in ('Add {X}.','Add {R} for each unknown thing.','Add {R}{R}. Spend this mana only to cast imaginary spells.','Add one mana of any color combination.','Add one mana of any color. Spend this mana only to cast creature spells.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry).status,text)
        for text in ('Add one mana of any color.','Add three mana of any one color.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertIn('mana.production.fixed_resolution_choice',ir.faces[0].nodes[0].capability_dependencies)
        shape={'op':'mana','player':'$controller','color':'B','amount':3,'source':'$source'}
        for change in ({'amount':True},{'amount':0},{'color':'secret'},{'extra':1}):
            self.assertFalse(fixed_resolution_mana_capabilities(effects=({**shape,**change},),target_schema=None,mechanic_ids=('fixed-resolution-mana',)))
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in value['capabilities'] if c['id']=='mana.production.fixed_resolution').update(status='blocked',blockers=['missing mana effect owner'])
        registry=CapabilityRegistry(value);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Add {B}{B}{B}.'),type_line='Instant'),capability_registry=registry).status)

    def test_restricted_bundle_allows_each_spell_type_but_no_ability_payment(self):
        from quorune.compiler.fixed_resolution_mana import fixed_resolution_mana_effect_template
        from quorune.mana_restrictions import mana_restriction_allows,spell_mana_spend_context,ability_mana_spend_context
        template=fixed_resolution_mana_effect_template('Add {R}{R}. Spend this mana only to cast artifact or creature spells.')
        self.assertIsNotNone(template);restriction=template[1][0]['spend_restriction']
        for type_line in ('Artifact','Creature','Artifact Creature'):
            self.assertTrue(mana_restriction_allows(restriction,spell_mana_spend_context(type_line)))
        self.assertFalse(mana_restriction_allows(restriction,spell_mana_spend_context('Instant')))
        self.assertFalse(mana_restriction_allows(restriction,ability_mana_spend_context('Artifact')))


    def test_noncast_mana_rider_has_exact_authority_and_spend_context(self):
        from quorune.mana_restrictions import (
            ability_mana_spend_context, mana_restriction_allows, spell_mana_spend_context,
        )
        from quorune.mana_spend_rules import (
            ManaSpendRule, NONCAST_MANA_PAYMENT_CAPABILITY, NONCAST_MANA_PAYMENT_RESTRICTION,
        )

        text = "Add {C}{C}. This mana can't be spent to cast spells."
        card = replace(query_record(text), type_line="Instant")
        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        registry = load_default_capability_registry()
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(NONCAST_MANA_PAYMENT_CAPABILITY, node.capability_dependencies)
        self.assertEqual(NONCAST_MANA_PAYMENT_RESTRICTION, node.effects[0]["spend_restriction"])
        self.assertTrue(mana_restriction_allows(NONCAST_MANA_PAYMENT_RESTRICTION, None))
        self.assertTrue(mana_restriction_allows(NONCAST_MANA_PAYMENT_RESTRICTION, ability_mana_spend_context("Artifact")))

        def assert_no_cast_payment():
            for type_line in ("Instant", "Creature", "Artifact", "Enchantment — Room"):
                self.assertFalse(mana_restriction_allows(NONCAST_MANA_PAYMENT_RESTRICTION, spell_mana_spend_context(type_line)))

        assert_no_cast_payment()
        with patch.object(ManaSpendRule, "allows", return_value=True):
            with self.assertRaises(AssertionError):
                assert_no_cast_payment()
        row = next(row for row in data["capabilities"] if row["id"] == NONCAST_MANA_PAYMENT_CAPABILITY)
        row.update(status="blocked", blockers=["non-cast payment authority removed"])
        missing = CapabilityRegistry(data)
        missing.mark_evidence_verified("0" * 64)
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=missing, capability_profile="commander_review").status)
        malformed = replace(card, oracle_text=text.replace("cast spells", "cast creature spells"))
        self.assertNotEqual("exact", compile_oracle_card(malformed, capability_registry=registry).status)


class FixedResolutionManaRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'fixed-resolution-mana.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/fixed-resolution-mana-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Resolution mana',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def test_original_mana_replacement_carriers_fail_closed_at_whole_runtime_admission(self):
        from high_risk_interaction_support import _observed_piece_ids, _record as boundary_record
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from quorune.semantics import SemanticRegistry
        residuals = {
            'residual.replacement.replacement-applicability',
            'residual.replacement.self-replacement-and-prevention-ordering'}
        records=[self.db.by_oracle_id(oid) for oid in ('4b1da9aa-a30c-44b8-a10e-f9dc3fe70b6f','5b5bf1fa-6502-4790-b66b-f0f8504ebc7c',
                '907ae517-22d5-4ac7-bc3a-3f4d5eaeeb57','947b40a2-e272-4ef6-85fb-3d767a21bc7a',
                'bdf476e5-1d57-4b17-b45b-d52fd75aadeb')]
        records.append(boundary_record('resolution-mana-replacement-boundary'))
        for record in records:
            oid=record.oracle_id
            with self.subTest(oracle_id=oid):
                pieces = {
                    ('capability.mana.activated.fixed_output'
                     if oid == 'bdf476e5-1d57-4b17-b45b-d52fd75aadeb'
                     else 'capability.mana.production.fixed_resolution'),
                    *residuals,
                }
                if any('Saga' in face.get('type_line','') for face in record.faces):
                    pieces-=residuals
                    pieces|={'residual.card_form.ordinary-saga-chapter-event-binding',
                        'residual.effect_clause.exact-typed-chapter-effect-body'}
                supported=record.name=='Cabal Ritual'
                if supported:
                    pieces-=residuals
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                row = analyze_card_unlocks(ir, program=program, program_error=None,
                    capabilities=self.registry, profile='commander_review')
                self.assertLessEqual(pieces, _observed_piece_ids(row))
                self.assertEqual('trusted' if supported else 'residual', row['card_program_status'])
                self.assertIsNone(row['hard_construction_failure'])
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(supported,binding['strict_capability_ready'])
                self.assertEqual(supported,binding['compatible_ready'])
                if supported:self.assertEqual('exact',ir.status,ir.material_residuals)
                else:self.assertIn('trust_basis:unresolved', binding['blockers'])

    def test_thran_turbine_upkeep_offer_pays_an_ability_and_never_a_spell(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.engine import TURN_STEPS
        from quorune.mana_provenance import restricted_mana
        from quorune.mana_spend_rules import NONCAST_MANA_PAYMENT_RESTRICTION

        for apply in (False, True):
            with self.subTest(apply=apply):
                session = self.session(328030 + apply)
                engine = session.engine
                source = self.add(engine, "Thran Turbine")
                ball = self.add(engine, "Crystal Ball")
                spell = self.add(engine, "Spatial Contortion", zone="hand")
                self.add(engine, "Generic Bound Body", seat="B")
                private = self.add(engine, "Dark Ritual", seat="C", zone="hand", ref="private-c-turbine")
                original = self.db.lookup("Thran Turbine", fuzzy=False)
                program = compile_best_available_card_program(self.db, original, semantic_registry=engine.semantics,
                    capability_registry=self.registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)

                engine.state.active_player = "A"
                engine.state.phase, engine.state.step = "beginning", "upkeep"
                engine.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))
                engine._enter_step()
                engine._grant_priority("A")
                engine.pump()
                self.assertEqual(1, len(session.state.stack))
                self.assertEqual("triggered_ability", session.state.stack[-1].kind)
                self.assertEqual(0, session.state.players["A"].mana_pool.get("C", 0))
                self.checkpoint(session)
                self.assertEqual("semantic.choice", witnesses.BoundEffectProgramRuntimeTests.resolve(self, session).kind)
                packet = session.packet("pilot:A", full=True)["decision"]
                self.assertEqual("offer_optional_effect", packet["ctx"]["operation"])
                for seat in "BCD":
                    projected = session.packet("pilot:" + seat, full=True)
                    self.assertIsNone(projected.get("decision"))
                    if seat != "C":
                        self.assertNotIn(private.ref, str(projected))
                answer = {"action_id": "choose", "choice": "apply" if apply else "decline"}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:B", answer).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                outcome = session.act("pilot:A", answer)
                self.assertTrue(outcome.ok, outcome.summary)
                player = session.state.players["A"]
                self.assertEqual(2 if apply else 0, player.mana_pool.get("C", 0))
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                self.assertFalse(any(action.get("card") == spell.ref for action in actions))
                activation = next((action for action in actions if action["id"].startswith("activate:" + ball.ref + ":")), None)
                if apply:
                    self.assertEqual(2, restricted_mana(player)[NONCAST_MANA_PAYMENT_RESTRICTION]["C"])
                    self.assertIsNotNone(activation)
                    spent = session.act("pilot:A", {"action_id": activation["id"], "pay": "auto"})
                    self.assertTrue(spent.ok, spent.summary)
                    self.assertEqual(1, session.state.players["A"].mana_pool.get("C", 0))
                    self.assertTrue(session.state.cards[ball.object_id].tapped)
                    self.assertFalse(session.state.cards[source.object_id].tapped)
                    self.assertEqual("activated_ability", session.state.stack[-1].kind)
                else:
                    self.assertIsNone(activation)
                self.replay(session, load=True)

    def test_actual_dark_ritual_uses_stack_and_adds_exact_black_bundle_then_replays(self):
        s=self.session(328001);e=s.engine;source=self.add(e,'Dark Ritual',zone='hand')
        action=self.ready(s,source,{'B':1});self.checkpoint(s);before=authoritative_state_hash(e.state)
        result=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,e.state.players['A'].mana_pool['B']);self.assertEqual(1,len(e.state.stack));self.resolve(s)
        self.assertEqual(3,e.state.players['A'].mana_pool['B']);self.assertEqual('graveyard',e.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_channel_suns_adds_all_five_colors_and_replays(self):
        s=self.session(328002);e=s.engine;source=self.add(e,'Channel the Suns',zone='hand')
        action=self.ready(s,source,{'G':1,'C':3});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual({c:1 for c in 'WUBRG'},{c:e.state.players['A'].mana_pool[c] for c in 'WUBRG'});self.replay(s,load=True)

    def test_actual_culling_sacrifice_is_paid_but_countered_spell_produces_no_mana(self):
        s=self.session(328003);e=s.engine;source=self.add(e,'Culling the Weak',zone='hand');body=self.add(e,'Generic Bound Body');counter=self.add(e,'Counterspell',seat='B',zone='hand')
        action=self.ready(s,source,{'B':1});e.state.players['B'].mana_pool['U']=2;self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',e.state.cards[body.object_id].zone);target=e.state.stack[-1].ref
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:B':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==counter.ref)
        result=s.act('pilot:B',{'action_id':action['id'],'targets':[target],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(0,e.state.players['A'].mana_pool['B']);self.assertEqual('graveyard',e.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_geosurge_restricted_mana_rejects_instant_and_pays_creature(self):
        s=self.session(328004);e=s.engine;source=self.add(e,'Geosurge',zone='hand');shock=self.add(e,'Shock',zone='hand');body=self.add(e,'Blitz Automaton',zone='hand')
        action=self.ready(s,source,{'R':4});self.checkpoint(s)
        instant=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==shock.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(7,e.state.players['A'].mana_pool['R']);self.assertIn('spell.types_any=artifact,creature',str(e.state.players['A'].stats))
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a.get('card')==shock.ref for a in actions))
        before=authoritative_state_hash(e.state);result=s.act('pilot:A',{'action_id':instant['id'],'targets':['B'],'pay':'auto'})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        creature=next(a for a in actions if a.get('card')==body.ref and a.get('cast_method')!='prototype')
        result=s.act('pilot:A',{'action_id':creature['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(0,e.state.players['A'].mana_pool['R']);self.assertEqual('battlefield',e.state.cards[body.object_id].zone);self.replay(s,load=True)

    def test_actual_entry_mana_uses_stack_and_survives_source_departure(self):
        s=self.session(328005);e=s.engine;source=self.add(e,'Burning-Tree Emissary',zone='hand');bounce=self.add(e,'Unsummon',zone='hand')
        private=self.add(e,'Generic Bound Growth',seat='C',zone='hand',ref='private-c-growth')
        action=self.ready(s,source,{'R':2,'U':1});self.checkpoint(s)
        self.assertNotIn(private.ref,str(s.packet('pilot:B',full=True)))
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        for _ in range(40):
            if e.state.cards[source.object_id].zone=='battlefield':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('battlefield',e.state.cards[source.object_id].zone);self.assertEqual(1,len(e.state.stack));self.assertEqual(0,e.state.players['A'].mana_pool['R'])
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==bounce.ref)
        before=authoritative_state_hash(e.state);wrong=s.act('pilot:B',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual('hand',e.state.cards[source.object_id].zone);self.assertEqual((1,1),(e.state.players['A'].mana_pool['R'],e.state.players['A'].mana_pool['G']));self.replay(s,load=True)

    def test_actual_revolt_entry_checks_departure_history(self):
        for departed in (False,True):
            s=self.session(328006+departed);e=s.engine;herbalist=self.add(e,'Hidden Herbalists',zone='hand')
            source=self.add(e,'Culling the Weak',zone='hand') if departed else herbalist
            body=self.add(e,'Generic Bound Body') if departed else None
            action=self.ready(s,source,{'G':2,**({'B':1} if departed else {})});self.checkpoint(s)
            if departed:
                result=s.act('pilot:A',{'action_id':action['id'],'cost_cards':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
                action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==herbalist.ref)
            result=s.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'G':2}});self.assertTrue(result.ok,result.summary);self.resolve(s)
            self.assertEqual(2 if departed else 0,e.state.players['A'].mana_pool['G']);self.replay(s,load=True)

    def test_actual_loyalty_mana_uses_stack_and_counter_cost_then_replays(self):
        s=self.session(328008);e=s.engine;source=self.add(e,'Chandra, Novice Pyromancer',zone='hand')
        action=self.ready(s,source,{'R':1,'C':3});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        current=e.state.cards[source.object_id];before_loyalty=current.counters['loyalty']
        ability=next(a for a in e._activated_abilities(current) if a.effect_text=='Add {R}{R}.')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+current.ref+':') and a['id'].endswith(':'+ability.ability_id))
        result=s.act('pilot:A',{'action_id':action['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(before_loyalty-1,e.state.cards[source.object_id].counters['loyalty']);self.assertEqual(0,e.state.players['A'].mana_pool['R']);self.assertEqual(1,len(e.state.stack))
        self.resolve(s);self.assertEqual(2,e.state.players['A'].mana_pool['R']);self.replay(s,load=True)

    def test_omitting_mana_result_mutant_is_killed_by_actual_ritual(self):
        from quorune.effect_runtime.state_and_permissions import HANDLERS
        with patch.dict(HANDLERS,{'mana':lambda *args,**kwargs:None}):
            with self.assertRaises(AssertionError):self.test_actual_dark_ritual_uses_stack_and_adds_exact_black_bundle_then_replays()

class PostcombatLifeLossManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "life-loss-mana.sqlite3"
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in (
            "bound-effect-program-cards", "fixed-resolution-mana-cards",
            "activation-public-state-cards", "additional-conditional-modifier-cards",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current-turn loss", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_neheb_uses_actual_opponent_life_loss_through_resolution_and_replays(self):
        game = self.session(60311006)
        source = self.add(game.engine, "Neheb, the Eternal")
        lethal = self.add(game.engine, "Shock", zone="hand", ref="neheb-lethal")
        response = self.add(game.engine, "Shock", zone="hand", ref="neheb-response")
        gain = self.add(game.engine, "Revitalize", seat="C", zone="hand")
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="neheb-other-private")
        for index in range(2):
            self.add(game.engine, "Mountain", ref=f"neheb-red-land-{index}")
            self.add(game.engine, "Plains", seat="C", ref=f"neheb-white-land-{index}")
        self.assert_closed(game, source)
        game.state.players["B"].life = 2
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", lethal, ["B"])
        self.settle(game)
        self.assertFalse(game.state.players["B"].in_game)
        self.until(game, lambda current: any(item.source_object_id == source.object_id
                                             and item.kind == "triggered_ability" for item in current.state.stack))
        self.assertEqual("postcombat_main", game.state.phase)
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        self.cast_for(game, "A", response, ["C"])
        self.resolve_top(game)
        self.cast_for(game, "C", gain, [])
        self.resolve_top(game)
        self.assertEqual(41, game.state.players["C"].life)
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self.settle(game)
        self.assertEqual(4, game.state.players["A"].mana_pool["R"])
        self.replay(game, load=True)

    def test_neheb_material_shape_requires_the_own_postcombat_and_history_authority(self):
        from quorune.rules.postcombat_life_loss_mana import CAPABILITY, effect, effect_is_closed

        card = self.db.lookup("Neheb, the Eternal", fuzzy=False)
        exact = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", exact.status, exact.material_residuals)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(item for item in raw["capabilities"] if item["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Postcombat history authority withheld"])
        copy = CapabilityRegistry(raw)
        copy.mark_evidence_verified(self.registry.evidence_fingerprint)
        withheld = compile_oracle_card(card, capability_registry=copy, capability_profile="commander_review")
        self.assertNotEqual("exact", withheld.status)
        self.assertIn(CAPABILITY, str(withheld.material_residuals))
        self.assertTrue(effect_is_closed(effect("R")))
        self.assertFalse(effect_is_closed({**effect("R"), "amount": {"kind": "postcombat_opponent_life_loss_mana_amount",
                                                                      "schema_version": True}}))

    def test_postcombat_mana_mutation_is_killed_by_the_phase_binding(self):
        from types import SimpleNamespace
        from quorune.rules.postcombat_life_loss_mana import CAPABILITY, condition, effect, program_dependencies

        native = SimpleNamespace(effects=(effect("R"),), handlers=(), capability_dependencies=(CAPABILITY,),
            event="step.begin", active_zone="battlefield", event_condition=condition(), target_schema=None,
            cost_schema=None, trigger_limit=None)
        self.assertIn(CAPABILITY, program_dependencies(native))
        native.event_condition = {"all": native.event_condition["all"][1:]}
        with self.assertRaisesRegex(ValueError, "exact untargeted phase"):
            program_dependencies(native)


class PlayerAttackCombatManaTests(InheritedNativeFixture, unittest.TestCase):
    name = "Avatar Roku, Firebender"

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        work = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        sources = {
            "fixed-resolution-mana-cards.json": {cls.name},
            "attack-counter-unblockable-cards.json": {"Firebender Ascension"},
            "event-spell-copy-cards.json": {"Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains"},
        }
        paths = []
        for file, names in sources.items():
            record = json.loads((fixtures / file).read_bytes())
            record["cards"] = [row for row in record["cards"] if row["name"] in names]
            assert names == {row["name"] for row in record["cards"]}, file
            ids = {row["oracle_id"] for row in record["cards"]}
            record["rulings"] = [row for row in record.get("rulings", ()) if row["oracle_id"] in ids]
            path = work / file
            path.write_text(json.dumps(record), encoding="utf-8")
            paths.append(path)
        path = work / "combat-mana.sqlite3"
        build_fixture_database(paths, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Player attack combat mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def send(self, game, actor, command, *, accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def save_record(self, game, comment):
        from hashlib import sha256
        from quorune.record import replay_record

        parent = os.environ.get("QUORUNE_ROKU_RECORD_PARENT")
        if parent is None:
            self.replay(game, load=True)
            return
        path = Path(parent) / comment
        game.save(path)
        verified = replay_record(path, self.db, verify=True)
        self.assertTrue(verified["ok"], verified)
        digest = authoritative_state_hash(game.state)
        self.assertEqual(digest, verified["final_state_hash"])
        path.with_suffix(".receipt.json").write_text(json.dumps({
            "manifest_sha256": sha256((path / "manifest.json").read_bytes()).hexdigest(),
            "final_state_sha256": digest, "commands": len(game.commands),
        }, indent=2) + "\n", encoding="utf-8")

    def begin(self, seed, actor):
        game = self.session(seed)
        source = self.add(game.engine, self.name)
        first = self.add(game.engine, "Grizzly Bears", seat=actor, ref="roku-first-attacker")
        second = self.add(game.engine, "Grizzly Bears", seat=actor, ref="roku-second-attacker")
        private = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="roku-d-private")
        self.assert_closed(game, source)
        self.prepare(game, active=actor, actor=actor)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        return game, source, first, second, private

    def declaration(self, game, source, *, attacker, choices):
        from quorune.rules.attack_declaration_mana import EVENT

        self.send(game, attacker, {"action_id": "attack", "attackers": choices})
        results = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(int(bool(choices)), len(results))
        if results:
            trigger, = results
            self.assertEqual((EVENT, "A", attacker), (trigger.context["event"], trigger.controller, trigger.context["player"]))
            self.assertEqual(len(choices), len(trigger.context["attack_transition"]["assignments"]))
        return results

    def red(self, game, actor="A"):
        return game.state.players[actor].mana_pool.get("R", 0)

    def test_roku_own_declaration_adds_one_bundle_and_pays_existing_activation(self):
        game, source, first, second, _ = self.begin(5083001, "A")
        self.declaration(game, source, attacker="A", choices={first.ref: "B", second.ref: "C"})
        self.save_record(game, "roku-two-attackers-one-trigger-game-record")
        self.settle(game)
        self.assertEqual(6, self.red(game))
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"] == f"activate:{source.ref}:ab2"]
        self.assertIn(first.ref, offer["target_schema"]["legal_refs"])
        self.send(game, "D", {"action_id": offer["id"], "targets": [first.ref], "pay": "auto"}, accepted=False)
        self.send(game, "A", {"action_id": offer["id"], "targets": [first.ref], "pay": "auto"})
        self.assertEqual(3, self.red(game))
        self.settle(game)
        power = lambda: game.engine._numeric_stat(first.object_id, "power")
        self.assertEqual(5, power())
        self.until(game, lambda current: current.state.step == "end_combat")
        self.assertEqual(3, self.red(game))
        self.assertEqual(5, power())
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(0, self.red(game))
        self.assertEqual(5, power())
        from optional_result_followup_fixture import OptionalResultFollowupFixture
        OptionalResultFollowupFixture.finish_turn(self, game)
        self.assertEqual(2, power())
        self.save_record(game, "roku-combat-payment-through-cleanup-game-record")

    def test_roku_opponent_attack_pays_trigger_controller_and_empty_declaration_skips(self):
        for attack in (False, True):
            with self.subTest(attack=attack):
                game, source, first, second, private = self.begin(5083002 + int(attack), "B")
                attackers = {first.ref: "A", second.ref: "C"} if attack else {}
                self.declaration(game, source, attacker="B", choices=attackers)
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                self.settle(game)
                self.assertEqual(6 if attack else 0, self.red(game))
                self.assertEqual(0, self.red(game, "B"))
                self.until(game, lambda current: current.state.step == "end_combat")
                self.assertEqual(6 if attack else 0, self.red(game))
                self.until(game, lambda current: current.state.phase == "postcombat_main")
                self.assertEqual(0, self.red(game))
                self.save_record(game, f"roku-{'opponent' if attack else 'no'}-attack-game-record")

    def test_roku_exact_normal_face_and_declaration_receipt_require_typed_dependencies(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.attack_declaration_mana import CAPABILITY, EVENT
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup(self.name, fuzzy=False)
        self.assertEqual(("normal", ()), (card.layout, card.faces))
        report, semantics = register_card(self.db, self.registry, self.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = {program.ability_id: program for program in semantics.programs_for_oracle(card.oracle_id)}
        self.assertEqual({"trigger:front:n1", "ability:ab2"}, set(programs))
        trigger = programs["trigger:front:n1"]
        self.assertEqual((EVENT, 6, "end_of_combat"),
                         (trigger.event, trigger.effects[0]["amount"], trigger.effects[0]["retain_until"]))
        deps = tuple(value for value in trigger.capability_dependencies if value != CAPABILITY)
        wrong = replace(trigger, capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        admitted = bind_semantic_program_runtime(wrong, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(admitted["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{CAPABILITY}", admitted["blockers"])
        for substitution in (("a player attacks", "a creature enters attacking"),
                             ("Until end of combat", "Until your next turn"),
                             ("add six", "add X")):
            changed = replace(card, oracle_text=card.oracle_text.replace(*substitution))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)


    def ascension_scene(self, seed, actor):
        game = self.session(seed)
        source = self.add(game.engine, self.name)
        ascension = self.add(game.engine, "Firebender Ascension")
        ally = self.add(game.engine, "Grizzly Bears", seat=actor, ref="synergy-first-attacker")
        extra = self.add(game.engine, "Grizzly Bears", seat=actor, ref="synergy-second-attacker")
        for card in (source, ascension):
            self.assert_closed(game, card)
        self.prepare(game, active=actor, actor=actor)
        ascension.counters["quest"] = 3
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        return game, source, ascension, ally, extra

    def test_roku_attack_causes_one_ascension_trigger_and_copy_adds_a_second_bundle(self):
        from quorune.rules.attack_trigger_copy import COPY_CONTEXT

        game, source, ascension, ally, _ = self.ascension_scene(60335081, "A")
        self.declaration(game, source, attacker="A", choices={source.ref: "B", ally.ref: "C"})
        self.assertEqual(2, len(game.state.stack))
        original, observer = game.state.stack
        self.assertEqual((source.object_id, ascension.object_id), (original.source_object_id, observer.source_object_id))
        self.assertEqual(original.stack_id, observer.context[COPY_CONTEXT]["stack_item"]["stack_id"])
        self.assertEqual(0, self.red(game))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(4, game.state.cards[ascension.object_id].counters["quest"])
        self.save_record(game, "roku-ascension-causal-copy-pending-game-record")
        self.send(game, "A", {"action_id": "choose", "choice": "apply"})
        self.assertEqual(2, len(game.state.stack))
        self.assertEqual([source.object_id, source.object_id], [item.source_object_id for item in game.state.stack])
        self.settle(game)
        self.assertEqual(12, self.red(game))
        self.assertEqual(4, game.state.cards[ascension.object_id].counters["quest"])
        self.save_record(game, "roku-ascension-copied-attack-mana-game-record")

    def test_opponent_declaration_keeps_roku_mana_without_ascension_causation(self):
        game, source, ascension, first, second = self.ascension_scene(60335082, "B")
        self.declaration(game, source, attacker="B", choices={first.ref: "A", second.ref: "C"})
        self.assertEqual([source.object_id], [item.source_object_id for item in game.state.stack])
        self.settle(game)
        self.assertEqual((6, 0, 3), (self.red(game), self.red(game, "B"),
                                     game.state.cards[ascension.object_id].counters["quest"]))
        self.save_record(game, "roku-ascension-opponent-negative-game-record")
