# Golden-money-explosion

B 站视频本地转写（SenseVoice）。单条 BV 用 `bili-asr/bili_asr.py`；按"视频组"每日调度用 `bili-asr/bili_daily.py`。

## 视频组日调度

同一个视频往往有多个 UP 同时上传，每天只需要处理发布最早的那一条。

```bash
cd ~/bili-asr            # 或仓库实际位置
cp groups.example.json groups.json
vi groups.json           # 填组名、UP 的 UID、标题正则
python bili_daily.py --dry-run   # 只看今天会选中哪些视频，不下载不转写
python bili_daily.py             # 正式跑
```

`groups.json` 关键字段：

| 字段 | 作用 |
| --- | --- |
| `output_root` | 结果根目录，展开 `~`；产物落在 `<output_root>/results/<组名>/<日期>.{txt,srt,metadata.json}`，日志在 `<output_root>/logs/<日期>.log` |
| `groups[].name` | 视频组名，直接用作目录名（`/ \ : * ? " < > |` 会替换成 `_`） |
| `groups[].title_pattern` | Python 正则，标题命中才算这个组的新视频；不写则全部算 |
| `groups[].ups[]` | 组内 UP，`uid` 是 `space.bilibili.com/<uid>` 里的数字，`alias` 只用于日志 |
| `recent_limit` | 每个 UP 只看最近 N 条投稿（默认 20，可按组覆盖）；当天投稿多于这个数就调大 |
| `cookie_file` | B 站空间列表和下载常被风控（HTTP 412），需要时填你导出的 Netscape cookies 路径 |

选片规则：对组内每个 UP 拉投稿扁平列表 → 保留**发布日期等于今天**且标题命中正则的条目 → 全部候选里取**发布时间最早**的一条处理；没有任何 UP 命中则跳过该组。

常用参数：`--group 组名`（只跑某组，可重复）、`--date 2026-10-01`（补跑某天）、`--force`（今日已处理也重跑，并忽略转录片段缓存）、`--dry-run`、`--cookies-file`、`--output`。

同一天重复运行是安全的：日志里已有 `OK` 记录且结果文件存在时，该组会被 `SKIP`；每组失败只影响该组，其余继续，退出码非 0。
