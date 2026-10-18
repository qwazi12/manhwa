import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from guard import allowed_tunnel, blocked, sandbox_profile, validate
from e2e_backend import GenerationBoundary, install_boundaries


@pytest.mark.parametrize('host',['www.googleapis.com:443','youtube.com:443','upload-post.com:443',
 'generativelanguage.googleapis.com.evil.test:443','generativelanguage.googleapis.com:80',
 '127.0.0.1:443','asurascans.com@evil.test:443','texttospeech.googleapis.com:443/path'])
def test_proxy_refuses_forbidden_destinations(host):assert not allowed_tunnel(host)


@pytest.mark.parametrize('host',['generativelanguage.googleapis.com:443','texttospeech.googleapis.com:443',
 'asurascans.com:443','cdn.asurascans.com:443'])
def test_only_required_source_and_generation_destinations(host):assert allowed_tunnel(host)


@pytest.mark.parametrize('method,path',[('POST','/api/publishing/publish'),('GET','/api/publishing/connect'),
 ('POST','/api/drive/copy'),('GET','/api/social/status'),('POST','/api/schedule'),
 ('POST','/api/review'),('POST','/api/autopilot/run'),('POST','/api/jobs/resume')])
def test_forbidden_http_never_reaches_application(method,path):
 async def fail(request):raise AssertionError('external operation reached app')
 app=Starlette(routes=[Route('/{path:path}',fail,methods=[method])])
 with TestClient(GenerationBoundary(app)) as c:assert c.request(method,path).status_code==403


def test_normal_ingest_render_and_local_packaging_remain_available():
 for p in ['/api/ingest','/api/render-queue/add','/api/publish/prepare','/api/seo/generate']:
  assert not blocked('POST',p)


def test_actual_after_export_cannot_copy_or_delete_clips():
 import ast
 p=HERE.parents[1]/'manhwa-recap-v1/review_ui/server.py'
 node=next(n for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_after_export')
 calls=[]
 drive=SimpleNamespace(configured=lambda:True,copy_chapter=lambda *a:pytest.fail('upload'))
 server=SimpleNamespace(_drive=drive)
 install_boundaries(server)
 scope={'_mqa':SimpleNamespace(start=lambda *a:calls.append('qa')),
        '_prepare_publish':lambda *a:calls.append('packaging'), '_qc_bundle':lambda *a:calls.append('qc'),
        '_drive':drive,'_drive_copy':server._drive_copy}
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),scope)
 scope['_after_export']('/isolated/project','final.mp4')
 assert calls==['qa','packaging','qc']
 for fn in [server._drive_copy,drive.copy_chapter,server._schedule_post_pass,server._scheduler_pass,server._resume_interrupted]:
  with pytest.raises(PermissionError):fn(None,None)


def test_state_and_credentials_fail_closed(tmp_path):
 env={'AUTOPILOT_SCHEDULER':'0','BACKUP_ENABLED':'0','HTTPS_PROXY':'http://127.0.0.1:8149',
      'MANHWA_DATA_DIR':str(tmp_path/'data'),'GEMINI_API_KEY':'fake'}
 assert validate(tmp_path,env)==tmp_path
 for key in ['UPLOADPOST_API_KEY','GOOGLE_SERVICE_ACCOUNT_JSON','YOUTUBE_API_KEY','Flamingo_Remix_DRIVE_FOLDER_ID']:
  with pytest.raises(RuntimeError):validate(tmp_path,{**env,key:'fake'})
 path=tmp_path/'app/manhwa-recap-v1/review_ui';path.mkdir(parents=True)
 (path/'projects').symlink_to(tmp_path.parent)
 with pytest.raises(RuntimeError):validate(tmp_path,env)


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS OS sandbox')
def test_os_boundary_refuses_host_write_and_direct_egress_in_child(tmp_path):
 outside=tmp_path/'production';outside.mkdir();f=outside/'preserved';f.write_text('original')
 stage=tmp_path/'isolated';stage.mkdir()
 script='''import pathlib,socket,subprocess,sys
try:pathlib.Path(sys.argv[1]).write_text('bad')
except PermissionError:pass
else:raise AssertionError('production write escaped')
s=socket.socket();s.settimeout(1)
try:s.connect(('1.1.1.1',443))
except OSError:pass
else:raise AssertionError('direct egress escaped')
r=subprocess.run(['/bin/sh','-c','echo bad > "$1"','sh',sys.argv[1]],capture_output=True)
assert r.returncode!=0
pathlib.Path(sys.argv[2]).write_text('private write permitted')
'''
 env={'PATH':os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'}
 subprocess.run(['/usr/bin/sandbox-exec','-p',sandbox_profile(stage),sys.executable,'-c',script,str(f),str(stage/'result')],env=env,check=True,timeout=10)
 assert f.read_text()=='original'


def test_launcher_environment_copies_only_generation_credentials(tmp_path):
 import json
 spec=importlib.util.spec_from_file_location('e2e_run',HERE/'run.py')
 runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
 (tmp_path/'credentials.json').write_text(json.dumps({'user':'u','password':'p','shared_secret':'s'}))
 env=runner.environment(tmp_path,providers=True,inherited={'PATH':os.environ['PATH'],
  'GEMINI_API_KEY':'fake','UPLOADPOST_API_KEY':'forbidden','GOOGLE_SERVICE_ACCOUNT_JSON':'forbidden',
  'DATABASE_URL':'production','BACKEND_URL':'https://production','YOUTUBE_API_KEY':'forbidden'})
 assert env['GEMINI_API_KEY']=='fake'
 assert not any(k in env for k in ['UPLOADPOST_API_KEY','GOOGLE_SERVICE_ACCOUNT_JSON','YOUTUBE_API_KEY'])
 assert env['DATABASE_URL']==f'sqlite:///{tmp_path}/data/staging.db'
 assert env['AUTOPILOT_SCHEDULER']==env['BACKUP_ENABLED']=='0'
 assert env['MAX_DAILY_SPEND_USD']==env['MAX_DAILY_SPEND_CEILING_USD']=='5'


def test_inherited_worker_forces_verified_tls_and_rejects_direct_network():
 script='''import socket,ssl
assert ssl._create_unverified_context().verify_mode==ssl.CERT_REQUIRED
assert ssl._create_unverified_context().check_hostname
for address in [('8.8.8.8',443),('127.0.0.1',9999)]:
 try:socket.socket().connect(address)
 except PermissionError:pass
 else:raise AssertionError('direct network permitted')
'''
 env={'PATH':os.environ['PATH'],'FLAMINGO_E2E':'1','PYTHONPATH':str(HERE),'PYTHONDONTWRITEBYTECODE':'1'}
 subprocess.run([sys.executable,'-c',script],env=env,check=True,timeout=10)


def test_live_proxy_rejects_forbidden_connect_without_upstream_call(monkeypatch):
 import socket,threading
 import proxy
 monkeypatch.setattr(proxy.socket,'create_connection',lambda *a,**k:pytest.fail('forbidden destination dialled'))
 with proxy.Server(('127.0.0.1',0),proxy.Handler) as server:
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  conn=socket.socket();conn.connect(server.server_address)
  conn.sendall(b'CONNECT www.googleapis.com:443 HTTP/1.1\r\nHost: www.googleapis.com:443\r\n\r\n')
  assert conn.recv(4096).startswith(b'HTTP/1.1 403')
  conn.close();server.shutdown();thread.join()
