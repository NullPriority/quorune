from __future__ import annotations

"""Native conditional Aura composition and complete-card rejection contrasts."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls
import test_source_attack_alone as attack_controls


class DeliveryInteractionBoundaryTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ProvokeTests.session
    add=controls.ProvokeTests.add
    prepare=controls.ProvokeTests.prepare
    checkpoint=controls.ProvokeTests.checkpoint
    resolve=controls.ProvokeTests.resolve
    replay=controls.ProvokeTests.replay
    binding=controls.ProvokeTests.binding
    private_replay=controls.ProvokeTests.private_replay
    priority=controls.ProvokeTests.priority
    actions=controls.ProvokeTests.actions
    until = attack_controls.SourceAttackAloneNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'interaction-boundaries.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/delivery-interaction-boundary-cards.json',
            ROOT/'tests/fixtures/constructed-resolution-mana-saga-boundary.json',
            ROOT/'tests/fixtures/source-attack-alone-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Interaction boundaries',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def reject(self,s,command,*,principal='pilot:A'):
        before=authoritative_state_hash(s.state);result=s.act(principal,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def cast(self,s,c,payment,targets=(),*,seat='A',auto=False):
        self.priority(s,seat)
        rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        row=next((r for r in rows if r.get('card')==c.ref and r.get('kind')=='cast'),None)
        self.assertIsNotNone(row,c.printed_name)
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto' if auto else 'manual'}
        if not auto:command['payment']=payment
        self.reject(s,command,principal='pilot:D')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_actual_natures_embrace_creature_target_and_typed_restriction(self):
        s=self.session(764100);aura=self.add(s.engine,"Nature's Embrace",zone='hand')
        recipient=self.add(s.engine,'Generic Bound Body');artifact=self.add(s.engine,'Civic Saber')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='aura-boundary-private-c')
        self.assertTrue(self.binding(s,aura)['strict_capability_ready'])
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s)
        self.reject(s,{'action_id':'cast:'+aura.ref,'targets':[artifact.ref],'pay':'manual','payment':{'C':2,'G':1}})
        self.cast(s,aura,{'C':2,'G':1},[recipient.ref]);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        current=s.state.cards[recipient.object_id]
        self.assertEqual((4,8),tuple(s.engine._numeric_stat(current.object_id,k) for k in ('power','toughness')))
        self.assertFalse(any(a.mana_ability for a in s.engine._activated_abilities(current)))
        self.assertEqual(recipient.object_id,s.state.cards[aura.object_id].attached_to)
        self.private_replay(s,private)

    def test_actual_natures_embrace_land_and_animated_land_keep_two_mana_grant(self):
        for index,animate in enumerate((False,True)):
            s=self.session(764101+index);aura=self.add(s.engine,"Nature's Embrace",zone='hand')
            land=self.add(s.engine,'Forest');land.acquired_control_turn_count=-1
            revolt=self.add(s.engine,"Nature's Revolt",zone='hand') if animate else None
            counter=self.add(s.engine,'Counterspell',zone='hand')
            bolt=self.add(s.engine,'Lightning Bolt',seat='B',zone='hand')
            s.state.players['B'].mana_pool['R']=1
            self.prepare(s,mana={'C':5 if animate else 2,'G':3 if animate else 1});self.checkpoint(s)
            self.cast(s,aura,{'C':2,'G':1},[land.ref]);self.assertIsNone(self.resolve(s))
            if animate:
                self.cast(s,revolt,{'C':3,'G':2});self.assertIsNone(self.resolve(s))
                self.assertEqual((4,4),tuple(s.engine._numeric_stat(land.object_id,k) for k in ('power','toughness')))
            else:self.assertNotIn('creature',s.engine._type_parts(s.engine._effective_card_data(land)['type_line'])[0])
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            self.cast(s,bolt,{'R':1},['A'],seat='B');target=s.state.stack[-1].ref
            self.cast(s,counter,{},[target],auto=True)
            self.assertTrue(s.state.cards[land.object_id].tapped)
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(40,s.state.players['A'].life)
            self.assertEqual(('graveyard','graveyard'),(s.state.cards[counter.object_id].zone,s.state.cards[bolt.object_id].zone))
            self.replay(s)

    def partial_source(self,name,seed):
        s=self.session(seed);s.state.config.semantic_policy='trusted_only'
        c=self.add(s.engine,name,zone='battlefield')
        ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
        self.assertFalse(self.binding(s,c)['strict_capability_ready'])
        return s,c,ir

    def test_partial_pact_weapon_cannot_admit_or_activate_its_nonmana_equip(self):
        s,c,ir=self.partial_source('Pact Weapon',764110)
        target=self.add(s.engine,'Generic Bound Body');cost=self.add(s.engine,'Generic Bound Growth',zone='hand')
        self.assertTrue(any('attachment.equip.fixed_nonmana' in n.capability_dependencies for f in ir.faces for n in f.nodes))
        self.prepare(s,mana={'C':8,'B':2});self.checkpoint(s)
        self.assertFalse(any(r['id'].startswith('activate:'+c.ref+':') for r in self.actions(s)))
        self.assertFalse(any(a.builtin_semantic_key=='builtin:equip' for a in s.engine._activated_abilities(c)))
        self.reject(s,{'action_id':'activate:'+c.ref+':ab3','targets':[target.ref],'cost_cards':[cost.ref],'pay':'auto'})
        self.replay(s)

    def test_partial_reconfigure_companions_cannot_admit_or_attach(self):
        s,c,ir=self.partial_source('Cloudsteel Kirin',764112)
        target=self.add(s.engine,'Generic Bound Body')
        self.assertTrue(any('attachment.reconfigure.fixed_mana' in n.capability_dependencies for f in ir.faces for n in f.nodes))
        self.prepare(s,mana={'C':8,'U':2,'W':2});self.checkpoint(s)
        self.assertFalse(any(a.reconfigure for a in s.engine._activated_abilities(c)))
        self.assertFalse(any(r['id'].startswith('activate:'+c.ref+':') for r in self.actions(s)))
        self.reject(s,{'action_id':'activate:'+c.ref+':ab3-reconfigure-attach','targets':[target.ref],'pay':'auto'})
        self.replay(s)


    def test_kami_war_all_colors_preserves_its_original_scope(self):
        from dataclasses import replace

        name = "The Kami War // O-Kagachi Made Manifest"
        game = self.session(764114)
        original = self.add(game.engine, name, zone="battlefield")
        self.assertTrue(self.binding(game, original)["strict_capability_ready"])
        card = self.db.lookup(name, fuzzy=False)
        changed_faces = (*card.faces[:-1], {**card.faces[-1], "oracle_text":
                         card.faces[-1]["oracle_text"] + "\nInvent an unsupported attack outcome."})
        changed = replace(card, faces=changed_faces, oracle_text=card.oracle_text + "\nInvent an unsupported attack outcome.")
        ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)
        self.assertTrue(any("continuous.characteristics.all_colors_definition" in node.capability_dependencies
                            for face in ir.faces for node in face.nodes))

    def kami_attack(self, seed, *, transform=False, has_bounce=False, empty=False):
        from quorune.engine import TURN_STEPS

        game = self.session(seed)
        kami = self.add(game.engine, "The Kami War // O-Kagachi Made Manifest", ref="kami-war-attacker")
        if transform:
            kami.counters["lore"] = 2
            self.add(game.engine, "Fervor")
        else:
            kami.active_face = "O-Kagachi Made Manifest"
            kami.acquired_control_turn_count = -1
        high = low = None
        if not empty:
            high = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="kami-graveyard-two")
            low = self.add(game.engine, "Lightning Bolt", zone="graveyard", ref="kami-graveyard-one")
        land = self.add(game.engine, "Generic Bound Plains", zone="graveyard", ref="kami-graveyard-land")
        enemy = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="kami-foreign-card")
        unknown = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="kami-private-hand")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand", ref="kami-bounce") if has_bounce else None
        self.assertTrue(self.binding(game, kami)["strict_capability_ready"])
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        if bounce is not None:
            self.assertTrue(self.binding(game, bounce)["strict_capability_ready"])
            self.add(game.engine, "Island", seat="C", ref="kami-bounce-island")
        if transform:
            game.state.phase_index = TURN_STEPS.index(("beginning", "draw"))
            game.engine._grant_priority("A")
            game.engine.pump()
        self.checkpoint(game)
        if transform:
            self.until(game, lambda active: any(item.context.get("event") == "saga.chapter.3"
                                                for item in active.state.stack))
            self.assertIsNone(self.resolve(game))
        current = game.state.cards[kami.object_id]
        self.assertEqual("O-Kagachi Made Manifest", current.active_face)
        self.assertEqual({"W", "U", "B", "R", "G"}, set(game.engine._effective_card_data(current)["colors"]))
        self.until(game, lambda active: active.state.pending_decision is not None
                                       and active.state.pending_decision.kind == "combat.attackers")
        action = game.act("pilot:A", {"action_id": "attack", "attackers": {current.ref: "B"}})
        self.assertTrue(action.ok, action.summary)
        trigger, = (item for item in game.state.stack if item.context.get("event") == "creature.attacks")
        self.assertEqual("B", trigger.context["defending_player"])
        self.assertEqual(kami.object_id, trigger.source_object_id)
        return game, kami, high, low, land, enemy, unknown, bounce

    def kami_choose(self, game, chooser, refs, selected):
        self.assertEqual(("semantic.choice", [chooser]), (game.state.pending_decision.kind, list(game.state.pending_decision.actors)))
        packet = game.packet("pilot:" + chooser, full=True)["decision"]
        choice = packet["legal_actions"][0]["choice_schema"]
        self.assertEqual(set(refs), set(choice["legal_refs"]))
        actor = "A" if chooser != "A" else "D"
        command = {"action_id": "choose", "card": selected.ref}
        self.reject(game, command, principal="pilot:" + actor)
        self.reject(game, {"action_id": "choose"}, principal="pilot:" + chooser)
        result = game.act("pilot:" + chooser, command)
        self.assertTrue(result.ok, result.summary)

    def test_kami_war_transforms_attacks_and_defender_chooses_its_power_bonus_in_replay(self):
        game, kami, high, low, land, enemy, unknown, _ = self.kami_attack(764120, transform=True)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        for bad in (land, enemy):
            self.reject(game, {"action_id": "choose", "card": bad.ref}, principal="pilot:B")
        self.kami_choose(game, "B", (high.ref, low.ref), high)
        self.assertEqual("hand", game.state.cards[high.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[low.object_id].zone)
        self.assertEqual((8, 6), tuple(game.engine._numeric_stat(kami.object_id, stat) for stat in ("power", "toughness")))
        self.assertNotIn(unknown.ref, str(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_kami_war_returns_the_choice_after_source_departure_and_skips_growth(self):
        game, kami, high, low, _, _, _, bounce = self.kami_attack(764121, has_bounce=True)
        self.cast(game, bounce, {}, [kami.ref], seat="C", auto=True)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual("hand", game.state.cards[kami.object_id].zone)
        game = self.replay(game, load=True)
        self.kami_choose(game, "B", (high.ref, low.ref), low)
        self.assertEqual("hand", game.state.cards[low.object_id].zone)
        self.assertEqual("hand", game.state.cards[kami.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[high.object_id].zone)
        self.replay(game, load=True)

    def test_kami_war_departed_defender_reassigns_card_choice_to_another_opponent(self):
        game, kami, high, low, _, _, _, _ = self.kami_attack(764122)
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["A"], list(game.state.pending_decision.actors))
        assignment = {"action_id": "choose", "choice": "C"}
        self.reject(game, assignment, principal="pilot:D")
        game = self.replay(game, load=True)
        result = game.act("pilot:A", assignment)
        self.assertTrue(result.ok, result.summary)
        self.kami_choose(game, "C", (high.ref, low.ref), low)
        self.assertEqual("hand", game.state.cards[low.object_id].zone)
        self.assertEqual(7, game.engine._numeric_stat(kami.object_id, "power"))
        self.replay(game, load=True)

    def test_kami_war_no_eligible_nonland_completes_without_choice_or_growth(self):
        game, kami, _, _, land, enemy, _, _ = self.kami_attack(764124, empty=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(6, game.engine._numeric_stat(kami.object_id, "power"))
        self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[enemy.object_id].zone)
        self.replay(game, load=True)

    def test_kami_war_defending_return_authority_and_scope_fail_closed(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.graveyard_return_choices import DEFENDING_NONLAND_RETURN_CAPABILITY

        name = "The Kami War // O-Kagachi Made Manifest"
        game = self.session(764123)
        card = self.add(game.engine, name)
        program, = (program for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if DEFENDING_NONLAND_RETURN_CAPABILITY in program.capability_dependencies)
        kept = tuple(value for value in program.capability_dependencies if value != DEFENDING_NONLAND_RETURN_CAPABILITY)
        altered_effect = {**program.effects[0], "graveyard_owner": "$context.defending_player"}
        for changed in (
            replace(program, effects=[altered_effect]),
            replace(program, event_condition=None),
            replace(program, capability_dependencies=kept,
                    capability_closure=self.registry.closure(kept, profile="commander_review").to_dict()),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        with patch("quorune.compiler.graveyard_return_choice_templates.defending_nonland_return_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                self.db.lookup(name), capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_exact_resolution_mana_fragment_does_not_admit_unowned_saga_and_replacement(self):
        s=self.session(764115)
        s.state.config.semantic_policy='trusted_only'
        source=self.add(s.engine,'Constructed Resolution Mana Saga Boundary')
        promoted=self.add(s.engine,'The Legend of Roku // Avatar Roku')
        promoted.active_face='Avatar Roku'
        control=self.add(s.engine,'Quirion Sentinel',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        ir=compile_oracle_card(self.db.lookup(source.printed_name),
            capability_registry=self.registry,capability_profile='commander_review')
        capability='mana.production.fixed_resolution_choice'
        promoted_ir=compile_oracle_card(self.db.lookup(promoted.printed_name),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',promoted_ir.status,promoted_ir.material_residuals)
        self.assertTrue(self.binding(s,promoted)['strict_capability_ready'])
        self.assertTrue(any(capability in node.capability_dependencies
            for face in ir.faces for node in face.nodes))
        self.assertNotEqual('exact',ir.status)
        blockers={blocker for residual in ir.material_residuals for blocker in residual.blockers}
        self.assertTrue({'ordinary Saga chapter event binding','exact typed chapter effect body'} <= blockers)
        from high_risk_interaction_support import _record as boundary_record
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        boundary=boundary_record('resolution-mana-replacement-boundary')
        boundary_ir=compile_oracle_card(boundary,capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(any(capability in node.capability_dependencies for face in boundary_ir.faces for node in face.nodes))
        self.assertTrue({'replacement applicability','self-replacement and prevention ordering'} <=
            {blocker for residual in boundary_ir.material_residuals for blocker in residual.blockers})
        boundary_program=compile_best_available_card_program(self.db,boundary,semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        boundary_binding=bind_card_program_runtime(boundary_program,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(boundary_binding['strict_capability_ready']);self.assertFalse(boundary_binding['compatible_ready'])
        self.assertFalse(self.binding(s,source)['strict_capability_ready'])
        self.assertIn('trust_basis:unresolved',self.binding(s,source)['blockers'])
        self.assertTrue(self.binding(s,control)['strict_capability_ready'])

        self.prepare(s,mana={'C':12,'G':1})
        self.checkpoint(s)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertIn('enchantment',s.engine._type_parts(s.engine._effective_card_data(source)['type_line'])[0])
        offers=self.actions(s)
        ability, = s.engine._activated_abilities(source)
        self.assertIsNone(ability.builtin_semantic_key)
        activation_id=f'activate:{source.ref}:{ability.ability_id}'
        self.assertFalse(any(row['id']==activation_id for row in offers))
        self.assertTrue(any(row.get('kind')=='cast' and row.get('card')==control.ref for row in offers))

        self.cast(s,control,{'C':1,'G':1})
        pending=self.resolve(s)
        self.assertIsNotNone(pending)
        self.assertEqual('semantic.choice',pending.kind)
        context=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual(('choose_mana',1),(context['operation'],context['amount']))
        self.reject(s,{'action_id':'choose','choice':'G'},principal='pilot:D')
        result=s.act('pilot:A',{'action_id':'choose','choice':'G'})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.players['A'].mana_pool['G'])
        self.assertEqual('battlefield',s.state.cards[control.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertFalse(self.binding(s,source)['strict_capability_ready'])
        self.private_replay(s,private)

        # The variable-firebending token body is outside the fixed recipe
        # grammar. Reject before payment or stack mutation.
        command={'action_id':activation_id,'pay':'manual','payment':{'C':8}}
        card_ids=set(s.state.cards)
        mana=dict(s.state.players['A'].mana_pool)
        self.reject(s,command,principal='pilot:D')
        self.reject(s,command)
        self.assertFalse(s.state.stack)
        self.assertEqual(card_ids,set(s.state.cards))
        self.assertEqual(mana,s.state.players['A'].mana_pool)
        self.assertFalse(self.binding(s,source)['strict_capability_ready'])
        s=self.replay(s,load=True)
        self.assertFalse(s.state.stack)
        self.assertFalse(any(row['id']==activation_id for row in self.actions(s)))
        self.private_replay(s,private)
        action=next(row for row in self.actions(s) if row.get('source')==promoted.ref and row.get('kind')=='activate')
        command={'action_id':action['id'],'pay':'manual','payment':{'C':8}}
        self.reject(s,command,principal='pilot:D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        dragon,=[c for c in s.state.cards.values() if c.is_token and c.controller=='A' and c.zone=='battlefield']
        self.assertEqual((4,4),tuple(s.engine._numeric_stat(dragon.object_id,k) for k in ('power','toughness')))
        self.assertIn('Flying',s.engine._effective_card_data(dragon)['keywords'])
        self.private_replay(s,private)
