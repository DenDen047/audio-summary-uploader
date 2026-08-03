"""notebooklm_py_backend のテスト.

`notebooklm-py` は upstream の main を追うため、ライブラリ側の引数名が変わると
実行時にしか気づけない（実例: `poll_interval` → `initial_interval` / `max_interval`）。
呼び出し引数を実ライブラリのシグネチャへ束縛して、その種の破壊を検知する。
"""

import inspect
from types import SimpleNamespace
from typing import Any

import pytest
from notebooklm.client import ArtifactsAPI

from podcast.notebooklm_py_backend import NotebookLMPyBackend


class _SignatureCheckedArtifacts:
    """呼び出し引数を実ライブラリのシグネチャで検証するスタブ."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def wait_for_completion(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        bound = inspect.signature(ArtifactsAPI.wait_for_completion).bind(
            self, *args, **kwargs
        )
        self.calls.append(dict(bound.arguments))
        return SimpleNamespace(status="COMPLETED", task_id="task-1")


class _FakeClient:
    def __init__(self, artifacts: _SignatureCheckedArtifacts) -> None:
        self.artifacts = artifacts

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_wait_for_audio_matches_library_signature() -> None:
    artifacts = _SignatureCheckedArtifacts()
    backend = NotebookLMPyBackend(poll_interval=10, timeout=1200)
    backend._get_client = lambda: _async_value(_FakeClient(artifacts))  # type: ignore[method-assign]

    result = await backend.wait_for_audio("notebook-1", "task-1")

    assert result.status == "COMPLETED"
    call = artifacts.calls[0]
    assert call["notebook_id"] == "notebook-1"
    assert call["task_id"] == "task-1"
    assert call["timeout"] == 1200.0
    assert call["max_interval"] == 10.0
    # 初回間隔は上限を超えない
    assert call["initial_interval"] <= call["max_interval"]


@pytest.mark.asyncio
async def test_wait_for_audio_initial_interval_capped_by_poll_interval() -> None:
    artifacts = _SignatureCheckedArtifacts()
    backend = NotebookLMPyBackend(poll_interval=1, timeout=60)
    backend._get_client = lambda: _async_value(_FakeClient(artifacts))  # type: ignore[method-assign]

    await backend.wait_for_audio("notebook-1", "task-1")

    call = artifacts.calls[0]
    assert call["initial_interval"] == 1.0
    assert call["max_interval"] == 1.0


async def _async_value(value: Any) -> Any:
    return value
