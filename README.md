# 虚拟面试官（Virtual Interviewer）

基于实时视频生成与语音交互的虚拟面试官系统。候选人与数字人面试官进行语音对话，
系统用 RAG 检索题库与评分标准来组织提问，面试结束后输出带证据的结构化评估报告。

- 需求：[REQUIREMENTS.md](./REQUIREMENTS.md)（v0.4）
- 架构：[ARCHITECTURE.md](./ARCHITECTURE.md)（v0.3）

当前状态：**代码基础架构已搭好**。全链路编排、状态机、RAG、Debug 子系统可用，
LLM 与数字人通过配置接入。默认 `VOICE_MODE=text`（浏览器 Web Speech + LiveTalking 内置 TTS）；
`VOICE_MODE=omni` 时走 Qwen3-Omni Realtime 转写 + 口播音频驱动数字人对口型。

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
| `VOICE_MODE` | `text`（默认）或 `omni` |
| `OMNI_API_BASE` | Qwen3-Omni 地址，如 `http://127.0.0.1:8091` 或隧道后的本机端口 |
| `OMNI_MODEL` / `OMNI_SPEAKER` | 默认为 `Qwen/Qwen3-Omni-30B-A3B-Instruct` / `chelsie` |

私有化 GPU 服务器的地址与凭证放在 `deploy/server.conf`（已被 gitignore），
模板见 `deploy/server.conf.example`。

## 远端部署 Qwen3-Omni + 数字人

需要本机 `sshpass`（`brew install sshpass`）和已填写的 `deploy/server.conf`。
Realtime **必须** `--no-async-chunk`。单卡自动走统一进程；两卡及以上按 stage0 / stage1+2 分进程。

```bash
chmod +x deploy/remote/*.sh
./deploy/remote/status.sh          # nvidia-smi + 端口探活
./deploy/remote/sync.sh            # rsync 代码到 REMOTE_DIR（不含凭证）
HF_ENDPOINT=https://hf-mirror.com ./deploy/remote/setup_omni.sh
./deploy/remote/start_omni.sh      # 等待 /v1/models
./deploy/remote/start_livetalking.sh
./deploy/remote/status.sh
```

本机隧道（Omni 未对公网开放时）：

```bash
ssh -L 8091:127.0.0.1:8091 -p 21624 vipuser@<gpu-host>
# .env
# VOICE_MODE=omni
# OMNI_API_BASE=http://127.0.0.1:8091
```

Realtime 烟测（官方客户端，需 16kHz mono PCM16 wav）：

```bash
python examples/online_serving/qwen3_omni/openai_realtime_client.py \
  --url ws://127.0.0.1:8091/v1/realtime \
  --model Qwen/Qwen3-Omni-30B-A3B-Instruct \
  --input-wav input_16k_mono.wav \
  --output-wav realtime_output.wav
```

验收：远端 `/v1/models` OK → 本机 realtime 出 wav → 面试开场数字人出声/口型 →
用户按住说话后出现转写并进入下一问 → Omni 挂掉时 Setup 显示降级、文字面试不崩。

显存：当前探测为 **1× A100 40GB**，`start_omni.sh` 会走单卡统一进程；两卡及以上才分 stage。
30B-A3B + LiveTalking 可能争抢显存，LiveTalking 优先 ultralight 或错开卡。
首次拉权重很慢，脚本支持 `HF_ENDPOINT` 镜像与断点续传。磁盘建议预留 ≥80GB。

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
  providers/         llm / embedding / avatar / voice / omni_realtime / videogen
web/                 React + Vite 前端，含 DebugPanel、按住说话
scripts/smoke.py     全链路自检
scripts/smoke_voice.py  假 Omni 语音路径自检
deploy/remote/       SSH 同步、安装、分 stage 启动 Omni / LiveTalking
```

替换任一外部能力只改 `providers/` 下对应实现和 `main.py` 的 `Container`，
上层编排不动。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/meta` | LLM / 数字人 / 向量库健康状态 |
| POST | `/api/sessions` | 创建会话 |
| POST | `/api/sessions/{id}/message` | 面试主链路，SSE 流式返回 |
| POST | `/api/sessions/{id}/voice/turn` | 整段录音上传（PCM/WAV），SSE：`transcript`/`delta`/`done` |
| POST | `/api/sessions/{id}/voice/interrupt` | 打断数字人当前口播 |
| POST | `/api/sessions/{id}/rtc/offer` | WebRTC(WHEP) 信令转发 |
| GET | `/api/sessions/{id}/report` | 拉取评估报告 |
| POST | `/api/sessions/{id}/debug` | 开关 Debug 模式 |
| GET | `/api/sessions/{id}/debug/history` | 拉取 Debug 事件（断线重连补齐） |
| GET/POST | `/api/corpus` | 语料查询 / 写入 |
| POST | `/api/corpus/status` | 语料状态流转（draft/active/disabled） |
| POST | `/api/corpus/agent` | 语料 Agent 生成（一律落 draft） |

`/api/sessions/{id}/message` 的 SSE 事件：`delta`、`thinking`、`done`、`evaluating`、
`report`、`debug`、`error`。语音回合额外有 `transcript`。Debug 事件搭在同一条流上（ADR-12）。
`GET /api/meta` 含 `omni` 与 `voice_mode`。

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
- 全双工流式语音（`WS /voice/stream` 已预留，MVP 为按住说完再传）
- 多路并发与鉴权（MVP 单并发、无鉴权，见架构 §8）
