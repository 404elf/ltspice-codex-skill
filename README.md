# LTspice Codex Skill v2

这是一个独立、可移植的 Codex Skill，用于根据自然语言生成 LTspice 网表、执行仿真、校验 RAW/LOG、测量指标，并用 Weave 将最终 NET 转换为 LTspice ASC 原理图。

本仓库不包含 LTspice 安装包、专有模型或任何旧版 LTSPICE-AI 文件。

## 快速开始

1. 单独安装 Analog Devices 发布的 LTspice。
2. 将本仓库安装或交给 Codex，然后直接发送下面这一句话：

   > Install and configure this LTspice simulation skill on this machine.

3. 配置完成后，直接描述要设计和仿真的电路，例如：

   > Design a 1 kHz Butterworth low-pass filter and simulate it with LTspice.

Codex 会根据本仓库中的 `bootstrap.py` 自动完成配置。初始化脚本会自动：

- 检测本机 LTspice；
- 创建 Skill 专用 `.venv` 并安装 `requirements.txt`；
- 获取 Weave CLI 并安装其 npm 依赖；
- 写入本机配置文件 `.ltspice-codex-config.json`；
- 执行一次全新的 RC 冒烟测试。

提示词可以使用英文。安装或配置时不需要把 PowerShell 当前目录切换到某个固定位置；如果手动执行脚本，则需要在仓库根目录运行。

手动安装或排错：

```powershell
py -3 bootstrap.py
```

只检查已有配置，不运行冒烟测试：

```powershell
py -3 bootstrap.py --check-only
```

## 使用

安装完成后，可以使用 Skill 名称调用，也可以直接用自然语言描述：

```text
$ltspice-sim-v2
设计并仿真一个截止频率为 1 kHz 的 RC 低通滤波器。
```

Skill 支持 `AUTO`、`QUICK`、`STANDARD`、`STRICT` 和 `BATCH` 模式。`STANDARD`/`STRICT` 会把 AC、瞬态、DC、角落或扫参等重复工作交给确定性的 validation suite 执行，减少模型与工具之间的重复往返；模型只负责规格、拓扑、初始设计、工程判断和失败后的针对性修改。

validation suite 的核心调用形式为：

```powershell
<configured-python> scripts/run_validation_suite.py `
  --net <circuit.net> `
  --spec <validation-spec.json> `
  --ltspice <configured-ltspice.exe> `
  --markdown <output-directory>/validation_summary.md
```

它会为每个分析和确定性 corner 执行一次 LTspice，要求每次都产生新的 RAW/LOG，解析 LOG 错误，并只读取规格中请求的 RAW traces。结果集中写入 `validation_summary.json`；其中包含 PASS/FAIL、测量值、失败 corner、日志状态、LTspice 调用次数、实际工具耗时和产物路径。缓存只允许复用未改变 NET/spec 的 preflight 状态，不能把旧 RAW/LOG 当成新仿真。

最终 NET 通过验证后才调用 Weave。Weave round-trip 必须返回 `MATCH`；`STRICT` 还会运行 Weave 生成的 ASC。ASC 校验会在临时工作目录中运行，避免 LTspice 生成的同名 `.net` 覆盖源 NET，附加结果使用 `<stem>-asc.raw` 和 `<stem>-asc.log`。

## 输出目录和文件

默认输出根目录是 Skill 目录下的 `outputs/`，每个电路使用独立子目录：

```text
<skill-directory>/outputs/<circuit-name>/
```

成功运行结束时，Codex 必须报告以下路径：

- `<circuit-name>.net`：提交给 LTspice 的最终 SPICE 网表，也是电路的 source of truth；
- `<circuit-name>.asc`：按需由 Weave 从同一个最终 NET 生成；
- `<circuit-name>.raw`：当前 NET 仿真生成的波形数据；
- `<circuit-name>.log`：当前 NET 仿真的 LTspice 日志；
- `*weave-verification*.txt`：Weave round-trip 结果，只有包含 `MATCH` 才算通过；
- `validation_summary.json` / `validation_summary.md`：确定性验证摘要；
- `*.png`：按请求生成的瞬态或 AC 图；
- `*-asc.raw` / `*-asc.log`：STRICT 模式下生成的 ASC 附加 LTspice 校验结果。

普通参数修改直接更新已有 NET，并替换对应 RAW/LOG，再从该 NET 替换 ASC；除非明确要求保留历史，否则不创建版本化目录。BATCH 只为选中的最终候选生成 ASC。

## 验证边界

- LTspice 退出码为 0 不能单独证明仿真成功；必须有新 RAW、新 LOG，且 LOG 无 parser/simulation fatal error；
- Weave `MATCH` 只证明原理图连通性与 NET 等价，不代表电气指标自动正确；
- `STRICT` 的成功条件是：最终 NET 仿真通过、RAW/LOG 校验通过、Weave 返回 `MATCH`、生成的 ASC 也通过 LTspice；
- Skill 不手工猜测 ASC 坐标，NET-to-ASC 转换始终由 Weave 完成。

## 许可证

本项目代码采用 GPL-3.0-or-later。Weave、PyLTSpice/spicelib、elkjs 和 LTspice 的许可与归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
