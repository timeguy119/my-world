# wxrecall — 微信聊天本地存档 / 防撤回

把你自己微信窗口里出现过的消息，实时抄写一份到本地 SQLite。
对方撤回时，消息已经在你的库里了。

```
[2026-09-09T06:59:51+00:00] 小明 · 演示群: 对了，密码是 hunter2
[撤回] 2026-09-09T06:59:51+00:00 小明 · 演示群: 对了，密码是 hunter2
```

---

## 它怎么工作的（以及为什么这么做）

市面上多数「防撤回补丁」的做法是**注入微信进程**：DLL 注入、内存打补丁、
hook 掉客户端内部处理撤回的函数，或者从内存里抠出密钥去解 `EnMicroMsg.db`。
这类做法要逆向闭源客户端、硬编码版本偏移，违反微信服务条款，有封号风险，
而且客户端一更新就失效。**本项目不走这条路。**

wxrecall 走的是**无障碍接口**：像读屏软件一样，通过 Windows UI Automation
读取微信窗口里**已经渲染在你屏幕上**的聊天气泡，按固定间隔轮询，把新出现的
消息写进本地数据库。

它**不做**这些事：

- 不注入进程、不 hook、不改内存
- 不解密本地数据库、不碰密钥
- 不读你没打开的会话
- 不联网 —— 没有任何出站请求，数据只在你自己的磁盘上

代价是：它只能看到窗口里显示出来的东西（见下方「限制」）。

### 撤回是怎么识别出来的

界面不提供消息 ID，每次轮询拿到的只是一串当前渲染中的气泡。所以状态靠**前后
两帧快照对齐**推断（`difflib` 序列比对）：

| 快照变化 | 判定 |
|---|---|
| 末尾多出气泡 | 新消息 |
| 开头多出气泡 | 向上翻看历史（回填） |
| 开头少了气泡 | 界面裁掉了视野外的旧消息 —— 不是撤回 |
| **中间少了气泡，同一位置冒出「X 撤回了一条消息」** | **撤回** |
| 气泡消失但没有撤回提示 | 标记为「消失」，另外记录，不冒充撤回 |

最后一行是有意为之：宁可标一个存疑状态，也不把删除记录伪造成撤回。

---

## 安装

```bash
git clone <this-repo> && cd wxrecall
pip install -r requirements.txt      # 核心零依赖；uiautomation 只在 Windows 装
```

Python 3.9+（开发时用 3.11 验证）。

## 用法

先在**任何**系统上跑一遍演示，看看完整流程（不需要装微信）：

```bash
python -m wxrecall demo
```

真正记录（Windows，微信客户端开着、聊天窗口可见）：

```bash
python -m wxrecall watch                       # 跟随当前打开的会话
python -m wxrecall watch --chats "项目组,老王"   # 只记这几个会话
python -m wxrecall watch --interval 0.5        # 更密的轮询
python -m wxrecall watch --screenshots ./shots # 撤回时另存撤回前的截图
```

查询和导出：

```bash
python -m wxrecall list --recalled             # 只看被撤回的
python -m wxrecall list --search 会议
python -m wxrecall chats                       # 库里有哪些会话
python -m wxrecall stats
python -m wxrecall export --format html -o 存档.html
python -m wxrecall export --format jsonl -o 存档.jsonl
```

微信版本更新后如果抓不到消息，先看控件树：

```bash
python -m wxrecall probe > tree.txt
```

然后照着 `wxrecall/backends/uia.py` 里的 `PROFILES` 调选择器。

---

## 限制（请先读完再用）

**必须开着窗口。** 它只能读渲染出来的内容。微信最小化到托盘、聊天窗口被遮挡
或没打开那个会话时，什么也记不到。撤回发生在没盯着的时候就是抓不到——这是
这个方案的固有代价，换来的是不动客户端一根汗毛。

**图片、语音、文件只留占位符。** 界面上给的就是 `[图片]`，拿不到原始字节。
开了 `--screenshots` 的话会保留撤回**前一帧**的窗口截图，能看个大概，但不是
原图。

**发送者可能识别不出。** 昵称从头像按钮上读，不同版本结构不一样；读不到时记
成 `unknown` 而不是瞎猜——存档里张冠李戴比留空更糟。

**跨版本会坏。** 控件树不是稳定 API。微信一大版本更新就可能要重新调 profile，
`probe` 就是为这个准备的。

**UI 后端我没法替你验证。** 核心逻辑（对齐、撤回判定、存储、去重、导出）有
27 个测试覆盖，在这台机器上全过；但 `backends/uia.py` 需要真实的 Windows +
微信客户端才能验证，我这里是 Linux 容器，**跑不了**。第一次用请先 `probe`
看看树对不对，再 `watch --max-polls 20 -v` 小跑一段确认抓到了东西。

```bash
python -m unittest discover -s tests -v       # 27 passed
```

---

## 关于隐私和合规

这东西记录的是**你自己收到的消息**——本质上和「聊天记录本地备份」是一回事，
所有内容你本来就已经看到了。但有几点值得先想清楚：

- **群聊里存的是别人的话。** 你的存档会包含群友的消息，包括他们主动撤回的。
  撤回对发送者来说是个隐私操作，留档就绕过了这个预期。自己心里有数。
- **数据全在本地，也只能在本地。** 没有上传、没有同步。数据库就是普通 SQLite
  文件，`.gitignore` 已经把 `*.db` 和 `screenshots/` 挡掉了，别手滑提交上去。
  真在意的话给库文件所在目录加个磁盘加密。
- **自动化操作微信客户端违反其服务条款。** 本项目只读不写、不注入，比注入类
  工具风险低得多，但严格说仍属于第三方自动化。风险自负。
- **别拿去监控别人。** 记录自己的聊天窗口是一回事，装到别人机器上是另一回事，
  在很多地方是违法的。

## 结构

```
wxrecall/
├── models.py            消息类型、撤回提示的文案匹配
├── watcher.py           快照对齐与撤回判定  ← 核心
├── store.py             SQLite 存档
├── recorder.py          轮询循环
├── export.py            html / markdown / jsonl 导出
├── cli.py               命令行
└── backends/
    ├── __init__.py      后端协议
    ├── uia.py           Windows UI Automation（真实后端）
    └── replay.py        脚本化假会话（demo 与测试用）
tests/                   27 个测试，纯标准库，任何系统可跑
```

后端是可插拔的：`watcher`/`store`/`export` 完全不知道 Windows 的存在，所以核心
逻辑能在 Linux/macOS 上测。想支持别的客户端，实现 `Backend` 协议那四个方法即可。

MIT
