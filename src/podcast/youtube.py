"""YouTube API 操作: 認証・アップロード・サムネイル設定."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from loguru import logger

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


@dataclass
class YouTubeUploadParams:
    file_path: Path
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    category_id: str = "27"
    privacy_status: str = "unlisted"
    default_language: str = "ja"
    thumbnail_path: Path | None = None
    playlist_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UploadResult:
    """アップロード結果。thumbnail_set=False は 429 等でサムネ未適用（要再適用）."""
    youtube_url: str
    thumbnail_set: bool


def _apply_thumbnail(youtube: object, video_id: str, thumbnail_path: Path) -> str:
    """既存/新規動画にカスタムサムネを設定し 'ok'|'quota'|'error' を返す.

    'quota' は 429 uploadRateLimitExceeded（一時的・後で再適用可能）。呼び出し側は
    バッチ中に 'quota' を受けたら残りの再適用を打ち切る（無駄な試行を避ける）。
    """
    try:
        youtube.thumbnails().set(  # type: ignore[attr-defined]
            videoId=video_id,
            media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png"),
        ).execute()
        return "ok"
    except HttpError as exc:
        if exc.resp.status == 429:
            logger.warning(
                "Thumbnail rate-limited (429) for {}; will retry later", video_id
            )
            return "quota"
        logger.warning("Failed to set thumbnail for {}: {}", video_id, exc)
        return "error"
    except Exception as exc:
        logger.warning("Failed to set thumbnail for {}: {}", video_id, exc)
        return "error"


_AUTH_REQUIRED_MSG = (
    "YouTube の認証が必要です。"
    "ターミナルで 'uv run podcast auth youtube' を実行して再認証してください。"
    "再認証後、Web UI からリトライできます。"
)


def authenticate(
    client_secret_path: Path,
    token_path: Path,
    allow_interactive: bool = True,
) -> Credentials:
    """YouTube API の OAuth 認証を行い Credentials を返す.

    allow_interactive=False の場合（Web サーバー等の非対話コンテキスト）、
    有効なトークンがなければブラウザフローを開始せず即座にエラーにする。
    """
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if creds and creds.expired and creds.refresh_token:
        logger.info("Refreshing YouTube token")
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except RefreshError as exc:
            logger.warning("Token refresh failed ({}), re-authenticating", exc)
            token_path.unlink(missing_ok=True)
            creds = None
    if not creds or not creds.valid:
        if not allow_interactive:
            raise RuntimeError(_AUTH_REQUIRED_MSG)
        logger.info("Starting YouTube OAuth flow")
        if not client_secret_path.exists():
            msg = f"Client secret not found: {client_secret_path}"
            raise FileNotFoundError(msg)
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret_path), _SCOPES
        )
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info("YouTube token saved to {}", token_path)

    return creds


def _upload_video_sync(
    creds: Credentials, params: YouTubeUploadParams
) -> UploadResult:
    """動画をアップロードし結果（URL＋サムネ適用可否）を返す（同期）."""
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": params.title,
            "description": params.description,
            "tags": params.tags,
            "categoryId": params.category_id,
            "defaultLanguage": params.default_language,
        },
        "status": {
            "privacyStatus": params.privacy_status,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(
        str(params.file_path),
        mimetype="video/mp4",
        resumable=True,
    )

    logger.info("Uploading video: {!r}", params.title)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.debug("Upload progress: {:.0%}", status.progress())

    video_id = response["id"]
    youtube_url = f"https://youtu.be/{video_id}"
    logger.info("Video uploaded: {}", youtube_url)

    # 以降の後処理は失敗しても動画自体はアップロード済みなので WARN に留める。
    # ここで例外を投げるとジョブが failed になり、リトライで動画が重複するため。

    # サムネイル設定。429（クォータ上限）や一時失敗は thumbnail_set=False として
    # 呼び出し側に返し、後続の再適用スイープで自己修復させる。
    thumbnail_set = True
    if params.thumbnail_path and params.thumbnail_path.exists():
        logger.info("Setting custom thumbnail")
        thumbnail_set = (
            _apply_thumbnail(youtube, video_id, params.thumbnail_path) == "ok"
        )

    # プレイリストに追加（カテゴリ別＋全動画横断）
    for playlist_id in params.playlist_ids:
        logger.info("Adding to playlist {}", playlist_id)
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    },
                },
            ).execute()
        except Exception as exc:
            logger.warning(
                "Failed to add {} to playlist {}: {}",
                youtube_url,
                playlist_id,
                exc,
            )

    return UploadResult(youtube_url=youtube_url, thumbnail_set=thumbnail_set)


async def upload_video(creds: Credentials, params: YouTubeUploadParams) -> UploadResult:
    """動画をアップロードし結果（URL＋サムネ適用可否）を返す（async ラッパー）."""
    return await asyncio.to_thread(_upload_video_sync, creds, params)


def _set_thumbnail_sync(
    creds: Credentials, video_id: str, thumbnail_path: Path
) -> str:
    """既存動画にカスタムサムネを再適用し 'ok'|'quota'|'error' を返す（同期）."""
    youtube = build("youtube", "v3", credentials=creds)
    return _apply_thumbnail(youtube, video_id, thumbnail_path)


async def set_thumbnail(
    creds: Credentials, video_id: str, thumbnail_path: Path
) -> str:
    """既存動画へのカスタムサムネ再適用（async ラッパー）. 'ok'|'quota'|'error'."""
    return await asyncio.to_thread(_set_thumbnail_sync, creds, video_id, thumbnail_path)
