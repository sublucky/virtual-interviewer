# 素材库（Assets）

虚拟面试官的形象、音频等素材统一存放在这里。

## 目录结构

| 目录 | 用途 |
| --- | --- |
| `avatars/` | 数字人形象素材：头像、形象参考图、LiveTalking 训练/推理用图 |
| `audio/` | 音频素材：音色参考（TTS 克隆）、提示音、背景音乐 |

## 当前素材

- `avatars/interviewer_female_01.png`：女性面试官形象（960×1280，半身正装照）
- `avatars/interviewer_female_01_silence.mp4`：静音循环视频，用于 LiveTalking wav2lip 口型驱动
- `audio/interviewer_spk_emb.txt`：ChatTTS 面试官音色嵌入（首次启动自动生成，勿手改）

## LiveTalking 形象生成

```bash
# 远端：安装环境 → 从 silence.mp4 生成 avatar → 启动
./deploy/remote/setup_livetalking.sh
./deploy/remote/prepare_avatar.sh   # AVATAR_ID=interviewer_female_01
./deploy/remote/start_livetalking.sh
# 本机隧道（WebRTC 需 UDP，若拉流失败请把 8010 对浏览器可达）
ssh -L 8010:127.0.0.1:8010 -p <port> <user>@<gpu-host>
```

生成产物在远端 `LiveTalking/data/avatars/interviewer_female_01/`（`full_imgs/`、`face_imgs/`、`coords.pkl`）。

## 使用说明

- LiveTalking 数字人形象通常需要 25fps 的讲解/静音视频，参考 `deploy/remote/prepare_avatar.sh`。
- ChatTTS 音色由 `CHATTTS_SPEAKER_EMB` 指定；删掉该文件后重启会重新随机采样音色。
- TTS 音色参考音频建议 5–15 秒、单人声、无背景噪声的 WAV（16kHz+）。
- 大文件（视频、模型权重）不要提交 git，放到 `data/` 或远端服务器。
