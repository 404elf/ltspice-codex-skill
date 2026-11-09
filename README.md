# LTspice Codex Skill v2

**用自然语言设计、仿真和验证电路，得到可编辑的 LTspice 原理图。**

告诉 Codex 电路用途、供电和目标指标，它会生成 SPICE 网表，调用本机 LTspice 验证，再通过 Weave 转成 `.asc` 原理图。交付内容包括电路文件、仿真数据和验证报告；你可以继续在 LTspice 中打开、查看和修改。

[快速开始](#快速开始) · [使用示例](#使用示例) · [交付文件](#交付文件) · [常见问题](#常见问题) · [进阶文档](#进阶文档)

## 能做什么

- **设计电路**：从需求、传递函数或电路图片出发，生成可仿真的电路。
- **验证指标**：检查截止频率、增益、偏置、瞬态电压等，报告测量值与 PASS / FAIL。
- **修改与比较**：调整已有电路，检查元件容差，比较多个候选方案。
- **交付原理图**：生成可编辑的 ASC，核对连通性，检查元件重叠，并对最终 ASC 再运行一次仿真。

```text
电路需求 → NET 网表 → LTspice 仿真与指标验证 → Weave → ASC 原理图与仿真检查
```

## 快速开始

### 1. 准备环境

当前安装与运行流程面向 **Windows**。你需要能够执行本机命令的 Codex，以及以下工具：

| 工具 | 用途 | 安装入口 |
| --- | --- | --- |
| LTspice | 电路仿真与原理图编辑 | [Analog Devices 官方下载](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html) |
| Python 3 | 仿真、数据读取和验证；已在 Python 3.12 验证 | [Python for Windows](https://www.python.org/downloads/windows/) |
| Node.js 18+，含 npm | 运行 Weave 原理图转换 | [Node.js 下载](https://nodejs.org/en/download) |

已有这些工具时可以直接复用。首次配置需要联网获取依赖；本仓库不包含 LTspice 安装包或专有模型。

### 2. 让 Codex 安装并配置

把下面这段话复制给 Codex：

```text
请从 https://github.com/404elf/ltspice-codex-skill 的最新 main 分支安装并配置
ltspice-sim-v2 Skill。先检查本机已有的 LTspice、Python、Node.js 和 npm，
复用有效配置，并按仓库说明运行 bootstrap.py 完成初始化和验证。
保留已有 LTspice 安装、用户配置和电路输出。
```

初始化会创建 Skill 专用 Python 环境，安装依赖，获取固定版本的 Weave，写入本机配置，并运行一个新的 RC 测试电路。成功后，Codex 会报告配置文件和输出目录的位置。

<details>
<summary>手动配置（可选）</summary>

将仓库 `main` 分支的内容安装到本机 Codex 的 Skill 目录后，在仓库根目录运行：

```powershell
py -3 .\bootstrap.py
```

查看已有配置和工具路径，不执行仿真：

```powershell
py -3 .\bootstrap.py --check-only
```

若系统没有 `py` 命令，可用已安装的 Python 3 解释器执行同一脚本。自定义 LTspice 路径和排错方法见 [配置与排错](references/operations.md)。

</details>

### 3. 试做第一个电路

```text
$ltspice-sim-v2
设计并仿真一个 1 kHz RC 低通滤波器，截止频率误差不超过 2%。
输入为 AC 1 V，输出按高阻测量，扫频范围为 10 Hz～100 kHz。
给我频响图、实测截止频率、可编辑的 ASC 原理图和验证报告。
```

成功交付时，你会看到实测指标、是否满足要求，以及 `.asc`、网表和报告的文件链接。打开 `.asc` 即可继续在 LTspice 中使用。

中文和英文都可以。安装后也可以直接描述电路需求，不必每次写 Skill 名称。

## 使用示例

描述时尽量给出 **供电、输入信号、负载、目标指标和允许误差**。需要特定器件时，请同时说明型号或提供可读取的模型文件。

### 使用真实运放模型

```text
使用 LT1001 设计一个同相放大器，供电为 ±15 V，负载为 100 kΩ。
要求 10 Hz 小信号增益为 11 倍，误差不超过 1%。
使用可读取的 LT1001 模型，给出仿真测量值和 ASC 原理图。
```

### 检查元件容差

```text
继续上面的 RC 低通，检查 R ±5%、C ±10% 的容差角落。
标称截止频率仍要求在 1 kHz 的 ±2% 内，容差角落允许 ±20%。
列出最差角落和对应截止频率。
```

### 修改已有电路

```text
把这个网表中的 R1 改为 20 kΩ，保留其余电路和原验收要求。
先保留可恢复副本，再重新验证并更新 ASC。
告诉我哪些指标通过、哪些失败。
```

也可以要求比较多个候选方案。请明确候选范围和评价指标；最终只为选中的方案生成 ASC。

## 交付文件

每个电路有独立目录。顶层 `.asc` 是日常打开的原理图，其余文件集中在支持目录：

```text
<output-root>/<circuit-name>/
├── <circuit-name>.asc
└── <circuit-name>_files/
    ├── <circuit-name>.net
    ├── validation_summary.json / validation_summary.md
    ├── *.raw / *.log
    ├── *weave-verification*.txt / *.layout.json
    ├── simulation_evidence.json
    └── 模型依赖与按需生成的图表
```

| 想做什么 | 看哪个文件 |
| --- | --- |
| 在 LTspice 中打开或编辑原理图 | 顶层 `.asc` |
| 查看最终电路定义 | 支持目录中的 `.net`；后续验证以它为准 |
| 查看指标、通过情况和失败原因 | `validation_summary.md` 或 `.json` |
| 查看波形或诊断仿真错误 | `.raw` 波形数据、`.log` 日志 |
| 查看原理图连通性与仿真检查 | `*weave-verification*.txt` |
| 查看布局模式、碰撞与标签移动 | `*weave-verification*.layout.json` |

默认输出位置由本机 `.ltspice-codex-config.json` 中的 `output_root` 决定；首次初始化默认为 Skill 目录下的 `outputs/`。

**保留整个电路目录。** 支持目录可能包含 ASC 需要的模型文件。手动点击 LTspice 的 Run 后，顶层可能出现同名 `.net`、`.raw`、`.log`；这些是手动运行的附加文件，不能替代支持目录中的最终网表和验证记录。

## 常见问题

**已经安装过，怎么更新？**

把下面这段话发给 Codex，无需重新安装 LTspice：

```text
请从 https://github.com/404elf/ltspice-codex-skill 的最新 main
更新已安装的 ltspice-sim-v2 Skill。保留本机配置、LTspice 和 Weave 安装、
以及已有电路输出。更新后核对实际安装文件并验证。
```

**为什么仿真通过了，还要检查 ASC？**

网表验证检查电气行为；原理图转换还需核对连通性、模型绑定和布局。最终 ASC 必须通过 Weave `MATCH`、布局门禁和一次 LTspice 仿真检查，才算完成交付。

**还会出现元件或文字重叠吗？**

转换器会尝试有限组布局，拒绝已知元件边界框相交或无法检查的符号，并让拥挤节点标签沿原有导线避让。文字尺寸仍是估计值，需要仔细阅读的图应再检查实际显示；布局 PASS 不能保证所有字体、缩放下都无视觉遮挡。

**密集电路的数值太长，能只显示元件编号吗？**

可以。元件较多、数值较长的 RLC 电路会自动保留图上的编号，把完整数值放进下方 `Parameters` 区；简单电路仍就近显示数值。也可以直接说“图上只显示元件编号，数值放到下方参数区”。每个元件使用独立参数，保留原始精度，并在参数化后完成仿真验证。电源信息和芯片型号继续显示，数值可在 `.param` 命令中修改。

**会优先使用常见的元件参数吗？**

会。设计新电路时，默认优先选择能满足原指标的常见 R/L/C 标称值，并用最终选值完成仿真。你明确给出的数值、已有电路的保留要求或理想电路综合要求优先；显示参数区时也不会偷偷取整。如果常见值无法满足指标，会说明偏差和可选方案。需要实际搭建时，请说明用途及器件约束，选型还需检查公差、额定值和寄生参数；常见标称值不等于已确认有货。

**只有 PASS，没有定量指标，代表设计达标了吗？**

PASS 只证明已声明的检查通过。没有填写定量验收要求时，只能证明仿真执行通过；因此请在需求中写清指标和误差范围。

**找不到工具、模型或仿真失败怎么办？**

把失败信息和当前文件交给 Codex。工具路径问题先检查本机配置；模型需要可读取的依赖；电气指标失败则查看测量值和日志。详细处理方法见 [配置与排错](references/operations.md)。

## 进阶文档

普通使用只需描述需求；以下内容供需要控制验证计划或维护脚本时阅读。

| 文档 | 内容 |
| --- | --- |
| [SKILL.md](SKILL.md) | Codex 的完整执行与交付流程 |
| [验证计划与可运行示例](references/validation-intent.md) | 分析指令、测量字段、容差、标称与角落的验收要求 |
| [电路检查要点](references/circuit-checks.md) | 常见标称值与实物选型、滤波器负载、运放反馈、电桥、开关电源等设计检查 |
| [配置与排错](references/operations.md) | 本机配置、命令行入口、依赖和失败处理 |

`AUTO`、`QUICK`、`STANDARD`、`STRICT`、`BATCH` 是验证计划标签。实际覆盖由明确填写的分析、指标和容差决定；它们不会自动添加检查或生成候选电路。准确匹配的仿真数据可以复用，报告会区分新仿真与复用结果。具体模型的来源和适用性仍需确认；`real_device_required` 只拦截已知通用占位模型。

## 许可证与致谢

本项目代码采用 [GPL-3.0-or-later](LICENSE)。原理图转换使用 [Weave](https://github.com/senolgulgonul/weave)，仿真工具依赖 PyLTSpice / spicelib；第三方许可与归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。LTspice 由 Analog Devices 单独提供。
