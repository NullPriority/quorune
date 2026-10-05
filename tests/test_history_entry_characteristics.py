"""Unchanged-main trusted action reproduction; event capture is observation only."""
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import ORACLE_COMPILER_VERSION
from scripts.build_test_database import build_fixture_database

class HistoryEntryCharacteristicReproduction(unittest.TestCase):
    fixture_path = ROOT / "tests/fixtures/history-entry-characteristic-cards.json"
    from test_token_entry_boundaries import TokenEntryActionReproductions as _helpers
    session = _helpers.session
    add = _helpers.add
    register = _helpers.register
    seal = _helpers.seal
    cast = _helpers.cast
    finish = _helpers.finish
    finish_creation = _helpers.finish_creation
    order_triggers = _helpers.order_triggers
    replay = _helpers.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "history-entry.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/linked-exile-return-cards.json",
            ROOT / "tests/fixtures/token-entry-boundary-cards.json",
            cls.fixture_path,
        ], path)
        cls.db = CardDatabase(path)
        cls.deck = DeckDefinition("Generic history entry", [
            DeckEntry("Generic Blink Commander", 1, "commander"),
            DeckEntry("Generic Blink Plains", 15),
        ], ["Generic Blink Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _check_post_event_entry_characteristics(self, *, ordinary_zone_entry=False, owner_only=False):
        session = self.session(48020001)
        source_name = "Generic History Entry Characteristic Creature"
        observer_name = "Generic History Entry Power Observer"
        spell_name = ("Generic History Group Reanimation" if ordinary_zone_entry
                      else "Generic Entry Two Copies")
        self.register(session, (source_name, observer_name) if owner_only
                      else (source_name, observer_name, spell_name))
        source = self.add(session, source_name, "SOURCE", zone="battlefield")
        observer = self.add(session, observer_name, "OBSERVER", zone="battlefield")
        spell = None if owner_only else self.add(session, spell_name, "ENTRY")
        moved = [self.add(session, source_name, f"GRAVE{index}", zone="graveyard")
                 for index in range(2)] if ordinary_zone_entry else []
        self.assertFalse(session.state.turn_history.events)
        self.assertEqual("2", str(session.engine._effective_card_data(source)["power"]))
        self.seal(session)
        baseline_draws = sum(event.code == "card.draw" for event in session.state.events)
        occurrences = []
        discovered_observer_ids = set()
        dispatch = session.engine._dispatch_semantic_event
        def capture(kind, context, **kwargs):
            if kind == "creature.enter":
                occurrences.append(deepcopy(context))
            result = dispatch(kind, context, **kwargs)
            for item in kwargs.get("trigger_batch") or ():
                if item.source_object_id == observer.object_id:
                    discovered_observer_ids.add(item.stack_id)
            return result
        with patch.object(session.engine, "_dispatch_semantic_event", side_effect=capture):
            if owner_only:
                from quorune.zone_transitions import ZoneTransitionOwner
                ZoneTransitionOwner(session.engine).move_cards_simultaneously(
                    [(card.object_id, "battlefield") for card in moved],
                    reason="isolated ordinary simultaneous history/characteristic owner diagnostic",
                )
                session.engine.pump()
                self.order_triggers(session)
            else:
                self.cast(session, spell, [] if ordinary_zone_entry else [source.ref])
                self.finish_creation(session)
        tokens = ([session.state.cards[card.object_id] for card in moved]
                  if ordinary_zone_entry else [card for card in session.state.cards.values() if card.is_token])
        triggers = [item for item in session.state.stack
                    if item.kind == "triggered_ability" and item.source_object_id == observer.object_id]
        current = {card.logical_object_id: session.engine._effective_card_data(card)["power"]
                   for card in tokens}
        rows = [row for row in session.state.turn_history.events if row.kind == "permanent_entered"]
        self.assertEqual(2, len(tokens))
        self.assertEqual(2, len(occurrences))
        self.assertEqual(2, len(rows))
        self.assertEqual({card.logical_object_id for card in tokens},
                         {row.object_incarnation for row in rows})
        self.finish(session)
        draws = sum(event.code == "card.draw" for event in session.state.events) - baseline_draws
        if not owner_only:
            self.replay(session)
        print(json.dumps({"head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
              "compiler":ORACLE_COMPILER_VERSION,"producer":"ordinary_zone" if ordinary_zone_entry else "token",
              "entry_power":[row["power"] for row in occurrences],
              "current_power":current,"pending_observer_triggers":len(triggers),
              "discovered_observer_ids":sorted(discovered_observer_ids),"draws":draws,
              "history_rows":len(rows),"matching_version_replay":"not an action-path witness" if owner_only else "passed"}, sort_keys=True))
        self.assertEqual([3, 3], [int(row["power"]) for row in occurrences])
        self.assertEqual(2, len(discovered_observer_ids))
        if not owner_only:
            self.assertEqual(2, len(triggers))
        self.assertEqual(2, draws)

    def test_two_simultaneous_copies_use_post_event_history_characteristics(self):
        self._check_post_event_entry_characteristics()

    def test_ordinary_simultaneous_zone_owner_history_characteristic_diagnostic(self):
        self._check_post_event_entry_characteristics(ordinary_zone_entry=True, owner_only=True)

    def _run_token_control(self, *, prior, count, sequential, independent, expected, additional_food=False):
        session = self.session(48020100 + prior * 10 + count + int(sequential) * 100 + int(independent) * 1000)
        source_name = ("Generic Entry Vigilance Creature" if independent
                       else "Generic History Entry Characteristic Creature")
        observer_name = "Generic History Entry Power Observer"
        spell_name = "Generic Entry One Copy" if sequential or count == 1 or additional_food else "Generic Entry Two Copies"
        warm_spell_name = "Generic Entry One Copy" if prior == 1 else "Generic Entry Two Copies"
        names = {source_name, observer_name, spell_name}
        if prior:
            names.update(("Generic Entry Vigilance Creature", warm_spell_name))
        if independent:
            names.add("Generic History Independent Anthem")
        if additional_food:
            names.add("Generic History Additional Food")
        self.register(session, tuple(sorted(names)))
        source = self.add(session, source_name, "SOURCE", zone="battlefield")
        observer = self.add(session, observer_name, "OBSERVER", zone="battlefield")
        if independent:
            self.add(session, "Generic History Independent Anthem", "ANTHEM", zone="battlefield")
        if additional_food:
            self.add(session, "Generic History Additional Food", "ADDITIONAL", zone="battlefield")
        spells = [self.add(session, spell_name, f"COPY{index}")
                  for index in range(count if sequential else 1)]
        if prior:
            warm_source = self.add(session, "Generic Entry Vigilance Creature", "WARM_SOURCE", zone="battlefield")
            warm_spell = self.add(session, warm_spell_name, "WARM_SPELL")
        self.assertFalse(session.state.turn_history.events)
        self.seal(session)
        if prior:
            self.cast(session, warm_spell, [warm_source.ref])
            self.finish_creation(session)
            self.finish(session)
        before_rows = [row for row in session.state.turn_history.events if row.kind == "permanent_entered"]
        self.assertEqual(prior, len(before_rows))
        baseline_draws = sum(event.code == "card.draw" for event in session.state.events)
        baseline_token_ids = {card.object_id for card in session.state.cards.values() if card.is_token}
        occurrences = []
        discovered = set()
        dispatch = session.engine._dispatch_semantic_event
        def capture(kind, context, **kwargs):
            if kind == "creature.enter":
                occurrences.append(deepcopy(context))
            result = dispatch(kind, context, **kwargs)
            for item in kwargs.get("trigger_batch") or ():
                if item.source_object_id == observer.object_id:
                    discovered.add(item.stack_id)
            return result
        with patch.object(session.engine, "_dispatch_semantic_event", side_effect=capture):
            for spell in spells:
                self.cast(session, spell, [source.ref])
                self.finish_creation(session)
                self.finish(session)
        copies = [card for card in session.state.cards.values()
                  if card.is_token and card.object_id not in baseline_token_ids]
        rows = [row for row in session.state.turn_history.events if row.kind == "permanent_entered"]
        self.assertEqual(count, len(copies))
        if additional_food:
            self.assertEqual(1, sum(card.annotations.get("copied_from") == source.object_id for card in copies))
            self.assertEqual(1, sum("food" in session.engine._type_parts(
                str(session.engine._effective_card_data(card)["type_line"]))[1] for card in copies))
        self.assertEqual(prior + count, len(rows))
        self.assertEqual({card.logical_object_id for card in copies},
                         {row.object_incarnation for row in rows[len(before_rows):]})
        powers = [int(row["power"]) for row in occurrences]
        draws = sum(event.code == "card.draw" for event in session.state.events) - baseline_draws
        self.replay(session)
        print(json.dumps({"control":{"prior":prior,"count":count,"sequential_actions":sequential,
              "independent_static":independent,"additional_food":additional_food},"entry_power":powers,"expected":expected,
              "observer_ids":len(discovered),"draws":draws,"history":len(rows),"replay":"passed"},sort_keys=True))
        self.assertEqual(expected, powers)
        expected_triggers = sum(power >= 3 for power in expected)
        self.assertEqual(expected_triggers, len(discovered))
        self.assertEqual(expected_triggers, draws)

    def test_history_characteristic_distinguishing_controls(self):
        cases = (
            (0, 1, False, False, [2]),
            (1, 1, False, False, [3]),
            (2, 2, False, False, [3, 3]),
            (0, 2, True, False, [2, 3]),
            (0, 2, False, True, [3, 3]),
        )
        for prior, count, sequential, independent, expected in cases:
            with self.subTest(prior=prior, count=count, sequential_actions=sequential,
                              independent_static=independent):
                self._run_token_control(prior=prior,count=count,sequential=sequential,
                                        independent=independent,expected=expected)

    def test_replacement_adjusted_history_characteristic_quantity(self):
        self._run_token_control(prior=0,count=2,sequential=False,independent=False,
                                expected=[3],additional_food=True)

class HistoryEntrySealingOwnerTests(unittest.TestCase):
    """Isolated query-order and mutation witness, not extra grammar admission."""

    def sealing_contract(self, dispatch_creation):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.zone_trigger_events import type_parts
        cards = {
            name: CardInstance(object_id=name, ref=name.upper(),
                oracle_id="generic:" + name, printed_name="Generic token",
                owner="A", controller="A", zone="battlefield", is_token=True)
            for name in ("first", "second")
        }
        rows, occurrences = [], []
        def current_data(card):
            complete = len(rows) == 2
            return {"type_line":"Creature — Wizard", "power":"3" if complete else "2",
                    "toughness":"3" if complete else "2", "mana_value":0,
                    "keywords":["Vigilance", "Flying"] if complete else ["Vigilance"]}
        host = SimpleNamespace(
            state=SimpleNamespace(cards=cards,turn_sequence=1,
                players={"A":SimpleNamespace(stats={})}),
            _effective_card_data=current_data, _type_parts=type_parts,
            _copyable_characteristics=lambda card: {"type_line":"Creature — Wizard",
                "power":"2", "toughness":"2", "keywords":["Vigilance"]},
            _log=lambda *args, **kwargs: None,
            _record_turn_history=lambda kind, **kwargs: rows.append((kind,kwargs)),
        )
        def dispatch(kind, context, **kwargs):
            self.assertEqual(2,len(rows))
            if kind == "creature.enter":
                occurrences.append(deepcopy(context))
        host._dispatch_semantic_event = dispatch
        dispatch_creation(host,"A",("first","second"),name="Generic",base_quantity=2,
                          replacement_components=(),replacement_journal=(),reason="owner model")
        self.assertEqual([3,3],[row["power"] for row in occurrences])
        self.assertTrue(all("flying" in row["keywords"] for row in occurrences))
        self.assertEqual(2,len(rows))

    def test_history_precedes_final_power_and_keyword_sealing(self):
        from quorune.token_creation import _record_and_dispatch_token_creation
        self.sealing_contract(_record_and_dispatch_token_creation)

    def test_pre_history_characteristic_sealing_mutant_is_killed(self):
        import inspect
        from quorune import token_creation as owner
        source = inspect.getsource(owner._record_and_dispatch_token_creation)
        history = '''    for identity, types in entry_occurrences:
        host._record_turn_history(
            "permanent_entered",
            actor=controller,
            object_incarnation=identity,
            types=types,
        )
'''
        anchor = "    # One committed creation instruction is simultaneous."
        self.assertEqual(1,source.count(history))
        self.assertEqual(1,source.count(anchor))
        mutant = source.replace(history, "").replace(anchor, history + anchor)
        namespace = dict(vars(owner))
        exec(compile(mutant,"<stale-token-entry-characteristics>","exec"),namespace)
        with self.assertRaises(AssertionError):
            self.sealing_contract(namespace["_record_and_dispatch_token_creation"])

if __name__ == "__main__":
    unittest.main()
