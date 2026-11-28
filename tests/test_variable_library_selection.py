"""Pinned CR609.3/701.20 count boundaries before any library inspection."""

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture

from quorune.object_predicate import ObjectQuerySpec
from quorune.variable_library_selection_model import (
    ALL_LOOKED,fixed_selection_for_resolved_count,validate_variable_library_selection,
)


class VariableLibrarySelectionValueTests(unittest.TestCase):
    def effect(self,count=4):
        return {'op':'variable_library_selection','schema_version':1,'player':'A','look_count':count,
            'public_reveal':False,'selected_reveal':False,'selection_policy':'fixed_any',
            'minimum':1,'maximum':1,'predicate_groups':[],
            'remainder_destination':'library_bottom','remainder_order':'chosen'}

    def test_zero_has_no_inspection_and_positive_delegates_exact_fixed_shape(self):
        self.assertIsNone(fixed_selection_for_resolved_count(self.effect(0)))
        original=self.effect();before=deepcopy(original)
        fixed=fixed_selection_for_resolved_count(original)
        self.assertEqual(before,original)
        self.assertEqual('fixed_library_selection',fixed['op']);self.assertNotIn('schema_version',fixed)
        self.assertEqual(4,fixed['look_count']);self.assertEqual((1,1),(fixed['minimum'],fixed['maximum']))
        fixed['predicate_groups'].append({'mutated':True})
        self.assertEqual([],original['predicate_groups'])

    def test_fixed_requested_selection_does_only_as_much_as_the_inspection_allows(self):
        effect={**self.effect(1),'minimum':2,'maximum':2}
        fixed=fixed_selection_for_resolved_count(effect)
        self.assertEqual((1,1,1),(fixed['look_count'],fixed['minimum'],fixed['maximum']))

    def test_all_matching_maximum_follows_the_resolved_count(self):
        query=ObjectQuerySpec(zones=('library',),types_any=('creature',)).to_dict()
        effect={**self.effect(3),'public_reveal':True,'selection_policy':'all_matching',
            'minimum':0,'maximum':ALL_LOOKED,'predicate_groups':[[query]]}
        fixed=fixed_selection_for_resolved_count(effect)
        self.assertEqual((3,0,3),(fixed['look_count'],fixed['minimum'],fixed['maximum']))
        self.assertEqual([[query]],fixed['predicate_groups']);self.assertTrue(fixed['public_reveal'])

    def test_two_optional_slots_reduce_to_one_union_for_one_inspected_card(self):
        creature=ObjectQuerySpec(zones=('library',),types_any=('creature',)).to_dict()
        land=ObjectQuerySpec(zones=('library',),types_any=('land',)).to_dict()
        effect={**self.effect(1),'selection_policy':'optional_slots','minimum':0,'maximum':2,
            'selected_reveal':True,'predicate_groups':[[creature],[land]]}
        fixed=fixed_selection_for_resolved_count(effect)
        self.assertEqual(('up_to_matching',0,1),(fixed['selection_policy'],fixed['minimum'],fixed['maximum']))
        self.assertEqual([[creature,land]],fixed['predicate_groups'])
        self.assertTrue(fixed['selected_reveal'])
        fixed=fixed_selection_for_resolved_count({**effect,'look_count':2})
        self.assertEqual('optional_slots',fixed['selection_policy'])
        self.assertEqual([[creature],[land]],fixed['predicate_groups'])

    def test_unknown_authority_shapes_and_boolean_counts_reject_even_at_zero(self):
        for change in ({'look_count':True},{'look_count':-1},{'look_count':{}},{'maximum':False},
                       {'maximum':'arbitrary'},{'schema_version':True},{'player':' A'},
                       {'public_reveal':1},{'remainder_destination':'battlefield'},{'extra':0}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                fixed_selection_for_resolved_count({**self.effect(0),**change})

    def test_unresolved_count_requires_an_existing_typed_amount_owner(self):
        from quorune.query_effect_amount_model import CastXAmountSpec
        effect={**self.effect(),'look_count':CastXAmountSpec().to_dict()}
        validate_variable_library_selection(effect)
        with self.assertRaises(ValueError):fixed_selection_for_resolved_count(effect)

    def test_public_count_and_cast_x_compiler_keep_complete_selection_shape(self):
        from quorune.compiler.variable_library_selection_templates import variable_library_selection_effect_template
        public=variable_library_selection_effect_template(
            'Look at the top X cards of your library, where X is the number of artifacts you control. '
            'Put one of those cards into your hand and the rest on the bottom of your library in any order.',source_name='Source')
        self.assertIsNotNone(public)
        self.assertEqual('variable_library_selection',public[1][0]['op'])
        self.assertIsInstance(public[1][0]['look_count'],dict)
        self.assertEqual((1,1),(public[1][0]['minimum'],public[1][0]['maximum']))
        cast=variable_library_selection_effect_template(
            'Reveal the top X cards of your library. Put all creature cards revealed this way into your hand '
            'and the rest on the bottom of your library in any order.',source_name='Source',cast_x_available=True)
        self.assertIsNotNone(cast)
        self.assertEqual(ALL_LOOKED,cast[1][0]['maximum']);self.assertTrue(cast[1][0]['public_reveal'])

    def test_count_bridge_has_no_query_or_choice_and_retains_remaining_work(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.variable_library_selection import VariableLibrarySelectionHandler
        class NoInspectionContext:
            actor='A';stack_controller='A'
            @property
            def query(self):raise AssertionError('Count bridge must not inspect cards')
        handler=VariableLibrarySelectionHandler();context=NoInspectionContext()
        zero=handler.prepare(self.effect(0),context)
        self.assertIsNone(zero.request);self.assertEqual((),zero.preparation_intents)
        self.assertEqual((),zero.auto_continue.prepend_effects)
        positive=handler.prepare(self.effect(2),context)
        self.assertIsNone(positive.request);self.assertEqual((),positive.preparation_intents)
        self.assertEqual(1,len(positive.auto_continue.prepend_effects))
        self.assertEqual('fixed_library_selection',positive.auto_continue.prepend_effects[0]['op'])
        with self.assertRaises(ValueError):handler.prepare(self.effect(2),SimpleNamespace(actor='B',stack_controller='A'))

    def test_compiler_rejects_other_libraries_unowned_x_and_incomplete_procedure(self):
        from quorune.compiler.variable_library_selection_templates import variable_library_selection_effect_template as compile
        base='Look at the top X cards of your library. Put one of those cards into your hand and the rest on the bottom of your library in any order.'
        self.assertIsNone(compile(base,source_name='Source'))
        for text in (base.replace('your library',"target opponent's library"),
                     base.replace('the top X cards','the top X plus one cards'),
                     base.replace(' and the rest on the bottom of your library in any order.','.'),
                     base+' Draw a card.'):
            with self.subTest(text=text):self.assertIsNone(compile(text,source_name='Source',cast_x_available=True))

    def test_compiler_and_dependency_withdrawal_fail_closed(self):
        import json
        from common import ROOT
        from dataclasses import replace
        from unittest.mock import patch
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        source=replace(query_record('Look at the top X cards of your library, where X is the number of artifacts you control. '
            'Put one of those cards into your hand and the rest on the bottom of your library in any order.'),
            type_line='Instant',power=None,toughness=None)
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,[(row.kind,row.reason,row.blockers) for row in ir.material_residuals])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in ('library.select.variable_controller','library.select.fixed_controller'):
            changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn by focused mutation']
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)
        with patch('quorune.compiler.variable_library_selection_templates.variable_library_selection_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review').status)


class VariableLibrarySelectionNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'variable-inspection.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/variable-library-selection-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Variable inspection',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def top_cards(self,engine):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        return tuple(H.add(self,engine,name,zone='library',ref='TOP'+str(index))
            for index,name in enumerate(('Generic Bound Body','Generic Bound Growth','Generic Bound Plains')))

    def test_original_machinate_locks_public_count_then_keeps_look_and_order_private(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,929001);engine=session.engine
        for _ in range(3):engine.create_token('A',name='Counter source',characteristics={'type_line':'Token Artifact'})
        top=self.top_cards(engine)
        source=H.add(self,engine,'Machinate',zone='hand');self.assert_closed(source)
        action=H.ready(self,session,source,{'U':2,'C':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        self.assertNotIn('Generic Bound Body',str(session.packet('pilot:B',full=True)))
        session=H.replay(self,session,load=True);engine=session.engine
        response={'action_id':'choose','cards':{'hand':[top[0].ref],'bottom':[top[1].ref,top[2].ref]}}
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',response)
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',response)
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual('hand',engine.state.cards[top[0].object_id].zone)
        self.assertEqual(2,sum(engine.state.cards[card.object_id].zone=='library' for card in top))
        self.assertNotIn('Generic Bound Body',str(session.packet('pilot:B',full=True)))
        H.replay(self,session,load=True)

    def test_original_enshrined_memories_announced_zero_and_three_reveal_only_inspected_cards(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for count in (0,3):
            with self.subTest(count=count):
                session=H.session(self,929010+count);engine=session.engine
                top=self.top_cards(engine);source=H.add(self,engine,'Enshrined Memories',zone='hand')
                self.assert_closed(source)
                action=H.ready(self,session,source,{'G':1,'C':3});H.checkpoint(self,session)
                original_library=list(engine.state.players['A'].zones['library'])
                accepted=session.act('pilot:A',{'action_id':action['id'],'x':count,'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                if count==0:
                    self.assertIsNone(decision);self.assertEqual(original_library,engine.state.players['A'].zones['library'])
                    self.assertNotIn('Generic Bound Body',str(session.packet('pilot:B',full=True)))
                else:
                    self.assertIsNotNone(decision)
                    self.assertIn('Generic Bound Body',str(session.packet('pilot:B',full=True)))
                    session=H.replay(self,session,load=True);engine=session.engine
                    before=authoritative_state_hash(session.state)
                    rejected=session.act('pilot:A',{'action_id':'choose','cards':{'hand':[],
                        'bottom':[card.ref for card in top]}})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
                    accepted=session.act('pilot:A',{'action_id':'choose','cards':{'hand':[top[0].ref],
                        'bottom':[top[1].ref,top[2].ref]}})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[top[0].object_id].zone)
                H.replay(self,session,load=True)

    def test_original_machinate_short_library_and_stale_top_reject_before_movement(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,stale in enumerate((False,True)):
            with self.subTest(stale=stale):
                session=H.session(self,929020+index);engine=session.engine
                for oid in tuple(engine.state.players['A'].zones['library']):engine.move_card(oid,'outside',log=False)
                top=H.add(self,engine,'Generic Bound Body',zone='library',ref='SHORT_TOP')
                for _ in range(3):engine.create_token('A',name='Artifact count',characteristics={'type_line':'Token Artifact'})
                source=H.add(self,engine,'Machinate',zone='hand');self.assert_closed(source)
                action=H.ready(self,session,source,{'U':2,'C':1});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
                session=H.replay(self,session,load=True);engine=session.engine
                if stale:
                    # Deliberate authoritative-state corruption exercises the
                    # continuation's immutable top binding, not a legal action.
                    engine.move_card(top.object_id,'graveyard',log=False)
                    before=authoritative_state_hash(engine.state)
                    rejected=session.act('pilot:A',{'action_id':'choose','cards':{'hand':[top.ref],'bottom':[]}})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                    self.assertEqual('graveyard',engine.state.cards[top.object_id].zone)
                else:
                    accepted=session.act('pilot:A',{'action_id':'choose','cards':{'hand':[top.ref],'bottom':[]}})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[top.object_id].zone)
                    self.assertEqual([],engine.state.players['A'].zones['library'])
                    H.replay(self,session,load=True)


class BlockedDamageLibraryCastTests(InheritedNativeFixture, unittest.TestCase):
    name = "Duelist's Flame"

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name)
        base = ROOT / "tests/fixtures"
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains"},
            "event-spell-copy-cards.json": {"Grizzly Bears"},
            "exile-cast-opponent-damage-cards.json": {"Mountain", "Wastes"},
            "variable-library-selection-cards.json": {cls.name},
        }
        paths = []
        for name, names in selected.items():
            content = json.loads((base / name).read_bytes())
            content["cards"] = [row for row in content["cards"] if row["name"] in names]
            assert names == {row["name"] for row in content["cards"]}, name
            ids = {row["oracle_id"] for row in content["cards"]}
            content["rulings"] = [row for row in content.get("rulings", ()) if row["oracle_id"] in ids]
            scoped = path / name
            scoped.write_text(json.dumps(content), encoding="utf-8")
            paths.append(scoped)
        db = path / "blocked-damage.sqlite3"
        build_fixture_database(paths, db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Blocked combat damage library cast", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def send(self, game, seat, command, *, accepted=True):
        from quorune.record import authoritative_state_hash

        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def save_record(self, game, label):
        from hashlib import sha256
        from quorune.record import authoritative_state_hash, replay_record

        root = os.environ.get("QUORUNE_DUELIST_RECORD_PARENT")
        if root is None:
            self.replay(game, load=True)
            return
        path = Path(root) / label
        game.save(path)
        report = replay_record(path, self.db, verify=True)
        self.assertTrue(report["ok"], report)
        state_hash = authoritative_state_hash(game.state)
        self.assertEqual(state_hash, report["final_state_hash"])
        path.with_suffix(".receipt.json").write_text(json.dumps({
            "manifest_sha256": sha256((path / "manifest.json").read_bytes()).hexdigest(),
            "final_state_sha256": state_hash, "commands": len(game.commands),
        }, indent=2) + "\n", encoding="utf-8")

    def window(self, seed):
        game = self.session(seed)
        spark = self.add(game.engine, self.name, zone="hand")
        source = self.add(game.engine, "Grizzly Bears", ref="flame-attacker")
        unblocked = self.add(game.engine, "Grizzly Bears", ref="flame-unblocked")
        blocker_ref, = game.engine.create_token("B", name="Soldier Token", quantity=1,
            characteristics={"type_line": "Creature — Soldier", "power": "1", "toughness": "1"})
        outside = self.add(game.engine, "Grizzly Bears", zone="library", ref="flame-outside-window")
        third = self.add(game.engine, "Generic Bound Plains", zone="library", ref="flame-third-land")
        reward = self.add(game.engine, "Grizzly Bears", zone="library", ref="flame-exile-spell")
        first = self.add(game.engine, "Generic Bound Plains", zone="library", ref="flame-first-land")
        lands = [self.add(game.engine, name, ref=f"duelist-mana-{index}")
                 for index, name in enumerate(("Mountain", "Mountain", "Wastes", "Wastes"))]
        self.assert_closed(game, spark)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.send(game, "A", {"action_id": "attack", "attackers": {source.ref: "B", unblocked.ref: "C"}})
        self.priority(game, "A")
        self.send(game, "A", {"action_id": "cast:" + spark.ref, "x": 2,
                              "targets": [source.ref], "pay": "auto"}, accepted=False)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers"
                   and current.state.pending_decision.actors == ["B"])
        self.send(game, "B", {"action_id": "block", "blocks": {blocker_ref: source.ref}})
        if game.state.pending_decision.kind == "combat.blockers":
            self.send(game, "C", {"action_id": "block", "blocks": {}})
        self.priority(game, "A")
        offer = self.action(game, "A", spark)
        self.assertEqual([source.ref], offer["target_schema"]["legal_refs"])
        command = {"action_id": offer["id"], "x": 2, "pay": "auto"}
        self.send(game, "A", {**command, "targets": [unblocked.ref]}, accepted=False)
        self.send(game, "A", {**command, "targets": [source.ref]})
        self.assertEqual(2, game.state.stack[-1].x_value)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.settle(game)
        self.assertEqual(4, game.engine._numeric_stat(source.object_id, "power"))
        self.assertIn("trample", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.damage")
        self.send(game, "A", {"a": "dmg", "assignments": [
            {"source": source.ref, "target": blocker_ref, "amount": 1},
            {"source": source.ref, "target": "B", "amount": 3},
            {"source": unblocked.ref, "target": "C", "amount": 2},
        ]})
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(37, game.state.players["B"].life)
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual([first.ref, reward.ref, third.ref], [row["id"] for row in packet["ctx"]["objects"]])
        self.assertEqual(3, game.state.stack[-1].context["amount"])
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            visible = json.dumps(game.packet(viewer, full=True))
            for card in (first, reward, third, outside):
                self.assertNotIn(card.ref, visible)
        self.send(game, "B", {"action_id": "choose", "cards": [reward.ref]}, accepted=False)
        for rejected in ([first.ref], [outside.ref], [reward.ref, third.ref], {"exile": [reward.ref], "bottom": [third.ref, first.ref]}):
            self.send(game, "A", {"action_id": "choose", "cards": rejected}, accepted=False)
        return game, source, reward, first, third, outside

    def test_duelist_paid_x_uses_committed_damage_private_window_and_native_free_cast(self):
        game, source, reward, first, third, outside = self.window(5091001)
        self.save_record(game, "duelist-committed-three-card-window-game-record")
        self.send(game, "A", {"action_id": "choose", "cards": [reward.ref]})
        self.assertEqual("exile", game.state.cards[reward.object_id].zone)
        self.assertEqual({first.object_id, third.object_id}, set(game.state.players["A"].zones["library"][:2]))
        self.assertEqual(outside.object_id, game.state.players["A"].zones["library"][-1])
        choice = game.packet("pilot:A", full=True)["decision"]
        option, = choice["ctx"]["cast_options"]
        self.assertEqual(reward.ref, option["card"])
        self.save_record(game, "duelist-partition-then-free-cast-pending-game-record")
        self.send(game, "A", {"action_id": "choose", "choice": "cast", "cast": {"cost_option": option["id"], "pay": "auto"}})
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[reward.object_id].zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        from optional_result_followup_fixture import OptionalResultFollowupFixture

        OptionalResultFollowupFixture.finish_turn(self, game)
        self.assertEqual(2, game.engine._numeric_stat(source.object_id, "power"))
        self.assertNotIn("trample", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.save_record(game, "duelist-free-cast-and-cleanup-game-record")

    def test_duelist_empty_selection_and_declined_cast_do_not_leave_permission(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                game, _, reward, first, third, outside = self.window(5091010 + int(selected))
                self.send(game, "A", {"action_id": "choose", "cards": [reward.ref] if selected else []})
                if selected:
                    self.send(game, "A", {"action_id": "choose", "choice": "decline"})
                self.settle(game)
                count = 2 if selected else 3
                ids = {first.object_id, third.object_id} if selected else {first.object_id, third.object_id, reward.object_id}
                self.assertEqual(ids, set(game.state.players["A"].zones["library"][:count]))
                self.assertEqual(outside.object_id, game.state.players["A"].zones["library"][-1])
                self.assertEqual("exile" if selected else "library", game.state.cards[reward.object_id].zone)
                if selected:
                    self.priority(game, "A")
                    self.send(game, "A", {"action_id": "cast:" + reward.ref, "pay": "auto"}, accepted=False)
                self.save_record(game, f"duelist-{'decline' if selected else 'empty'}-game-record")

    def test_duelist_complete_grant_and_damage_window_require_independent_capability(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.blocked_damage_cast import CAPABILITY, GRANT_VERSION, WINDOW_VERSION
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup(self.name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, self.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = [program for program in semantics.programs_for_oracle(card.oracle_id)]
        self.assertEqual({GRANT_VERSION, WINDOW_VERSION}, {program.effects[0]["schema_version"] for program in programs})
        for program in programs:
            dependencies = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
            mutant = replace(program, capability_dependencies=dependencies,
                             capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
            binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{CAPABILITY}", binding["blockers"])
        no_announced_x = replace(card, mana_cost="{2}{R}{R}")
        self.assertNotEqual("exact", compile_oracle_card(no_announced_x, capability_registry=self.registry,
                                                       capability_profile="commander_review").status)
        for edit in (("blocked creature you control", "unblocked creature you control"),
                     ("nonland card", "creature card"), ("a random order", "any order")):
            altered = replace(card, oracle_text=card.oracle_text.replace(*edit))
            ir = compile_oracle_card(altered, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)
