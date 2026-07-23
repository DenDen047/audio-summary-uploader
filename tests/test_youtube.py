"""YouTube 認証・アップロードのテスト."""

from __future__ import annotations

from pathlib import Path

import pytest

from summary.youtube import authenticate


def test_authenticate_non_interactive_raises_without_token(
    tmp_path: Path,
) -> None:
    """非対話コンテキストではトークンがない場合に OAuth フローを開始せず即エラー.

    Web サーバーから呼ばれた際にブラウザフロー待ちでイベントループが
    ブロックされるのを防ぐガード。
    """
    with pytest.raises(RuntimeError, match="auth youtube"):
        authenticate(
            client_secret_path=tmp_path / "client_secret.json",
            token_path=tmp_path / "token.json",
            allow_interactive=False,
        )
