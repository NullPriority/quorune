"""Announced target pricing and private library ordering."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from quorune.rules.capabilities import load_default_capability_registry


class DireDowndraftPricingTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "dire-downdraft.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "creature-power-multiple-cards.json",
            fixtures / "self-target-reduction-cards.json",
            fixtures / "dire-downdraft-pricing-cards.json",
        ], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Announced-target price",
            [DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [ruler])

    def scene(self, seed):
        game = self.session(seed)
        spell = self.add(game.engine, "Dire Downdraft", zone="hand", ref="DIRE")
        self.assert_closed(game,spell)
        game.state.config.semantic_policy="trusted_only"
        return game, spell

    def paid(self, game, spell, total):
        item = next(i for i in game.state.stack if i.card_object_id==spell.object_id)
        self.assertEqual(total, item.context["mana_spent_total"])

    def finish(self, game, target, owner, *, choice):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice",[owner]), (pending.kind,pending.actors))
        command={"action_id":"choose","choice":choice}
        before=authoritative_state_hash(game.state)
        foreign="C" if owner=="B" else "B"
        self.assertFalse(game.act("pilot:"+foreign,command).ok)
        self.assertEqual(before,authoritative_state_hash(game.state))
        game=self.replay(game,load=True)
        result=game.act("pilot:"+owner,command)
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(game))
        index=0 if choice=="bottom" else -1
        self.assertEqual(target.object_id,game.state.players[owner].zones["library"][index])
        self.replay(game,load=True)

    def test_tapped_target_discount_is_locked_and_does_not_discount_another_target(self):
        game, spell = self.scene(71022301)
        target = self.add(game.engine,"Grizzly Bears",seat="C",ref="TAPPED-OWNED-BY-C")
        foreign = self.add(game.engine,"Grizzly Bears",seat="B",ref="UNDISCOUNTED")
        response = self.add(game.engine,"Refocus",seat="B",zone="hand",ref="FLASH-UNTAP")
        game.engine.change_control(target.object_id,"B")
        target.tapped=True
        self.assert_closed(game,response)
        game.state.players["B"].mana_pool.update({"C":1,"U":1})
        self.prepare(game,mana={"C":2,"U":1})
        self.checkpoint(game)
        before=authoritative_state_hash(game.state)
        rejected=game.act("pilot:A",{"action_id":"cast:"+spell.ref,"targets":[foreign.ref],"pay":"auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(game.state))
        self.cast(game,"A",spell,[target.ref])
        self.paid(game,spell,3)
        self.cast(game,"B",response,[target.ref])
        self.resolve_top(game)
        self.assertFalse(game.state.cards[target.object_id].tapped)
        self.paid(game,spell,3)
        self.finish(game,target,"C",choice="bottom")

    def test_full_price_can_choose_an_ordinary_untapped_creature(self):
        game, spell = self.scene(71022302)
        target=self.add(game.engine,"Grizzly Bears",seat="B",ref="FULL-PRICE")
        self.prepare(game,mana={"C":3,"U":1})
        self.checkpoint(game)
        self.cast(game,"A",spell,[target.ref])
        self.paid(game,spell,4)
        self.finish(game,target,"B",choice="top")

    def test_real_untapped_attacker_receives_the_same_single_reduction(self):
        game, spell = self.scene(71022303)
        target=self.add(game.engine,"Serra Angel",seat="B",ref="UNTAPPED-VIGILANCE-ATTACKER")
        target.acquired_control_turn_count=-1
        for index in range(3):
            self.add(game.engine,"Island",ref=f"CASTING-MANA-{index}")
        self.prepare(game,actor="B",active="B")
        self.checkpoint(game)
        self.until(game,lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind=="combat.attackers")
        result=game.act("pilot:B", {"action_id":"attack","attackers":{target.ref:"C"}})
        self.assertTrue(result.ok,result.summary)
        self.assertFalse(game.state.cards[target.object_id].tapped)
        self.assertEqual("C",game.state.cards[target.object_id].attacking)
        self.cast(game,"A",spell,[target.ref])
        self.paid(game,spell,3)
        self.finish(game,target,"B",choice="bottom")


    def test_a_composed_target_goes_to_the_same_library_end_in_the_owners_private_order(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.permanent_composition import MergePosition
        from quorune.composition_order_model import CompositionOrderRequest
        from quorune.composition_ordering import validate_original_composition_effect
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.semantic_choices.defaults import default_semantic_choice_registry
        from quorune.zone_transitions import ZoneTransitionOwner

        for index, position in enumerate(("bottom","top")):
            with self.subTest(position=position):
                game, spell = self.scene(71022304+index)
                host=game.engine
                root=self.add(host,"Ornithopter",seat="C",ref="DIVIDED-PUBLIC-ROOT")
                part=self.add(host,"Ornithopter",seat="C",zone="hand",ref="DIVIDED-PHYSICAL-PART")
                host.change_control(root.object_id,"B")
                self.prepare(game,actor="C",active="C")
                self.cast(game,"C",part)
                item=next(i for i in game.state.stack if i.card_object_id==part.object_id)
                target=ContinuousObjectIdentity(root.object_id,root.logical_object_id)
                component=ContinuousObjectIdentity(part.object_id,part.logical_object_id)
                ZoneTransitionOwner(host).commit_composition_merge(prepare_composition_merge(
                    host,target=target,source=component,stack_ref=item.ref,position=MergePosition.BOTTOM))
                self.prepare(game,mana={"C":3,"U":1})
                self.checkpoint(game)
                self.cast(game,"A",spell,[root.ref])
                self.paid(game,spell,4)
                choice=self.resolve(game)
                self.assertEqual(("semantic.choice",["C"]),(choice.kind,choice.actors))
                accepted=game.act("pilot:C",{"action_id":"choose","choice":position})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual(["pilot:C"],list(game.pending_principals()))
                packet=game.packet("pilot:C",full=True)
                legal=packet["decision"]["ctx"]["legal_actions"][0]["choice_schema"]["legal_refs"]
                self.assertEqual({root.ref,part.ref},set(legal))
                self.assertIsNone(game.packet("pilot:B",full=True)["decision"])
                saved = game.state.pending_decision.continuation
                _, continuation = default_semantic_choice_registry().decode_continuation(saved)
                original = continuation.effect["original_effect"]
                request = CompositionOrderRequest.from_dict(continuation.effect["request"])
                item = game.state.stack[-1]
                pointer = continuation.semantic_frame.instruction_pointer
                validate_original_composition_effect(game.engine, item, original, pointer, request)
                wrong_end = "top" if position == "bottom" else "bottom"
                for effect, altered_pointer, altered_request in (
                    ({**original, "position": wrong_end}, pointer, request),
                    ({**original, "card": part.ref}, pointer, request),
                    ({**original, "destination": "graveyard"}, pointer, request),
                    (original, pointer + 1, request),
                    ({**original, "position": 2}, pointer, replace(request, position=2)),
                ):
                    with self.assertRaises(GameRuleError):
                        validate_original_composition_effect(game.engine, item, effect, altered_pointer, altered_request)
                before=authoritative_state_hash(game.state)
                for principal, order in (("pilot:B",[part.ref,root.ref]),("pilot:C",[part.ref,part.ref])):
                    self.assertFalse(game.act(principal,{"action_id":"choose","cards":order}).ok)
                    self.assertEqual(before,authoritative_state_hash(game.state))
                game=self.replay(game,load=True)
                before_library=list(game.state.players["C"].zones["library"])
                accepted=game.act("pilot:C",{"action_id":"choose","cards":[part.ref,root.ref]})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertIsNone(self.resolve(game))
                cards=[part.object_id,root.object_id]
                expected=cards+before_library if position=="bottom" else before_library+cards
                self.assertEqual(expected,game.state.players["C"].zones["library"])
                self.assertNotIn(part.ref,str(game.packet("pilot:B",full=True)["state"]))
                self.replay(game,load=True)

    def test_price_clause_is_separate_from_the_spells_legal_creature_target(self):
        card = self.db.lookup("Dire Downdraft", fuzzy=False)
        def compile_card(value):
            return compile_oracle_card(value, capability_registry=self.registry, capability_profile="commander_review")

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        spell = next(n for n in ir.faces[0].nodes if n.kind=="spell_ability")
        pricing = next(n for n in ir.faces[0].nodes if n.kind=="static_ability")
        self.assertNotIn("combat_state", spell.target_schema)
        self.assertEqual("attacking_or_tapped", pricing.handlers[0]["modifier"]["target_schema"]["combat_state"])
        for wording in ("attacking and tapped", "blocking or tapped", "attacking or enchanted"):
            changed = replace(card, oracle_text=card.oracle_text.replace("attacking or tapped", wording))
            self.assertNotEqual("exact", compile_card(changed).status)
