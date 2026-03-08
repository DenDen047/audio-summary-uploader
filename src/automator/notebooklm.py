"""NotebookLM 操作 — 抽象インターフェース (Strategy パターン)."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notebooklm import GenerationStatus


class NotebookLMBackend(ABC):
    @abstractmethod
    async def create_notebook(self, title: str) -> str:
        """ノートブックを作成し、notebook_id を返す."""
        ...

    @abstractmethod
    async def add_source(self, notebook_id: str, url: str) -> None:
        """ノートブックに URL ソースを追加する."""
        ...

    @abstractmethod
    async def add_file_source(self, notebook_id: str, file_path: Path) -> None:
        """ノートブックにローカルファイルをソースとして追加する."""
        ...

    @abstractmethod
    async def start_audio_generation(
        self,
        notebook_id: str,
        language: str = "ja",
        instructions: str = "",
        audio_length: str | None = None,
    ) -> str:
        """音声生成を開始し task_id を返す（完了を待たない）."""
        ...

    @abstractmethod
    async def check_audio_status(
        self, notebook_id: str, task_id: str
    ) -> "GenerationStatus":
        """生成ステータスを1回チェックする."""
        ...

    @abstractmethod
    async def wait_for_audio(
        self, notebook_id: str, task_id: str
    ) -> "GenerationStatus":
        """音声生成の完了をポーリングで待機する."""
        ...

    @abstractmethod
    async def generate_audio(
        self,
        notebook_id: str,
        language: str = "ja",
        instructions: str = "",
        audio_length: str | None = None,
    ) -> str:
        """Audio Overview を生成し、audio_id を返す."""
        ...

    @abstractmethod
    async def download_audio(self, notebook_id: str, output_path: Path) -> Path:
        """生成された音声をダウンロードする."""
        ...

    @abstractmethod
    async def delete_notebook(self, notebook_id: str) -> None:
        """ノートブックを削除する."""
        ...
