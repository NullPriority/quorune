"""Existing keyword programs granted through current attachment/query scopes."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_job_select as actions
import test_ring_native_actions as traversal
from test_qualified_zone_event_queries import query_record
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class GrantedKeywordEventCompilerTests(unittest.TestCase):
    def record(self,text):
        return replace(query_record(text),type_line='Artifact — Equipment',keywords=())

    def test_one_keyword_reuses_the_existing_complete_inner_event_program(self):
        registry=load_default_capability_registry()
        for text,event,op in (
            ('Equipped creature gets +2/+0 and has afflict 1.','creature.becomes_blocked','lose_life'),
            ('Enchanted creature gets +10/+10 and has trample and annihilator 2.','creature.attacks','choose_cards_apnap'),
            ('Artifact creatures you control have afflict 3.','creature.becomes_blocked','lose_life'),
            ('As long as you control an artifact, creatures you control have afflict 2.','creature.becomes_blocked','lose_life'),
            ('Creatures you control have firebending 2.','creature.attacks','mana'),
            ('Creatures you control have ingest.','damage.dealt.self','exile_top_library_card'),
            ('Creatures you control have mobilize 1.','creature.attacks','choose_attacking_token_destinations'),
            ('Creatures you control have soulshift 2.','permanent.graveyard.self','offer_optional_effect'),
            ('Creatures you control have exploit.','permanent.enter.self','exploit'),
            ('Creatures you control have provoke.','creature.attacks','offer_optional_effect'),
        ):
            ir=compile_oracle_card(self.record(text),capability_registry=registry)
            self.assertEqual('exact',ir.status,text)
            nodes=[node for face in ir.faces for node in face.nodes]
            inner=next(node for node in nodes if node.kind=='granted_triggered_ability')
            outer=next(node for node in nodes if node.kind=='static_ability')
            self.assertEqual(event,inner.event);self.assertEqual(op,inner.effects[0]['op'])
            self.assertEqual(outer.span,inner.span)
            self.assertEqual(text,outer.text)
            self.assertIn('continuous.ability.fixed_keyword_event_grant',outer.capability_dependencies)
            self.assertNotIn(CURRENT_ABILITY_FRAGMENT_COVERAGE,inner.runtime_coverage)
        for text in ('Equipped creature has afflict X.','Equipped creature has afflict 0.',
                     'Equipped creature has afflict 21.','Equipped creature has afflict 2 and draw a card.',
                     'Equipped creature has afflict 1 and annihilator 2.'):
            self.assertNotEqual('exact',compile_oracle_card(self.record(text),capability_registry=registry).status)

    def test_grant_lowering_omission_is_killed(self):
        from quorune.compiler import granted_keyword_event_nodes
        card=self.record('Equipped creature has afflict 1.')
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(card,capability_registry=registry).status)
        with patch.object(granted_keyword_event_nodes,'compile_granted_keyword_event_nodes',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry).status)

    def test_dependency_fail_closed_uses_verified_evidence_and_an_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        card=self.record('Equipped creature has afflict 1.')
        for omitted in ('continuous.ability.fixed_keyword_event_grant','trigger.keyword.afflict.fixed',
                        'life.change.effect','trigger.placement.apnap','variant.monarch.designate'):
            value=deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==omitted).update(status='blocked',blockers=['tested omitted owner'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            with self.subTest(omitted=omitted):
                self.assertEqual(omitted=='variant.monarch.designate',
                    compile_oracle_card(card,capability_registry=registry).status=='exact')


class GrantedKeywordEventNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.JobSelectRuntimeTests.session
    add=actions.JobSelectRuntimeTests.add
    prepare=actions.JobSelectRuntimeTests.prepare
    cast=actions.JobSelectRuntimeTests.cast
    checkpoint=actions.JobSelectRuntimeTests.checkpoint
    resolve=actions.JobSelectRuntimeTests.resolve
    replay=actions.JobSelectRuntimeTests.replay
    until=traversal.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'granted-keywords.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/granted-keyword-event-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Granted keyword events',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',24)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,name):
        program=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'],name)

    def afflict_fixture(self,seed,*,name='Khenra Eternal'):
        self.assert_closed(name);session=self.session(seed)
        source=self.add(session.engine,name,ref='granted-keyword-attacker')
        blocker=self.add(session.engine,'Generic Bound Body',seat='C',ref='granted-keyword-borrowed-blocker')
        session.engine.change_control(blocker.object_id,'B',reason='pre-action borrowed blocker')
        response=self.add(session.engine,'Unsummon',zone='hand',ref='granted-keyword-source-response')
        self.add(session.engine,'Island',ref='granted-keyword-response-land')
        private={seat:self.add(session.engine,'Generic Bound Growth',seat=seat,zone='hand',ref='granted-keyword-private-'+seat)
                 for seat in 'ABCD'}
        return session,source,blocker,response,private

    def attack(self,session,source,defender='B',*,actor='A'):
        self.until(session,lambda current:current.state.pending_decision is not None
                   and current.state.pending_decision.kind=='combat.attackers' and current.state.active_player==actor)
        result=session.act('pilot:'+actor,{'action_id':'attack','attackers':{source.ref:defender}})
        self.assertTrue(result.ok,result.summary)

    def block(self,session,source,blocker):
        self.until(session,lambda current:current.state.pending_decision is not None
                   and current.state.pending_decision.kind=='combat.blockers' and current.state.pending_decision.actors==['B'])
        command={'action_id':'block','blocks':{blocker.ref:source.ref}}
        unchanged=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',command).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary)

    def test_two_equipment_grants_add_instances_and_survive_source_departure(self):
        self.assert_closed('Dagger of the Worthy')
        session,source,blocker,response,private=self.afflict_fixture(702_130_201)
        equipment=[self.add(session.engine,'Dagger of the Worthy',ref='granted-keyword-dagger-'+str(index)) for index in range(2)]
        self.prepare(session,mana={'U':4,'C':20});self.checkpoint(session)
        for dagger in equipment:
            offered=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            action=next(row for row in offered if row.get('source')==dagger.ref and row['id'].startswith('activate:'))
            result=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.attack(session,source);self.block(session,source,blocker)
        self.until(session,lambda current:len([item for item in current.state.stack if item.source_object_id==source.object_id])==3)
        session=self.replay(session,load=True)
        for viewer in 'ABCD':
            projected=json.dumps(session.packet('pilot:'+viewer,full=True))
            for owner,card in private.items():
                if owner==viewer:self.assertIn(card.ref,projected)
                else:self.assertNotIn(card.ref,projected)
        self.cast(session,'A',response,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertEqual({'A':40,'B':37,'C':40,'D':40},{seat:session.state.players[seat].life for seat in 'ABCD'})
        self.replay(session,load=True)

    def test_query_self_grant_creates_exactly_one_instance(self):
        session,source,blocker,_response,_private=self.afflict_fixture(702_130_202,name='Cyberman Patrol')
        self.prepare(session);self.checkpoint(session);self.attack(session,source);self.block(session,source,blocker)
        self.assertIsNone(self.resolve(session));self.assertEqual(37,session.state.players['B'].life)
        self.replay(session,load=True)

    def test_annihilator_grant_keeps_defender_after_provider_removal(self):
        self.assert_closed('Eldrazi Conscription');session=self.session(702_086_201)
        source=self.add(session.engine,'Generic Bound Body',seat='B',ref='granted-annihilator-attacker')
        aura=self.add(session.engine,'Eldrazi Conscription',zone='hand',ref='granted-annihilator-provider')
        response=self.add(session.engine,'Disenchant',zone='hand',ref='granted-annihilator-response')
        chosen=self.add(session.engine,'Generic Bound Body',seat='C',ref='granted-annihilator-owned-C')
        session.engine.change_control(chosen.object_id,'D',reason='pre-action borrowed sacrifice permanent')
        other=self.add(session.engine,'Generic Bound Body',seat='D',ref='granted-annihilator-other')
        for index in range(2):self.add(session.engine,'Generic Bound Plains',seat='D',ref='granted-annihilator-land-'+str(index))
        private=self.add(session.engine,'Generic Bound Growth',seat='D',zone='hand',ref='granted-annihilator-private-D')
        for index in range(2):self.add(session.engine,'Generic Bound Plains',ref='granted-annihilator-response-land-'+str(index))
        starting_card=session.state.players['A'].zones['hand'][0]
        session.engine.move_card(starting_card,'graveyard',reason='pre-action response hand-size fixture')
        self.prepare(session,mana={'W':4,'C':20});self.checkpoint(session)
        self.cast(session,'A',aura,[source.ref]);self.assertIsNone(self.resolve(session))
        self.attack(session,source,'D',actor='B')
        self.until(session,lambda current:current.state.pending_decision is not None
                   and current.state.pending_decision.kind=='priority' and current.state.pending_decision.actors==['A'])
        self.assertEqual('B',next(item.controller for item in session.state.stack if item.source_object_id==source.object_id))
        self.cast(session,'A',response,[aura.ref])
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        self.assertEqual(['D'],session.state.pending_decision.actors)
        session=self.replay(session,load=True);unchanged=authoritative_state_hash(session.state)
        command={'action_id':'choose','cards':[chosen.ref,other.ref]}
        for seat in 'ABC':
            packet=session.packet('pilot:'+seat,full=True)
            self.assertIsNone(packet['decision']);self.assertNotIn(private.ref,json.dumps(packet))
            self.assertFalse(session.act('pilot:'+seat,command).ok)
            self.assertEqual(unchanged,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:D',{'action_id':'choose','cards':[chosen.ref,source.ref]}).ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        result=session.act('pilot:D',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertIn(chosen.object_id,session.state.players['C'].zones['graveyard'])
        self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
        self.assertEqual(40,session.state.players['D'].life)
        self.replay(session,load=True)

    def test_conditional_grant_reads_current_state_before_the_event(self):
        self.assert_closed('Generic Conditional Afflict Provider')
        for index,remove in enumerate((False,True)):
            session,source,blocker,response,_private=self.afflict_fixture(702_130_203+index)
            self.add(session.engine,'Generic Conditional Afflict Provider',ref='conditional-keyword-provider')
            artifact=self.add(session.engine,'Ornithopter',ref='conditional-keyword-artifact')
            self.prepare(session,mana={'U':4,'C':20});self.checkpoint(session);self.attack(session,source)
            if remove:
                self.cast(session,'A',response,[artifact.ref]);self.assertIsNone(self.resolve(session))
            self.block(session,source,blocker)
            expected=1 if remove else 2
            self.until(session,lambda current:len([item for item in current.state.stack
                       if item.source_object_id==source.object_id])==expected)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(39 if remove else 37,session.state.players['B'].life)
            self.replay(session,load=True)


class TemporaryPaidFirebendingTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry

        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        primary = ROOT / "tests/fixtures/bound-effect-program-cards.json"
        already = {row["oracle_id"] for row in json.loads(primary.read_bytes())["cards"]}
        data = json.loads((ROOT / "tests/fixtures/granted-keyword-event-cards.json").read_bytes())
        data["cards"] = [row for row in data["cards"] if row["name"] in
                         {"Mountain", "Grizzly Bears", "Fire Nation Palace", "Fire Nation Turret"} and row["oracle_id"] not in already]
        selected = {row["oracle_id"] for row in data["cards"]}
        data["rulings"] = [row for row in data["rulings"] if row["oracle_id"] in selected]
        fixture = folder / "temporary-firebending-cards.json"
        fixture.write_text(json.dumps(data), encoding="utf-8")
        path = folder / "temporary-firebending.sqlite3"
        build_fixture_database([primary, fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary Firebending", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def save_replay(self, game, name):
        from hashlib import file_digest
        from quorune.record import authoritative_state_hash, replay_record

        parent = os.environ.get("QUORUNE_TEMPORARY_FIREBENDING_RECORD_PARENT") or os.environ.get("QUORUNE_PALACE_RECORD_PARENT")
        if parent is None:
            self.replay(game, load=True)
            return
        destination = Path(parent) / name
        game.save(destination)
        result = replay_record(destination, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        state_hash = authoritative_state_hash(game.state)
        self.assertEqual(state_hash, result["final_state_hash"])
        with (destination / "manifest.json").open("rb") as handle:
            manifest_hash = file_digest(handle, "sha256").hexdigest()
        destination.with_suffix(".receipt.json").write_text(
            json.dumps({"manifest_sha256": manifest_hash, "final_state_sha256": state_hash,
                        "commands": len(game.commands)}, indent=2) + "\n", encoding="utf-8")

    def act(self, game, seat, command, *, accepted=True):
        from quorune.record import authoritative_state_hash

        state_hash = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(state_hash, authoritative_state_hash(game.state))

    def actions(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

    def test_native_land_preflight_excludes_its_granted_keyword_reminder(self):
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.preflight import semantic_preflight

        deck = DeckDefinition("Whole temporary grant land", [DeckEntry("Fire Nation Palace", 1)], [])
        result, = semantic_preflight(self.db, deck, capability_profile="commander_review",
                                     collect_source_diagnostics=True)["cards"]
        self.assertEqual("fully_playable", result["status"], result["unresolved"])
        self.assertTrue(result["card_program_strict_capability_ready"], result["unresolved"])
        self.assertEqual(0, result["oracle_ir"]["material_residual_count"])

    def test_palace_native_entry_and_regular_red_mana_keep_existing_owners(self):
        for basic in (False, True):
            with self.subTest(basic=basic):
                game = self.session(702189651 + int(basic))
                palace = self.add(game.engine, "Fire Nation Palace", zone="hand")
                self.add(game.engine, "Mountain", seat="A" if basic else "B")
                self.assert_closed(game, palace)
                self.prepare(game)
                self.checkpoint(game)
                play = next(row["id"] for row in self.actions(game)
                            if row["id"].startswith("play-land:") and row.get("card") == palace.ref)
                self.act(game, "A", {"action_id": play})
                current = game.state.cards[palace.object_id]
                self.assertEqual(("battlefield", not basic), (current.zone, current.tapped))
                if basic:
                    mana = next(row["id"] for row in self.actions(game)
                                if row["id"] == f"activate:{current.ref}:ab2")
                    self.act(game, "A", {"action_id": mana, "pay": "auto"})
                    self.assertEqual(1, game.state.players["A"].mana_pool["R"])
                    self.assertFalse(game.state.stack)
                self.save_replay(game, f"palace-{'untapped' if basic else 'tapped'}-game-record")

    def test_palace_two_paid_grants_create_independent_combat_mana_then_expire(self):
        game = self.session(702189653)
        lands = [self.add(game.engine, "Fire Nation Palace", ref=f"paid-palace-{i}") for i in range(2)]
        own = self.add(game.engine, "Grizzly Bears", ref="firebending-receiver")
        enemy = self.add(game.engine, "Grizzly Bears", seat="B", ref="firebending-nonreceiver")
        for land in lands:
            self.assert_closed(game, land)
        self.prepare(game, mana={"C": 2, "R": 2})
        self.checkpoint(game)
        for land in lands:
            action = next(row for row in self.actions(game) if row["id"] == f"activate:{land.ref}:ab3")
            options = action["target_schema"]["legal_refs"]
            self.assertIn(own.ref, options)
            self.assertNotIn(enemy.ref, options)
            self.act(game, "A", {"action_id": action["id"], "targets": [enemy.ref], "pay": "auto"}, accepted=False)
            self.act(game, "A", {"action_id": action["id"], "targets": [own.ref], "pay": "auto"})
            self.assertTrue(game.state.cards[land.object_id].tapped)
            self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.players["A"].mana_pool.get("R", 0))
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.act(game, "A", {"action_id": "attack", "attackers": {own.ref: "B"}})
        pending = [*game.state.stack, *(item for batch in game.state.pending_trigger_batches for item in batch.items)]
        children = [item for item in pending if item.source_object_id == own.object_id]
        self.assertEqual(2, len(children))
        self.save_replay(game, "palace-two-grants-pending-game-record")
        if game.state.pending_decision.kind == "trigger.order":
            rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
            self.act(game, "A", {"action_id": "order", "triggers": [row["id"] for row in rows]})
        self.settle(game)
        self.assertEqual(8, game.state.players["A"].mana_pool["R"])
        self.until(game, lambda current: current.state.step == "end_combat")
        self.assertEqual(8, game.state.players["A"].mana_pool["R"])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(0, game.state.players["A"].mana_pool.get("R", 0))
        self.until(game, lambda current: current.state.active_player == "B")
        owner = game.engine._effective_card_data(game.state.cards[own.object_id])
        self.assertFalse(any(fragment.get("kind") == "granted_triggered"
                             for fragment in owner.get("ability_fragments", [])))
        self.save_replay(game, "palace-retention-and-grant-expired-game-record")

    def test_palace_requires_its_exact_target_price_duration_and_inherited_child(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup("Fire Nation Palace", fuzzy=False)
        profile = "commander_review"
        bound, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = {program.ability_id: program for program in semantics.programs_for_oracle(card.oracle_id)}
        self.assertEqual({"static:front:n1", "ability:ab2", "ability:ab3", "trigger:front:n3:granted"},
                         set(programs))
        child = programs["trigger:front:n3:granted"]
        self.assertEqual("creature.attacks", child.event)
        self.assertEqual((4, "end_of_combat"), (child.effects[0]["amount"], child.effects[0]["retain_until"]))
        for changed in (card.oracle_text.replace("creature you control", "creature an opponent controls"),
                        card.oracle_text.replace("until end of turn", "until your next turn"),
                        card.oracle_text.replace("firebending 4", "firebending X")):
            ir = compile_oracle_card(replace(card, oracle_text=changed),
                                     capability_registry=self.registry, capability_profile=profile)
            self.assertNotEqual("exact", ir.status)


    def turret_targets(self, game):
        self.until(game, lambda current: current.state.pending_decision.kind == "semantic.target")
        decision = game.packet("pilot:A", full=True)["decision"]
        choose, = [row for row in decision["legal_actions"] if row["id"] == "choose"]
        return choose["target_schema"]["legal_refs"]

    def turret_creature_data(self, game, creature):
        return game.engine._effective_card_data(game.state.cards[creature.object_id])

    def test_turret_combat_choice_couples_live_stats_and_one_attack_trigger(self):
        game = self.session(702189661)
        turret = self.add(game.engine, "Fire Nation Turret")
        self.add(game.engine, "Fire Nation Turret", seat="B", ref="turret-opponent-copy")
        own = self.add(game.engine, "Grizzly Bears", ref="turret-own-receiver")
        enemy = self.add(game.engine, "Grizzly Bears", seat="B", ref="turret-foreign-receiver")
        self.assert_closed(game, turret)
        self.prepare(game)
        self.checkpoint(game)
        options = self.turret_targets(game)
        self.assertEqual({own.ref, enemy.ref}, set(options))
        self.act(game, "B", {"action_id": "choose", "targets": [enemy.ref]}, accepted=False)
        self.act(game, "A", {"action_id": "choose", "targets": [own.ref, enemy.ref]}, accepted=False)
        self.save_replay(game, "turret-combat-target-pending-game-record")
        self.act(game, "A", {"action_id": "choose", "targets": [own.ref]})
        self.settle(game)
        own_data = self.turret_creature_data(game, own)
        self.assertEqual((4, 2), (int(own_data["power"]), int(own_data["toughness"])))
        enemy_data = self.turret_creature_data(game, enemy)
        self.assertEqual((2, 2), (int(enemy_data["power"]), int(enemy_data["toughness"])))
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.act(game, "A", {"action_id": "attack", "attackers": {own.ref: "C"}})
        children = [item for item in game.state.stack if item.source_object_id == own.object_id]
        self.assertEqual(1, len(children))
        self.settle(game)
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])
        self.until(game, lambda current: current.state.step == "end_combat")
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(0, game.state.players["A"].mana_pool.get("R", 0))
        self.until(game, lambda current: current.state.active_player == "B")
        own_data = self.turret_creature_data(game, own)
        self.assertEqual(2, int(own_data["power"]))
        self.assertFalse(any(fragment["kind"] == "granted_triggered"
                             for fragment in own_data.get("ability_fragments", [])))
        self.save_replay(game, "turret-attack-through-cleanup-game-record")

    def test_turret_optional_choice_can_be_empty_or_choose_an_opponents_creature(self):
        for choose_foreign in (False, True):
            with self.subTest(choose_foreign=choose_foreign):
                game = self.session(702189662 + int(choose_foreign))
                self.add(game.engine, "Fire Nation Turret")
                enemy = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-receiver")
                self.prepare(game)
                self.checkpoint(game)
                self.assertIn(enemy.ref, self.turret_targets(game))
                self.act(game, "A", {"action_id": "choose", "targets": [enemy.ref] if choose_foreign else []})
                self.settle(game)
                data = self.turret_creature_data(game, enemy)
                self.assertEqual(4 if choose_foreign else 2, int(data["power"]))
                self.assertEqual(int(choose_foreign), sum(fragment["kind"] == "granted_triggered"
                                 for fragment in data.get("ability_fragments", [])))
                self.save_replay(game, f"turret-{'foreign' if choose_foreign else 'empty'}-game-record")

    def test_turret_charge_payment_uses_actual_named_fifty_before_damage_resolves(self):
        game = self.session(702189664)
        turret = self.add(game.engine, "Fire Nation Turret")
        self.prepare(game, mana={"R": 1})
        turret.counters.update({"charge": 49, "other": 2})
        self.checkpoint(game)
        discharge_id = f"activate:{turret.ref}:ab3"
        self.assertNotIn(discharge_id, [row["id"] for row in self.actions(game)])
        self.act(game, "A", {"action_id": discharge_id, "targets": ["B"], "pay": "auto"}, accepted=False)
        charge_id = f"activate:{turret.ref}:ab2"
        self.assertIn(charge_id, [row["id"] for row in self.actions(game)])
        self.act(game, "A", {"action_id": charge_id, "pay": "auto"})
        self.assertFalse(game.state.cards[turret.object_id].tapped)
        self.settle(game)
        self.assertEqual({"charge": 50, "other": 2}, game.state.cards[turret.object_id].counters)
        discharge = next(row for row in self.actions(game) if row["id"] == discharge_id)
        self.assertIn("B", discharge["target_schema"]["legal_refs"])
        self.act(game, "A", {"action_id": discharge_id, "targets": ["B"], "pay": "auto"})
        self.assertEqual({"other": 2}, game.state.cards[turret.object_id].counters)
        life = game.state.players["B"].life
        self.save_replay(game, "turret-paid-pending-damage-game-record")
        self.settle(game)
        self.assertEqual(life - 50, game.state.players["B"].life)
        self.save_replay(game, "turret-damage-settled-game-record")

    def test_turret_illegal_sole_target_leaves_no_continuous_effect(self):
        game = self.session(702189665)
        turret = self.add(game.engine, "Fire Nation Turret")
        own = self.add(game.engine, "Grizzly Bears", ref="turret-doomed")
        self.prepare(game)
        turret.counters["charge"] = 50
        self.checkpoint(game)
        self.assertIn(own.ref, self.turret_targets(game))
        self.act(game, "A", {"action_id": "choose", "targets": [own.ref]})
        discharge_id = f"activate:{turret.ref}:ab3"
        self.assertIn(discharge_id, [row["id"] for row in self.actions(game)])
        self.act(game, "A", {"action_id": discharge_id, "targets": [own.ref], "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[own.object_id].zone)
        self.assertTrue(game.state.stack)
        self.save_replay(game, "turret-sole-target-illegal-pending-game-record")
        self.settle(game)
        self.assertFalse(game.state.continuous_effects)

    def test_turret_exact_binding_rejects_neighbor_timing_duration_and_cost(self):
        from quorune.card_programs.runtime_requirements import program_dynamic_runtime_requirements
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup("Fire Nation Turret", fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        self.assertEqual("capability_closed", bound["trust_basis"])
        programs = {program.ability_id: program for program in semantics.programs_for_oracle(card.oracle_id)}
        self.assertEqual({"trigger:front:n1", "trigger:front:n1:granted", "ability:ab2", "ability:ab3"},
                         set(programs))
        self.assertEqual("step.begin", programs["trigger:front:n1"].event)
        self.assertEqual(1, programs["trigger:front:n1"].target_schema["up_to"])
        self.assertEqual("creature.attacks", programs["trigger:front:n1:granted"].event)
        self.assertEqual(2, programs["trigger:front:n1:granted"].effects[0]["amount"])
        parent = programs["trigger:front:n1"]
        required, blockers = program_dynamic_runtime_requirements(parent)
        self.assertFalse(blockers)
        self.assertIn("trigger.keyword.firebending.fixed", required)
        for changed_program in (replace(parent, event_condition=None),
                                replace(parent, target_schema={**parent.target_schema, "up_to": 2})):
            _, blockers = program_dynamic_runtime_requirements(changed_program)
            self.assertTrue(any("runtime_temporary_combat_firebending:invalid:" in item for item in blockers))
        for changed in (card.oracle_text.replace("on your turn", "on each opponent's turn"),
                        card.oracle_text.replace("until end of turn", "until your next turn"),
                        card.oracle_text.replace("from this artifact", "from target artifact")):
            ir = compile_oracle_card(replace(card, oracle_text=changed),
                                     capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)
