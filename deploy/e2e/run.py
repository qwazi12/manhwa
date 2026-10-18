"""Prepare/launch the isolated real Chapter34 studio on this Mac only.

Uses existing generation credentials from the launch environment, never copies
publishing/Drive/account credentials. No production configuration is changed.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
from guard import sandbox_profile, validate
spec=importlib.util.spec_from_file_location('offline_prepare',ROOT/'deploy/staging/prepare.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


def environment(stage, *, providers=False, inherited=None):
    inherited=os.environ if inherited is None else inherited
    stage=Path(stage).resolve()
    creds=json.loads((stage/'credentials.json').read_text())
    env=prepare.environment(stage,creds,inherited)
    env.update(FLAMINGO_E2E='1',FLAMINGO_E2E_ROOT=str(stage),
        TMPDIR=str(stage/'tmp'),XDG_CACHE_HOME=str(stage/'home/.cache'),
        HTTPS_PROXY='http://127.0.0.1:8149',HTTP_PROXY='http://127.0.0.1:8149',
        ALL_PROXY='http://127.0.0.1:8149',NO_PROXY='localhost,127.0.0.1,::1',
        MAX_DAILY_SPEND_USD='5',MAX_DAILY_SPEND_CEILING_USD='5',
        MAX_DAILY_GEMINI_CALLS='1200',MAX_GEMINI_CALLS_PER_JOB='1200',
        MAX_DAILY_TTS_CHARS='120000',MAX_TTS_CHARS_PER_JOB='120000',
        MAX_DAILY_CLAUDE_CALLS='0',MAX_CLAUDE_CALLS_PER_JOB='0',
        EXPORT_SPEED='1.1',RENDER_CLIP_PARALLEL='2',RENDER_WORKERS='1',
        PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1',
        HYPERFRAMES_BROWSER_PATH=str(stage/'browser-with-proxy'),
        HYPERFRAMES_TELEMETRY_DISABLED='1',DO_NOT_TRACK='1',HYPERFRAMES_SKIP_SKILLS='1',
        HF_WORKSPACE=str(stage/'app/manhwa-recap-v1/hyperframes/segments-workspace'))
    env['PYTHONPATH']=os.pathsep.join(str(stage/'app'/p) for p in
        ['deploy/e2e','manhwa-recap-v1/review_ui','manhwa-recap-v1','panel-describe'])
    if providers:
        for key in ('GEMINI_API_KEY','TTS_API_KEY'):
            value=next((v for k,v in inherited.items() if k.lower()==key.lower() and v),None)
            if value:env[key]=value
        if not env.get('GEMINI_API_KEY'):raise RuntimeError('Generation credential not configured')
    validate(stage,env)
    return env


def prepare_browser(stage):
    # Reuse already installed public browser/font assets, no downloads or profile data.
    cache=stage/'home/.cache/hyperframes'
    if not (cache/'chrome').exists():
        source=Path.home()/'.cache/hyperframes'
        cache.mkdir(parents=True,exist_ok=True)
        for name in ('chrome','fonts'):
            if (source/name).is_dir():
                subprocess.run(['/bin/cp','-cR',str(source/name),str(cache/name)],check=True)
    browsers=sorted((cache/'chrome/chrome-headless-shell').glob('mac_arm-*/chrome-headless-shell-mac-arm64/chrome-headless-shell'))
    if not browsers:raise RuntimeError('Install the pinned local browser before launching this test')
    shim=stage/'browser-with-proxy'
    shim.write_text('#!/bin/sh\nexec '+shlex.quote(str(browsers[-1]))+' --proxy-server=http://127.0.0.1:8149 --disable-background-networking "$@"\n')
    shim.chmod(0o700)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=['prepare','build','launch'])
    ap.add_argument('directory',type=Path)
    args=ap.parse_args();stage=args.directory.resolve()
    if args.action=='prepare':
        result=prepare.prepare(stage,dependencies=False)
        result.update(mode='isolated-generation',paid_calls_enabled=False,profile_commit=result['commit'])
        # APFS clone-on-write copies keep dependency paths inside the checkout.
        for rel in ('manhwa-recap-v1/web/node_modules','manhwa-recap-v1/hyperframes/node_modules'):
            subprocess.run(['/bin/cp','-cR',str(ROOT/rel),str(stage/'app'/rel)],check=True)
        (stage/'manifest.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result));return
    if not stage.is_relative_to(ROOT/'work'):raise ValueError('Test directory must be under private work/')
    prepare_browser(stage)
    env=environment(stage,providers=args.action=='launch')
    if args.action=='launch':
        manifest=json.loads((stage/'manifest.json').read_text());manifest.update(mode='isolated-generation',paid_calls_enabled=True,estimated_spend_cap_usd=5,publishing_enabled=False,drive_enabled=False,scheduling_enabled=False);(stage/'manifest.json').write_text(json.dumps(manifest,indent=2))
    profile=stage/'test.sb';profile.write_text(sandbox_profile(stage))
    command=['/usr/bin/sandbox-exec','-f',str(profile)]
    web=stage/'app/manhwa-recap-v1/web'
    if args.action=='build':
        subprocess.run(command+['node','node_modules/typescript/bin/tsc','--noEmit'],cwd=web,env=env,check=True)
        subprocess.run(command+['node','node_modules/next/dist/bin/next','build'],cwd=web,env=env,check=True)
        return
    if not (web/'.next/BUILD_ID').exists():raise RuntimeError('Build this studio first')
    children=[];handles=[]
    def stop(*_):
        for p in children:
            if p.poll() is None:p.terminate()
        for p in children:
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        for f in handles:f.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        # Proxy receives no generation/provider credentials.
        proxy_env={k:env[k] for k in ('PATH','LANG') if k in env}
        proxy_env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
        for name,cmd,cwd,child_env in [
            ('proxy',[sys.executable,str(stage/'app/deploy/e2e/proxy.py')],stage,proxy_env),
            ('backend',command+[sys.executable,'-m','uvicorn','e2e_backend:app','--host','127.0.0.1','--port','8138'],stage/'app',env),
            ('frontend',command+['node','node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3138'],web,{k:v for k,v in env.items() if k not in ('GEMINI_API_KEY','TTS_API_KEY')})]:
            log=(stage/'logs'/f'{name}.log').open('a');handles.append(log)
            children.append(subprocess.Popen(cmd,cwd=cwd,env=child_env,stdout=log,stderr=subprocess.STDOUT))
        (stage/'pids.json').write_text(json.dumps([p.pid for p in children]))
        print('Isolated updated studio: http://localhost:3138 — publishing/scheduling/Drive disabled',flush=True)
        # Wait on children without an unbounded silent shell wait to the agent.
        import time
        while all(p.poll() is None for p in children):time.sleep(1)
        raise RuntimeError('A test service exited; see private logs')
    finally:stop()


if __name__=='__main__':main()
