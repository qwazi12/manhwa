"""Actual updated studio with an isolated generation-only application boundary."""
import os
from pathlib import Path
from starlette.responses import JSONResponse
from guard import blocked, validate


def deny_external(*args, **kwargs):
    raise PermissionError('Isolated test: publication, Drive and automatic scheduling are disabled')


def install_boundaries(server):
    # These are worker boundaries too: HTTP middleware alone cannot stop them.
    server._drive.configured=lambda:False
    server._drive.copy_chapter=deny_external
    server._drive_copy=deny_external
    server._drive_copy_later=deny_external
    server._schedule_post_pass=deny_external
    server._scheduler_pass=deny_external
    server._resume_interrupted=deny_external
    server._resume_budget_paused=deny_external
    # Offline credentials + deny-all OS egress + CONNECT allowlist provide
    # independent protection if a future call bypasses a function boundary.


class GenerationBoundary:
    def __init__(self, application):self.application=application
    async def __call__(self,scope,receive,send):
        if scope['type']=='http' and blocked(scope['method'],scope['path']):
            return await JSONResponse({'code':'isolated_test_disabled', 'detail':
                'Isolated Chapter 34 test: publishing, schedules, accounts and Drive are disabled.'},status_code=403)(scope,receive,send)
        return await self.application(scope,receive,send)


def create_app():
    if os.environ.get('FLAMINGO_E2E')!='1':raise RuntimeError('Use the isolated real-generation launcher')
    stage=validate(os.environ['FLAMINGO_E2E_ROOT'],os.environ)
    if Path(__file__).resolve().parents[2] != stage/'app':
        raise RuntimeError('Test backend must run from its isolated checkout')
    import server
    if Path(server._ingest_mod.PROJECTS).resolve() != stage/'app/manhwa-recap-v1/review_ui/projects':
        raise RuntimeError('Test projects directory mismatch')
    install_boundaries(server)
    # Do not let startup housekeeping delete test evidence or recover work.
    import cleanup, resume
    cleanup.startup_cleanup=lambda:None
    resume.recover_interrupted=lambda:None
    return GenerationBoundary(server.app)


if os.environ.get('FLAMINGO_E2E')=='1':app=create_app()
