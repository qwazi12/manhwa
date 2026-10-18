"""Execute the actual Next middleware without starting a staging web server."""
import os
from pathlib import Path
import subprocess

import pytest

WEB = Path(__file__).resolve().parents[2] / "manhwa-recap-v1/web"


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    output = tmp_path_factory.mktemp("staging-middleware")
    subprocess.run(["node", "node_modules/typescript/bin/tsc", "middleware.ts", "lib/session.ts",
                    "--module", "commonjs", "--target", "ES2020", "--skipLibCheck",
                    "--esModuleInterop", "--outDir", str(output)], cwd=WEB, check=True,
                   capture_output=True)
    return output / "middleware.js"


@pytest.mark.parametrize("backend,password,expected", [
    ("", "p", 503),
    ("https://recap-studio-production.up.railway.app", "p", 503),
    ("http://127.0.0.1:8138", "", 503),
    ("http://127.0.0.1:8138", "p", 200),
])
def test_staging_auth_and_backend_fail_closed(compiled, backend, password, expected):
    env = {"PATH": os.environ["PATH"], "NODE_PATH": str(WEB / "node_modules"),
           "FLAMINGO_STAGING": "1", "BACKEND_URL": backend,
           "BASIC_AUTH_USER": "u", "BASIC_AUTH_PASSWORD": password, "SHARED_SECRET": "test-secret"}
    script = '''
const assert = require('node:assert/strict');
const {NextRequest} = require('next/server');
const {middleware} = require(process.argv[1]);
(async()=>{
  const req = new NextRequest('http://127.0.0.1:3138/api/projects',
    {headers:{authorization:'Basic '+Buffer.from('u:p').toString('base64')}});
  const result = await middleware(req);
  assert.equal(result.status, Number(process.argv[2]));
  if (result.status===200) {
    assert.equal(result.headers.get('x-middleware-rewrite'), 'http://127.0.0.1:8138/api/projects');
    const denied=await middleware(new NextRequest('http://127.0.0.1:3138/api/projects'));
    assert.equal(denied.status,401);
  }
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(["node", "-e", script, str(compiled), str(expected)], env=env, check=True,
                   capture_output=True, text=True, timeout=15)
