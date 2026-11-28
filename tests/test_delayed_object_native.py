"""CR603.7: real combat creates delayed actions without immediate movement."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DelayedObjectNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'delayed-object.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/delayed-object-action-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Delayed object actions',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_combat_delays_destruction_and_sacrifice_and_skips_the_blinked_wall_after_load(self):
        s=self.session(603700001)
        fog=self.add(s.engine,'Fog Elemental',ref='actual-delayed-fog')
        first=self.add(s.engine,'Grizzly Bears',ref='first-delayed-attacker')
        second=self.add(s.engine,'Grizzly Bears',ref='second-delayed-attacker')
        asp=self.add(s.engine,'Tangle Asp',ref='actual-delayed-counterpart-source')
        counterpart=self.add(s.engine,'Grizzly Bears',seat='B',ref='actual-delayed-counterpart')
        blinked=self.add(s.engine,'Cinder Wall',seat='B',ref='actual-blinked-wall')
        doomed=self.add(s.engine,'Cinder Wall',seat='B',ref='actual-doomed-wall')
        blink=self.add(s.engine,'Cloudshift',seat='B',zone='hand',ref='actual-delayed-blink')
        self.add(s.engine,'Plains',seat='B',ref='delayed-blink-mana')
        self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='delayed-private-hand')
        for card in (fog,first,second,asp):card.acquired_control_turn_count=-1
        for card in (fog,blinked,doomed,blink,asp):self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s)
        self.attack(s,[(fog,'D'),(first,'B'),(second,'B'),(asp,'B')]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,len(s.state.delayed_triggers))
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{blinked.ref:first.ref,doomed.ref:second.ref,counterpart.ref:asp.ref}}
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:len(game.state.delayed_triggers)==4 and not game.state.stack and game.state.pending_decision.kind=='priority')
        for card in (fog,blinked,doomed,asp,counterpart):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        original=s.state.cards[blinked.object_id].logical_object_id
        self.cast(s,'B',blink,[blinked.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(original,s.state.cards[blinked.object_id].logical_object_id)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat' and bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(4,len(s.state.stack))
        self.assertEqual(36,s.state.players['D'].life)
        self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual('graveyard',s.state.cards[asp.object_id].zone)
        self.assertEqual(1,s.state.cards[counterpart.object_id].marked_damage)
        for card in (fog,blinked,doomed,counterpart):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[blinked.object_id].zone)
        for card in (fog,doomed,counterpart):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.assertEqual(4,len(s.state.delayed_triggers));self.assertFalse(any(t.active for t in s.state.delayed_triggers))
        for seat in 'ABD':self.assertNotIn('delayed-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)


class EarthbendNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "earthbend.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "mutate-casting-cards.json",
            fixtures / "qualified-attack-cards.json",
            fixtures / "counter-placement-event-cards.json",
            fixtures / "worldwagon-cards.json",
            fixtures / "optional-object-action-counters.json",
            fixtures / "earthbend-cards.json",
            fixtures / "per-player-query-damage-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Earthbend", [DeckEntry("Generic Bound Commander", 1, "commander"),
                          DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"],
        )

    def _ba_actions(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

    def test_ba_sing_se_uses_basic_supertype_and_ordinary_tapped_mana_rules(self):
        for basic in (False, True):
            with self.subTest(basic=basic):
                game = self.session(70166404 + basic)
                source = self.add(game.engine, "Ba Sing Se", zone="hand")
                self.add(game.engine, "Forest" if basic else "Temple Garden")
                self.add(game.engine, "Forest", seat="B", ref="ba-opposing-basic")
                self.assert_closed(game, source)
                self.prepare(game, mana={"C":2,"G":1})
                self.checkpoint(game)
                play, = [r for r in self._ba_actions(game) if r["action"] == "play_land" and source.ref in r["id"]]
                result = game.act("pilot:A", {"action_id":play["id"]})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(not basic, game.state.cards[source.object_id].tapped)
                game = self.replay(game, load=True)
                mana = [r for r in self._ba_actions(game) if r.get("mana_ability") and source.ref in r["id"]]
                self.assertEqual(int(basic), len(mana))
                if mana:
                    result = game.act("pilot:A", {"action_id":mana[0]["id"],"pay":"auto"})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual(2, game.state.players["A"].mana_pool["G"])
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertFalse(any("Earthbend" in r["label"] and source.ref in r["id"] for r in self._ba_actions(game)))
                self.replay(game,load=True)

    def test_ba_sing_se_requires_sorcery_timing_and_replays_earthbend_return(self):
        game = self.session(70166409)
        source = self.add(game.engine, "Ba Sing Se")
        land = self.add(game.engine, "Forest", ref="ba-eligible")
        rival = self.add(game.engine, "Forest", seat="B", ref="ba-ineligible")
        doom = self.add(game.engine, "Doom Blade",seat="B",zone="hand")
        self.assert_closed(game, source)
        self.prepare(game,mana={"C":2,"G":1})
        game.state.players["B"].mana_pool.update({"C":1,"B":1})
        self.checkpoint(game)
        option, = [r for r in self._ba_actions(game) if source.ref in r["id"] and "earthbend" in r["label"].casefold()]
        self.assertIn(land.ref,option["target_schema"]["groups"][0]["legal_refs"])
        self.assertNotIn(rival.ref,option["target_schema"]["groups"][0]["legal_refs"])
        command = {"action_id":option["id"],"pay":"auto","targets":[rival.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A",command).ok)
        self.assertEqual(before,authoritative_state_hash(game.state))
        result = game.act("pilot:A",{**command,"targets":[land.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        game = self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1":2},game.state.cards[land.object_id].counters)
        self.assertIn("haste",game.engine._combat_keywords(game.state.cards[land.object_id]))
        self.cast(game,"B",doom,[land.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard",game.state.cards[land.object_id].zone)
        game = self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[land.object_id]
        self.assertEqual(("battlefield","A",True,{}),(current.zone,current.controller,current.tapped,current.counters))
        self.replay(game,load=True)

        offturn = self.session(70166410)
        source = self.add(offturn.engine,"Ba Sing Se")
        self.add(offturn.engine,"Forest")
        self.prepare(offturn,actor="A",active="B",mana={"C":2,"G":1})
        self.checkpoint(offturn)
        rows = [r for r in self._ba_actions(offturn) if source.ref in r["id"]]
        self.assertTrue(any(row.get("mana_ability") for row in rows))
        self.assertFalse(any("earthbend" in row["label"].casefold() for row in rows))

    def test_earthbend_compiler_preserves_target_amount_and_capability_boundaries(self):
        from unittest.mock import patch

        from quorune.compiler.earthbend_templates import earthbend_effect_template
        from quorune.earthbend_model import EARTHBEND_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.earthbend_shapes import earthbend_node_capabilities

        lesson = self.db.lookup("Earthbending Lesson")
        for text in ("Earthbend 4.", "Earthbend X, where X is twice the number of Foods you control."):
            with self.subTest(text=text):
                _, effects, schema, mechanics = earthbend_effect_template(text, source_name=lesson.name)
                self.assertIn(EARTHBEND_CAPABILITY, earthbend_node_capabilities(
                    effects=effects, target_schema=schema, mechanic_ids=mechanics,
                ))
                self.assertFalse(earthbend_node_capabilities(
                    effects=effects, target_schema={**schema, "controller_relation": "any"}, mechanic_ids=mechanics,
                ))
        for text in ("Earthbend -1.", "Earthbend X.", "Earthbend 2 an opponent controls."):
            with self.subTest(text=text):
                self.assertIsNone(earthbend_effect_template(text, source_name=lesson.name))
        self.assertEqual("exact", compile_oracle_card(lesson, capability_registry=self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        capability = next(row for row in raw["capabilities"] if row["id"] == EARTHBEND_CAPABILITY)
        capability.update(status="blocked", blockers=["withdrawn for the dependency test"])
        self.assertNotEqual("exact", compile_oracle_card(lesson, capability_registry=CapabilityRegistry(raw)).status)
        with patch("quorune.compiler.resolution_effect_templates.earthbend_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(lesson, capability_registry=self.registry).status)

    def test_actual_earthbend_resumes_counter_replacements_and_returns_all_merged_cards(self):
        from quorune.characteristic_evaluation import type_parts

        s = self.session(70166001)
        lesson = self.add(s.engine, "Earthbending Lesson", zone="hand")
        heron = self.add(s.engine, "Dreamtail Heron", zone="hand")
        land = self.add(s.engine, "Forest", ref="earthbend-land")
        foreign = self.add(s.engine, "Forest", seat="B", ref="earthbend-foreign-land")
        scales = self.add(s.engine, "Hardened Scales")
        season = self.add(s.engine, "Branching Evolution")
        destroy = self.add(s.engine, "Doom Blade", seat="B", zone="hand")
        control = self.add(s.engine, "Act of Treason", seat="C", zone="hand")
        self.add(s.engine, "Vedalken Orrery", seat="C")
        private = self.add(s.engine, "Boomerang", seat="D", zone="hand", ref="earthbend-private")
        for source in (lesson, heron, scales, season, destroy, control):
            self.assert_closed(s, source)
        self.prepare(s, mana={"C": 6, "G": 1, "U": 1})
        s.state.players["B"].mana_pool.update({"C": 1, "B": 1})
        s.state.players["C"].mana_pool.update({"C": 2, "R": 1})
        self.checkpoint(s)
        offered = self.action(s, "A", lesson)
        before = authoritative_state_hash(s.state)
        invalid = {"action_id": offered["id"], "targets": [foreign.ref], "pay": "auto"}
        self.assertFalse(s.act("pilot:A", invalid).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.cast_for(s, "A", lesson, [land.ref], reserve={"U": 1})
        decision = self.resolve(s)
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["pilot:A"], s.pending_principals())
        current = s.state.cards[land.object_id]
        types, subtypes, supertypes = type_parts(s.engine._effective_card_data(current)["type_line"])
        self.assertEqual(({"land", "creature"}, {"forest"}, {"basic"}), (types, subtypes, supertypes))
        self.assertEqual({}, current.counters)
        self.assertFalse(current.tapped)
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        options = s.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        first = next(option["id"] for option in options if option["source"] == scales.ref)
        choice = {"action_id": "choose", "replacement": first}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act("pilot:B", choice).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        result = s.act("pilot:A", choice)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        current = s.state.cards[land.object_id]
        self.assertEqual({"+1/+1": 10}, current.counters)
        self.assertIn("haste", s.engine._combat_keywords(current))
        self.assertEqual(10, s.engine._numeric_stat(land.object_id, "power"))
        self.assertEqual(1, sum(trigger.active for trigger in s.state.delayed_triggers))

        self.priority(s, "A")
        actions = s.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        mana = next(row for row in actions if row.get("mana_ability") and land.ref in row["id"])
        result = s.act("pilot:A", {"action_id": mana["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, s.state.players["A"].mana_pool["G"])
        action = self.action(s, "A", heron)
        option, = [value for value in action["cost_options"] if value["id"].startswith("mutate")]
        result = s.act("pilot:A", {"action_id": action["id"], "cost_option": option["id"],
                                  "targets": [land.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(s).kind)
        result = s.act("pilot:A", {"action_id": "choose", "choice": "bottom"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertIsNotNone(s.state.cards[land.object_id].permanent_composition)
        self.cast(s, "C", control, [land.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual("C", s.state.cards[land.object_id].controller)
        self.cast(s, "B", destroy, [land.ref])
        decision = self.resolve(s)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertEqual("composition_departure_order", s.packet("pilot:A", full=True)["decision"]["ctx"]["operation"])
        result = s.act("pilot:A", {"action_id": "choose", "cards": [land.ref, heron.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("A", "Earthbend — return the land"), (s.state.stack[-1].controller, s.state.stack[-1].label))
        for oid in (land.object_id, heron.object_id):
            self.assertEqual("graveyard", s.state.cards[oid].zone)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        for oid in (land.object_id, heron.object_id):
            card = s.state.cards[oid]
            self.assertEqual(("battlefield", "A", "A", True, {}),
                             (card.zone, card.owner, card.controller, card.tapped, card.counters))
            self.assertIsNone(card.permanent_composition)
        self.assertNotIn(private.ref, json.dumps(s.packet("pilot:A", full=True)))
        self.replay(s, load=True)

    def test_actual_prevented_earthbend_counters_create_a_stale_delayed_return(self):
        s = self.session(70166002)
        lesson = self.add(s.engine, "Earthbending Lesson", zone="hand")
        land = self.add(s.engine, "Forest")
        solemnity = self.add(s.engine, "Solemnity", seat="B")
        reclaim = self.add(s.engine, "Reclaim", zone="hand")
        for source in (lesson, solemnity, reclaim):
            self.assert_closed(s, source)
        self.prepare(s, mana={"C": 3, "G": 2})
        self.checkpoint(s)
        self.cast_for(s, "A", lesson, [land.ref], reserve={"G": 1})
        self.resolve_top(s)
        self.assertEqual("graveyard", s.state.cards[land.object_id].zone)
        self.assertEqual("Earthbend — return the land", s.state.stack[-1].label)
        self.cast(s, "A", reclaim, [land.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual("library", s.state.cards[land.object_id].zone)
        self.assertEqual(land.object_id, s.state.players["A"].zones["library"][-1])
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        self.replay(s, load=True)

    def test_actual_earthbend_public_count_and_exile_return_follow_current_land(self):
        s = self.session(70166003)
        rock = self.add(s.engine, "Rockalanche", zone="hand")
        lesson = self.add(s.engine, "Earthbending Lesson", zone="hand")
        land = self.add(s.engine, "Island", ref="earthbend-count-land")
        self.add(s.engine, "Forest", ref="earthbend-first-forest")
        self.add(s.engine, "Forest", ref="earthbend-second-forest").tapped = True
        self.add(s.engine, "Forest", seat="B", ref="earthbend-other-forest")
        exile = self.add(s.engine, "Scour from Existence", seat="B", zone="hand")
        bounce = self.add(s.engine, "Boomerang", seat="B", zone="hand")
        for source in (rock, lesson, exile, bounce):
            self.assert_closed(s, source)
        self.prepare(s, mana={"C": 8, "G": 3})
        s.state.players["B"].mana_pool.update({"C": 7, "U": 2})
        self.checkpoint(s)
        self.cast_for(s, "A", rock, [land.ref], reserve={"G": 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual({"+1/+1": 2}, s.state.cards[land.object_id].counters)
        self.cast_for(s, "B", exile, [land.ref], reserve={"U": 2})
        self.resolve_top(s)
        self.assertEqual("exile", s.state.cards[land.object_id].zone)
        self.assertEqual("Earthbend — return the land", s.state.stack[-1].label)
        self.assertIsNone(self.resolve(s))
        current = s.state.cards[land.object_id]
        self.assertEqual(("battlefield", True, {}), (current.zone, current.tapped, current.counters))
        self.cast(s, "A", lesson, [land.ref])
        self.cast(s, "B", bounce, [land.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual("hand", s.state.cards[land.object_id].zone)
        self.assertEqual(1, len(s.state.delayed_triggers))
        self.replay(s, load=True)

    def test_actual_repeated_earthbend_resolves_each_legal_target_and_returns_once(self):
        for stale_first in (False, True):
            with self.subTest(stale_first=stale_first):
                s = self.session(70166010 + int(stale_first))
                technique = self.add(s.engine, "Cracked Earth Technique", zone="hand")
                land = self.add(s.engine, "Forest", ref="repeated-earthbend-land")
                first = self.add(s.engine, "Island", ref="stale-earthbend-land") if stale_first else land
                response = self.add(s.engine, "Boomerang" if stale_first else "Doom Blade", seat="B", zone="hand")
                for source in (technique, response):
                    self.assert_closed(s, source)
                self.prepare(s, mana={"C": 4, "G": 1})
                s.state.players["B"].mana_pool.update({"U": 2} if stale_first else {"C": 1, "B": 1})
                self.checkpoint(s)
                action = self.action(s, "A", technique)
                command = {
                    "action_id": action["id"], "pay": "auto",
                    "targets": [{"group": "target_0", "ref": first.ref}, {"group": "target_1", "ref": land.ref}],
                }
                cast = s.act("pilot:A", command)
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual(
                    {"target_0": [first.ref], "target_1": [land.ref]},
                    s.state.stack[-1].context["target_groups"],
                )
                if stale_first:
                    self.cast(s, "B", response, [first.ref])
                self.assertIsNone(self.resolve(s))
                self.assertEqual(43, s.state.players["A"].life)
                current = s.state.cards[land.object_id]
                self.assertEqual({"+1/+1": 3 if stale_first else 6}, current.counters)
                active = [trigger for trigger in s.state.delayed_triggers if trigger.active]
                self.assertEqual(1 if stale_first else 2, len(active))
                self.assertTrue(all(
                    trigger.condition["land_identity"] == current.logical_object_id for trigger in active
                ))
                if stale_first:
                    self.assertEqual("hand", s.state.cards[first.object_id].zone)
                else:
                    self.cast(s, "B", response, [land.ref])
                    self.resolve_top(s)
                    self.assertEqual("trigger.order", s.state.pending_decision.kind)
                    offered = s.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
                    self.assertEqual(2, len(offered))
                    ordered = s.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in offered]})
                    self.assertTrue(ordered.ok, ordered.summary)
                    departed = s.state.cards[land.object_id].zone_change_counter
                    self.assertEqual("graveyard", s.state.cards[land.object_id].zone)
                    self.assertIsNone(self.resolve(s))
                    current = s.state.cards[land.object_id]
                    self.assertEqual(("battlefield", True, departed + 1),
                                     (current.zone, current.tapped, current.zone_change_counter))
                self.replay(s, load=True)

    def test_actual_earthbend_linked_untap_keeps_the_announced_land(self):
        s = self.session(70166020)
        source = self.add(s.engine, "Avatar Kyoshi, Earthbender")
        land = self.add(s.engine, "Forest", ref="earthbend-linked-untap")
        land.tapped = True
        self.assert_closed(s, source)
        self.prepare(s)
        self.checkpoint(s)
        self.target(s, "A", [land.ref])
        self.assertIsNone(self.resolve(s))
        current = s.state.cards[land.object_id]
        self.assertEqual({"+1/+1": 8}, current.counters)
        self.assertFalse(current.tapped)
        self.assertEqual(1, sum(trigger.active for trigger in s.state.delayed_triggers))
        self.replay(s, load=True)


class LinkedLandfallPlayTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-landfall.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "land-play-trigger-cards.json", "earthbend-cards.json",
            "ability-source-counter-cards.json", "announced-destruction-controller-tokens-cards.json",
            "delayed-object-action-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public landfall exile", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _rows(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

    def _play(self, game, card):
        action, = [row for row in self._rows(game) if row["action"] == "play_land" and card.ref in row["id"]]
        result = game.act("pilot:A", {"action_id":action["id"]})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(game))

    def _source(self, seed):
        game = self.session(seed)
        source = self.add(game.engine,"Valakut Exploration")
        self.assert_closed(game,source)
        return game,source

    def _endstep(self, game, source):
        self.until(game, lambda s:s.state.step=="end_step" and any(
            row.source_object_id==source.object_id for row in s.state.stack))

    def test_dragonhawk_entry_play_and_exact_exile_damage(self):
        game = self.session(603701401)
        source = self.add(game.engine, "Dragonhawk, Fate's Tempest", zone="hand")
        companion = self.add(game.engine, "Grizzly Bears", ref="dragonhawk-companion")
        companion.counters["+1/+1"] = 2
        self.add(game.engine, "Grizzly Bears", seat="B").counters["+1/+1"] = 3
        self.add(game.engine, "Grizzly Bears", ref="dragonhawk-below-threshold")
        remains = self.add(game.engine, "Shock", zone="library", ref="dragonhawk-remains")
        land = self.add(game.engine, "Generic Bound Plains", zone="library", ref="dragonhawk-land")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "R": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        linked = [card.object_id for card in game.state.cards.values() if card.zone == "exile"]
        self.assertCountEqual(linked, [land.object_id, remains.object_id])
        self._play(game, land)
        self._endstep(game, source)
        self.assertEqual("A", game.state.active_player)
        parked = game.state.cards[remains.object_id]
        self.assertFalse(any(row["id"].startswith(f"cast:{parked.ref}:") for row in self._rows(game)))
        before = {seat: game.state.players[seat].life for seat in ("A", "B", "C", "D")}
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[remains.object_id].zone)
        self.assertEqual(before["A"],game.state.players["A"].life)
        for seat in ("B", "C", "D"):
            self.assertEqual(before[seat] - 2, game.state.players[seat].life)
        self.replay(game, load=True)

    def test_dragonhawk_reentry_keeps_each_original_damage_source_and_batch(self):
        from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT

        game = self.session(603701403)
        source = self.add(game.engine, "Dragonhawk, Fate's Tempest", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Generic Bound Plains", ref="dragonhawk-blink-payment")
        later = self.add(game.engine, "Generic Bound Plains", zone="library", ref="dragonhawk-later")
        earlier = self.add(game.engine, "Shock", zone="library", ref="dragonhawk-earlier")
        self.prepare(game, mana={"C": 3, "R": 2, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        original_source = game.state.cards[source.object_id]
        original_identity = original_source.logical_object_id
        self.cast(game, "A", blink, [original_source.ref])
        self.assertIsNone(self.resolve(game))
        self._play(game, later)
        reentered = game.state.cards[source.object_id]
        self.assertNotEqual(original_identity, reentered.logical_object_id)
        first, second = [trigger for trigger in game.state.delayed_triggers if trigger.active]
        self.assertEqual([earlier.object_id], first.referred_object_ids)
        self.assertEqual([later.object_id], second.referred_object_ids)
        self.assertEqual(original_identity, first.stack_template["context"][SOURCE_DAMAGE_CONTEXT]["logical_object_id"])
        self.assertEqual(reentered.logical_object_id, second.stack_template["context"][SOURCE_DAMAGE_CONTEXT]["logical_object_id"])
        self.until(game, lambda state: state.state.step == "end_step"
                   and state.state.pending_decision.kind == "trigger.order")
        game = self.replay(game, load=True)
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        ordered = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in offered]})
        self.assertTrue(ordered.ok, ordered.summary)
        before = game.state.players["B"].life
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before - 2, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_dragonhawk_waits_through_an_opponents_end_step(self):
        from quorune.impulse_access import current_temporary_play_permission

        game = self.session(603701404)
        source = self.add(game.engine, "Dragonhawk, Fate's Tempest", seat="B")
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        parked = self.add(game.engine, "Shock", seat="B", zone="library", ref="dragonhawk-next-own-end-step")
        self.prepare(game, actor="B", mana={"W": 1})
        self.checkpoint(game)
        self.cast(game, "B", blink, [source.ref])
        self.assertIsNone(self.resolve(game))
        game = self.replay(game, load=True)
        self.until(game, lambda state: state.state.active_player == "A" and state.state.step == "end_step")
        self.assertFalse(game.state.stack)
        self.assertIsNotNone(current_temporary_play_permission(game.state, "B", game.state.cards[parked.object_id]))
        self._endstep(game, source)
        self.assertEqual("B", game.state.active_player)
        self.assertIsNone(current_temporary_play_permission(game.state, "B", game.state.cards[parked.object_id]))
        before = game.state.players["A"].life
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before - 2, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_dragonhawk_attack_zero_still_schedules_next_own_end_step(self):
        game = self.session(603701402)
        source = self.add(game.engine, "Dragonhawk, Fate's Tempest")
        source.counters["-1/-1"] = 2
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        self.assertIsNone(self.resolve(game))
        delayed, = [trigger for trigger in game.state.delayed_triggers if trigger.active]
        self.assertEqual([], delayed.referred_object_ids)
        self._endstep(game, source)
        self.assertEqual("A", game.state.active_player)
        before = {seat: game.state.players[seat].life for seat in ("B", "C", "D")}
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, {seat: game.state.players[seat].life for seat in before})
        self.replay(game, load=True)

    def test_glimpse_exile_play_before_and_after_delayed_trigger_and_spawn_mana(self):
        game = self.session(603700901)
        source = self.add(game.engine, "Glimpse the Impossible", zone="hand")
        remains = self.add(game.engine, "Grizzly Bears", zone="library", ref="glimpse-remains")
        instant = self.add(game.engine, "Shock", zone="library", ref="glimpse-respond")
        land = self.add(game.engine, "Generic Bound Plains", zone="library", ref="glimpse-land")
        self.add(game.engine, "Mountain")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self._play(game, land)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self._endstep(game, source)
        self.cast(game, "A", instant, ["B"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[remains.object_id].zone)
        spawn, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        data = game.engine._effective_card_data(spawn)
        self.assertEqual({"eldrazi", "spawn"}, game.engine._type_parts(data["type_line"])[1])
        self.assertEqual((0, 1), tuple(game.engine._numeric_stat(spawn.object_id, stat) for stat in ("power", "toughness")))
        self.assertEqual([], list(data["colors"]))
        rows = self._rows(game)
        action = next(row for row in rows if row["id"].startswith(f"activate:{spawn.ref}:"))
        before = game.state.players["A"].mana_pool["C"]
        done = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(done.ok, done.summary)
        self.assertEqual(before + 1, game.state.players["A"].mana_pool["C"])
        self.assertFalse(any(card.object_id == spawn.object_id and card.zone == "battlefield"
                             for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_glimpse_rechecks_if_any_cards_remain_at_next_end_step(self):
        game = self.session(603700902)
        source = self.add(game.engine, "Glimpse the Impossible", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", zone="library", ref="glimpse-cast-bear")
        instant = self.add(game.engine, "Shock", zone="library", ref="glimpse-cast-shock")
        land = self.add(game.engine, "Generic Bound Plains", zone="library", ref="glimpse-play-land")
        self.prepare(game, mana={"C": 4, "R": 2, "G": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", creature)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", instant, ["B"])
        self.assertIsNone(self.resolve(game))
        self._play(game, land)
        self.until(game, lambda s: s.state.step == "end_step")
        self.assertFalse(game.state.stack)
        self.assertFalse(any(row.active for row in game.state.delayed_triggers))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_glimpse_orders_actual_graveyard_arrivals_and_counts_after_replacement(self):
        from unittest.mock import patch
        from quorune.replacement.model import ReplacementClass, ReplacementEffect
        from quorune.replacement.operations import SetField
        from quorune.semantic_runtime import zone_replacements

        game = self.session(603700903)
        source = self.add(game.engine, "Glimpse the Impossible", zone="hand")
        first, second, redirected = [self.add(game.engine, "Generic Bound Plains", zone="library", ref=f"glimpse-{i}")
                                     for i in range(3)]
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self._endstep(game, source)
        collector = zone_replacements.collect_zone_change_replacement_effects
        effect = ReplacementEffect(
            effect_id="fixture:spawn-redirect", source_id="fixture:spawn-redirect",
            event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
            conditions={"origin": {"eq": "exile"}, "destination": {"eq": "graveyard"},
                        "logical_object_id": {"eq": game.state.cards[redirected.object_id].logical_object_id}},
            operations=(SetField("destination", "hand"),),
        )

        def with_redirect(host, **kwargs):
            return (*collector(host, **kwargs), effect)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", with_redirect):
            self.assertEqual("semantic.choice", self.resolve(game).kind)
            self.assertEqual("exile", game.state.cards[redirected.object_id].zone)
            game = self.replay(game, load=True)
            ordered = game.act("pilot:A", {"action_id": "choose", "cards": [second.ref, first.ref]})
            self.assertTrue(ordered.ok, ordered.summary)
            self.assertIsNone(self.resolve(game))
            self.assertEqual([second.object_id, first.object_id], game.state.players["A"].zones["graveyard"][-2:])
            self.assertEqual("hand", game.state.cards[redirected.object_id].zone)
            self.assertEqual(2, sum(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
            self.replay(game, load=True)

    def test_glimpse_printed_durations_and_spawn_profile_fail_closed(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.impulse_spawn_settlement import CAPABILITY

        card = self.db.lookup("Glimpse the Impossible")
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertIn(CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)
        for old, new in (("this turn", "until your next turn"), ("Eldrazi Spawn", "Eldrazi Scion"),
                         ("if any of those cards remain exiled", "if you control a creature")):
            mutant = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(mutant, capability_registry=self.registry).status)

    def test_valakut_requires_the_two_exact_linked_instructions(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.linked_landfall_play import EXPLORE_EXILE_CAPABILITY, EXPLORE_SETTLE_CAPABILITY

        card=self.db.lookup("Valakut Exploration",fuzzy=False)
        ir=compile_oracle_card(card,capability_registry=self.registry)
        self.assertEqual("exact",ir.status,ir.material_residuals)
        self.assertIn(EXPLORE_EXILE_CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
        self.assertIn(EXPLORE_SETTLE_CAPABILITY,ir.faces[0].nodes[1].capability_dependencies)
        for old,new in (("for as long as it remains exiled","twice this turn"),
                        ("their owner's graveyard","their owner's library")):
            changed=replace(card,oracle_text=card.oracle_text.replace(old,new))
            self.assertNotEqual("exact",compile_oracle_card(changed,capability_registry=self.registry).status)

    def test_valakut_played_card_does_not_count_in_original_end_step_settlement(self):
        game,source=self._source(70166501)
        self.add(game.engine,"Exploration")
        first=self.add(game.engine,"Generic Bound Plains",zone="hand",ref="valakut-land-one")
        second=self.add(game.engine,"Generic Bound Plains",zone="hand",ref="valakut-land-two")
        held=self.add(game.engine,"Generic Bound Plains",zone="library",ref="valakut-held")
        instant=self.add(game.engine,"Shock",zone="library",ref="valakut-cast-before-settlement")
        self.add(game.engine,"Mountain")
        self.prepare(game);self.checkpoint(game)
        self._play(game,first)
        self.assertEqual("exile",game.state.cards[instant.object_id].zone)
        self.assertIn(instant.ref,json.dumps(game.packet("pilot:B",full=True)))
        from quorune.impulse_access import current_temporary_play_permission
        self.assertIsNone(current_temporary_play_permission(game.state,"B",game.state.cards[instant.object_id]))
        self._play(game,second)
        self.assertEqual("exile",game.state.cards[held.object_id].zone)
        self.assertFalse(any(row["action"]=="play_land" and held.ref in row["id"] for row in self._rows(game)))
        game=self.replay(game,load=True)
        self._endstep(game,source)
        self.cast(game,"A",instant,["D"])
        game=self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40,39,39,37],[game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("graveyard",game.state.cards[held.object_id].zone)
        self.replay(game,load=True)

    def test_valakut_reentered_source_does_not_recover_old_exile_link(self):
        game,source=self._source(70166502)
        land=self.add(game.engine,"Generic Bound Plains",zone="hand")
        held=self.add(game.engine,"Shock",zone="library",ref="valakut-old-link")
        bounce=self.add(game.engine,"Boomerang",seat="B",zone="hand")
        self.add(game.engine,"Mountain")
        self.prepare(game,mana={"C":2,"R":1})
        game.state.players["B"].mana_pool["U"]=2
        self.checkpoint(game)
        identity=source.logical_object_id
        self._play(game,land)
        self.cast(game,"B",bounce,[source.ref])
        self.assertIsNone(self.resolve(game))
        self.cast(game,"A",source)
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(identity,game.state.cards[source.object_id].logical_object_id)
        self.until(game,lambda s:s.state.step=="end_step")
        self.assertEqual([],game.state.stack)
        self.assertEqual("exile",game.state.cards[held.object_id].zone)
        game=self.replay(game,load=True)
        self.cast(game,"A",held,["D"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40,40,40,38],[game.state.players[seat].life for seat in "ABCD"])
        self.replay(game,load=True)

    def test_valakut_exile_redirect_does_not_count_as_a_graveyard_arrival(self):
        game,source=self._source(70166504)
        self.add(game.engine,"Rest in Peace",seat="D")
        self.add(game.engine,"Mountain")
        land=self.add(game.engine,"Generic Bound Plains",zone="hand")
        held=self.add(game.engine,"Shock",zone="library",ref="valakut-diverted-settlement")
        self.prepare(game);self.checkpoint(game)
        self._play(game,land)
        prior=game.state.cards[held.object_id].logical_object_id
        self._endstep(game,source)
        game=self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        result=game.state.cards[held.object_id]
        self.assertEqual("exile",result.zone)
        self.assertNotEqual(prior,result.logical_object_id)
        self.assertFalse(any(row["id"]=="cast:"+held.ref for row in self._rows(game)))
        self.assertEqual([40,40,40],[game.state.players[seat].life for seat in "BCD"])
        self.replay(game,load=True)

    def test_valakut_exiled_land_uses_an_available_land_play(self):
        game,source=self._source(70166505)
        self.add(game.engine,"Exploration")
        land=self.add(game.engine,"Generic Bound Plains",zone="hand",ref="valakut-played-from-hand")
        held=self.add(game.engine,"Generic Bound Plains",zone="library",ref="valakut-played-from-exile")
        self.prepare(game);self.checkpoint(game)
        self._play(game,land)
        self.assertEqual("exile",game.state.cards[held.object_id].zone)
        game=self.replay(game,load=True)
        self._play(game,held)
        self.assertEqual(("battlefield","A"),(game.state.cards[held.object_id].zone,game.state.cards[held.object_id].controller))
        self.replay(game,load=True)

    def test_valakut_orders_actual_graveyard_arrivals_before_damage(self):
        game,source=self._source(70166503)
        self.add(game.engine,"Exploration")
        lands=[self.add(game.engine,"Generic Bound Plains",zone="hand",ref=f"valakut-play-{i}") for i in range(2)]
        cards=[self.add(game.engine,"Generic Bound Plains",zone="library",ref=f"valakut-order-{i}") for i in range(2)]
        self.prepare(game);self.checkpoint(game)
        for land in lands:self._play(game,land)
        self._endstep(game,source)
        self.assertEqual("semantic.choice",self.resolve(game).kind)
        self.assertTrue(all(game.state.cards[card.object_id].zone=="exile" for card in cards))
        game=self.replay(game,load=True)
        result=game.act("pilot:A",{"action_id":"choose","cards":[card.ref for card in cards]})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([card.object_id for card in cards],game.state.players["A"].zones["graveyard"][-2:])
        self.assertEqual([38,38,38],[game.state.players[seat].life for seat in "BCD"])
        self.replay(game,load=True)
