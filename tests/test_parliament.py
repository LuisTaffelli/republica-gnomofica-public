import json
import io
from unittest.mock import patch
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from cli import parliament_cli as p
ROOT = Path(__file__).resolve().parents[1]

class ParliamentTests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable,'-B','-m','cli.parliament_cli',*args], cwd=ROOT, capture_output=True, timeout=10)

    def test_demo_determinism_disagreement_and_budgets(self):
        a, b = self.cli('--mode','demo'), self.cli('--mode','demo')
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, b.stdout)
        r = json.loads(a.stdout)['result']
        self.assertTrue(r['label'].startswith('SIMULADO'))
        self.assertEqual(len(r['intervenciones']), 6)
        self.assertEqual(r['limites']['llamadas'], 6)
        self.assertEqual(r['conclusion']['posturas']['construccion'], 'abstencion')
        self.assertEqual(len(r['conclusion']['reservas']), 3)
        self.assertFalse(r['conclusion']['ejecutado'])
        self.assertLessEqual(len(a.stdout), 16384)

    def test_explicit_json_preserves_pipe_bytes(self):
        default = self.cli('--mode', 'demo')
        explicit = self.cli('--mode', 'demo', '--format', 'json')
        self.assertEqual(default.stdout, explicit.stdout)
        self.assertEqual(explicit.returncode, 0)
        self.assertEqual(json.loads(explicit.stdout)['status'], 'completed')

    def test_human_utf8_multiline(self):
        run = self.cli('--mode', 'demo', '--format', 'human')
        self.assertEqual(run.returncode, 0, run.stderr)
        text = run.stdout.decode('utf-8')
        for phrase in ('SIMULADO', 'Tema', 'Participantes', 'RONDA 1', 'RONDA 2',
                       'Objeción:', 'Incertidumbre:', 'Evidencia disponible', 'CONCLUSIÓN', 'abstención'):
            self.assertIn(phrase, text)
        self.assertNotIn(r'\u00', text)
        self.assertGreater(len(text.splitlines()), 30)

    def test_terminal_default_human_json_override(self):
        receipt = self.cli('--mode', 'demo').stdout
        class Terminal(io.BytesIO):
            def isatty(self): return True
        for format_, human in (('auto', True), ('json', False)):
            output = Terminal()
            with patch.object(sys, 'stdout', output):
                self.assertEqual(p.output_receipt(receipt, 0, format_, 'demo'), 0)
            if human:
                self.assertTrue(output.getvalue().startswith(b'MINI PARLAMENTO'))
            else:
                self.assertEqual(output.getvalue(), receipt)

    def test_wrapping_and_untrusted_controls(self):
        receipt = json.loads(self.cli('--mode', 'demo').stdout)
        payload = 'a\x1b[31mb\x9bc\x7fd\r\nFORGED\t\u202ee\u2028f'
        receipt['result']['tema'] = payload
        receipt['result']['intervenciones'][0]['argumento'] = payload
        receipt['result']['evidencia'][0]['source'] = payload
        text = p.human_receipt(receipt, 40)
        self.assertTrue(all(len(line) <= 40 for line in text.splitlines()))
        self.assertTrue(all(c == '\n' or c.isprintable() for c in text))
        self.assertNotIn('\nFORGED', text)

    def test_human_error_keeps_exit_status(self):
        encoded = p.bridge._encode(p.bridge._error('deadline_exceeded', 'Se agotó el plazo.'))
        output = io.BytesIO()
        with patch.object(sys, 'stdout', output):
            self.assertEqual(p.output_receipt(encoded, 4, 'human', 'demo'), 4)
        self.assertIn('Ensayo incompleto', output.getvalue().decode())
        self.assertIn('SIMULADO', output.getvalue().decode())

    def test_public_rule_and_pending_human_decision(self):
        r = json.loads(self.cli('--mode','demo').stdout)['result']
        self.assertEqual(r['evidencia'][1]['source'], 'docs/CONSTITUCION.md — Artículo I, Soberanía')
        objection = r['intervenciones'][2]
        self.assertIn('artículo I', objection['objecion'])
        self.assertIn('E2', objection['evidencia'])
        self.assertIn('Decisión pendiente del humano', r['conclusion']['resumen'])

    def test_one_round_topic(self):
        r = json.loads(self.cli('--mode','demo','--rounds','1','--topic','Ensayo').stdout)['result']
        self.assertEqual(r['tema'], 'Ensayo')
        self.assertEqual(len(r['intervenciones']), 3)

    def test_cancellation_before_any_call(self):
        r = p.deliberate(None,p.TOPIC,2,1,simulated=True,cancelled=lambda:True)
        self.assertEqual(r['estado'],'cancelled')
        self.assertEqual(r['limites']['llamadas'],0)

    def test_error_no_retry_or_detail_leak(self):
        class Bad:
            def ask(self, prompt): raise RuntimeError('PRIVATE_DETAIL')
        r = p.deliberate(Bad(),p.TOPIC,2,1,simulated=False)
        self.assertEqual(r['limites']['llamadas'],1)
        self.assertEqual(r['estado'],'invalid_or_failed_response')
        self.assertNotIn('PRIVATE_DETAIL',json.dumps(r))
        self.assertFalse(r['intervenciones'])

    def test_schema_and_length(self):
        valid = dict(argumento='a',objecion='b',incertidumbre='c',postura='abstencion',evidencia=['E1'])
        for changes in (dict(argumento='x'*161),dict(evidencia=['unknown']),dict(postura='execute'),dict(objecion='\x1b'),dict(incertidumbre=''),dict(tool='shell')):
            with self.subTest(changes=changes), self.assertRaises(ValueError): p.validate(json.dumps({**valid,**changes}))
        for raw in ('x'*1025,'{}','{"a":1,"a":2}','NaN'):
            with self.assertRaises(ValueError): p.validate(raw)

    def test_invalid_cli(self):
        for args in ([],['--mode','local'],['--mode','demo','--model','foo'],['--mode','demo','--timeout','nan'],['--mode','demo','--rounds','3'],['--mode','demo','--topic','x'*241]):
            self.assertEqual(self.cli(*args).returncode,2)

    def test_prior_public_objections_in_second_round(self):
        class Capture(p.DemoProvider):
            def __init__(self): self.prompts=[]
            def ask(self,prompt):
                self.prompts.append(prompt)
                return super().ask(prompt)
        provider=Capture()
        p.deliberate(provider,p.TOPIC,2,1,simulated=True)
        for index, prompt in enumerate(provider.prompts):
            instructions, raw = prompt.split('\nDATOS=', 1)
            data = json.loads(raw)
            self.assertEqual(len(data['intervenciones']), index)
            self.assertIn('Tu tarea como ' + data['participante'], instructions)
            self.assertIn(p.ROLE_TASKS[data['participante']], instructions)
            if data['ronda'] == 2:
                others = [t for t in data['intervenciones']
                          if t['participante'] != data['participante']]
                self.assertGreaterEqual(len(others), 2)
                self.assertTrue(all(t['objecion'] for t in others))
                self.assertIn('dentro de argumento', instructions)
                self.assertIn('otro participante', instructions)
                self.assertIn('respondela', instructions)

    def test_all_abstentions_remain_valid_after_role_instructions(self):
        class Abstain(p.DemoProvider):
            def ask(self, prompt):
                value = json.loads(super().ask(prompt))
                value['postura'] = 'abstencion'
                return json.dumps(value)
        result = p.deliberate(Abstain(), p.TOPIC, 2, 1, simulated=True)
        self.assertEqual(result['estado'], 'completed')
        self.assertEqual(len(result['intervenciones']), 6)
        self.assertEqual(set(result['conclusion']['posturas'].values()), {'abstencion'})
        self.assertIn('sin consenso demostrado', result['conclusion']['resumen'])
        self.assertFalse(result['conclusion']['ejecutado'])

    def test_demo_isolation(self):
        guard='''
import sys, os
sys.path.insert(0,sys.argv[1])
def guard(event,args):
    if event.startswith('socket.') or event=='sqlite3.connect': raise RuntimeError('forbidden IO')
    if event=='open':
        path,mode,flags=args
        if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC): raise RuntimeError('forbidden write')
        if isinstance(path,str) and any(s in path for s in ('/profiles/','/data/','.env','config.toml')): raise RuntimeError('forbidden read')
sys.addaudithook(guard)
from cli.parliament_cli import worker
raise SystemExit(worker(sys.argv[2]))
'''
        launch=json.dumps(dict(mode='demo',model=None,topic=p.TOPIC,rounds=2,timeout=2))
        r=subprocess.run([sys.executable,'-I','-B','-c',guard,str(ROOT),launch],capture_output=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)

    def supervisor(self,timeout):
        script='''
import sys
from cli import delegated_read_cli as b
worker = "import sys,time; sys.path.insert(0,sys.argv.pop(1)); from providers.parliament_ollama import ParliamentOllamaProvider; ParliamentOllamaProvider.ask=lambda self,prompt: time.sleep(60); from cli.parliament_cli import worker; raise SystemExit(worker(sys.argv[1]))"
import json
launch=json.dumps(dict(mode='local',model='fake-never-contacted',topic='Ensayo',rounds=2,timeout=120))
encoded,code=b._supervise(launch,float(sys.argv[1]),worker_code=worker,allow_partial=False)
b._write(encoded)
raise SystemExit(code)
'''
        return subprocess.Popen([sys.executable,'-B','-c',script,str(timeout)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

    def test_stalled_worker_deadline(self):
        child=self.supervisor(.2)
        out,err=child.communicate(timeout=5)
        self.assertEqual(child.returncode,4,err)
        self.assertEqual(json.loads(out)['error']['code'],'deadline_exceeded')

    @unittest.skipUnless(os.name=='posix','POSIX signals')
    def test_ctrl_c(self):
        child=self.supervisor(10)
        try:
            time.sleep(.3)
            child.send_signal(signal.SIGINT)
            out,err=child.communicate(timeout=5)
            self.assertEqual(child.returncode,130,err)
            self.assertEqual(json.loads(out)['error']['code'],'interrupted')
        finally:
            if child.poll() is None: child.kill(); child.wait()


class DiagnosticTests(unittest.TestCase):
    PRIVATE = 'PRIVATE_SENTINEL_DO_NOT_EMIT'

    def failure_case(self, second, category, stage):
        original = p.DemoProvider()
        class Provider:
            calls = 0
            def ask(self, prompt):
                self.calls += 1
                if self.calls == 1:
                    return original.ask(prompt)
                return second(prompt)
        provider = Provider()
        output = io.BytesIO()
        launch = json.dumps(dict(mode='demo', model=None, topic=p.TOPIC, rounds=2, timeout=2))
        with patch.object(p, 'DemoProvider', return_value=provider), patch.object(sys, 'stdout', output):
            code = p.worker(launch)
        receipt = p.bridge._validate_receipt(output.getvalue(), code, allow_partial=False)
        result = receipt['result']
        self.assertEqual(code, 3)
        self.assertEqual(receipt['error']['code'], 'invalid_or_failed_response')
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(result['intervenciones']), 1)
        self.assertEqual(result['intervenciones'][0]['participante'], 'archivo')
        self.assertEqual(result['limites']['llamadas'], 2)
        self.assertFalse(result['conclusion']['ejecutado'])
        self.assertEqual({k: result['diagnostico'][k] for k in ('etapa', 'rol', 'ronda', 'categoria')}, dict(
            etapa=stage, rol='exploracion', ronda=1, categoria=category))
        self.assertLessEqual(set(result['diagnostico']), {'etapa', 'rol', 'ronda', 'categoria', 'campo', 'regla'})
        human = p.human_receipt(receipt)
        self.assertIn(category, human)
        for rendered in (output.getvalue().decode(), human):
            self.assertNotIn(self.PRIVATE, rendered)
            self.assertNotIn('Traceback', rendered)
            self.assertNotIn('DATOS=', rendered)

    def test_provider_categories_and_exception_text_never_escape(self):
        class Toxic(RuntimeError):
            def __str__(self):
                raise AssertionError('Exception stringification is forbidden')
        cases = [
            (p.LocalProviderError(self.PRIVATE), 'provider_transport'),
            (p.LocalResponseInvalid(self.PRIVATE), 'provider_response_invalid'),
            (p.LocalResponseLimit(self.PRIVATE), 'provider_limit'),
            (p.LocalGenerationLimit(self.PRIVATE), 'provider_limit'),
            (Toxic(self.PRIVATE), 'unexpected_failure'),
        ]
        for error, category in cases:
            def fail(_prompt, error=error):
                raise error
            with self.subTest(category=category, type=type(error).__name__):
                self.failure_case(fail, category, 'provider')

    def test_invalid_turn_categories_preserve_rejection_and_public_prefix(self):
        valid = dict(argumento='a', objecion='b', incertidumbre='c',
                     postura='abstencion', evidencia=['E1'])
        cases = [
            (self.PRIVATE + 'not json', 'turn_json_invalid'),
            ('{"x":1,"x":2}', 'turn_json_invalid'),
            ('NaN', 'turn_json_invalid'),
            (json.dumps({**valid, self.PRIVATE: 'extra'}), 'turn_fields_invalid'),
            (json.dumps({**valid, 'argumento': self.PRIVATE + 'x' * 161}), 'turn_text_invalid'),
            (json.dumps({**valid, 'objecion': self.PRIVATE + '\x1b'}), 'turn_text_invalid'),
            (json.dumps({**valid, 'incertidumbre': ''}), 'turn_text_invalid'),
            (json.dumps({**valid, 'postura': self.PRIVATE}), 'turn_position_invalid'),
            (json.dumps({**valid, 'evidencia': [self.PRIVATE]}), 'turn_evidence_invalid'),
            (json.dumps({**valid, 'evidencia': ['E1', 'E1']}), 'turn_evidence_invalid'),
            (self.PRIVATE * 50, 'turn_text_invalid'),
        ]
        for raw, category in cases:
            with self.subTest(category=category, length=len(raw)):
                self.failure_case(lambda _prompt, raw=raw: raw, category, 'validator')

    def test_unexpected_validator_and_unapproved_category_are_sanitized(self):
        original = p.validate
        for error in (RuntimeError(self.PRIVATE), p.TurnValidationError(self.PRIVATE)):
            calls = []
            def validate(raw):
                calls.append(True)
                if len(calls) == 1:
                    return original(raw)
                raise error
            with self.subTest(type=type(error).__name__), patch.object(p, 'validate', validate):
                self.failure_case(lambda _prompt: self.PRIVATE, 'unexpected_failure', 'validator')

    def test_http_200_invalid_body_has_provider_category_without_network(self):
        from providers.delegated_ollama import DelegatedOllamaProvider
        with patch('providers.delegated_ollama.http.client.HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = self.PRIVATE.encode()
            self.failure_case(DelegatedOllamaProvider('fake', 1).ask,
                              'provider_response_invalid', 'provider')
            self.assertEqual(connection.return_value.request.call_count, 1)
            connection.return_value.close.assert_called_once()

    def test_text_diagnostics_use_only_fixed_field_and_rule_names(self):
        valid = dict(argumento='a', objecion='b', incertidumbre='c',
                     postura='abstencion', evidencia=['E1'])
        for field in ('argumento', 'objecion', 'incertidumbre'):
            for value, rule in [(None, 'type'), ('   ', 'empty'), ('x' * 161, 'length'),
                                ('a\nb', 'control'), ('a\ud800', 'unicode_surrogate')]:
                with self.subTest(field=field, rule=rule):
                    with self.assertRaises(p.TurnValidationError) as caught:
                        p.validate(json.dumps({**valid, field: value}))
                    diagnostic = p.failure_diagnostic('validator', 'exploracion', 1, caught.exception)
                    self.assertEqual(diagnostic['campo'], field)
                    self.assertEqual(diagnostic['regla'], rule)
        private = p.TurnValidationError('turn_text_invalid', self.PRIVATE, self.PRIVATE)
        diagnostic = p.failure_diagnostic('validator', 'exploracion', 1, private)
        self.assertNotIn(self.PRIVATE, json.dumps(diagnostic))
        self.assertNotIn('campo', diagnostic)
        self.assertNotIn('regla', diagnostic)

    def test_generation_limit_has_fixed_rule(self):
        diagnostic = p.failure_diagnostic('provider', 'archivo', 1, p.LocalGenerationLimit(self.PRIVATE))
        self.assertEqual(diagnostic, dict(etapa='provider', rol='archivo', ronda=1,
                                         categoria='provider_limit', campo='response',
                                         regla='generation_tokens'))

    def test_fieldset_rules_do_not_disclose_received_keys(self):
        valid = dict(argumento='a', objecion='b', incertidumbre='c', postura='abstencion', evidencia=[])
        missing = {k: v for k, v in valid.items() if k != 'argumento'}
        for value, rule in [(None, 'type'), (missing, 'missing_fields'),
                            ({**valid, self.PRIVATE: 'x'}, 'extra_fields'),
                            ({**missing, self.PRIVATE: 'x'}, 'fields_mismatch')]:
            with self.subTest(rule=rule):
                with self.assertRaises(p.TurnValidationError) as caught:
                    p.validate(json.dumps(value))
                diagnostic = p.failure_diagnostic('validator', 'archivo', 1, caught.exception)
                self.assertEqual(diagnostic['campo'], 'turn')
                self.assertEqual(diagnostic['regla'], rule)
                self.assertNotIn(self.PRIVATE, json.dumps(diagnostic))

    def test_valid_boundaries_and_success_have_no_diagnostic(self):
        value = dict(argumento='a' * 160, objecion='b' * 160,
                     incertidumbre='c' * 160, postura='abstencion', evidencia=[])
        self.assertEqual(p.validate(json.dumps(value)), value)
        result = p.deliberate(p.DemoProvider(), p.TOPIC, 2, 1, simulated=True)
        self.assertEqual(result['estado'], 'completed')
        self.assertEqual(len(result['intervenciones']), 6)
        self.assertNotIn('diagnostico', result)

if __name__=='__main__': unittest.main()
