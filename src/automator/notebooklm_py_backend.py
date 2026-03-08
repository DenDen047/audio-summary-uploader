"""NotebookLM 操作 — notebooklm-py による実装 (Phase 1)."""

from pathlib import Path

from loguru import logger
from notebooklm import AudioLength, GenerationStatus, NotebookLMClient

from automator.notebooklm import NotebookLMBackend

_AUDIO_LENGTH_MAP: dict[str, AudioLength] = {
    "short": AudioLength.SHORT,
    "long": AudioLength.LONG,
    "default": AudioLength.DEFAULT,
}


class NotebookLMPyBackend(NotebookLMBackend):
    """notebooklm-py ライブラリを利用した実装."""

    def __init__(self, poll_interval: int = 10, timeout: int = 600) -> None:
        self._poll_interval = poll_interval
        self._timeout = timeout

    async def _get_client(self) -> NotebookLMClient:
        """認証済みクライアントを取得する."""
        return await NotebookLMClient.from_storage()

    async def create_notebook(self, title: str) -> str:
        logger.info("Creating notebook: {!r}", title)
        async with await self._get_client() as client:
            notebook = await client.notebooks.create(title)
            notebook_id = notebook.id
        logger.info("Created notebook: {}", notebook_id)
        return notebook_id

    async def add_source(self, notebook_id: str, url: str) -> None:
        logger.info("Adding source {} to notebook {}", url, notebook_id)
        async with await self._get_client() as client:
            source = await client.sources.add_url(
                notebook_id, url, wait=True, wait_timeout=120.0
            )
            logger.info(
                "Source added successfully: {} (status={})",
                source.id,
                source.status,
            )

    async def add_file_source(self, notebook_id: str, file_path: Path) -> None:
        logger.info("Adding file source {} to notebook {}", file_path, notebook_id)
        async with await self._get_client() as client:
            source = await client.sources.add_file(
                notebook_id, file_path, wait=True, wait_timeout=120.0
            )
            logger.info(
                "File source added successfully: {} (status={})",
                source.id,
                source.status,
            )

    async def start_audio_generation(
        self,
        notebook_id: str,
        language: str = "ja",
        instructions: str = "",
        audio_length: str | None = None,
    ) -> str:
        logger.info(
            "Starting audio generation for notebook {} (lang={}, length={})",
            notebook_id,
            language,
            audio_length,
        )
        async with await self._get_client() as client:
            audio_length_enum = _AUDIO_LENGTH_MAP.get(
                audio_length or "default", AudioLength.DEFAULT
            )
            gen_status = await client.artifacts.generate_audio(
                notebook_id,
                language=language,
                instructions=instructions or None,
                audio_length=audio_length_enum,
            )
        logger.info(
            "Audio generation started: task_id={}", gen_status.task_id
        )
        return gen_status.task_id

    async def check_audio_status(
        self, notebook_id: str, task_id: str
    ) -> GenerationStatus:
        logger.info(
            "Checking audio status for notebook {} task {}",
            notebook_id,
            task_id,
        )
        async with await self._get_client() as client:
            status = await client.artifacts.poll_status(notebook_id, task_id)
        logger.info("Audio status: {}", status.status)
        return status

    async def wait_for_audio(
        self, notebook_id: str, task_id: str
    ) -> GenerationStatus:
        logger.info(
            "Waiting for audio completion: notebook={} task={}",
            notebook_id,
            task_id,
        )
        async with await self._get_client() as client:
            result = await client.artifacts.wait_for_completion(
                notebook_id,
                task_id=task_id,
                timeout=float(self._timeout),
                poll_interval=float(self._poll_interval),
            )
        logger.info("Audio wait result: status={}", result.status)
        return result

    async def generate_audio(
        self,
        notebook_id: str,
        language: str = "ja",
        instructions: str = "",
        audio_length: str | None = None,
    ) -> str:
        task_id = await self.start_audio_generation(
            notebook_id, language, instructions, audio_length
        )
        result = await self.wait_for_audio(notebook_id, task_id)
        logger.info("Audio generation complete: {}", result.task_id)
        return result.task_id

    async def download_audio(self, notebook_id: str, output_path: Path) -> Path:
        logger.info(
            "Downloading audio for notebook {} → {}",
            notebook_id,
            output_path,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async with await self._get_client() as client:
            downloaded = await client.artifacts.download_audio(
                notebook_id, output_path=str(output_path)
            )

        logger.info("Audio downloaded: {}", downloaded)
        return Path(downloaded)

    async def delete_notebook(self, notebook_id: str) -> None:
        logger.info("Deleting notebook: {}", notebook_id)
        async with await self._get_client() as client:
            await client.notebooks.delete(notebook_id)
        logger.info("Notebook deleted: {}", notebook_id)
