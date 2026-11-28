"""Commander native departure X payment and reflexive player damage."""
from pathlib import Path
import json
import os
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

SOURCE = "Electro, Assaulting Battery"


class DepartureXReflexiveTests(InheritedNativeFixture, unittest.TestCase):
    @staticmethod
    def make_deck():
        commander = "Isamaru, Hound of Konda"
        return DeckDefinition("Departure X payment", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains",30)], [commander])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        p = Path(cls.temporary.name) / "departure-x.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "departure-x-reflexive-cards.json",
             fixtures / "targeted-opponent-library-top-exile-cards.json"], p)
        cls.db = CardDatabase(p)
        cls.registry = load_default_capability_registry()
        cls.deck = cls.make_deck()

    def command(self, game, seat, request, *, succeeds=True):
        before=authoritative_state_hash(game.state)
        result=game.act("pilot:"+seat,request)
        self.assertEqual(succeeds,result.ok,result.summary)
        if not succeeds:self.assertEqual(before,authoritative_state_hash(game.state))

    def scene(self, seed, *, made_mana=False):
        game=self.session(seed)
        source=self.add(game.engine, SOURCE, zone="hand")
        bounce=self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="departure-bounce")
        other=self.add(game.engine, "Grizzly Bears", seat="C")
        spell=self.add(game.engine, "Unsummon", zone="hand", ref="departure-own-cast") if made_mana else None
        self.assert_closed(game,source)
        self.prepare(game,mana={"R":8,"U":2})
        game.state.players["B"].mana_pool["U"]=1
        self.checkpoint(game)
        self.cast_for(game,"A",source)
        self.resolve_top(game)
        if spell is not None:
            amount=game.state.players["A"].mana_pool["R"]
            self.cast_for(game,"A",spell,[other.ref])
            self.assertGreaterEqual(len(game.state.stack),1)
            self.until(game,lambda s: not s.state.stack)
            self.assertEqual(amount+1,game.state.players["A"].mana_pool["R"])
        self.cast_for(game,"B",bounce,[source.ref])
        self.resolve_top(game)
        self.assertEqual("hand",game.state.cards[source.object_id].zone)
        trigger,=game.state.stack
        self.assertEqual(("A",[]),(trigger.controller,trigger.targets))
        choice=self.resolve(game)
        self.assertEqual(("semantic.choice",("A",)),(choice.kind,tuple(choice.actors)))
        return game,source

    def test_actual_departure_x_payment_is_separate_from_reflexive_targeting(self):
        game,source=self.scene(932717001,made_mana=True)
        original_red=game.state.players["A"].mana_pool["R"]
        self.assertGreaterEqual(original_red,2)
        self.command(game,"B",{"action_id":"choose","choice":"pay"},succeeds=False)
        self.command(game,"A",{"action_id":"choose","choice":"pay"})
        self.command(game,"A",{"action_id":"choose","x":999},succeeds=False)
        game=self.replay(game,load=True)
        before_total=sum(game.state.players["A"].mana_pool.values())
        self.command(game,"A",{"action_id":"choose","x":2})
        self.assertEqual(before_total-2,sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual(40,game.state.players["C"].life)
        self.target(game,"A",["C"])
        self.assertEqual(40,game.state.players["C"].life)
        trigger,=game.state.stack
        self.assertEqual(("triggered_ability","A",["C"],source.object_id),
                         (trigger.kind,trigger.controller,trigger.targets,trigger.source_object_id))
        game=self.replay(game,load=True)
        self.resolve_top(game)
        self.assertEqual(38,game.state.players["C"].life)
        self.replay(game,load=True)

    def test_decline_skips_target_while_paid_zero_creates_one_reflexive_trigger(self):
        game,_=self.scene(932717002)
        self.command(game,"A",{"action_id":"choose","choice":"decline"})
        self.assertFalse(game.state.stack)
        self.assertEqual(40,game.state.players["C"].life)
        self.replay(game,load=True)
        game,_=self.scene(932717003)
        before_total=sum(game.state.players["A"].mana_pool.values())
        self.command(game,"A",{"action_id":"choose","choice":"pay"})
        self.command(game,"A",{"action_id":"choose","x":0})
        self.assertEqual(before_total,sum(game.state.players["A"].mana_pool.values()))
        self.target(game,"A",["C"])
        self.assertTrue(game.state.stack)
        self.resolve_top(game)
        self.assertEqual(40,game.state.players["C"].life)
        self.replay(game,load=True)


    def save_leyline_replay(self, game, name):
        from hashlib import file_digest
        from quorune.record import replay_record

        parent = os.environ.get("QUORUNE_LEYLINE_RECORD_PARENT")
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

    def leyline_scene(self, seed, *, death=True):
        game = self.session(seed)
        source = self.add(game.engine, "Leyline Tyrant")
        original_identity = source.logical_object_id
        removal = self.add(game.engine, "Murder" if death else "Unsummon", seat="B", zone="hand")
        victim = self.add(game.engine, "Grizzly Bears", seat="C")
        self.prepare(game, mana={"R": 2, "W": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2} if death else {"U": 1})
        for card in (source, removal):
            self.assert_closed(game, card)
        self.checkpoint(game)
        self.cast_for(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard" if death else "hand", game.state.cards[source.object_id].zone)
        if death:
            trigger, = game.state.stack
            self.assertEqual(("A", [], source.object_id), (trigger.controller, trigger.targets, trigger.source_object_id))
            pending = self.resolve(game)
            self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        return game, source, original_identity, victim

    def test_actual_leyline_red_quote_locks_paid_damage_and_allows_a_creature_target(self):
        game, source, original_identity, victim = self.leyline_scene(932717021)
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.command(game, "B", {"action_id": "choose", "choice": "pay"}, succeeds=False)
        self.command(game, "A", {"action_id": "choose", "choice": "pay"})
        choice = game.packet("pilot:A", full=True)["decision"]
        schema = choice["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 2), (schema["minimum"], schema["maximum"]))
        self.command(game, "A", {"action_id": "choose", "x": 3}, succeeds=False)
        self.command(game, "A", {"action_id": "choose", "x": True}, succeeds=False)
        self.save_leyline_replay(game, "leyline-red-quote-game-record")
        self.command(game, "A", {"action_id": "choose", "x": 2})
        self.assertEqual(0, game.state.players["A"].mana_pool.get("R", 0))
        self.assertEqual(2, game.state.players["A"].mana_pool.get("W", 0))
        self.assertEqual("battlefield", game.state.cards[victim.object_id].zone)
        self.save_leyline_replay(game, "leyline-reflexive-target-pending-game-record")
        self.target(game, "A", [victim.ref])
        child, = game.state.stack
        self.assertEqual(("trigger.reflexive", original_identity, source.object_id, [victim.ref], 2),
                         (child.context["event"], child.context["source_logical_object_id"],
                          child.source_object_id, child.targets, child.context["dynamic_effects"][0]["amount"]))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)
        self.save_leyline_replay(game, "leyline-paid-damage-resolved-game-record")

    def test_leyline_nondeath_exit_decline_and_paid_zero_leave_distinct_results(self):
        game, _, _, _ = self.leyline_scene(932717022, death=False)
        self.assertFalse(game.state.stack)
        self.save_leyline_replay(game, "leyline-nondeath-exit-game-record")
        for pay in (False, True):
            with self.subTest(pay=pay):
                game, _, _, _ = self.leyline_scene(932717023 + int(pay))
                self.command(game, "A", {"action_id": "choose", "choice": "pay" if pay else "decline"})
                if pay:
                    self.command(game, "A", {"action_id": "choose", "x": 0})
                    self.target(game, "A", ["C"])
                    child, = game.state.stack
                    self.assertEqual(0, child.context["dynamic_effects"][0]["amount"])
                    self.resolve_top(game)
                else:
                    self.assertFalse(game.state.stack)
                self.assertEqual(40, game.state.players["C"].life)
                self.assertEqual((2, 2), (game.state.players["A"].mana_pool.get("R", 0),
                                          game.state.players["A"].mana_pool.get("W", 0)))
                self.save_leyline_replay(game, f"leyline-{'zero' if pay else 'decline'}-game-record")

    def test_leyline_mode_seals_red_death_scope_and_independent_authority(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.departing_x_reflexive import RED_DEATH_CAPABILITY, RED_DEATH_VERSION
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup("Leyline Tyrant", fuzzy=False)
        profile = "commander_review"
        bound, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = (row for row in semantics.programs_for_oracle(card.oracle_id)
                    if row.event == "creature.dies.self")
        self.assertEqual(RED_DEATH_VERSION, program.effects[0]["schema_version"])
        self.assertIsNone(program.target_schema)
        deps = tuple(value for value in program.capability_dependencies if value != RED_DEATH_CAPABILITY)
        mutant = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=profile).to_dict())
        declined = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile=profile)
        self.assertIn("capability:undeclared_runtime_dependency:" + RED_DEATH_CAPABILITY, declined["blockers"])
        for changed in (card.oracle_text.replace("amount of {R}", "amount of {G}"),
                        card.oracle_text.replace("any target", "target player"),
                        card.oracle_text.replace("dies", "leaves the battlefield")):
            result = compile_oracle_card(
                replace(card, oracle_text=changed), capability_registry=self.registry, capability_profile=profile,
            )
            self.assertNotEqual("exact", result.status)
