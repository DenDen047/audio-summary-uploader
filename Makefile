.PHONY: setup build up down logs restart status

setup:
	@echo "=== 認証情報チェック ==="
	@test -d credentials || mkdir credentials
	@test -f credentials/youtube_client_secret.json \
		&& echo "✔ credentials/youtube_client_secret.json" \
		|| echo "✘ credentials/youtube_client_secret.json が見つかりません（README.md を参照）"
	@test -f credentials/youtube_token.json \
		&& echo "✔ credentials/youtube_token.json" \
		|| echo "✘ credentials/youtube_token.json が見つかりません（uv run automator auth youtube を実行）"
	@test -f $(HOME)/.notebooklm/storage_state.json \
		&& echo "✔ ~/.notebooklm/storage_state.json" \
		|| echo "✘ ~/.notebooklm/storage_state.json が見つかりません（uv run notebooklm login を実行）"
	@echo ""
	@echo "=== 設定ファイルチェック ==="
	@test -f config/settings.yaml \
		&& echo "✔ config/settings.yaml" \
		|| echo "✘ config/settings.yaml が見つかりません"

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

restart:
	docker compose restart

status:
	docker compose ps
