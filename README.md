# 虚拟面试官（Virtual Interviewer）

基于实时视频生成与语音交互的虚拟面试官系统。候选人与数字人面试官进行语音对话，
系统用 RAG 检索题库与评分标准来组织提问，面试结束后输出带证据的结构化评估报告。

- 需求：[REQUIREMENTS.md](./REQUIREMENTS.md)（v0.4）
- 架构：[ARCHITECTURE.md](./ARCHITECTURE.md)（v0.3）

当前状态：**代码基础架构已搭好**。全链路编排、状态机、RAG、Debug 子系统可用，
LLM 与数字人通过配置接入；语音识别走浏览器 Web Speech API。

## 快速开始

```bash
make venv          # 创建 .venv、安装依赖、生成 .env
make smoke         # 用假 LLM 跑通「开场 → 答题 → 收尾 → 报告」全链路
make dev           # 启动后端 http://127.0.0.1:8090
make web           # 另开终端启动前端 http://127.0.0.1:5173
```

`make smoke` 不需要 GPU、模型或数字人服务，用于验证骨架自洽。真实面试需要配置 LLM。

### 语料入库（RAG）

```bash
make ingest            # 进程内：强制导入 seed/*.yaml + Agent 生成 draft
make ingest-http       # 对已启动的后端发 HTTP（服务占用内嵌 Qdrant 时用这个）
# 若要把本次 Agent 草稿直接启用：
./.venv/bin/python scripts/ingest_corpus.py --http http://127.0.0.1:8090 --activate
```

种子语料覆盖 5 个岗位（后端/前端/算法/产品/客户端）+ 通用行为面；Agent 产出默认 `draft`，需审核后 `POST /api/corpus/status` 启为 `active` 才参与检索。

## 配置

编辑 `.env`（模板见 `.env.example`）：

| 变量 | 说明 |
| --- | --- |
| `LLM_API_BASE` / `LLM_MODEL` | OpenAI 兼容端点。私有化 vLLM 填 `http://<gpu-host>:8000/v1` |
| `LLM_FALLBACK_API_BASE` | 主服务熔断后的降级端点，留空则不降级 |
| `LIVETALKING_BASE` | LiveTalking 地址，未配置时自动退化为纯文字面试 |
| `EMBEDDING_PROVIDER` | `hash`（离线开发）/ `bge-m3`（本地）/ `dashscope`（远程） |
| `QDRANT_URL` | 留空则用内嵌 Qdrant（本地文件，无需 Docker） |
| `DEBUG_DEFAULT` | Debug 模式默认开关，会话可单独覆盖 |

私有化 GPU 服务器的地址与凭证放在 `deploy/server.conf`（已被 gitignore），
模板见 `deploy/server.conf.example`。

用 `GET /api/meta` 确认各依赖是否就绪：

```bash
curl -s localhost:8090/api/meta | python -m json.tool
```

## 代码结构

```
server/
  main.py            FastAPI 接入层：只做协议转换与依赖装配
  pipeline.py        一轮交互的编排：状态机 + 引擎 + 数字人 + Debug
  session.py         会话状态机（Created→Opening→…→Done）与会话仓库
  debug.py           DebugEmitter：采集、脱敏、环形缓冲
  storage.py         SQLite：会话、消息、报告、语料元数据
  models.py          跨层共享的 Pydantic 模型
  config.py          环境变量 → 分组配置对象
  interview/         prompts / engine（提问）/ evaluator（报告）
  rag/               store（Qdrant 封装）/ retriever（检索与降级）
  corpus/            manager（CRUD + 状态机）/ agent（语料生成）/ seed（种子语料）
  providers/         llm / embedding / avatar / voice / videogen
web/                 React + Vite 前端，含 DebugPanel
scripts/smoke.py     全链路自检
```

替换任一外部能力只改 `providers/` 下对应实现和 `main.py` 的 `Container`，
上层编排不动。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/meta` | LLM / 数字人 / 向量库健康状态 |
| POST | `/api/sessions` | 创建会话 |
| POST | `/api/sessions/{id}/message` | 面试主链路，SSE 流式返回 |
| POST | `/api/sessions/{id}/rtc/offer` | WebRTC(WHEP) 信令转发 |
| GET | `/api/sessions/{id}/report` | 拉取评估报告 |
| POST | `/api/sessions/{id}/debug` | 开关 Debug 模式 |
| GET | `/api/sessions/{id}/debug/history` | 拉取 Debug 事件（断线重连补齐） |
| GET/POST | `/api/corpus` | 语料查询 / 写入 |
| POST | `/api/corpus/status` | 语料状态流转（draft/active/disabled） |
| POST | `/api/corpus/agent` | 语料 Agent 生成（一律落 draft） |

`/api/sessions/{id}/message` 的 SSE 事件：`delta`、`thinking`、`done`、`evaluating`、
`report`、`debug`、`error`。Debug 事件搭在同一条流上，不额外建长连接（ADR-12）。

## Debug 模式

创建会话时传 `debug: true`，或对已有会话调 `POST /api/sessions/{id}/debug`。
面板分五类展示：状态机流转、RAG 检索命中与耗时、与 LLM/数字人的通讯、分段延迟、原始日志。
密钥与凭证在入缓冲前脱敏，未开启时 emit 为空操作。

## Docker

```bash
docker compose up -d --build     # 应用 + Qdrant
docker compose --profile gpu up  # 在 GPU 机器上额外起 vLLM
```

## 尚未接入

- 全视频生成（`providers/videogen.py` 为空实现，前端用静态背景）
- 服务端 ASR / 独立 TTS（`providers/voice.py` 为占位，MVP 走浏览器 + LiveTalking 内置）
- 语料管理后台页面（接口已就绪，前端未做）
- 多路并发与鉴权（MVP 单并发、无鉴权，见架构 §8）
