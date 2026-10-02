# Golden-money-explosion

B 站视频本地转写（SenseVoice）。单条 BV 用 `bili-asr/bili_asr.py`；按"视频组"每日调度用 `bili-asr/bili_daily.py`。

## 视频组日调度

同一个视频往往有多个 UP 同时上传，每天只需要处理发布最早的那一条。

```bash
cd ~/Golden-money-explosion/bili-asr   # 仓库里的实际位置
cp groups.example.json groups.json
vi groups.json                         # 填组名、UP 的 UID、标题正则
python bili_daily.py --dry-run         # 只看今天会选中哪些视频，不下载不转写
python bili_daily.py                   # 正式跑
```

`groups.json` 关键字段：

| 字段 | 作用 |
| --- | --- |
| `output_root` | 结果根目录，展开 `~`；产物在 `<output_root>/results/<组名>/<日期>.{txt,srt,metadata.json}`，日志在 `<output_root>/logs/<日期>.log` |
| `groups[].name` | 视频组名，直接用作目录名（`/ \ : * ? " < > \|` 换成 `_`） |
| `groups[].title_pattern` | Python 正则，标题命中才算这个组的新视频；不写则全部算 |
| `groups[].ups[]` | 组内 UP，`uid` 是 `space.bilibili.com/<uid>` 里的数字，`alias` 只用于日志 |
| `recent_limit` | 每个 UP 的投稿列表一次取最近 N 条（默认 30，即一页，可按组覆盖） |
| `probe_limit` | 每个 UP 每天最多逐条补查几篇稿件的标题/发布时间（默认 12，可按组覆盖） |
| `cookie_file` | B 站空间列表和下载常被风控（HTTP 412），需要时填你导出的 Netscape cookies 路径 |
| `model_dir` / `vad_dir` | 本地模型目录，离线加载；留空则联网下载 `iic/SenseVoiceSmall` 与 `fsmn-vad` |

选片规则：对组内每个 UP 拉投稿列表（按发布时间倒序）→ 从最新往回逐条补查标题与发布时间，查到早于当天的稿件即停 → 保留**发布日期等于当天**且标题命中正则的条目 → 所有候选里取**发布时间最早**的一条处理；没有任何 UP 命中则跳过该组。
之所以要逐条补查：当前 yt-dlp 的空间投稿扁平列表只给 BV 号，不给标题和发布时间。当天投稿一般排在最前面，所以实际每人只需补查 1～3 条。

常用参数：`--group 组名`（只跑某组，可重复）、`--date 2026-10-01`（补跑某天）、`--force`（当天已处理也重跑，并忽略转录片段缓存）、`--dry-run`、`--cookies-file`、`--output`、`--config`。

同一天重复运行是安全的：日志里已有该组当天的 `OK` 记录且结果文件存在时，该组会被 `SKIP`；每个组只处理一条视频，产物按 `<组名>/<日期>` 命名，天然幂等。单个 UP 取列表失败只写 INFO；一个组里所有 UP 都失败会记 `FAILED` 并使退出码为 1，其余组继续。

日志一行一条：`时间 | OK/SKIP/FAILED/DRY/INFO | 组名 | 说明`。

离线回归测试（不联网、不需要模型）：`python test_daily.py`，全部通过输出 `RESULT: ALL PASS`。

定时任务示例（Termux + `termux-cron`，每天 12:30）：

```cron
30 12 * * * cd ~/Golden-money-explosion/bili-asr && /data/data/com.termux/files/usr/bin/python bili_daily.py >> ~/bili-daily/logs/cron.log 2>&1
```

## 单条 BV 转写

```bash
python bili_asr.py BV1sbeS6KESx --download-only --cookies-file ~/bili_cookies.txt
python bili_asr.py BV1sbeS6KESx --language zh \
  --model-dir ~/models/SenseVoiceSmall --vad-dir ~/models/fsmn-vad
```

产物在 `<output>/<BV>/results/<BV>.{txt,srt,metadata.json}`。常用参数：
`--output`、`--audio 本地音频`（跳过下载）、`--language`、`--chunk-seconds`、
`--cookies-file` / `--cookies-from-browser`、`--insecure`（默认）/ `--verify-ssl`、
`--resume-download`（默认关闭）、`--force`、`--model-dir` / `--vad-dir`（离线加载）。

## 运行环境

只有 Termux（Android）一个运行环境；开发机上没有 ASR 模型，逻辑改动用 `test_daily.py` 离线验证。

```bash
pkg install python ffmpeg
pip install yt-dlp funasr torch torchaudio sentencepiece
mkdir -p ~/models && cd ~/models
git clone https://www.modelscope.cn/iic/SenseVoiceSmall.git
git clone https://www.modelscope.cn/fsmn-vad.git
```

- `bili_asr.py` 的 `ffmpeg_exe()` 优先用 `imageio-ffmpeg`，Termux 上没有对应 wheel，所以走 `PATH` 里的系统 ffmpeg。
- 风控：B 站空间列表与播放地址接口对裸请求返回 412/403，需要带 Cookie；Termux 没有浏览器可提取，只能从电脑导出 Netscape 格式 cookies 传到手机（`cookie_file` / `--cookies-file`）。请把 cookies 文件权限设为 600，不要提交到仓库。
- 412 也可能是同一 IP 请求过密；空间列表一次只取一页，逐条补查控制在 `probe_limit` 条内。持续失败就停止自动重试、稍后再试。
- 只用你有权下载的内容。Cookie、SESSDATA 不要写进日志或公开。
- `bili_daily.py` 用本地时区判定"当天"，Termux 与手机系统时区一致，跨时区旅行时注意。

## 已知限制

- 每组每天只处理一条视频：当天第二条命中、且发布时间晚于选中那条的稿件不会转写。
- 逐条补查按"倒序遇到更早稿件即停"，若某个 UP 当天稿件数超过 `probe_limit`，会漏掉更早的那些并在日志 INFO 里说明。
- 结果目录以组名为单位，不含 BV 号；BV 号、UP、发布时间记录在 `<日期>.metadata.json` 里。
- 转录片段缓存在 `<output_root>/work/<组名>/<日期>/chunks/`，用于断点续跑；想彻底重跑当天某组，加 `--force` 或删掉对应 work 目录。
