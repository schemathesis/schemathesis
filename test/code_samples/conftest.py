from __future__ import annotations

import shlex
from dataclasses import dataclass, field

import pytest


@dataclass
class CurlWrapper:
    testdir: field()

    def run(self, command: str):
        if "⚠️" in command:
            command = command.split("⚠️")[0].strip()
        return self.testdir.run(*shlex.split(command))

    def assert_valid(self, command: str):
        result = self.run(command)
        if result.ret != 0:
            assert "Failed to connect" in result.stderr.lines[-1]


@pytest.fixture
def curl(testdir):
    return CurlWrapper(testdir)
