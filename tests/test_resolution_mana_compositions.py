"""Known fixed mana leaves retain normal ordered/optional/chapter timing."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
import json,unittest
from unittest.mock import patch
from common import ROOT
from test_qualified_zone_event_queries import query_record
import mana_composition_fixture as fixtures
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry

CAPABILITY='mana.production.fixed_resolution'


class ManaCompositionCompilerTests(unittest.TestCase):
    def test_retained_combat_mana_and_public_counter_total_keep_their_existing_owners(self):
        from quorune.semantics import SemanticProgram
        from quorune.card_programs import compile_card_program,bind_card_program_runtime
        from quorune.compiler.keyword_event_effect_nodes import FixedKeywordEventEffectSpec
        from quorune.rules.fixed_resolution_mana_shapes import fixed_resolution_mana_runtime_dependencies
        fire=FixedKeywordEventEffectSpec('firebending',2)
        program=SemanticProgram(key="test:retained-combat-mana", label="Retained combat mana", effects=list(fire.effects))
        self.assertEqual((fire.capability_id,),fixed_resolution_mana_runtime_dependencies(program))
        for change in ({'retain_until':'end_of_turn'},{'color':'G'},{'amount':True},{'extra':1}):
            with self.assertRaises(ValueError):fixed_resolution_mana_runtime_dependencies(replace(program, effects=[{**fire.effects[0], **change}]))
        registry=load_default_capability_registry();database=type('NoRulings',(),{'rulings':lambda self,record:()})()
        for amount,total in (('counter',True),('charge counter',False)):
            record=replace(query_record('{T}: Add {U} for each '+amount+' on this creature.'),type_line='Artifact Creature — Scientist')
            ir=compile_oracle_card(record,capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertEqual(total,'quantity_expression.public_counter_total' in ir.faces[0].nodes[0].capability_dependencies)
            compiled=compile_card_program(database,record,capability_registry=registry,capability_profile='commander_review',trust_level='trusted')
            self.assertTrue(bind_card_program_runtime(compiled,capability_registry=registry,profile='commander_review')['strict_capability_ready'])

    def test_fixed_mana_components_compose_with_owned_ordered_and_optional_results(self):
        registry=load_default_capability_registry()
        for text,kind in (('You lose 1 life and add {B}.','Sorcery'),('Add {R}. Draw a card.','Instant'),('Whenever this creature attacks, you may add {R}{R}.','Creature — Elf'),('Whenever you sacrifice an artifact, put a +1/+1 counter on this creature and add {R}.','Creature — Goblin')):
            ir=compile_oracle_card(replace(query_record(text),type_line=kind),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals);self.assertIn(CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
        from quorune.rules.fixed_effect_clause_shapes import closed_effect_component_capabilities
        mana={'op':'mana','player':'$controller','color':'R','amount':1,'source':'$source'}
        self.assertIn(CAPABILITY,closed_effect_component_capabilities((mana,),target_schema=None,mechanics={'fixed-resolution-mana'}))
        for change in ({'amount':True},{'amount':0},{'color':'unknown'},{'player':'B'},{'source':'B'},{'extra':1}):
            self.assertEqual((),closed_effect_component_capabilities(({**mana,**change},),target_schema=None,mechanics={'fixed-resolution-mana'}))
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Add {R}. Rewrite history.'),type_line='Instant'),capability_registry=registry).status)

    def test_verified_mana_owner_withdrawal_rejects_composition_and_keeps_ordinary_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['fixed mana mutation owner withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('You lose 1 life and add {B}.'),type_line='Instant'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Instant'),capability_registry=registry).status)

    def test_missing_fixed_bundle_component_mutant_is_killed(self):
        import quorune.rules.fixed_effect_clause_shapes as owner
        from quorune.rules.fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities
        def witness():self.assertEqual('exact',compile_oracle_card(replace(query_record('You lose 1 life and add {B}.'),type_line='Instant'),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch.object(owner,'_COMPONENT_RESOLVERS',tuple(r for r in owner._COMPONENT_RESOLVERS if r is not fixed_resolution_mana_capabilities)),self.assertRaises(AssertionError):witness()


class OrdinaryManaCompositionNativeTests(fixtures.ManaCompositionFixture,unittest.TestCase):
    def test_actual_firebending_retains_only_its_mana_and_requires_its_keyword_owner(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(608648100);source=self.add(session.engine,'Fire Sages',zone='hand');land=self.add(session.engine,'Mountain');self.add(session.engine,'Fervor');self.add(session.engine,'Grizzly Bears',seat='B');self.assert_closed(session,source)
        fire=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if 'trigger.keyword.firebending.fixed' in p.capability_dependencies)
        deps=tuple(c for c in fire.capability_dependencies if c!='trigger.keyword.firebending.fixed')
        stripped=replace(fire,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review');self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:trigger.keyword.firebending.fixed',bound['blockers'])
        self.prepare(session,mana={'C':1,'R':1});self.checkpoint(session);self.cast(session,'A',source);self.settle(session);self.attack(session,[(source,'B')]);self.assertEqual(0,session.state.players['A'].mana_pool['R']);self.assertEqual(1,self.trigger_count(session,source));self.settle(session)
        self.priority(session,'A');action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+land.ref+':'));result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertEqual(2,session.state.players['A'].mana_pool['R'])
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers');self.assertEqual(1,session.state.players['A'].mana_pool['R'])
        self.until(session,lambda s:s.state.phase=='postcombat_main' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority');self.assertEqual(0,session.state.players['A'].mana_pool['R']);self.replay(session,load=True)

    def test_actual_nardole_counts_all_counter_kinds_and_preserves_noncreature_restriction(self):
        session=self.session(608648110);source=self.add(session.engine,'Nardole, Resourceful Cyborg');source.counters.update({'charge':2,'+1/+1':1});source.acquired_control_turn_count=-1
        creature=self.add(session.engine,'Nardole, Resourceful Cyborg',zone='hand',ref='restricted-blue-creature');draw=self.add(session.engine,'Twiddle',zone='hand');self.assert_closed(session,source);self.prepare(session);self.checkpoint(session);self.priority(session,'A')
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'));command={'action_id':action['id'],'pay':'auto'};before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertFalse(session.state.stack);self.assertTrue(session.state.cards[source.object_id].tapped);self.assertEqual(3,session.state.players['A'].mana_pool['U'])
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+creature.ref,'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',draw,[source.ref]);self.assertEqual(2,session.state.players['A'].mana_pool['U']);self.assertEqual('semantic.choice',self.resolve(session).kind);result=session.act('pilot:A',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary);self.settle(session);self.assertFalse(session.state.cards[source.object_id].tapped);self.replay(session,load=True)

    def test_runtime_fixed_mana_component_owner_is_inferred_without_annotations(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(608648090);checked=0
        for name in ('Crime Novelist','Carnival of Souls','Radha, Heir to Keld','Skycloud Egg'):
            source=self.add(session.engine,name)
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                if CAPABILITY not in program.capability_dependencies:continue
                deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY);changed=replace(program,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers']);checked+=1
        self.assertEqual(4,checked)

    def test_omitted_runtime_mana_leaf_inference_mutant_is_killed(self):
        with patch('quorune.rules.fixed_resolution_mana_shapes.fixed_resolution_mana_runtime_dependencies',return_value=()),self.assertRaises(AssertionError):
            self.test_runtime_fixed_mana_component_owner_is_inferred_without_annotations()

    def test_actual_novelist_treasure_trigger_waits_on_stack_and_keeps_red_after_source_departure(self):
        for removed in (False,True):
            with self.subTest(removed=removed):
                session=self.session(608648001+removed);source=self.add(session.engine,'Crime Novelist',zone='hand');create=self.add(session.engine,'Strike It Rich',zone='hand');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'R':2});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'R':1});self.settle(session);self.cast(session,'A',create);self.settle(session)
                treasure=next(c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield');self.priority(session,'A');offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+treasure.ref+':'))
                before=authoritative_state_hash(session.state);command={'action_id':offered['id'],'mana_output':{'G':1},'pay':'auto'};self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual(1,session.state.players['A'].mana_pool['G']);self.assertEqual(0,session.state.players['A'].mana_pool['R']);self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0));self.assertEqual(1,self.trigger_count(session,source))
                if removed:self.cast(session,'B',bounce,[source.ref]);self.resolve_top(session)
                session=self.replay(session,load=True);self.settle(session);self.assertEqual(1,session.state.players['A'].mana_pool['R']);self.assertEqual(0 if removed else 1,session.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(session,load=True)

    def test_actual_carnival_uses_enchantment_controller_for_own_and_foreign_creature_entries(self):
        session=self.session(608648010);source=self.add(session.engine,'Carnival of Souls',zone='hand');own=self.add(session.engine,'Grizzly Bears',zone='hand');foreign=self.add(session.engine,'Memnite',seat='B',zone='hand');self.add(session.engine,'Vedalken Orrery',seat='B');self.assert_closed(session,source)
        self.prepare(session,mana={'C':2,'B':1,'G':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':1,'G':1});self.settle(session);life={s:session.state.players[s].life for s in 'ABCD'}
        self.cast(session,'A',own);self.resolve_top(session);self.assertEqual(1,self.trigger_count(session,source));self.assertEqual(0,session.state.players['A'].mana_pool['B']);self.settle(session);self.assertEqual(life['A']-1,session.state.players['A'].life);self.assertEqual(1,session.state.players['A'].mana_pool['B'])
        self.cast(session,'B',foreign);self.resolve_top(session);self.assertEqual(1,self.trigger_count(session,source));session=self.replay(session,load=True);self.settle(session)
        self.assertEqual(life['A']-2,session.state.players['A'].life);self.assertEqual(2,session.state.players['A'].mana_pool['B']);self.assertEqual(0,session.state.players['B'].mana_pool['B'])
        for seat in 'BCD':self.assertEqual(life[seat],session.state.players[seat].life)
        self.replay(session,load=True)

    def test_actual_radha_optional_attack_mana_waits_for_choice_and_expires_before_blockers(self):
        for apply in (False,True):
            with self.subTest(apply=apply):
                session=self.session(608648020+apply);source=self.add(session.engine,'Radha, Heir to Keld',zone='hand');self.add(session.engine,'Fervor');self.add(session.engine,'Island');untap=self.add(session.engine,'Twiddle',zone='hand');spell=self.add(session.engine,'Shock',zone='hand');self.assert_closed(session,source)
                self.prepare(session,mana={'R':1,'G':1});self.checkpoint(session);self.cast(session,'A',source);self.settle(session);self.priority(session,'A');mana=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'));result=session.act('pilot:A',{'action_id':mana['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertFalse(session.state.stack);self.assertEqual(1,session.state.players['A'].mana_pool['G'])
                self.cast(session,'A',untap,[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind);result=session.act('pilot:A',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary);self.settle(session)
                self.attack(session,[(source,'B')]);self.assertEqual(0,session.state.players['A'].mana_pool['R']);self.assertEqual('semantic.choice',self.resolve(session).kind)
                result=session.act('pilot:A',{'action_id':'choose','choice':'apply' if apply else 'decline'});self.assertTrue(result.ok,result.summary);self.settle(session);self.assertEqual(2 if apply else 0,session.state.players['A'].mana_pool['R'])
                if apply:self.cast(session,'A',spell,['B']);self.settle(session);self.assertEqual(1,session.state.players['A'].mana_pool['R'])
                self.until(session,lambda s:s.state.phase=='postcombat_main' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority');self.assertEqual(0,session.state.players['A'].mana_pool['R']);self.replay(session,load=True)

    def next_precombat(self,session):
        from quorune.model import TurnEntry
        engine=session.engine;engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.priority_player=None;session.state.priority_passes=[]
        engine._begin_turn(TurnEntry(turn_id='prepared-saga-turn-'+str(session.state.turn_sequence),player='A',created_sequence=session.state.turn_sequence));engine.pump();self.checkpoint(session)

    def test_actual_arni_chapter_two_no_target_resolves_but_lost_target_counters_all_results(self):
        for mode in ('legal','none','lost'):
            with self.subTest(mode=mode):
                session=self.session(608648030+len(mode));source=self.add(session.engine,'Arni Slays the Troll',zone='hand');body=self.add(session.engine,'Grizzly Bears');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.assert_closed(session,source)
                self.prepare(session,mana={'R':1,'G':1});self.checkpoint(session);self.cast(session,'A',source)
                self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target');group=session.packet('pilot:A',full=True)['decision']['ctx']['target_schema']['groups'][0]['id']
                self.target(session,'A',[{'group':group,'ref':body.ref}]);self.settle(session);self.assertEqual(1,session.state.cards[source.object_id].counters.get('lore'))
                self.next_precombat(session);self.target(session,'A',[] if mode=='none' else [body.ref]);self.assertEqual(2,session.state.cards[source.object_id].counters.get('lore'));self.assertEqual(0,session.state.players['A'].mana_pool['R'])
                if mode=='lost':self.cast(session,'B',bounce,[body.ref]);self.resolve_top(session)
                session=self.replay(session,load=True);self.settle(session);self.assertEqual(0 if mode=='lost' else 1,session.state.players['A'].mana_pool['R']);self.assertEqual(2 if mode=='legal' else 0,session.state.cards[body.object_id].counters.get('+1/+1',0))
                if mode=='legal':
                    life=session.state.players['A'].life;self.next_precombat(session);self.until(session,lambda s:s.state.phase=='precombat_main' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority');self.settle(session);self.assertEqual(life+4,session.state.players['A'].life);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                self.replay(session,load=True)



class SavageVentmawNativeTests(fixtures.ManaCompositionFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attack-turn-retained-mana.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/mana-component-composition-cards.json",
            ROOT / "tests/fixtures/fixed-qualified-stack-copy-cards.json",
            ROOT / "tests/fixtures/activation-paid-result-slot-cards.json",
            ROOT / "tests/fixtures/savage-ventmaw-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attack mana", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def place_attacker(self, game, *, name="Savage Ventmaw"):
        source = self.add(game.engine, name)
        source.acquired_control_turn_count = -1
        return source

    def pool(self, game, seat="A"):
        mana = game.state.players[seat].mana_pool
        return mana["R"], mana["G"]

    def mana_lots(self, game):
        from quorune.mana_provenance import mana_provenance_lots

        return [(lot.retention, lot.bundle) for lot in mana_provenance_lots(game.state.players["A"])]

    def action_for(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        (offered,) = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
        return offered["id"]

    def test_actual_ventmaw_keeps_only_its_attack_mana_after_departure_until_cleanup(self):
        game = self.session(50810631)
        source = self.place_attacker(game)
        another = self.place_attacker(game, name="Grizzly Bears")
        burn = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        mountain = self.add(game.engine, "Mountain")
        spend = self.add(game.engine, "Grizzly Bears", zone="hand", ref="ventmaw-paid-creature")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="ventmaw-private-card")
        self.add(game.engine, "Grizzly Bears", seat="D", ref="ventmaw-defending-bear")
        first_hand_card = game.state.players["A"].zones["hand"][0]
        game.engine.move_card(first_hand_card, "library", log=False)
        self.assert_closed(game, source)
        self.prepare(game)
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        self.attack(game, [(source, "B"), (another, "D")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual((0, 0), self.pool(game))
        self.cast(game, "B", burn, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((3, 3), self.pool(game))
        self.assertEqual((0, 0), self.pool(game, "B"))
        self.assertEqual([("end_of_turn", (("R", 3), ("G", 3)))], self.mana_lots(game))
        command = {"action_id": self.action_for(game, mountain), "pay": "auto"}
        minted = game.act("pilot:A", command)
        self.assertTrue(minted.ok, minted.summary)
        self.assertEqual((4, 3), self.pool(game))
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.blockers")
        self.assertEqual((3, 3), self.pool(game))
        self.until(game, lambda s: s.state.phase == "postcombat_main" and s.state.pending_decision is not None
                   and s.state.pending_decision.kind == "priority")
        self.assertEqual((3, 3), self.pool(game))
        self.priority(game, "A")
        paid = game.act("pilot:A", {"action_id": "cast:" + spend.ref, "pay": "manual", "payment": {"R": 1, "G": 1}})
        self.assertTrue(paid.ok, paid.summary)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[spend.object_id].zone)
        self.assertEqual((2, 2), self.pool(game))
        self.until(game, lambda s: s.state.step == "end_step")
        self.assertEqual((2, 2), self.pool(game))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self.until(game, lambda s: s.state.active_player != "A")
        self.assertEqual((0, 0), self.pool(game))
        self.assertFalse(self.mana_lots(game))
        self.replay(game, load=True)

    def test_actual_ventmaw_ignores_another_attacker_and_can_be_countered(self):
        for attacks in (False, True):
            with self.subTest(source_attacks=attacks):
                game = self.session(50810633 + attacks)
                source = self.place_attacker(game)
                other = self.place_attacker(game, name="Grizzly Bears")
                answer = self.add(game.engine, "Stifle", seat="B", zone="hand")
                self.add(game.engine, "Island", seat="B")
                self.assert_closed(game, source)
                self.assert_closed(game, answer)
                self.prepare(game)
                game.state.config.semantic_policy = "trusted_only"
                self.checkpoint(game)
                self.attack(game, [(source if attacks else other, "B")])
                self.assertEqual(int(attacks), self.trigger_count(game, source))
                if attacks:
                    original = game.state.stack[-1]
                    self.cast(game, "B", answer, [original.ref])
                    self.resolve_top(game)
                self.assertEqual((0, 0), self.pool(game))
                self.assertFalse(self.mana_lots(game))
                self.replay(game, load=True)

    def test_actual_ventmaw_copied_trigger_adds_a_second_exact_bundle(self):
        game = self.session(50810635)
        template = self.place_attacker(game)
        source_ref, = game.engine.create_token("A", name="", copy_of=template.ref)
        source = game.engine._resolve_object("A", source_ref, zones={"battlefield"})
        source.acquired_control_turn_count = -1
        other = self.place_attacker(game, name="Grizzly Bears")
        remover = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        resonator = self.add(game.engine, "Strionic Resonator")
        self.add(game.engine, "Wastes", ref="ventmaw-first-wastes")
        self.add(game.engine, "Wastes", ref="ventmaw-second-wastes")
        self.add(game.engine, "Savage Ventmaw", seat="B", ref="ventmaw-flying-blocker")
        self.assert_closed(game, template)
        self.assert_closed(game, resonator)
        self.prepare(game)
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        self.attack(game, [(source, "B"), (other, "B")])
        original = game.state.stack[-1]
        self.assertEqual(source.object_id, original.source_object_id)
        self.cast(game, "B", remover, [source.ref])
        self.resolve_top(game)
        self.assertEqual("outside", game.state.cards[source.object_id].zone)
        command = {"action_id": self.action_for(game, resonator), "targets": [original.ref], "pay": "auto"}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(2, self.trigger_count(game, source))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((3, 3), self.pool(game))
        self.resolve_top(game)
        self.assertEqual((6, 6), self.pool(game))
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.blockers")
        self.assertEqual((6, 6), self.pool(game))
        self.replay(game, load=True)

    def test_ventmaw_exact_bundle_and_trigger_require_independent_runtime_binding(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.turn_retained_resolution_mana import ATTACK_CAPABILITY, attack_effects

        game = self.session(50810637)
        source = self.place_attacker(game)
        self.assert_closed(game, source)
        (program,) = [candidate for candidate in game.engine.semantics.programs_for_oracle(source.oracle_id)
                      if ATTACK_CAPABILITY in candidate.capability_dependencies]
        self.assertEqual(attack_effects(), tuple(program.effects))
        deps = tuple(value for value in program.capability_dependencies if value != ATTACK_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + ATTACK_CAPABILITY, bound["blockers"])
        for malformed in (
            replace(program, event_condition=None),
            replace(program, event="spell.cast"),
            replace(program, effects=[program.effects[0]]),
            replace(program, effects=[{**effect, "source": "$controller"} for effect in program.effects]),
        ):
            bound = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"], bound)
        record = self.db.by_oracle_id(source.oracle_id)
        wrong = record.oracle_text.replace("this creature attacks", "a creature you control attacks")
        self.assertNotEqual("exact", compile_oracle_card(replace(record, oracle_text=wrong),
                                                       capability_registry=self.registry).status)
