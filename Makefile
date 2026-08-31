PY := ./.venv/bin/python
PIP := ./.venv/bin/pip

.PHONY: help venv free-port dev web open-web smoke check compose-up compose-down clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

venv: ## 创建虚拟环境并安装后端依赖
	python3 -m venv .venv && $(PIP) install -q -r requirements.txt
	@test -f .env || cp .env.example .env

# 释放本机监听端口（默认 8090）；被旧 uvicorn / server.main 占用时自动杀掉
free-port: ## 关闭占用 PORT 的本机进程（默认 8090）
	@port=$${PORT:-8090}; \
	pids=$$(lsof -tiTCP:$$port -sTCP:LISTEN 2>/dev/null || true); \
	if [ -n "$$pids" ]; then \
	  echo "释放 :$$port ← PID $$pids"; \
	  kill $$pids 2>/dev/null || true; \
	  sleep 0.5; \
	  still=$$(lsof -tiTCP:$$port -sTCP:LISTEN 2>/dev/null || true); \
	  if [ -n "$$still" ]; then kill -9 $$still 2>/dev/null || true; fi; \
	else \
	  echo ":$$port 空闲"; \
	fi

dev: free-port ## 启动后端（先释放端口，默认 127.0.0.1:8090）
	@echo "后端 API → http://127.0.0.1:$${PORT:-8090}  （前端请用 make web → :5173）"
	$(PY) -m uvicorn server.main:app --reload --port $${PORT:-8090}

web: ## 启动前端开发服务器（http://127.0.0.1:5173）
	@port=5173; \
	pids=$$(lsof -tiTCP:$$port -sTCP:LISTEN 2>/dev/null || true); \
	if [ -n "$$pids" ]; then echo "释放 :$$port ← PID $$pids"; kill $$pids 2>/dev/null || true; sleep 0.5; fi
	@echo "前端界面 → http://127.0.0.1:5173"
	cd web && npm install && npm run dev -- --host 127.0.0.1 --port 5173

open-web: ## 用系统浏览器打开前端
	@open http://127.0.0.1:5173

smoke: ## 假 LLM 跑通全链路自检
	$(PY) scripts/smoke.py

smoke-voice: ## 假 Omni 跑通语音转写 + 数字人音频路径
	$(PY) scripts/smoke_voice.py

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
