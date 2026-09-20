import asyncio
from types import SimpleNamespace
from app.macros import MacroManager, MacroDefinition, MacroStep
from app.models import RobotActionResult


def test_macro_renews_lease_without_repeating_drive_command():
    class Robot:
        direction='stop'
        commands=[]
        heartbeats=0
        async def manual(self, direction):
            self.direction=direction
            self.commands.append(direction)
            return RobotActionResult(ok=True,action=direction)
        async def keepalive(self, direction):
            self.heartbeats+=1
            return RobotActionResult(ok=True,action='keepalive')
    manager=MacroManager.__new__(MacroManager)
    manager.robot=Robot()
    manager.history=SimpleNamespace(log=lambda *a, **k:None)
    macro=MacroDefinition(id='test', name='test', created_at='',duration_ms=500,
        steps=[MacroStep(at_ms=0,command='forward'),MacroStep(at_ms=500,command='stop')])
    asyncio.run(manager._run_macro(macro))
    assert manager.robot.commands.count('forward')==1
    assert manager.robot.heartbeats>=1
    assert manager.robot.direction=='stop'
