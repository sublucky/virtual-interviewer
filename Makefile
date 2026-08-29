PY := ./.venv/bin/python
PIP := ./.venv/bin/pip

.PHONY: help venv dev web smoke check compose-up compose-down clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

venv: ## 创建虚拟环境并安装后端依赖
	python3 -m venv .venv && $(PIP) install -q -r requirements.txt
	@test -f .env || cp .env.example .env

dev: ## 启动后端（默认 127.0.0.1:8090）
	$(PY) -m uvicorn server.main:app --reload --port $${PORT:-8090}

web: ## 启动前端开发服务器（127.0.0.1:5173）
	cd web && npm install && npm run dev

smoke: ## 假 LLM 跑通全链路自检
	$(PY) scripts/smoke.py

ingest: ## 强制导入种子语料 + Agent 生成 draft
	$(PY) scripts/ingest_corpus.py

ingest-http: ## 对已启动服务做入库（HTTP）
	$(PY) scripts/ingest_corpus.py --http http://127.0.0.1:$${PORT:-8090}

check: ## 语法与导入检查
	$(PY) -m compileall -q server scripts && $(PY) -c "from server.main import app; print('ok')"

compose-up: ## Docker Compose 起应用 + Qdrant
	docker compose up -d --build

compose-down:
	docker compose down

clean:
	rm -rf data __pycache__ .venv web/node_modules web/dist
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
