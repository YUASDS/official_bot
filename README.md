# official_bot

基于 NoneBot2 的 QQ 克苏鲁冒险 Bot（StoryTeller 插件）。

## 运行环境

- Python 3.10+
- 推荐 conda 环境：`official_bot`（`E:\anaconda3\envs\official_bot\python.exe`）
- 依赖：`pip install -r requirements.txt`，然后 `playwright install chromium`
- 配置：复制/编辑 `.env.dev` 或服务器 `.env.prod`，不要提交生产 token

## 启动

```bash
python main.py
```

Windows 服务器可使用 `run.bat`（会自动拉取 `origin` 更新并重启）。`run.bat` 中的
`PYTHON_EXE` 指向官方 conda 环境，请按部署机实际路径调整。

## 雾鸣镇失船案

门扉与二周目各达成过一个死亡、疯狂以外的结局后，在新调查员第 1 天使用 `/今日冒险`，选择「接听未结的呼救」。二周目重游门扉取得同名结局也计入。奇数冒险日在三十一年前调查，偶数冒险日回到今天。可点按钮或用 `/行动 选项` 作答，`/航海日志` 随时回看已发现的线索。

## 测试

```bash
pytest test -q
```

测试使用 `test/conftest.py` 设置的临时 SQLite 数据库与临时每日状态目录，不会写入生产
`plugins/StoryTeller/inv.db`、`database/userData.db` 或 `util/day/`。

`test/balance_sim/` 是历史平衡模拟的冻结快照（其中 `sim/` 不会自动同步生产 `src/`），
不属于回归测试；不要把它的旧结果直接当作线上结论。

## 目录

- `plugins/StoryTeller/`：插件主体（`data/` 配置、`src/models`、`src/services`、`src/plugins`）
- `database/`：用户账户库与数据库访问
- `util/`：日志、每日状态、浏览器渲染等基础工具
- `test/`：单元/回归测试（测试源码纳入版本管理，生成物不入库）
