import unittest
from engine.parser import Parser
from engine.runtime import Runtime

class EngineTests(unittest.TestCase):
    def test_parse_core(self):
        src='''var BOT_TOKEN = "abc";
const timer = {ontime = 10s}
cmdlist {"start":"inicia"};
lang = {es,en};
cmd start = { txt = {"Hola"},{hello}; }
'''
        p=Parser(src,'test.tgl').parse()
        self.assertEqual(p.variables['BOT_TOKEN'],'abc')
        self.assertEqual(p.constants['timer']['ontime'],'10s')
        self.assertEqual(p.cmdlist['start'],'inicia')
        self.assertEqual(p.commands['start']['txt'][0]['id'],'hello')
    def test_events_and_rules(self):
        src='const timeout = {ontime = 10s}\nif timeout = 0 return {editmessage.timer{txt = {"finalizado"}}}\non @user join {sendmessage {txt = {"hola"}}}'
        p=Parser(src,'test.tgl').parse()
        self.assertEqual(len(p.rules),1)
        self.assertEqual(p.events[0]['event'],'join')
    def test_comment(self):
        p=Parser('//comentario\\\\\ncmd ping = {txt = {"pong"};}', 'x.tgl').parse()
        self.assertIn('ping',p.commands)
    def test_math_and_negative_guard(self):
        p=Parser('const money = {value = 10}\nconst points = {value = 5; negativeOn}\ncmd ping = {txt = {"ok"}}','test.tgl').parse()
        r=Runtime(p)
        r.eval_math('money.value - 11',{})
        self.assertEqual(p.constants['money']['value'],10)
        r.eval_math('money.value - 4',{})
        self.assertEqual(p.constants['money']['value'],6)
        r.eval_math('points.value - 8',{})
        self.assertEqual(p.constants['points']['value'],-3)


    def test_relative_imports(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as td:
            root=Path(td)
            (root/'vars.txt').write_text('BOT_TOKEN=abc\nBOT_NAME=Reidi',encoding='utf-8')
            (root/'db.js').write_text('const database = [{"name":"Oris"}];',encoding='utf-8')
            source='var {import vars.txt}\nimport ./db.js\ncmd ping = {txt = {"ok"}}'
            program=Parser(source,str(root/'main.tgl')).parse()
            self.assertEqual(program.variables['BOT_TOKEN'],'abc')
            self.assertIn('database', Runtime(program).data)

    def test_reset_alias_and_user_state_storage(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as td:
            program=Parser('cmd ping = {txt = {"ok"}}',str(Path(td)/'main.tgl')).parse()
            runtime=Runtime(program)
            runtime.users[42]={'coins':7}
            runtime.save_user(42)
            runtime.db.close()
            again=Runtime(program)
            self.assertEqual(again.users[42]['coins'],7)
            again.db.close()

if __name__=='__main__':unittest.main()
