"""Normal generated Tribute registration and actual offered entry choices."""
import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class TributeNativeFixture(InheritedNativeFixture):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[r for r in basic['cards'] if r['name']=='Generic Bound Plains'];basic['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(basic),encoding='utf-8')
        path=folder/'tribute.sqlite3'
        build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/tribute-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Tribute',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,before=None):
        s=self.session(2021016+len(name));source=self.add(s.engine,name,zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='tribute-private-C')
        if before is not None:before(s,source)
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,private

    def option(self,s,seat,*,ends=None,decline=False):
        rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['options']
        return next(r for r in rows if (r['id'].startswith('decline:')==decline)
            and (ends is None or r['id'].endswith(ends)))

    def choose(self,s,seat,row):
        command={'action_id':'choose','replacement':row['id']}
        self.reject(s,'D' if seat!='D' else 'C',command)
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def begin_entry(self,s,source,opponent='B'):
        self.cast(s,'A',source)
        self.assertEqual('replacement.order',self.resolve(s).kind)
        self.assertEqual(['A'],s.state.pending_decision.actors)
        self.assertEqual('stack',s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.choose(s,'A',self.option(s,'A',ends='opponent:'+opponent))
        self.assertEqual(['B'] if opponent=='B' else [opponent],s.state.pending_decision.actors)
        self.assertEqual('stack',s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].counters)
