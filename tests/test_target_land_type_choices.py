"""Temporary basic-land-type choices, admission boundaries and native behavior."""
from quorune.errors import GameRuleError
import unittest
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import patch
from quorune.target_land_type_model import *
from quorune.compiler.target_land_type_templates import target_land_type_choice_template
from quorune.rules.target_land_type import target_land_type_node_capabilities,target_land_type_runtime_dependencies,apply_target_land_type
from quorune.semantic_choices.target_land_type import TargetLandTypeChoiceHandler


class TargetLandTypePureTests(unittest.TestCase):
    def effect(self):return {'op':TARGET_LAND_TYPE_OPERATION,'schema_version':1,'card':'land','player':'A'}

    def fixture(self):
        holder={'logical':'land@1','controller':'A','types':('land',),'land_controller':'A'}
        def obj(ref,**kw):
            return SimpleNamespace(object_id='land',ref='land',logical_object_id=holder['logical'],types=holder['types'],phased_out=False,controller=holder['land_controller'],owner='B') if ref=='land' else None
        query=SimpleNamespace(object=obj,stack_object=lambda ref:SimpleNamespace(controller=holder['controller']),active_seats=tuple('ABCD'))
        context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='stack',source_object_id='source',
            source_logical_object_id='source@1',source_ref='source',query=query)
        return holder,query,context

    def test_whole_clause_and_declared_one_land_schema_are_closed(self):
        text='Target land becomes the basic land type of your choice until end of turn.'
        _,effects,schema,mechanics=target_land_type_choice_template(text)
        self.assertIn(TARGET_LAND_TYPE_CAPABILITY,target_land_type_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics))
        from quorune.replacement.immutable import FrozenMap
        self.assertIn(TARGET_LAND_TYPE_CAPABILITY,target_land_type_node_capabilities(effects=effects,target_schema=FrozenMap(schema),mechanic_ids=mechanics))
        for changed in ({'count':True},{'count':2},{'categories':['spell']},{'types_any':['creature']},{'optional':True}):
            with self.subTest(changed=changed):self.assertEqual((),target_land_type_node_capabilities(effects=effects,target_schema={**schema,**changed},mechanic_ids=mechanics))
        for wrong in ('Target land becomes the land type of your choice until end of turn.',
                'Target permanent becomes the basic land type of your choice until end of turn.',
                'Target land becomes the basic land types of your choice until end of turn.',text+' Draw a card.'):
            with self.subTest(wrong=wrong):self.assertIsNone(target_land_type_choice_template(wrong))

    def test_payload_drives_minimum_without_coverage_and_rejects_foreign_bindings(self):
        _,effects,schema,_=target_land_type_choice_template('Target land becomes the basic land type of your choice until end of turn.')
        p=SimpleNamespace(effects=effects,handlers=(),target_schema=schema,coverage=())
        self.assertIn(TARGET_LAND_TYPE_CAPABILITY,target_land_type_runtime_dependencies(p))
        from quorune.replacement.immutable import FrozenMap
        p.effects=tuple(FrozenMap(e) for e in effects);p.target_schema=FrozenMap(schema)
        self.assertIn(TARGET_LAND_TYPE_CAPABILITY,target_land_type_runtime_dependencies(p))
        self.assertIn('continuous.resolution.fixed_characteristics_until_end_of_turn',target_land_type_runtime_dependencies(p))
        for change in ({'player':'$target.0'},{'card':'$source'},{'schema_version':True},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                p.effects=[{**effects[0],**change}];target_land_type_runtime_dependencies(p)

    def test_resolving_controller_chooses_public_type_for_foreign_land(self):
        _,query,context=self.fixture();handler=TargetLandTypeChoiceHandler();prepared=handler.prepare(self.effect(),context)
        self.assertEqual(('Plains','Island','Swamp','Mountain','Forest'),prepared.request.choice.legal_values)
        c=SimpleNamespace(effect=prepared.continuation_effect,semantic_frame=SimpleNamespace(controller='A'),stack_ref='stack')
        result=handler.complete(c,{'land_type':'Island'},query);intent,=result.intents
        self.assertEqual(('A','land','land@1','island'),(intent.actor,intent.object_id,intent.logical_object_id,intent.basic_type))
        self.assertEqual(('source','source@1'),(intent.source_object_id,intent.source_logical_object_id))
        context.actor='B'
        with self.assertRaises(ValueError):handler.prepare(self.effect(),context)

    def test_unknown_type_changed_principal_and_reincarnation_do_not_create_intents(self):
        holder,query,context=self.fixture();h=TargetLandTypeChoiceHandler();p=h.prepare(self.effect(),context)
        c=SimpleNamespace(effect=p.continuation_effect,semantic_frame=SimpleNamespace(controller='A'),stack_ref='stack')
        for response in ({'land_type':'Desert'},{'land_type':True},{'land_type':['Island','Forest']},{'land_type':'Island','card':'other'}):
            with self.subTest(response=response),self.assertRaises(ValueError):h.complete(c,response,query)
        holder['logical']='land@2'
        with self.assertRaises(ValueError):h.complete(c,{'land_type':'Island'},query)
        holder['logical']='land@1';holder['controller']='B'
        with self.assertRaises(ValueError):h.complete(c,{'land_type':'Island'},query)

    def test_intent_rejects_nonbasic_and_incomplete_source_identity(self):
        good=TargetLandTypeSettingIntent('A','land','land','land@1','island','stack','resolution','source','source@1','source')
        for changes in ({'basic_type':'desert'},{'actor':True},{'source_logical_object_id':None},{'source_stack_ref':''}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(good,**changes)

    def test_runtime_delegates_one_type_operation_to_original_incarnation_in_canonical_journal(self):
        card=SimpleNamespace(object_id='land',ref='land',logical_object_id='land@1',zone='battlefield',phased_out=False)
        host=SimpleNamespace(active_seats=tuple('ABCD'),state=SimpleNamespace(cards={'land':card}),_effective_type_parts=lambda c:({'land'},set(),set()))
        intent=TargetLandTypeSettingIntent('A','land','land','land@1','island','stack','resolution')
        with patch('quorune.continuous_effect_state.create_resolution_continuous_effect',return_value='effect') as commit:
            self.assertEqual('effect',apply_target_land_type(host,intent));fields=commit.call_args.kwargs
            self.assertEqual((card,),fields['targets']);self.assertEqual('4',fields['sublayer'])
            operation,=fields['operations'];self.assertEqual('set_basic_land_types',operation.op);self.assertEqual(['island'],list(operation.value))
            commit.reset_mock();card.logical_object_id='land@2'
            with self.assertRaises(GameRuleError):apply_target_land_type(host,intent)
            commit.assert_not_called()


    def test_controlled_production_pins_version_and_declared_controller_predicate(self):
        _,effects,schema,mechanics=target_land_type_choice_template('Target land you control becomes the basic land type of your choice until end of turn.')
        effect,=effects;self.assertEqual(2,effect['schema_version']);self.assertEqual('$controller',effect['controller'])
        self.assertEqual('you',schema['controller_relation'])
        self.assertIn(TARGET_LAND_TYPE_CAPABILITY,target_land_type_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics))
        self.assertEqual((),target_land_type_node_capabilities(effects=effects,target_schema=target_land_type_schema(),mechanic_ids=mechanics))
        program=SimpleNamespace(effects=effects,handlers=(),target_schema=target_land_type_schema(),coverage=())
        with self.assertRaises(ValueError):target_land_type_runtime_dependencies(program)
        for changed in ({'schema_version':True},{'schema_version':2.0},{'controller':'$target.0'},{'controller':True}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):decode_target_land_type({**effect,**changed})
        legacy=target_land_type_choice_template('Target land becomes the basic land type of your choice until end of turn.')[1][0]
        self.assertEqual({'op':TARGET_LAND_TYPE_OPERATION,'schema_version':1,'card':'$target.0','player':'$controller'},legacy)

    def test_controlled_choice_uses_controller_not_owner_and_rechecks_control_change(self):
        holder,query,context=self.fixture();handler=TargetLandTypeChoiceHandler()
        effect={**self.effect(),'schema_version':2,'controller':'A'}
        prepared=handler.prepare(effect,context)
        continuation=SimpleNamespace(effect=prepared.continuation_effect,semantic_frame=SimpleNamespace(controller='A'),stack_ref='stack')
        intent,=handler.complete(continuation,{'land_type':'Island'},query).intents
        self.assertEqual('A',intent.required_controller)
        holder['land_controller']='B'
        with self.assertRaises(ValueError):handler.complete(continuation,{'land_type':'Island'},query)
        with self.assertRaises(ValueError):handler.prepare(effect,context)
        with self.assertRaises(ValueError):replace(intent,required_controller='B')

    def test_controlled_intent_rechecks_live_controller_before_the_journal_write(self):
        card=SimpleNamespace(object_id='land',ref='land',logical_object_id='land@1',zone='battlefield',phased_out=False,controller='A',owner='B')
        host=SimpleNamespace(active_seats=tuple('ABCD'),state=SimpleNamespace(cards={'land':card}),_effective_type_parts=lambda c:({'land'},set(),set()))
        intent=TargetLandTypeSettingIntent('A','land','land','land@1','island','stack','resolution',required_controller='A')
        with patch('quorune.continuous_effect_state.create_resolution_continuous_effect',return_value='effect') as commit:
            self.assertEqual('effect',apply_target_land_type(host,intent));commit.assert_called_once()
            commit.reset_mock();card.controller='B'
            with self.assertRaises(GameRuleError):apply_target_land_type(host,intent)
            commit.assert_not_called()

    def _compile_choice_clause(self,text):
        from quorune.compiler.delayed_draw_templates import fixed_next_turn_upkeep_draw_effect_template
        first=target_land_type_choice_template(text)
        if first is not None:return first
        delayed=fixed_next_turn_upkeep_draw_effect_template(text)
        return delayed.compiled() if delayed is not None else (None,(),None,())

    def test_two_real_clause_owners_keep_choice_before_next_turn_upkeep_draw(self):
        from quorune.compiler.fixed_effect_clause_sequences import fixed_effect_clause_sequence_template
        text="Target land becomes the basic land type of your choice until end of turn.\nDraw a card at the beginning of the next turn's upkeep."
        sequence=fixed_effect_clause_sequence_template(text,compile_clause=self._compile_choice_clause)
        self.assertIsNotNone(sequence)
        _,effects,schema,mechanics=sequence.compiled();choice,delayed=effects
        self.assertEqual(TARGET_LAND_TYPE_OPERATION,choice['op'])
        self.assertEqual(target_land_type_schema(),schema)
        self.assertEqual('delayed_trigger',delayed['op']);self.assertEqual('$controller',delayed['controller'])
        self.assertEqual({'phase':'beginning','step':'upkeep','next_turn_after_sequence':'$turn_sequence'},delayed['condition'])
        self.assertIs(True,delayed['once'])
        self.assertEqual([{'op':'draw','player':'$controller','count':1,'private':True}],delayed['stack']['context']['dynamic_effects'])
        program=SimpleNamespace(effects=effects,handlers=(),target_schema=schema,coverage=())
        self.assertIn(TARGET_LAND_TYPE_CAPABILITY,target_land_type_runtime_dependencies(program))
        from quorune.rules.fixed_effect_clause_shapes import (
            closed_effect_component_capabilities,fixed_effect_clause_sequence_node_capabilities,
        )
        self.assertTrue(fixed_effect_clause_sequence_node_capabilities(
            effects=effects,target_schema=schema,mechanic_ids=mechanics))
        self.assertFalse(closed_effect_component_capabilities(
            (delayed,),target_schema=None,mechanics=set(mechanics)))
        self.assertFalse(fixed_effect_clause_sequence_node_capabilities(
            effects=tuple(reversed(effects)),target_schema=schema,mechanic_ids=mechanics))

    def test_composition_keeps_unowned_delays_recipients_and_extra_clauses_out(self):
        from quorune.compiler.fixed_effect_clause_sequences import fixed_effect_clause_sequence_template
        first='Target land becomes the basic land type of your choice until end of turn.'
        draw="Draw a card at the beginning of the next turn's upkeep."
        for text in (first+' Draw X cards at the beginning of the next turn\'s upkeep.',
                first+' '+draw+' Perform an unrepresented temporal rewrite.',
                first.replace('Target land','Target permanent')+' '+draw,
                first.replace('until end of turn','permanently')+' '+draw):
            with self.subTest(text=text):
                self.assertIsNone(fixed_effect_clause_sequence_template(text,compile_clause=self._compile_choice_clause))



"""Normal original-card admission and actual temporary basic-type choices."""
import json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase,CardRecord
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.target_land_type_model import TARGET_LAND_TYPE_CAPABILITY as CAP
from scripts.build_test_database import build_fixture_database

ORIGINALS=('Dream Thrush','Moonbow Illusionist','Reef Shaman','Mystic Compass','Sea Snidd','Shimmering Mirage','Jinx','Tideshaper Mystic','Grixis Illusionist','Unstable Frontier','Pixie Illusionist')


class TargetLandTypeCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name)/'type-cards.sqlite3'
        build_fixture_database(ROOT/'tests/fixtures/target-land-type-choice-cards.json',p)
        cls.db=CardDatabase(p);cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def program(self,name):
        card=self.db.lookup(name);ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
        p=_compile_best_available_card_program_from_ir(self.db,card,ir,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        return ir,p

    def test_all_original_sources_preserve_complete_cost_body_and_delayed_draw_admission(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                ir,p=self.program(name);self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertEqual('capability_closed',p.trust_closure['trust_basis'],p.trust_closure['blockers'])
                b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review');self.assertTrue(b['strict_capability_ready'],b['blockers'])

    def test_actual_source_span_and_payload_minimum_survive_missing_coverage_hint(self):
        ir,p=self.program('Shimmering Mirage');card=self.db.lookup('Shimmering Mirage')
        node,=(n for n in ir.faces[0].nodes if any(e.get('op')=='choose_target_basic_land_type' for e in n.effects))
        self.assertEqual(node.text,card.oracle_text[node.span.start:node.span.end])
        ability,=(a for a in p.abilities if CAP in a.capability_dependencies)
        deps=tuple(c for c in ability.capability_dependencies if c!=CAP)
        bad=replace(ability,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(bad,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_unknown_plural_duration_and_recipient_siblings_stay_material(self):
        card=self.db.lookup('Shimmering Mirage')
        for text in ('Target land becomes two basic land types of your choice until end of turn.',
                'Target permanent becomes the basic land type of your choice until end of turn.',
                'Target land becomes the basic land type of your choice permanently.',
                'Target land becomes the basic land type of your choice until end of turn. Perform an unrepresented hidden action.'):
            with self.subTest(text=text):self.assertNotEqual('exact',compile_oracle_card(replace(card,oracle_text=text),capability_registry=self.registry,capability_profile='commander_review').status)

    def test_verified_setting_dependency_withdrawal_blocks_new_choice_without_unverifying_the_registry(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        base='continuous.basic_land_type.fixed_setting'
        next(r for r in raw['capabilities'] if r['id']==base).update(status='blocked',blockers=['Withheld type-setting kernel'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Shimmering Mirage'),capability_registry=registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)


class TargetLandTypeNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=seat_actions.RecipientControlNativeTests.reject
    finish=seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name)/'native.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/target-land-type-choice-cards.json',
            ROOT / 'tests/fixtures/fixed-direct-phasing-cards.json',
        ], p)
        cls.db=CardDatabase(p);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Basic type choices',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',12)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def choose_type(self,s,chosen):
        self.assertEqual('semantic.choice',s.state.pending_decision.kind)
        command={'action_id':'choose','land_type':chosen};self.reject(s,'B',command)
        self.reject(s,'A',{'action_id':'choose','land_type':'Desert'})
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));return s

    def activate(self,s,source,target,*,cost_cards=()):
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':') and r.get('target_schema'))
        command={'action_id':offered['id'],'targets':[target.ref],'pay':'auto','cost_cards':list(cost_cards)}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_recipient_untap_registered_activation_replays_and_expires_before_untapping(self):
        session = self.session(305742)
        engine = session.engine
        source = self.add(engine, "Generic Step Cartographer")
        land = self.add(engine, "Generic Bound Plains", seat="B", ref="registered-untap-land")
        land.tapped = True
        private = self.add(engine, "Generic Bound Growth", seat="C", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 4})
        self.checkpoint(session)
        self.activate(session, source, land)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual({"mountain"}, engine._effective_type_parts(session.state.cards[land.object_id])[1])
        session = self.replay(session, load=True)
        self.assertEqual({"mountain"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])
        self.until(session, lambda game: game.state.active_player == "B" and game.state.step == "upkeep")
        live = session.state.cards[land.object_id]
        self.assertEqual(("B", False), (live.controller, live.tapped))
        self.assertEqual({"plains"}, session.engine._effective_type_parts(live)[1])
        self.assertEqual({"intrinsic_plains"}, {ability.ability_id for ability in session.engine._activated_abilities(live)})
        self.finish(session, private)

    def test_recipient_untap_registry_withdrawal_and_template_omission_keep_residuals(self):
        from unittest.mock import patch
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.target_land_type_model import NEXT_UNTAP_LAND_TYPE_CAPABILITY

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        owner = next(row for row in raw["capabilities"] if row["id"] == NEXT_UNTAP_LAND_TYPE_CAPABILITY)
        owner.update(status="blocked", blockers=["Test withdrawal of the recipient untap duration"])
        source = self.db.lookup("Generic Step Cartographer")
        profile = "commander_review"
        normal = compile_oracle_card(source, capability_registry=self.registry, capability_profile=profile)
        self.assertEqual("exact", normal.status)
        withdrawn = compile_oracle_card(source, capability_registry=CapabilityRegistry(raw), capability_profile=profile)
        self.assertNotEqual("exact", withdrawn.status)
        self.assertTrue(any(NEXT_UNTAP_LAND_TYPE_CAPABILITY in blocker
            for residual in withdrawn.material_residuals for blocker in residual.blockers))
        with patch("quorune.compiler.target_land_type_templates.fixed_target_land_type_template", return_value=None):
            omitted = compile_oracle_card(source, capability_registry=self.registry, capability_profile=profile)
        self.assertNotEqual("exact", omitted.status)
        self.assertTrue(omitted.material_residuals)


    def _source_exit_land_fixture(self, seed, *, first_player="A"):
        from copy import deepcopy
        from common import keep_all
        from quorune.model import GameConfig
        from quorune.session import CommanderSession

        session = CommanderSession.create(
            self.db, {seat: deepcopy(self.deck) for seat in "ABCD"},
            first_player=first_player, seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        keep_all(session)
        engine = session.engine
        for player in session.state.players.values():
            for card_id in tuple(player.zones["hand"][:2]):
                engine.move_card(card_id, "library", log=False)
        source = self.add(engine, "Harbor Exit Keeper")
        target = self.add(engine, "Generic Bound Plains", seat="C", ref="departure-target")
        phase = self.add(engine, "Reality Ripple", seat="B", zone="hand")
        bounce = self.add(engine, "Boomerang", seat="B", zone="hand")
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="departure-private")
        for i in range(4):
            self.add(engine, "Tropical Island", seat="B", ref=f"departure-island-{i}")
        for i in range(2):
            self.add(engine, "Generic Bound Plains", ref=f"departure-mana-{i}")
        self.assert_closed(session, source)
        for reply in (phase, bounce):
            self.assert_closed(session, reply)
        self.prepare(session, active=first_player)
        self.checkpoint(session)
        return session, source, target, phase, bounce, private

    def test_source_exit_keeps_land_setting_through_phasing_and_ends_at_departure(self):
        from quorune.continuous_effect_model import ContinuousEffectDuration

        for phase_before_resolution in (False, True):
            with self.subTest(phase_before_resolution=phase_before_resolution):
                session, source, land, phase, bounce, private = self._source_exit_land_fixture(
                    305743 + int(phase_before_resolution), first_player="D",
                )
                original_identity = source.logical_object_id
                self.activate(session, source, land)
                if not phase_before_resolution:
                    self.assertIsNone(self.resolve(session))
                self.cast(session, "B", phase, [source.ref])
                self.assertIsNone(self.resolve(session))
                current_source = session.state.cards[source.object_id]
                self.assertTrue(current_source.phased_out)
                self.assertEqual(current_source.logical_object_id, original_identity)
                live_land = session.state.cards[land.object_id]
                self.assertEqual({"swamp"}, session.engine._effective_type_parts(live_land)[1])
                self.assertEqual(
                    {"intrinsic_swamp"},
                    {ability.ability_id for ability in session.engine._activated_abilities(live_land)},
                )
                spell_effect, = [e for e in session.state.continuous_effects
                                if e.duration is ContinuousEffectDuration.UNTIL_SOURCE_BATTLEFIELD_EXIT]
                self.assertEqual(spell_effect.duration_source.logical_object_id, original_identity)
                self.assertIsNone(spell_effect.duration_history)

                session = self.replay(session, load=True)
                self.assertTrue(session.state.cards[source.object_id].phased_out)
                self.until(session, lambda s: not s.state.cards[source.object_id].phased_out)
                self.assertEqual(session.state.active_player, "A")
                current_source = session.state.cards[source.object_id]
                self.assertFalse(current_source.phased_out)
                self.assertEqual({"swamp"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])

                self.cast(session, "B", session.state.cards[bounce.object_id], [current_source.ref])
                self.assertIsNone(self.resolve(session))
                current_source = session.state.cards[source.object_id]
                self.assertNotEqual(current_source.logical_object_id, original_identity)
                self.assertEqual("hand", current_source.zone)
                self.assertFalse(any(e.effect_id == spell_effect.effect_id for e in session.state.continuous_effects))
                self.assertEqual({"plains"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])

                self.until(session, lambda s: s.state.active_player == "A" and s.state.step == "main")
                self.cast(session, "A", session.state.cards[source.object_id], [])
                self.assertIsNone(self.resolve(session))
                self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
                self.assertEqual({"plains"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])
                self.finish(session, private)

    def test_source_exit_before_resolution_keeps_original_target_incarnation(self):
        from quorune.continuous_effect_model import ContinuousEffectDuration, Layer

        session, source, land, _, bounce, private = self._source_exit_land_fixture(305745)
        original_source, original_land = source.logical_object_id, land.logical_object_id
        self.activate(session, source, land)
        self.cast(session, "B", bounce, [source.ref])
        self.resolve_top(session)
        self.assertNotEqual(session.state.cards[source.object_id].logical_object_id, original_source)
        self.assertIsNone(self.resolve(session))
        effect, = [e for e in session.state.continuous_effects if e.layer is Layer.TYPE]
        self.assertEqual(effect.duration, ContinuousEffectDuration.ZONE_OBJECT)
        self.assertIsNone(effect.duration_source)
        self.assertEqual(effect.locked_objects[0].logical_object_id, original_land)
        self.assertEqual({"swamp"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])

        self.cast(session, "A", session.state.cards[source.object_id], [])
        self.assertIsNone(self.resolve(session))
        current_source = session.state.cards[source.object_id]
        self.assertEqual(current_source.zone, "battlefield")
        self.assertNotEqual(current_source.logical_object_id, original_source)
        self.assertEqual({"swamp"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])
        self.finish(session, private)

    def test_source_exit_owner_withdrawal_preserves_the_source_presence_family(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from test_granted_skulk import record

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        owner = next(row for row in raw["capabilities"] if row["id"] == SOURCE_EXIT_LAND_TYPE_CAPABILITY)
        owner.update(status="blocked", blockers=["Test withdrawal of the battlefield exit owner"])
        profile = "commander_review"
        source = self.db.lookup("Harbor Exit Keeper")
        normal = compile_oracle_card(source, capability_registry=self.registry, capability_profile=profile)
        self.assertEqual("exact", normal.status)
        withdrawn = compile_oracle_card(source, capability_registry=CapabilityRegistry(raw), capability_profile=profile)
        self.assertNotEqual("exact", withdrawn.status)
        self.assertTrue(any(SOURCE_EXIT_LAND_TYPE_CAPABILITY in blocker
                            for residual in withdrawn.material_residuals for blocker in residual.blockers))
        presence = record(
            "{T}: Target land becomes a Swamp for as long as this creature remains on the battlefield.",
            "Creature — Scout",
        )
        current = compile_oracle_card(presence, capability_registry=CapabilityRegistry(raw), capability_profile=profile)
        self.assertTrue(any(SOURCE_PRESENT_LAND_TYPE_CAPABILITY in node.capability_dependencies
                            for face in current.faces for node in face.nodes))
        with patch("quorune.compiler.target_land_type_templates.fixed_target_land_type_template", return_value=None):
            omitted = compile_oracle_card(source, capability_registry=self.registry, capability_profile=profile)
        self.assertNotEqual("exact", omitted.status)

    def test_four_original_tap_sources_choose_for_foreign_dual_land_and_expire_at_cleanup(self):
        s=self.session(305701);land=self.add(s.engine,'Tropical Island',seat='B');land.counters['charge']=2
        sources=[self.add(s.engine,n) for n in ('Dream Thrush','Reef Shaman','Sea Snidd','Mystic Compass')]
        for c in sources:c.acquired_control_turn_count=-1;self.assert_closed(s,c)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.prepare(s,mana={'C':8});self.checkpoint(s)
        for source,chosen in zip(sources,('Island','Swamp','Mountain','Plains')):
            self.activate(s,source,land);self.assertTrue(s.state.cards[source.object_id].tapped)
            self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_type(s,chosen)
            current=s.state.cards[land.object_id];types,subtypes,supertypes=s.engine._effective_type_parts(current)
            self.assertEqual({'land'},types);self.assertEqual({chosen.casefold()},subtypes);self.assertNotIn('basic',supertypes)
            self.assertEqual('Tropical Island',current.printed_name);self.assertEqual(2,current.counters['charge'])
            self.assertEqual({'intrinsic_'+chosen.casefold()},{a.ability_id for a in s.engine._activated_abilities(current)})
        self.until(s,lambda cur:cur.state.active_player!='A')
        self.assertEqual({'forest','island'},s.engine._effective_type_parts(s.state.cards[land.object_id])[1]);self.finish(s,private)

    def test_mirage_draw_follows_the_controller_choice_and_illegal_target_prevents_both(self):
        for bounce in (False,True):
            with self.subTest(bounce=bounce):
                s=self.session(305710+bounce);source=self.add(s.engine,'Shimmering Mirage',zone='hand')
                land=self.add(s.engine,'Tropical Island',seat='B');response=self.add(s.engine,'Boomerang',seat='B',zone='hand')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.assert_closed(s,source)
                self.prepare(s,mana={'C':4,'U':4});s.state.players['B'].mana_pool.update({'U':2})
                before=len(s.state.players['A'].draw_history);self.checkpoint(s);self.cast(s,'A',source,[land.ref])
                if bounce:self.cast(s,'B',response,[land.ref]);self.resolve_top(s)
                decision=self.resolve(s)
                if bounce:
                    self.assertIsNone(decision);self.assertEqual('hand',s.state.cards[land.object_id].zone)
                else:
                    self.assertEqual('semantic.choice',decision.kind);self.assertEqual(before,len(s.state.players['A'].draw_history))
                    s=self.choose_type(s,'Mountain');self.assertEqual({'mountain'},s.engine._effective_type_parts(s.state.cards[land.object_id])[1])
                self.assertEqual(before+(not bounce),len(s.state.players['A'].draw_history));self.finish(s,private)

    def test_jinx_draw_uses_original_controller_at_next_upkeep_after_type_expiry(self):
        s=self.session(305720);source=self.add(s.engine,'Jinx',zone='hand');land=self.add(s.engine,'Tropical Island',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.assert_closed(s,source)
        self.prepare(s,mana={'C':4,'U':2});a=len(s.state.players['A'].draw_history);b=len(s.state.players['B'].draw_history);self.checkpoint(s)
        self.cast(s,'A',source,[land.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_type(s,'Swamp')
        self.assertEqual(a,len(s.state.players['A'].draw_history));s=self.replay(s,load=True)
        self.until(s,lambda cur:cur.state.active_player!='A' and len(cur.state.players['A'].draw_history)==a+1)
        self.assertEqual(b,len(s.state.players['B'].draw_history));self.assertEqual({'forest','island'},s.engine._effective_type_parts(s.state.cards[land.object_id])[1]);self.finish(s,private)

    def test_moonbow_pays_real_land_return_and_returning_its_target_leaves_no_type_choice(self):
        for same in (False,True):
            with self.subTest(same=same):
                s=self.session(305730+same);source=self.add(s.engine,'Moonbow Illusionist')
                own=self.add(s.engine,'Snow-Covered Forest');foreign=self.add(s.engine,'Tropical Island',seat='B')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.assert_closed(s,source)
                self.prepare(s,mana={'C':4});self.checkpoint(s);target=own if same else foreign
                self.activate(s,source,target,cost_cards=[own.ref]);self.assertEqual('hand',s.state.cards[own.object_id].zone)
                pending=self.resolve(s)
                if same:self.assertIsNone(pending)
                else:
                    self.assertEqual('semantic.choice',pending.kind);s=self.choose_type(s,'Plains')
                    self.assertEqual({'plains'},s.engine._effective_type_parts(s.state.cards[foreign.object_id])[1])
                self.finish(s,private)

    def test_setting_preserves_land_creature_types_and_later_granted_mana_abilities(self):
        s=self.session(305740);source=self.add(s.engine,'Reef Shaman');source.acquired_control_turn_count=-1
        land=self.add(s.engine,'Dryad Arbor');land.acquired_control_turn_count=-1;grant=self.add(s.engine,'Joiner Adept')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.assert_closed(s,source);self.assert_closed(s,grant)
        self.prepare(s,mana={});self.checkpoint(s);self.activate(s,source,land)
        self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_type(s,'Island');current=s.state.cards[land.object_id]
        types,subtypes,_=s.engine._effective_type_parts(current);self.assertEqual({'land','creature'},types);self.assertEqual({'dryad','island'},subtypes)
        abilities=[a for a in s.engine._activated_abilities(current) if a.mana_ability]
        self.assertEqual(2,len(abilities));self.assertIn('intrinsic_island',{a.ability_id for a in abilities});self.assertNotIn('intrinsic_forest',{a.ability_id for a in abilities})
        offered=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('source')==current.ref and a.get('ability')=='intrinsic_island')
        before=s.state.players['A'].mana_pool.get('U',0);r=s.act('pilot:A',{'action_id':offered['id']});self.assertTrue(r.ok,r.summary)
        self.assertEqual(before+1,s.state.players['A'].mana_pool.get('U',0));self.finish(s,private)

    def test_departed_activation_source_does_not_cancel_the_original_land_choice(self):
        # CR 113.7a: the already activated ability exists independently.
        s=self.session(305750);source=self.add(s.engine,'Reef Shaman');source.acquired_control_turn_count=-1
        land=self.add(s.engine,'Tropical Island',seat='B');response=self.add(s.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        for card in (source,response):self.assert_closed(s,card)
        self.prepare(s,mana={});s.state.players['B'].mana_pool.update({'U':2});self.checkpoint(s)
        self.activate(s,source,land);self.assertTrue(s.state.cards[source.object_id].tapped)
        self.cast(s,'B',response,[source.ref]);self.resolve_top(s)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_type(s,'Mountain')
        self.assertEqual({'mountain'},s.engine._effective_type_parts(s.state.cards[land.object_id])[1])
        self.assertEqual('hand',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_tideshaper_own_turn_limit_rejects_an_untapped_off_turn_source(self):
        s=self.session(305760);first=self.add(s.engine,'Tideshaper Mystic',ref='tide-own-turn')
        second=self.add(s.engine,'Tideshaper Mystic',ref='tide-off-turn')
        for source in (first,second):source.acquired_control_turn_count=-1;self.assert_closed(s,source)
        land=self.add(s.engine,'Tropical Island',seat='B');private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.prepare(s,mana={});self.checkpoint(s);self.activate(s,first,land)
        self.assertTrue(s.state.cards[first.object_id].tapped)
        self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_type(s,'Mountain')
        self.assertEqual({'mountain'},s.engine._effective_type_parts(s.state.cards[land.object_id])[1])
        self.attack(s,[])
        self.until(s,lambda cur:cur.state.active_player=='B' and cur.state.phase=='precombat_main' and cur.state.step=='main')
        self.priority(s,'A');current=s.state.cards[second.object_id];self.assertFalse(current.tapped)
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+current.ref+':') for row in actions))
        ability,=[a for a in s.engine._activated_abilities(current) if not a.mana_ability]
        self.reject(s,'A',{'action_id':'activate:'+current.ref+':'+ability.ability_id,'targets':[land.ref],'pay':'auto'})
        self.assertFalse(s.state.cards[second.object_id].tapped)
        self.assertEqual({'forest','island'},s.engine._effective_type_parts(s.state.cards[land.object_id])[1]);self.finish(s,private)

    def test_controlled_originals_accept_borrowed_land_and_frontier_can_set_itself(self):
        s=self.session(305770);borrowed=self.add(s.engine,'Tropical Island');borrowed.owner='B'
        foreign=self.add(s.engine,'Snow-Covered Forest',seat='B');foreign.owner='A'
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        sources=[self.add(s.engine,name,ref='controlled-type-'+str(i)) for i,name in enumerate(('Grixis Illusionist','Unstable Frontier','Pixie Illusionist'))]
        for source in sources:source.acquired_control_turn_count=-1;self.assert_closed(s,source)
        self.prepare(s,mana={});self.checkpoint(s)
        for source,chosen in zip(sources,('Mountain','Island','Swamp')):
            offered=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':') and r.get('target_schema'))
            self.assertNotIn(foreign.ref,str(offered['target_schema']))
            self.reject(s,'A',{'action_id':offered['id'],'targets':[foreign.ref],'pay':'auto'})
            target=source if source.printed_name=='Unstable Frontier' else borrowed
            self.activate(s,source,target);self.assertTrue(s.state.cards[source.object_id].tapped)
            self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_type(s,chosen)
            self.assertEqual({chosen.casefold()},s.engine._effective_type_parts(s.state.cards[target.object_id])[1])
            if source.printed_name=='Unstable Frontier':
                self.assertEqual({'intrinsic_island'},{a.ability_id for a in s.engine._activated_abilities(s.state.cards[source.object_id])})
        self.assertEqual('A',s.state.cards[borrowed.object_id].controller);self.assertEqual('B',s.state.cards[borrowed.object_id].owner)
        self.until(s,lambda cur:cur.state.active_player!='A')
        self.assertEqual({'forest','island'},s.engine._effective_type_parts(s.state.cards[borrowed.object_id])[1])
        self.finish(s,private)

    def test_control_change_response_distinguishes_source_controller_from_target_controller(self):
        for source_changes in (False,True):
            with self.subTest(source_changes=source_changes):
                s=self.session(305780+source_changes);source=self.add(s.engine,'Grixis Illusionist');source.acquired_control_turn_count=-1
                land=self.add(s.engine,'Tropical Island');give=self.add(s.engine,'Donate',zone='hand');flash=self.add(s.engine,'Vedalken Orrery')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
                for card in (source,give,flash):self.assert_closed(s,card)
                self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s);self.activate(s,source,land)
                changed=source if source_changes else land
                self.cast(s,'A',give,['B',changed.ref]);self.resolve_top(s)
                self.assertEqual('B',s.state.cards[changed.object_id].controller)
                pending=self.resolve(s)
                if source_changes:
                    self.assertEqual('semantic.choice',pending.kind);s=self.choose_type(s,'Mountain')
                    self.assertEqual({'mountain'},s.engine._effective_type_parts(s.state.cards[land.object_id])[1])
                else:
                    self.assertIsNone(pending);self.assertEqual({'forest','island'},s.engine._effective_type_parts(s.state.cards[land.object_id])[1])
                self.assertTrue(s.state.cards[source.object_id].tapped);self.finish(s,private)

    def test_missing_resolution_journal_commit_is_killed_by_complete_original_actions(self):
        with patch('quorune.rules.target_land_type.apply_target_land_type',return_value=None):
            with self.assertRaises(AssertionError):self.test_four_original_tap_sources_choose_for_foreign_dual_land_and_expire_at_cleanup()

    def test_actual_tidal_warrior_replaces_printed_mana_and_expires(self):
        session = self.session(305761)
        engine = session.engine
        warrior = self.add(engine, "Tidal Warrior")
        warrior.acquired_control_turn_count = -1
        land = self.add(engine, "Dimir Guildgate")
        land.counters["charge"] = 2
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="fixed-land-private")
        for card in (warrior, land):
            self.assert_closed(session, card)
        printed = {a.ability_id for a in engine._activated_abilities(land)}
        self.assertTrue(printed)
        logical = land.logical_object_id
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, warrior, land)
        self.assertTrue(session.state.cards[warrior.object_id].tapped)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[land.object_id]
        self.assertEqual(({"land"}, {"island"}, set()), session.engine._effective_type_parts(current))
        self.assertEqual(logical, current.logical_object_id)
        self.assertEqual(2, current.counters["charge"])
        self.assertEqual({"intrinsic_island"}, {a.ability_id for a in session.engine._activated_abilities(current)})
        offer = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row.get("source") == current.ref and row.get("ability") == "intrinsic_island")
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players["A"].mana_pool["U"])
        self.until(session, lambda game: game.state.active_player != "A")
        restored = session.state.cards[land.object_id]
        self.assertEqual({"gate"}, session.engine._effective_type_parts(restored)[1])
        self.assertEqual(printed, {a.ability_id for a in session.engine._activated_abilities(restored)})
        self.finish(session, private)

    def test_actual_sealock_monstrosity_adds_the_island_type_past_cleanup(self):
        session = self.session(305762)
        engine = session.engine
        monster = self.add(engine, "Sealock Monster")
        land = self.add(engine, "Dimir Guildgate", seat="B")
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="sealock-hidden")
        for card in (monster, land):
            self.assert_closed(session, card)
        printed = {a.ability_id for a in engine._activated_abilities(land)}
        self.assertTrue(printed)
        logical = land.logical_object_id
        self.prepare(session, mana={"C": 5, "U": 2})
        self.checkpoint(session)
        offer = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row["id"].startswith("activate:" + monster.ref + ":"))
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        self.target(session, "A", [land.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(3, session.state.cards[monster.object_id].counters["+1/+1"])
        current = session.state.cards[land.object_id]
        self.assertEqual(({"land"}, {"gate", "island"}, set()), session.engine._effective_type_parts(current))
        self.assertEqual(logical, current.logical_object_id)
        self.assertEqual(printed | {"intrinsic_island"},
                         {a.ability_id for a in session.engine._activated_abilities(current)})
        self.until(session, lambda game: game.state.active_player != "A")
        self.assertEqual({"gate", "island"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])
        self.finish(session, private)


    def _land_type_offer(self, session, source):
        legal = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (
            row for row in legal
            if row["id"].startswith("activate:" + source.ref + ":")
            and (row.get("target_schema") or {}).get("types_any") == ["land"]
        )
        return offer

    def test_paid_island_sacrifices_and_opponent_relation_are_exact(self):
        session = self.session(305763)
        engine = session.engine
        limited = self.add(engine, "Kukemssa Serpent")
        ordinary = self.add(engine, "Dreamwinder")
        islands = [self.add(engine, "Tropical Island", ref=f"fixed-cost-island-{n}") for n in range(4)]
        wrong_cost = self.add(engine, "Snow-Covered Forest")
        gifted = self.add(engine, "Dimir Guildgate", seat="B", ref="gifted-gate")
        opponent = self.add(engine, "Tropical Island", seat="D", ref="opposing-dual")
        donate = self.add(engine, "Donate", seat="B", zone="hand")
        flash = self.add(engine, "Vedalken Orrery", seat="B")
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="island-payment-private")
        for card in (limited, ordinary, donate, flash):
            self.assert_closed(session, card)
        self.prepare(session, mana={"U": 3})
        session.state.players["B"].mana_pool.update({"C": 2, "U": 1})
        self.checkpoint(session)
        offer = self._land_type_offer(session, limited)
        self.assertIn(gifted.ref, offer["target_schema"]["legal_refs"])
        self.assertNotIn(islands[0].ref, offer["target_schema"]["legal_refs"])
        self.assertNotIn(wrong_cost.ref, offer["cost_summary"]["choose_cost"][0]["legal_refs"])
        command = {"action_id": offer["id"], "targets": [gifted.ref], "pay": "auto",
                   "cost_cards": [islands[0].ref]}
        self.reject(session, "C", command)
        self.reject(session, "A", {**command, "targets": [islands[0].ref]})
        self.reject(session, "A", {**command, "cost_cards": [wrong_cost.ref]})
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", session.state.cards[islands[0].object_id].zone)

        self.cast_for(session, "B", donate, ["A", gifted.ref])
        self.resolve_top(session)
        self.assertEqual("A", session.state.cards[gifted.object_id].controller)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"gate"}, session.engine._effective_type_parts(session.state.cards[gifted.object_id])[1])
        self.assertEqual("graveyard", session.state.cards[islands[0].object_id].zone)

        self.priority(session, "A")
        self.activate(session, limited, opponent, cost_cards=[islands[1].ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"island"}, session.engine._effective_type_parts(session.state.cards[opponent.object_id])[1])
        self.priority(session, "A")
        self.activate(session, ordinary, gifted, cost_cards=[islands[2].ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"island"}, session.engine._effective_type_parts(session.state.cards[gifted.object_id])[1])
        self.assertEqual("B", session.state.cards[gifted.object_id].owner)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("U", 0))
        self.assertEqual("battlefield", session.state.cards[islands[3].object_id].zone)
        for island in islands[:3]:
            self.assertEqual("graveyard", session.state.cards[island.object_id].zone)
        self.finish(session, private)

    def test_paid_counter_and_zombie_costs_and_later_type_settings(self):
        session = self.session(305764)
        engine = session.engine
        flood = self.add(engine, "Floodchaser", zone="hand")
        trail = self.add(engine, "Zombie Trailblazer")
        bear = self.add(engine, "Grizzly Bears")
        land = self.add(engine, "Dimir Guildgate", seat="D")
        aquitects = self.add(engine, "Streambed Aquitects")
        recluse = self.add(engine, "Kavu Recluse")
        slimy = self.add(engine, "Slimy Kavu")
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="other-types-private")
        for card in (flood, trail, aquitects, recluse, slimy):
            self.assert_closed(session, card)
        for card in (aquitects, recluse, slimy):
            card.acquired_control_turn_count = -1
        self.prepare(session, mana={"C": 5, "U": 2})
        self.checkpoint(session)
        self.cast_for(session, "A", flood, reserve={"U": 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(6, session.state.cards[flood.object_id].counters["+1/+1"])
        self.priority(session, "A")
        self.activate(session, flood, land)
        self.assertEqual(5, session.state.cards[flood.object_id].counters["+1/+1"])
        self.assertEqual(0, session.state.players["A"].mana_pool.get("U", 0))
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"island"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])

        self.priority(session, "A")
        offer = self._land_type_offer(session, trail)
        self.assertEqual({trail.ref}, set(offer["cost_summary"]["choose_cost"][0]["legal_refs"]))
        command = {"action_id": offer["id"], "targets": [land.ref], "pay": "auto", "cost_cards": [trail.ref]}
        self.reject(session, "A", {**command, "cost_cards": [bear.ref]})
        self.assertFalse(session.state.cards[trail.object_id].tapped)
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(session.state.cards[trail.object_id].tapped)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"swamp"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])

        for source, expected in ((aquitects, "island"), (recluse, "forest"), (slimy, "swamp")):
            self.priority(session, "A")
            offer = self._land_type_offer(session, source)
            accepted = session.act("pilot:A", {"action_id": offer["id"], "targets": [land.ref], "pay": "auto"})
            self.assertTrue(accepted.ok, accepted.summary)
            self.assertTrue(session.state.cards[source.object_id].tapped)
            self.assertIsNone(self.resolve(session))
            current = session.state.cards[land.object_id]
            self.assertEqual({expected}, session.engine._effective_type_parts(current)[1])
            self.assertEqual({"intrinsic_" + expected},
                             {a.ability_id for a in session.engine._activated_abilities(current)})
        self.finish(session, private)

    def test_paid_indefinite_setting_fault_retries_and_cannot_follow_a_new_land(self):
        from quorune import continuous_effect_state
        from quorune.record import authoritative_state_hash

        session = self.session(305765)
        engine = session.engine
        monk = self.add(engine, "Thelonite Monk")
        monk.acquired_control_turn_count = -1
        green = self.add(engine, "Grizzly Bears")
        land = self.add(engine, "Dimir Guildgate")
        grant = self.add(engine, "Joiner Adept")
        bounce = self.add(engine, "Boomerang", seat="B", zone="hand")
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="indefinite-type-private")
        for card in (monk, grant, bounce):
            self.assert_closed(session, card)
        logical = land.logical_object_id
        initial_abilities = {ability.ability_id for ability in engine._activated_abilities(land)}
        self.prepare(session)
        session.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(session)
        self.activate(session, monk, land, cost_cards=[green.ref])
        self.assertEqual("graveyard", session.state.cards[green.object_id].zone)
        self.assertTrue(session.state.cards[monk.object_id].tapped)
        original = continuous_effect_state.create_resolution_continuous_effect_components
        reached = []

        def fault(host, *args, **kwargs):
            result = original(host, *args, **kwargs)
            reached.extend(effect.duration.value for effect in result)
            raise GameRuleError("Injected exception after land-type journal append")

        with patch.object(continuous_effect_state, "create_resolution_continuous_effect_components", fault):
            for _ in range(8):
                principal = session.pending_principals()[0]
                before = authoritative_state_hash(session.state)
                rejected = session.act(principal, {"action_id": "pass"})
                if not rejected.ok:
                    self.assertIn("Injected exception after land-type journal append", rejected.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    break
            else:
                self.fail("The actual land setting did not reach its journal")
        self.assertEqual(["zone_object"], reached)
        self.assertEqual("graveyard", session.state.cards[green.object_id].zone)
        self.assertEqual({"gate"}, session.engine._effective_type_parts(session.state.cards[land.object_id])[1])
        session = self.replay(session, load=True)
        accepted = session.act(principal, {"action_id": "pass"})
        self.assertTrue(accepted.ok, accepted.summary)
        current = session.state.cards[land.object_id]
        self.assertEqual({"forest"}, session.engine._effective_type_parts(current)[1])
        self.assertEqual(logical, current.logical_object_id)
        abilities = {a.ability_id for a in session.engine._activated_abilities(current)}
        self.assertIn("intrinsic_forest", abilities)
        self.assertTrue(initial_abilities & abilities, "The granted mana ability remains")
        self.assertFalse(initial_abilities <= abilities, "The printed ability disappears")

        self.cast_for(session, "B", bounce, [current.ref])
        self.assertIsNone(self.resolve(session))
        self.priority(session, "A")
        current = session.state.cards[land.object_id]
        self.assertEqual("hand", current.zone)
        accepted = session.act("pilot:A", {"action_id": "play-land:" + current.ref})
        self.assertTrue(accepted.ok, accepted.summary)
        returned = session.state.cards[land.object_id]
        self.assertNotEqual(logical, returned.logical_object_id)
        self.assertEqual({"gate"}, session.engine._effective_type_parts(returned)[1])
        self.assertEqual(initial_abilities, {a.ability_id for a in session.engine._activated_abilities(returned)})
        self.finish(session, private)


    def test_actual_tundra_kavu_offers_exact_two_types_and_resumes_both(self):
        session = self.session(305766)
        source = self.add(session.engine, "Tundra Kavu")
        source.acquired_control_turn_count = -1
        land = self.add(session.engine, "Dimir Guildgate", seat="B")
        private = self.add(session.engine, "Counterspell", seat="C", zone="hand", ref="limited-land-private")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.activate(session, source, land)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        options = session.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]
        self.assertEqual(["Plains", "Island"], options["legal_values"])
        self.reject(session, "B", {"action_id": "choose", "land_type": "Plains"})
        self.reject(session, "A", {"action_id": "choose", "land_type": "Forest"})

        for name in ("Plains", "Island"):
            with self.subTest(name=name):
                fork = self.replay(session, load=True)
                accepted = fork.act("pilot:A", {"action_id": "choose", "land_type": name})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(fork))
                current = fork.state.cards[land.object_id]
                self.assertEqual({name.casefold()}, fork.engine._effective_type_parts(current)[1])
                self.assertEqual({"intrinsic_" + name.casefold()},
                                 {a.ability_id for a in fork.engine._activated_abilities(current)})
                self.until(fork, lambda game: game.state.active_player != "A")
                self.assertEqual({"gate"}, fork.engine._effective_type_parts(fork.state.cards[land.object_id])[1])
                self.finish(fork, private)


    def test_actual_kicked_tide_shaper_starts_only_with_its_source_and_ends_on_phasing(self):
        from quorune.continuous_effect_model import ContinuousEffectDuration

        session = self.session(305767)
        tide = self.add(session.engine, "Tide Shaper", zone="hand")
        gate = self.add(session.engine, "Dimir Guildgate", seat="B")
        haystack = self.add(session.engine, "Haystack")
        bounce = self.add(session.engine, "Boomerang", seat="B", zone="hand")
        private = self.add(session.engine, "Counterspell", seat="C", zone="hand", ref="tide-shaper-private")
        for card in (tide, haystack, bounce):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 3, "U": 1})
        session.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(session)
        offer = self.action(session, "A", tide)
        self.assertEqual({"normal", "kicked"}, {choice["id"] for choice in offer["cost_options"]})
        normal = self.replay(session, load=True)
        result = normal.act("pilot:A", {"action_id": offer["id"], "cost_option": "normal", "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(normal))
        self.assertEqual({"gate"}, normal.engine._effective_type_parts(normal.state.cards[gate.object_id])[1])
        self.assertEqual(1, normal.engine._numeric_stat(tide.object_id, "power"))
        self.finish(normal, private)

        paid = session.act("pilot:A", {"action_id": offer["id"], "cost_option": "kicked", "pay": "auto",
                                      "reserve": {"C": 2}})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        self.target(session, "A", [gate.ref])
        late = self.replay(session, load=True)
        self.cast_for(late, "B", bounce, [tide.ref])
        self.resolve_top(late)
        self.assertEqual("hand", late.state.cards[tide.object_id].zone)
        self.assertIsNone(self.resolve(late))
        self.assertEqual({"gate"}, late.engine._effective_type_parts(late.state.cards[gate.object_id])[1])
        self.finish(late, private)

        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"island"}, session.engine._effective_type_parts(session.state.cards[gate.object_id])[1])
        self.assertEqual(2, session.engine._numeric_stat(tide.object_id, "power"))
        effects = [row for row in session.state.continuous_effects
                   if row.duration is ContinuousEffectDuration.UNTIL_SOURCE_LEAVES and row.source_id == tide.object_id]
        self.assertEqual(1, len(effects))
        self.assertEqual(tide.logical_object_id, effects[0].duration_source.logical_object_id)
        self.priority(session, "A")
        self.activate(session, haystack, tide)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[tide.object_id].phased_out)
        self.assertEqual({"gate"}, session.engine._effective_type_parts(session.state.cards[gate.object_id])[1])
        self.assertFalse(any(row.effect_id == effects[0].effect_id for row in session.state.continuous_effects))
        self.finish(session, private)


    def test_actual_giant_orders_type_and_exile_and_rolls_back_the_complete_result(self):
        from quorune.record import authoritative_state_hash
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin

        session = self.session(400700701)
        giant = self.add(session.engine, "Cyclopean Giant", zone="hand")
        gate = self.add(session.engine, "Dimir Guildgate", seat="B")
        murder = self.add(session.engine, "Murder", seat="B", zone="hand")
        bounce = self.add(session.engine, "Boomerang", seat="B", zone="hand")
        private = self.add(session.engine, "Counterspell", seat="C", zone="hand", ref="death-type-private")
        for card in (giant, murder, bounce):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 2, "B": 2})
        session.state.players["B"].mana_pool.update({"C": 1, "B": 2, "U": 2})
        self.checkpoint(session)
        self.cast_for(session, "A", giant)
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "B", murder, [giant.ref], reserve={"U": 2})
        self.assertEqual("semantic.target", self.resolve(session).kind)
        self.target(session, "A", [gate.ref])

        lost = self.replay(session, load=True)
        self.cast_for(lost, "B", bounce, [gate.ref])
        self.resolve_top(lost)
        self.assertIsNone(self.resolve(lost))
        self.assertEqual("hand", lost.state.cards[gate.object_id].zone)
        self.assertEqual("graveyard", lost.state.cards[giant.object_id].zone)
        self.finish(lost, private)

        move = SemanticChoiceIntentHostMixin.move_objects_simultaneously_intent
        passed = []

        def fail_after_commit(host, intent):
            result = move(host, intent)
            if intent.destination != "exile":
                return result
            self.assertEqual({"swamp"}, host._effective_type_parts(host.state.cards[gate.object_id])[1])
            self.assertEqual("exile", host.state.cards[giant.object_id].zone)
            passed.extend(intent.object_refs)
            raise GameRuleError("Injected exception after the original exile")

        with patch.object(SemanticChoiceIntentHostMixin, "move_objects_simultaneously_intent", fail_after_commit):
            for _ in range(8):
                principal = session.pending_principals()[0]
                before = authoritative_state_hash(session.state)
                rejected = session.act(principal, {"action_id": "pass"})
                if not rejected.ok:
                    self.assertIn("Injected exception after the original exile", rejected.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    break
            else:
                self.fail("The original did not reach its final exile")
        self.assertEqual(1, len(passed))
        self.assertEqual({"gate"}, session.engine._effective_type_parts(session.state.cards[gate.object_id])[1])
        self.assertEqual("graveyard", session.state.cards[giant.object_id].zone)

        session = self.replay(session, load=True)
        accepted = session.act(principal, {"action_id": "pass"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual({"swamp"}, session.engine._effective_type_parts(session.state.cards[gate.object_id])[1])
        self.assertEqual("exile", session.state.cards[giant.object_id].zone)
        self.finish(session, private)

    def test_actual_giant_refers_to_both_physical_cards_after_mutation_changes_its_name(self):
        from quorune.event_card_return import DEATH_EXILE_ARRIVALS

        session = self.session(400700702)
        giant = self.add(session.engine, "Cyclopean Giant")
        spider = self.add(session.engine, "Glowstone Recluse", zone="hand")
        gate = self.add(session.engine, "Dimir Guildgate", seat="B")
        murder = self.add(session.engine, "Murder", seat="B", zone="hand")
        private = self.add(session.engine, "Counterspell", seat="C", zone="hand", ref="merged-death-private")
        for card in (giant, spider, murder):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 3, "G": 1})
        session.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(session)
        offer = self.action(session, "A", spider)
        cost, = [row for row in offer["cost_options"] if row["id"].startswith("mutate")]
        accepted = session.act("pilot:A", {"action_id": offer["id"], "cost_option": cost["id"],
                                         "targets": [giant.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.reject(session, "B", {"action_id": "choose", "choice": "top"})
        accepted = session.act("pilot:A", {"action_id": "choose", "choice": "top"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        root = session.state.cards[giant.object_id]
        self.assertEqual("Glowstone Recluse", session.engine._effective_card_data(root)["name"])
        self.cast_for(session, "B", murder, [root.ref])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual("composition_departure_order",
                         session.packet("pilot:A", full=True)["decision"]["ctx"]["operation"])
        session = self.replay(session, load=True)
        self.reject(session, "B", {"action_id": "choose", "cards": [giant.ref, spider.ref]})
        ordered = session.act("pilot:A", {"action_id": "choose", "cards": [spider.ref, giant.ref]})
        self.assertTrue(ordered.ok, ordered.summary)
        self.target(session, "A", [gate.ref])
        arrivals = session.state.stack[-1].context[DEATH_EXILE_ARRIVALS]
        self.assertEqual({giant.object_id, spider.object_id}, {row["object_id"] for row in arrivals})
        self.assertEqual({("graveyard", session.state.cards[row["object_id"]].logical_object_id)
                          for row in arrivals}, {(row["zone"], row["logical_object_id"]) for row in arrivals})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(["exile", "exile"], [session.state.cards[card.object_id].zone for card in (giant, spider)])
        self.assertEqual({"swamp"}, session.engine._effective_type_parts(session.state.cards[gate.object_id])[1])
        self.finish(session, private)


class FixedTargetLandTypeCompilerTests(unittest.TestCase):
    def test_fixed_basic_type_uses_an_independent_version_and_a_complete_land_mask(self):
        from quorune.compiler.target_land_type_templates import fixed_target_land_type_template
        from quorune.resolution_characteristic_model import fixed_resolution_characteristic_instruction
        from quorune.resolution_color_model import TARGET_COLOR_VERSIONS
        from quorune.rules.capabilities import capability_dependencies_for_node

        self.assertNotIn(FIXED_TARGET_LAND_TYPE_VERSION, TARGET_COLOR_VERSIONS)
        for text, operation, lifetime, relation in (
            ("Target land becomes an Island until end of turn.", "set_basic_land_types", "until_end_of_turn", "any"),
            ("Target land an opponent controls becomes an Island until end of turn.",
             "set_basic_land_types", "until_end_of_turn", "opponent"),
            ("Target land becomes a Forest.", "set_basic_land_types", "zone_object", "any"),
            ("Target land becomes an Island in addition to its other types.", "add_types", "zone_object", "any"),
        ):
            with self.subTest(text=text):
                _template, effects, target, mechanics = fixed_target_land_type_template(text)
                spec, reference = fixed_resolution_characteristic_instruction(effects[0])
                self.assertEqual("$target.0", reference)
                self.assertEqual((lifetime, relation), (spec.duration, spec.controller_relation))
                self.assertEqual(operation, spec.layer_operations()[0][2][0].op)
                owners = capability_dependencies_for_node(
                    effects=effects, target_schema=target, mechanic_ids=mechanics,
                )
                self.assertIn(FIXED_TARGET_LAND_TYPE_CAPABILITY, owners)

    def test_fixed_basic_type_infers_the_raw_owner_and_rejects_schema_changes(self):
        from quorune.compiler.target_land_type_templates import fixed_target_land_type_template
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies

        _template, effects, target, _mechanics = fixed_target_land_type_template(
            "Target land an opponent controls becomes an Island until end of turn.",
        )
        raw = {
            "effects": effects, "target_schema": target, "handlers": (), "coverage": (),
            "event": "activate", "active_zone": "battlefield", "event_condition": None,
        }
        self.assertIn(FIXED_TARGET_LAND_TYPE_CAPABILITY,
                      fixed_resolution_characteristics_runtime_dependencies(SimpleNamespace(**raw)))
        for invalid in (
            {"target_schema": {**target, "count": True}},
            {"target_schema": {**target, "controller_relation": "you"}},
            {"effects": ({**effects[0], "card": "$target.1"},)},
            {"effects": ({"op": "offer_optional_effect", "effects": list(effects)},)},
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "closed single target"):
                fixed_resolution_characteristics_runtime_dependencies(SimpleNamespace(**{**raw, **invalid}))

    def test_fixed_basic_type_leaves_other_choice_and_lifetime_text_to_its_owners(self):
        from quorune.compiler.target_land_type_templates import fixed_target_land_type_template

        for text in (
            "Target land becomes a Plains or an Island until end of turn.",
            "Target land becomes an Island until your next untap step.",
            "Target land becomes an Island until its controller’s next two untap steps.",
            "Target land becomes an Island until its controller’s next untap step in addition to its other types.",
            "Target land you control becomes an Island until its controller’s next untap step.",
            "Target land becomes an Island for as long as this creature is tapped.",
            "Target land becomes an Island until end of turn. Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_target_land_type_template(text))
        self.assertIsNotNone(target_land_type_choice_template(
            "Target land becomes the basic land type of your choice until end of turn.",
        ))

    def test_recipient_untap_type_setting_requires_its_own_closed_duration(self):
        from quorune.compiler.target_land_type_templates import fixed_target_land_type_template
        from quorune.resolution_characteristic_model import fixed_resolution_characteristic_instruction
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies
        from quorune.semantics import SemanticProgram
        from quorune.target_land_type_model import NEXT_UNTAP_LAND_TYPE_CAPABILITY

        parsed = fixed_target_land_type_template(
            "Target land becomes an Island until its controller’s next untap step.",
        )
        self.assertIsNotNone(parsed)
        _, effects, schema, _ = parsed
        spec, reference = fixed_resolution_characteristic_instruction(effects[0])
        self.assertEqual(("until_recipient_controller_untap", "$target.0"), (spec.duration, reference))
        self.assertEqual(effects[0], spec.to_effect())
        program = SemanticProgram(
            key="fixture:recipient-untap-land-type", label="Fixture recipient untap land type",
            effects=effects, event="resolve", active_zone="stack", target_schema=schema,
        )
        self.assertIn(NEXT_UNTAP_LAND_TYPE_CAPABILITY, fixed_resolution_characteristics_runtime_dependencies(program))
        program.target_schema = {**schema, "count": 2}
        with self.assertRaisesRegex(ValueError, "closed single target"):
            fixed_resolution_characteristics_runtime_dependencies(program)

    def test_fixed_type_round_trips_and_compiler_omission_loses_the_instruction(self):
        import json

        from quorune.oracle_ir import compile_oracle_card
        from quorune.resolution_characteristic_model import fixed_resolution_characteristic_instruction
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record

        card = record("Target land becomes an Island until end of turn.", "Instant")
        registry = load_default_capability_registry()
        profile = "commander_review"
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile=profile)
        original, = [
            e for face in ir.faces for node in face.nodes for e in node.effects
            if e.get("schema_version") == FIXED_TARGET_LAND_TYPE_VERSION
        ]
        saved = json.loads(json.dumps(original))
        spec, reference = fixed_resolution_characteristic_instruction(saved)
        self.assertEqual(("$target.0", original), (reference, spec.to_effect()))

        with patch("quorune.compiler.target_land_type_templates.fixed_target_land_type_template", return_value=None):
            missing = compile_oracle_card(card, capability_registry=registry, capability_profile=profile)
        self.assertTrue(missing.material_residuals)
        self.assertFalse(any(
            effect.get("schema_version") == FIXED_TARGET_LAND_TYPE_VERSION
            for face in missing.faces for node in face.nodes for effect in node.effects
        ))

    def test_genuine_withdrawals_disable_fixed_types_and_keep_the_color_path_exact(self):
        from copy import deepcopy

        from quorune.basic_type_setting_model import BASIC_TYPE_SETTING_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_granted_skulk import record

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        profile = "commander_review"
        land = record("Target land becomes an Island until end of turn.", "Instant")
        color = record("Target creature becomes blue until end of turn.", "Instant")
        self.assertEqual("exact", compile_oracle_card(
            land, capability_registry=load_default_capability_registry(), capability_profile=profile,
        ).status)
        for capability in (FIXED_TARGET_LAND_TYPE_CAPABILITY, BASIC_TYPE_SETTING_CAPABILITY):
            with self.subTest(withdrawn=capability):
                blocked = deepcopy(raw)
                next(row for row in blocked["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Test withdrawal of the actual type owner"],
                )
                registry = CapabilityRegistry(blocked)
                index = build_index(registry_value=blocked, declaration_source=declarations, discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertNotEqual("exact", compile_oracle_card(land, capability_registry=registry,
                                    capability_profile=profile).status)
                self.assertEqual("exact", compile_oracle_card(color, capability_registry=registry,
                                 capability_profile=profile).status)

    def test_two_printed_options_preserve_the_old_codec_and_require_distinct_basic_types(self):
        from quorune.rules.capabilities import capability_dependencies_for_node

        clause = "Target land becomes a Plains or an Island until end of turn."
        _, effects, schema, mechanics = target_land_type_choice_template(clause)
        effect, = effects
        self.assertEqual(3, effect["schema_version"])
        self.assertEqual(("Plains", "Island"), target_land_type_options(effect))
        self.assertIn(LIMITED_TARGET_LAND_TYPE_CAPABILITY, capability_dependencies_for_node(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        ))
        raw = SimpleNamespace(effects=effects, target_schema=schema, handlers=(), coverage=())
        self.assertIn(LIMITED_TARGET_LAND_TYPE_CAPABILITY, target_land_type_runtime_dependencies(raw))
        for invalid in (["Plains", "Plains"], ["Plains"], ["Plains", "Desert"],
                        ["plains", "Island"], ["Plains", "Island", "Forest"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decode_target_land_type({**effect, "choices": invalid})
        self.assertIsNone(target_land_type_choice_template(
            "Target land becomes a Plains or a Island until end of turn.",
        ))
        self.assertEqual(
            BASIC_LAND_TYPE_CHOICES,
            target_land_type_options(target_land_type_choice_template(
                "Target land becomes the basic land type of your choice until end of turn.",
            )[1][0]),
        )

    def test_limited_land_type_codec_round_trip_and_compiler_omission(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record

        row = record("Target land becomes a Plains or an Island until end of turn.", "Instant")
        registry = load_default_capability_registry()
        profile = "commander_review"
        normal = compile_oracle_card(row, capability_registry=registry, capability_profile=profile)
        instruction, = [e for face in normal.faces for node in face.nodes for e in node.effects
                        if e.get("op") == TARGET_LAND_TYPE_OPERATION and e.get("schema_version") == 3]
        decoded = json.loads(json.dumps(instruction))
        self.assertEqual(("$target.0", "$controller"), decode_target_land_type(decoded))
        self.assertEqual(("Plains", "Island"), target_land_type_options(decoded))
        with patch("quorune.compiler.target_land_type_templates.target_land_type_choice_template",
                   return_value=None):
            changed = compile_oracle_card(row, capability_registry=registry, capability_profile=profile)
        self.assertTrue(changed.material_residuals)
        self.assertFalse(any(
            e.get("op") == TARGET_LAND_TYPE_OPERATION and e.get("schema_version") == 3
            for face in changed.faces for node in face.nodes for e in node.effects
        ))

    def test_limited_choice_withdrawal_keeps_the_existing_all_type_admission(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_granted_skulk import record

        profile = "commander_review"
        pair = record("Target land becomes a Plains or an Island until end of turn.", "Instant")
        all_types = record("Target land becomes the basic land type of your choice until end of turn.", "Instant")
        self.assertEqual("exact", compile_oracle_card(
            pair, capability_registry=load_default_capability_registry(), capability_profile=profile,
        ).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == LIMITED_TARGET_LAND_TYPE_CAPABILITY).update(
            status="blocked", blockers=["Test withdrawal of the exact pair owner"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(pair, capability_registry=registry,
                              capability_profile=profile).status)
        self.assertEqual("exact", compile_oracle_card(all_types, capability_registry=registry,
                           capability_profile=profile).status)


    def test_source_exit_duration_has_a_closed_identity_only_codec(self):
        from quorune.compiler.target_land_type_templates import fixed_target_land_type_template
        from quorune.continuous_effect_model import (
            ContinuousEffect, ContinuousEffectDuration, ContinuousObjectIdentity, ContinuousEffectOrigin,
            ContinuousOperation, Layer,
        )
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies

        _, effects, schema, mechanics = fixed_target_land_type_template(
            "Target land becomes a Swamp until this creature leaves the battlefield.",
        )
        program = SimpleNamespace(
            effects=effects, target_schema=schema, handlers=(), coverage=mechanics,
            event="activate", active_zone="battlefield", event_condition=None,
        )
        required = fixed_resolution_characteristics_runtime_dependencies(program)
        self.assertIn(SOURCE_EXIT_LAND_TYPE_CAPABILITY, required)
        self.assertNotIn(SOURCE_PRESENT_LAND_TYPE_CAPABILITY, required)
        with self.assertRaisesRegex(ValueError, "battlefield source"):
            fixed_resolution_characteristics_runtime_dependencies(
                SimpleNamespace(**{**vars(program), "active_zone": "stack"}),
            )
        for phrase in (
            "Target land becomes a Swamp until another creature leaves the battlefield.",
            "Target land becomes a Swamp until this creature leaves the battlefield in addition to its other types.",
        ):
            self.assertIsNone(fixed_target_land_type_template(phrase))
        event = ContinuousEffect(
            effect_id="land-exit", source_id="source", layer=Layer.TYPE, sublayer="4", timestamp=3,
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_SOURCE_BATTLEFIELD_EXIT,
            operations=(ContinuousOperation("set_basic_land_types", ("swamp",)),),
            applies=ObjectQuerySpec(zones=("battlefield",)),
            locked_objects=(ContinuousObjectIdentity("land", "land@1"),),
            duration_source=ContinuousObjectIdentity("source", "source@1"),
        )
        payload = event.to_dict()
        self.assertEqual(event, ContinuousEffect.from_dict(payload))
        self.assertNotIn("duration_history", payload)
        self.assertNotIn("duration_controller", payload)
        for forged in (
            {k: v for k, v in payload.items() if k != "duration_source"},
            {**payload, "source_id": "other"},
            {**payload, "duration_controller": "A"},
            {**payload, "layer": int(Layer.CONTROL)},
        ):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                ContinuousEffect.from_dict(forged)

    def test_source_presence_land_setting_uses_the_battlefield_source_and_requires_its_owner(self):
        from quorune.compiler.target_land_type_templates import fixed_target_land_type_template
        from quorune.resolution_characteristic_model import fixed_resolution_characteristic_instruction
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies

        _, effects, schema, _mechanics = fixed_target_land_type_template(
            "Target land becomes an Island for as long as this creature remains on the battlefield.",
        )
        spec, reference = fixed_resolution_characteristic_instruction(effects[0])
        self.assertEqual(("until_source_leaves", "$target.0"), (spec.duration, reference))
        self.assertFalse(spec.retain_other_land_types)
        raw = {
            "effects": effects, "target_schema": schema, "handlers": (), "coverage": (),
            "event": "creature.enters", "active_zone": "battlefield", "event_condition": None,
        }
        owners = fixed_resolution_characteristics_runtime_dependencies(SimpleNamespace(**raw))
        self.assertIn(FIXED_TARGET_LAND_TYPE_CAPABILITY, owners)
        self.assertIn(SOURCE_PRESENT_LAND_TYPE_CAPABILITY, owners)
        with self.assertRaisesRegex(ValueError, "battlefield source"):
            fixed_resolution_characteristics_runtime_dependencies(SimpleNamespace(**{**raw, "active_zone": "stack"}))

    def test_presence_snapshot_expires_on_phase_or_incarnation_changes_without_requiring_tap(self):
        from quorune.continuous_effect_model import ContinuousEffectDuration, ContinuousObjectIdentity
        from quorune.control_effects import (
            characteristic_source_duration_is_current, prepare_source_presence_duration,
            tapped_source_duration_is_current,
        )
        from quorune.source_continuity import SourceContinuityHistory, SourceContinuitySnapshot

        source = SimpleNamespace(
            object_id="sea", logical_object_id="sea@2", zone="battlefield", controller="A",
            phased_out=False, tapped=False, source_continuity=SourceContinuityHistory(),
        )
        state = SimpleNamespace(cards={"sea": source})
        identity = ContinuousObjectIdentity("sea", "sea@2")
        lifetime = ContinuousEffectDuration.UNTIL_SOURCE_LEAVES
        snapshot = prepare_source_presence_duration(state, identity)
        restored = SourceContinuitySnapshot.from_dict(json.loads(json.dumps(snapshot.to_dict())))
        self.assertTrue(characteristic_source_duration_is_current(state, identity, restored, lifetime))
        self.assertFalse(tapped_source_duration_is_current(state, identity, restored))
        source.controller = "B"
        source.source_continuity = replace(source.source_continuity, control_epoch=1, untap_epoch=1)
        self.assertTrue(characteristic_source_duration_is_current(state, identity, restored, lifetime))
        source.phased_out = True
        self.assertFalse(characteristic_source_duration_is_current(state, identity, restored, lifetime))
        source.phased_out = False
        source.source_continuity = replace(source.source_continuity, phase_epoch=1)
        self.assertFalse(characteristic_source_duration_is_current(state, identity, restored, lifetime))
        source.logical_object_id = "sea@3"
        self.assertIsNone(prepare_source_presence_duration(state, identity))

    def test_omitting_the_source_presence_leaf_loses_the_whole_typed_result(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record

        engine = record(
            "When this creature enters, target land becomes an Island for as long as this creature remains on the battlefield.",
            "Creature — Merfolk",
        )
        registry = load_default_capability_registry()
        profile = "commander_review"
        baseline = compile_oracle_card(engine, capability_registry=registry, capability_profile=profile)
        nodes = [node for face in baseline.faces for node in face.nodes]
        self.assertTrue(any(SOURCE_PRESENT_LAND_TYPE_CAPABILITY in node.capability_dependencies for node in nodes))
        with patch("quorune.compiler.target_land_type_templates.fixed_target_land_type_template", return_value=None):
            omitted = compile_oracle_card(engine, capability_registry=registry, capability_profile=profile)
        self.assertTrue(omitted.material_residuals)
        self.assertFalse(any(SOURCE_PRESENT_LAND_TYPE_CAPABILITY in node.capability_dependencies
                             for face in omitted.faces for node in face.nodes))

    def test_source_presence_withdrawal_preserves_the_fixed_end_of_turn_result(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_granted_skulk import record

        statement = "When this creature enters, target land becomes an Island {}."
        presence = record(statement.format("for as long as this creature remains on the battlefield"), "Creature — Merfolk")
        finite = record(statement.format("until end of turn"), "Creature — Merfolk")
        profile = "commander_review"
        self.assertEqual("exact", compile_oracle_card(presence, capability_registry=load_default_capability_registry(),
                             capability_profile=profile).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == SOURCE_PRESENT_LAND_TYPE_CAPABILITY).update(
            status="blocked", blockers=["Test withdrawal of the actual source duration owner"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(presence, capability_registry=registry,
                              capability_profile=profile).status)
        self.assertEqual("exact", compile_oracle_card(finite, capability_registry=registry,
                           capability_profile=profile).status)

    def test_death_land_exile_requires_the_exact_source_and_ordered_land_result(self):
        from quorune.compiler.event_card_return_templates import source_death_land_exile_binding
        from quorune.event_card_return import (
            DEATH_EXILE_VERSION, DEATH_LAND_EXILE_CAPABILITY, death_land_exile_condition,
            death_source_exile_effect,
        )
        from quorune.rules.fixed_resolution_characteristic_shapes import fixed_resolution_characteristics_runtime_dependencies

        clause = "When this creature dies, target land becomes a Swamp. Exile this card."
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding

        binding = fixed_counter_trigger_binding(clause, card_name="Probe")
        self.assertEqual("creature.dies", binding.event.value)
        self.assertEqual(death_land_exile_condition(), binding.event_condition)
        from quorune.compiler.event_card_return_templates import event_card_return_template
        _, effects, schema, _mechanics = event_card_return_template(
            binding, binding.body, card_name="Probe", effect_template=None,
        )
        self.assertEqual(death_source_exile_effect(), effects[1])
        raw = {"effects": effects, "target_schema": schema, "handlers": (), "coverage": (),
               "event": "creature.dies", "active_zone": "battlefield", "event_condition": binding.event_condition}
        required = fixed_resolution_characteristics_runtime_dependencies(SimpleNamespace(**raw))
        self.assertIn(DEATH_LAND_EXILE_CAPABILITY, required)
        self.assertIn(FIXED_TARGET_LAND_TYPE_CAPABILITY, required)
        for change in (
            {"event": "creature.enters"},
            {"event_condition": {"field": "card", "op": "eq", "value": "$source.ref"}},
            {"effects": effects[::-1]},
            {"effects": (effects[0], {**effects[1], DEATH_EXILE_VERSION: True})},
            {"target_schema": {**schema, "count": 2}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                fixed_resolution_characteristics_runtime_dependencies(SimpleNamespace(**{**raw, **change}))
        self.assertIsNone(source_death_land_exile_binding(clause.replace("this card", "it"), card_name="Probe"))
        self.assertIsNone(source_death_land_exile_binding(clause.replace("this creature", "another creature"),
                                                          card_name="Probe"))

    def test_death_exile_seals_only_original_public_physical_arrivals(self):
        from quorune.event_card_return import (
            DEATH_EXILE_ARRIVALS, DEATH_LAND_EXILE_CAPABILITY, apply_death_source_exile,
            death_source_exile_context, death_source_exile_effect,
        )

        group = SimpleNamespace(permanent_departure=True, member_count=4, parent="source")
        event = SimpleNamespace(composition=group)
        program = SimpleNamespace(capability_dependencies=(DEATH_LAND_EXILE_CAPABILITY,))
        semantics = SimpleNamespace(programs_for_event=lambda event: (program,) if event == "creature.dies" else ())
        prior = {"card": "departed-source"}
        self.assertIs(prior, death_source_exile_context(SimpleNamespace(semantics=None), prior, event, ()))

        def component(identity, zone, *, token=False):
            return SimpleNamespace(
                composition=SimpleNamespace(physical_arrival=True, parent="source"),
                object_id=identity, card_ref="ref-" + identity, current_logical_object_id=identity + "@2",
                destination=zone, card_object=not token, token=token,
            )

        components = (
            component("grave", "graveyard"), component("public-replacement", "exile"),
            component("hidden", "hand"), component("token", "graveyard", token=True),
        )
        with self.assertRaisesRegex(ValueError, "complete committed departures"):
            death_source_exile_context(SimpleNamespace(semantics=semantics), prior, event, components[:-1])
        data = death_source_exile_context(SimpleNamespace(semantics=semantics), prior, event, components)
        data = json.loads(json.dumps(data))
        arrivals = data[DEATH_EXILE_ARRIVALS]
        self.assertEqual(["grave", "public-replacement"], [row["object_id"] for row in arrivals])

        moves = []
        cards = {
            row["object_id"]: SimpleNamespace(
                ref=row["reference"], logical_object_id=row["logical_object_id"],
                zone=row["zone"], is_card_object=True, is_token=False,
            )
            for row in arrivals
        }
        host = SimpleNamespace(state=SimpleNamespace(cards=cards),
                               move_objects_simultaneously_intent=lambda intent: moves.append(intent) or intent.object_refs)
        effect = {**death_source_exile_effect(), DEATH_EXILE_ARRIVALS: arrivals}
        self.assertEqual(("ref-grave", "ref-public-replacement"),
                         apply_death_source_exile(host, effect, actor="A", reason="Probe"))
        self.assertEqual(("A", "exile"), (moves[0].actor, moves[0].destination))
        cards["public-replacement"].logical_object_id = "public-replacement@3"
        self.assertEqual(("ref-grave",), apply_death_source_exile(host, effect, actor="A", reason="Probe"))
        self.assertEqual(2, len(moves))

    def test_omitting_the_complete_death_sequence_leaves_original_text_residual(self):
        from quorune.event_card_return import DEATH_LAND_EXILE_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record

        row = record("When this creature dies, target land becomes a Swamp. Exile this card.", "Creature — Giant")
        registry = load_default_capability_registry()
        baseline = compile_oracle_card(row, capability_registry=registry, capability_profile="commander_review")
        self.assertTrue(any(DEATH_LAND_EXILE_CAPABILITY in node.capability_dependencies
                            for face in baseline.faces for node in face.nodes))
        with patch("quorune.compiler.event_card_return_templates._source_death_land_exile_result", return_value=None):
            omitted = compile_oracle_card(row, capability_registry=registry, capability_profile="commander_review")
        self.assertTrue(omitted.material_residuals)
        self.assertFalse(any(DEATH_LAND_EXILE_CAPABILITY in node.capability_dependencies
                             for face in omitted.faces for node in face.nodes))

    def test_withdrawing_the_compound_death_owner_preserves_ordinary_death_returns(self):
        from quorune.event_card_return import DEATH_LAND_EXILE_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_granted_skulk import record

        profile = "commander_review"
        sequence = record("When this creature dies, target land becomes a Swamp. Exile this card.", "Creature — Giant")
        ordinary = record("When this creature dies, return this card to its owner's hand.", "Creature — Human")
        self.assertEqual("exact", compile_oracle_card(
            sequence, capability_registry=load_default_capability_registry(), capability_profile=profile,
        ).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == DEATH_LAND_EXILE_CAPABILITY).update(
            status="blocked", blockers=["Test withdrawal of the complete death result"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(sequence, capability_registry=registry,
                              capability_profile=profile).status)
        self.assertEqual("exact", compile_oracle_card(ordinary, capability_registry=registry,
                           capability_profile=profile).status)
