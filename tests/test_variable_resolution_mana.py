"""CR106/608.2h: sealed variable mana allocation during resolution."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record

CAPABILITY='mana.production.variable_resolution_choice'


class VariableResolutionManaCompilerTests(unittest.TestCase):
    def test_verified_variable_choice_withdrawal_keeps_fixed_choice(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Variable resolution choice withdrawn'])
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Add X mana of any one color, where X is the number of Shrines you control.',False),('Add two mana in any combination of colors.',True)):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(expected,ir.status=='exact')

    def test_existing_public_amounts_lower_one_color_and_printed_color_combinations(self):
        for text in ('Add X mana of any one color, where X is the number of Shrines you control.',
                'Add X mana in any combination of {R} and/or {G}, where X is the number of creatures you control.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual('choose_mana',node.effects[0]['op'])
                self.assertEqual(3,node.effects[0]['schema_version']);self.assertIn(CAPABILITY,node.capability_dependencies)

    def test_unrepresented_restriction_retention_and_quantity_do_not_widen(self):
        for text in ('Add X mana of any one color, where X is the remembered number.',
                'Add X mana of any one color, where X is the number of creatures you control. Spend this mana only to cast remembered spells.',
                "Add X mana in any combination of colors, where X is the number of creatures you control. Until end of turn, you don't lose this mana as steps and phases end."):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)


class VariableResolutionManaModelTests(unittest.TestCase):
    def test_omitting_printed_color_restriction_is_killed(self):
        from unittest.mock import patch
        from quorune.semantic_choices import resolution_mana as owner
        original=owner._validated
        def omit(effect,query,**kwargs):
            spec,actor=original(effect,query,**kwargs)
            return replace(spec,colors=tuple(sorted('WUBRG'))),actor
        method='test_sealed_large_allocation_rejects_wrong_color_total_and_chooser'
        baseline=unittest.TestResult();VariableResolutionManaModelTests(method).run(baseline)
        self.assertTrue(baseline.wasSuccessful(),baseline.errors+baseline.failures)
        with patch.object(owner,'_validated',side_effect=omit):
            result=unittest.TestResult();VariableResolutionManaModelTests(method).run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def expression(self):
        from quorune.compiler.public_quantity_mana_effects import public_quantity_mana_effect_template
        return public_quantity_mana_effect_template('Add X mana in any combination of {R} and/or {G}, where X is the number of creatures you control.',source_name='Generic')[1][0]

    def test_version_two_encoding_and_fixed_limit_stay_unchanged(self):
        from quorune.resolution_mana_model import ResolutionManaChoiceSpec
        from quorune.semantic_choices.model import ManaBundleChoice
        self.assertEqual({'op':'choose_mana','schema_version':2,'player':'$controller','source':'$source','selection':'combination','amount':2},ResolutionManaChoiceSpec('combination',2).effect())
        with self.assertRaises(ValueError):ResolutionManaChoiceSpec('combination',11)
        with self.assertRaises(ValueError):ManaBundleChoice('mana',('R','G'),11)
        self.assertEqual(27,ManaBundleChoice('mana',('R','G'),27,schema_version=2).choice_schema()['total'])

    def test_expression_and_sealed_amount_codecs_are_distinct_and_closed(self):
        from quorune.variable_resolution_mana_model import VariableResolutionManaChoiceSpec
        raw=self.expression();spec=VariableResolutionManaChoiceSpec.from_effect(raw)
        self.assertEqual(raw,spec.effect());self.assertEqual(('G','R'),spec.colors)
        resolved={**raw,'amount':27};self.assertEqual(27,VariableResolutionManaChoiceSpec.from_effect(resolved,resolved=True).amount)
        for delta in ({'amount':True},{'colors':['C']},{'colors':'GR'},{'schema_version':True},{'amount':-1},{'extra':1}):
            with self.assertRaises((TypeError,ValueError)):VariableResolutionManaChoiceSpec.from_effect({**resolved,**delta},resolved=True)
        with self.assertRaises(ValueError):VariableResolutionManaChoiceSpec.from_effect(resolved)
        with self.assertRaises(ValueError):VariableResolutionManaChoiceSpec.from_effect(raw,resolved=True)

    def test_sealed_large_allocation_rejects_wrong_color_total_and_chooser(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.resolution_mana import prepare_resolution_mana,complete_resolution_mana
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.model import SemanticChoiceError
        effect={**self.expression(),'player':'A','source':'source1','amount':27}
        query=SimpleNamespace(active_seats=tuple('ABCD'))
        context=SimpleNamespace(actor='A',source_ref='source1',stack_ref='S1',query=query)
        prepared=prepare_resolution_mana(effect,context)
        continuation=SimpleNamespace(effect=prepared.continuation_effect)
        completion=complete_resolution_mana(continuation,{'action_id':'choose','mana':{'G':20,'R':7}},query)
        self.assertEqual({'G':20,'R':7},{intent.color:intent.amount for intent in completion.intents})
        for response in ({'mana':{'U':27}},{'mana':{'G':26}},{'mana':{'G':True,'R':26}}):
            with self.assertRaises(SemanticChoiceError):complete_resolution_mana(continuation,response,query)
        with self.assertRaises(SemanticChoiceError):prepare_resolution_mana(effect,SimpleNamespace(actor='B',source_ref='source1',query=query))
        zero=prepare_resolution_mana({**effect,'amount':0},context)
        self.assertIsNone(zero.request);self.assertIsNotNone(zero.auto_continue)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class VariableResolutionManaNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'variable-resolution-mana.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/variable-resolution-mana-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Variable resolution mana',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def test_actual_shrine_trigger_counts_at_resolution_and_seals_pending_one_color(self):
        from quorune.turn_step_owner import TURN_STEPS
        s=self.session(106000201)
        source=self.add(s.engine,'Sanctum of Fruitful Harvest')
        shrine=self.add(s.engine,'Honden of Infinite Rage')
        removal=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        lands=[self.add(s.engine,'Plains',seat='B',ref='mana-choice-plains-'+str(i)) for i in range(2)]
        self.assert_closed(s,source);self.assert_closed(s,removal)
        self.prepare(s,mana={});s.engine.permissions.invalidate_current();s.state.pending_decision=None
        s.state.phase_index=TURN_STEPS.index(('beginning','draw'));s.state.phase='beginning';s.state.step='draw'
        s.engine._grant_priority('A');s.engine.pump();self.checkpoint(s)
        self.until(s,lambda current:current.state.phase=='precombat_main' and bool(current.state.stack))
        self.priority(s,'B')
        for land in lands:
            actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            mana=next(row for row in actions if row.get('mana_ability') and land.ref in row['id'])
            self.assertTrue(s.act('pilot:B',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'B',removal,[shrine.ref]);self.resolve_top(s)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual(1,ctx['amount'])
        self.reject(s,'B',{'action_id':'choose','choice':'G'})
        s=self.replay(s,load=True);self.assertTrue(s.act('pilot:A',{'action_id':'choose','choice':'G'}).ok)
        self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.players['A'].mana_pool.get('G',0))
        self.replay(s,load=True)

    def test_generated_combination_retains_subset_amount_private_projection_and_replay(self):
        s=self.session(106000202)
        spell=self.add(s.engine,'Generic Public Mana Surge',zone='hand')
        for i in range(12):self.add(s.engine,'Memnite',ref='mana-quantity-'+str(i))
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.assert_closed(s,spell);self.prepare(s,mana={'C':1});self.checkpoint(s)
        self.cast(s,'A',spell,[]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.reject(s,'B',{'action_id':'choose','mana':{'R':6,'G':6}})
        for bundle in ({'U':12},{'R':11},{'R':True,'G':11}):self.reject(s,'A',{'action_id':'choose','mana':bundle})
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        s=self.replay(s,load=True);self.assertTrue(s.act('pilot:A',{'action_id':'choose','mana':{'R':5,'G':7}}).ok)
        self.assertIsNone(self.resolve(s));self.assertEqual((5,7),(s.state.players['A'].mana_pool.get('R',0),s.state.players['A'].mana_pool.get('G',0)))
        self.replay(s,load=True)

    def test_generated_zero_quantity_auto_continues_without_choice_or_mana(self):
        s=self.session(106000203);spell=self.add(s.engine,'Generic Public Mana Surge',zone='hand')
        self.assert_closed(s,spell);self.prepare(s,mana={'C':1});self.checkpoint(s)
        self.cast(s,'A',spell,[]);self.assertIsNone(self.resolve(s))
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertFalse(s.state.stack)
        self.replay(s,load=True)

class MainPhaseOpponentManaNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "main-phase-mana.sqlite3"
        support = {"schema_version": 1, "cards": [], "rulings": []}
        for file, name in (("target-land-type-choice-cards.json", "Tidal Warrior"),
                           ("subtype-composite-grant-cards.json", "Island"),
                           ("fixed-qualified-stack-copy-cards.json", "Strionic Resonator")):
            data = json.loads((ROOT / "tests/fixtures" / file).read_text(encoding="utf-8"))
            card, = (row for row in data["cards"] if row["name"] == name)
            support["cards"].append(card)
            support["rulings"].extend(row for row in data["rulings"] if row["oracle_id"] == card["oracle_id"])
        supplement = path.with_name("main-phase-mana-support.json")
        supplement.write_text(json.dumps(support), encoding="utf-8")
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/variable-resolution-mana-cards.json", supplement,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Main-phase mana", [DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
                                  DeckEntry("Generic Bound Plains", 20)], ["Isamaru, Hound of Konda"])

    reject = VariableResolutionManaNativeTests.reject

    def _start_before_main(self, game):
        from quorune.turn_step_owner import TURN_STEPS

        self.prepare(game, mana={})
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.phase_index = TURN_STEPS.index(("beginning", "draw"))
        game.state.phase = "beginning"
        game.state.step = "draw"
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)

    def _main(self, game, phase):
        self.until(game, lambda current: current.state.phase == phase and current.state.pending_decision is not None)

    def _use_history(self, game, source):
        return tuple(event for event in game.engine._current_turn_history("trigger_optional_result_used")
                     if event.object_incarnation == game.state.cards[source.object_id].logical_object_id)

    def _choose_mana(self, game, amount, color):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        action, = [action for action in pending.payload_by_actor["A"]["legal_actions"] if action["id"] == "choose"]
        schema = action["choice_schema"]
        self.assertEqual(set("WUBRG") | {"decline"}, set(schema["legal_values"]))
        self.assertEqual(amount, game.packet("pilot:A", full=True)["decision"]["ctx"]["amount"])
        accepted = game.act("pilot:A", {"action_id": "choose", "choice": color})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_carpet_counts_live_targeted_islands_and_only_commits_paid_color_once(self):
        game = self.session(106000221)
        engine = game.engine
        source = self.add(engine, "Carpet of Flowers", ref="CARPET-SOURCE")
        warrior = self.add(engine, "Tidal Warrior", ref="CARPET-TYPE-CHANGE")
        warrior.acquired_control_turn_count = -1
        land = self.add(engine, "Forest", seat="B", ref="CARPET-LAND")
        self.add(engine, "Island", seat="B", ref="CARPET-TARGET-ISLAND")
        self.add(engine, "Island", seat="C", ref="CARPET-OTHER-ISLAND")
        self.add(engine, "Island", ref="CARPET-OWN-ISLAND")
        private = self.add(engine, "Counterspell", zone="hand", seat="C", ref="CARPET-PRIVATE")
        for card in (source, warrior):
            self.assert_closed(game, card)
        self._start_before_main(game)

        self._main(game, "precombat_main")
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertEqual({"B", "C", "D"}, set(options["legal_refs"]))
        self.reject(game, "A", {"action_id": "choose", "targets": ["A"]})
        self.target(game, "A", ["B"])
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        grant, = [row for row in actions if row["id"].startswith(f"activate:{warrior.ref}:")]
        changed = game.act("pilot:A", {"action_id": grant["id"], "targets": [land.ref], "pay": "auto"})
        self.assertTrue(changed.ok, changed.summary)
        self.resolve_top(game)
        self.assertIn("island", game.engine._effective_type_parts(game.state.cards[land.object_id])[1])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual((), self._use_history(game, source))
        self.reject(game, "B", {"action_id": "choose", "choice": "G"})
        self.reject(game, "A", {"action_id": "choose", "choice": "C"})
        self.reject(game, "A", {"action_id": "choose", "choice": "G", "mana": {"G": 3}})
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self._choose_mana(game, 2, "G")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.players["A"].mana_pool.get("G", 0))
        used, = self._use_history(game, source)
        self.assertEqual("A", used.actor)
        self._main(game, "postcombat_main")
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertFalse(game.state.stack)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("G", 0))
        self.assertEqual((used,), self._use_history(game, source))
        self.replay(game, load=True)

    def test_carpet_zero_declined_or_missed_first_phase_keeps_second_phase_available(self):
        for first in ("zero", "decline", "late_entry"):
            with self.subTest(first=first):
                self._verify_unpaid_first_phase(first)

    def test_carpet_departed_target_does_not_spend_first_phase(self):
        self._verify_unpaid_first_phase("departure")

    def _verify_unpaid_first_phase(self, first):
        game = self.session(106000224)
        engine = game.engine
        source = self.add(engine, "Carpet of Flowers",
                          zone="hand" if first == "late_entry" else "battlefield")
        self.add(engine, "Forest", ref="CARPET-FIRST-OWN")
        self.add(engine, "Forest" if first == "zero" else "Island", seat="B", ref="CARPET-FIRST-OPPONENT")
        self.add(engine, "Island", seat="C", ref="CARPET-SECOND-OPPONENT")
        self.assert_closed(game, source)
        self._start_before_main(game)
        self._main(game, "precombat_main")
        if first == "late_entry":
            self.assertFalse(game.state.stack)
            self.cast(game, "A", source)
            self.assertIsNone(self.resolve(game))
        else:
            self.target(game, "A", ["B"])
            if first == "departure":
                self.priority(game, "B")
                accepted = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                self.assertTrue(accepted.ok, accepted.summary)
            pending = self.resolve(game)
            if first == "decline":
                self.assertEqual("semantic.choice", pending.kind)
                self._choose_mana(game, 1, "decline")
                self.assertIsNone(self.resolve(game))
            else:
                self.assertIsNone(pending)
        self.assertEqual((), self._use_history(game, source))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self._main(game, "postcombat_main")
        self.target(game, "A", ["C"])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose_mana(game, 1, "U")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.players["A"].mana_pool.get("U", 0))
        self.assertEqual(1, len(self._use_history(game, source)))
        self.replay(game, load=True)

    def test_carpet_copied_trigger_rechecks_success_before_original_resolves(self):
        game = self.session(106000227)
        engine = game.engine
        source = self.add(engine, "Carpet of Flowers", ref="CARPET-COPIED")
        copier = self.add(engine, "Strionic Resonator", ref="CARPET-COPIER")
        self.add(engine, "Forest", ref="CARPET-COPY-FOREST-1")
        self.add(engine, "Forest", ref="CARPET-COPY-FOREST-2")
        self.add(engine, "Island", seat="B", ref="CARPET-COPY-ORIGINAL-ISLAND")
        self.add(engine, "Island", seat="C", ref="CARPET-COPY-RETARGET-ISLAND")
        for card in (source, copier):
            self.assert_closed(game, card)
        self._start_before_main(game)
        self._main(game, "precombat_main")
        self.target(game, "A", ["B"])
        original, = game.state.stack
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        copy_action, = [row for row in actions if row["id"].startswith(f"activate:{copier.ref}:")]
        activated = game.act("pilot:A", {"action_id": copy_action["id"], "targets": [original.ref], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        retargeted = game.act("pilot:A", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(retargeted.ok, retargeted.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose_mana(game, 1, "R")
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.stack)
        self.assertEqual(1, len(self._use_history(game, source)))
        self.assertEqual(1, game.state.players["A"].mana_pool.get("R", 0))
        self.replay(game, load=True)

    def test_carpet_requires_closed_main_phase_target_and_success_history(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.target_player_query_amounts import (
            MAIN_PHASE_OPPONENT_MANA_CAPABILITY, main_phase_opponent_mana_program_requirements,
        )

        original = self.db.lookup("Carpet of Flowers")
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("step.begin", node.event)
        self.assertEqual("opponent", node.target_schema["player_relation"])
        for text in (
            original.oracle_text.replace("each of your main phases", "each opponent's main phase"),
            original.oracle_text.replace("added mana", "targeted an opponent"),
            original.oracle_text.replace("target opponent controls", "target opponent owns"),
            original.oracle_text.replace("of any one color", "in any combination of colors"),
        ):
            with self.subTest(text=text):
                candidate = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry,
                                                capability_profile="commander_review")
                self.assertNotEqual("exact", candidate.status)

        session = self.session(106000226)
        source = self.add(session.engine, "Carpet of Flowers")
        self.assert_closed(session, source)
        program, = session.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertIn(MAIN_PHASE_OPPONENT_MANA_CAPABILITY, main_phase_opponent_mana_program_requirements(program))
        for changes in ({"event_condition": None},
                        {"target_schema": {**program.target_schema, "player_relation": "any"}},
                        {"trigger_limit": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                main_phase_opponent_mana_program_requirements(replace(program, **changes))
        remaining = tuple(cap for cap in program.capability_dependencies if cap != MAIN_PHASE_OPPONENT_MANA_CAPABILITY)
        with self.assertRaisesRegex(ValueError, "usage capability dependency"):
            replace(program, capability_dependencies=remaining,
                    capability_closure=self.registry.closure(remaining, profile="commander_review").to_dict())
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for owner in (MAIN_PHASE_OPPONENT_MANA_CAPABILITY, "quantity_expression.target_player_public_query",
                      "trigger.usage.once_per_turn_optional_result"):
            with self.subTest(owner=owner):
                value = deepcopy(raw)
                rule = next(row for row in value["capabilities"] if row["id"] == owner)
                rule.update(status="blocked", blockers=["Core targeted mana authority withdrawn"])
                candidate = compile_oracle_card(original, capability_registry=CapabilityRegistry(value),
                                                capability_profile="commander_review")
                self.assertNotEqual("exact", candidate.status)
