# LTspice Codex Skill

这是一个独立、可移植的 Codex Skill，用于从自然语言生成 LTspice 电路、执行仿真、校验 RAW/LOG，并使用 Weave 将实际仿真的 NET 转换为可编辑的 ASC 原理图。

工作流程：

```text
自然语言 → Codex → SPICE NET → LTspice 仿真 → RAW/LOG 校验 → Weave → 可编辑 ASC 原理图
```

本仓库只包含 Skill 指令和确定性的辅助脚本，不包含 LTspice、专有运放模型或任何 LTspice 安装文件。

## 快速开始

1. 单独安装 Analog Devices 发布的 LTspice。
2. 将本仓库交给 Codex，并使用下面的提示词：

   > Install and configure this LTspice simulation skill on this machine.

3. 让 Codex 设计并仿真电路，例如：

   > Design a 1 kHz Butterworth low-pass filter and simulate it with LTspice.

提示词可以使用英文。初始化脚本会检测 LTspice，创建隔离的 Python 环境，安装固定版本的 Python 依赖，从上游获取 Weave CLI，安装 Weave 的 npm 依赖，执行一次全新的 RC 冒烟测试，并将本机路径保存到被忽略的配置文件中。

## 校验内容

- 不会只根据 LTspice 的退出码判断仿真成功。
- 每次运行都要求生成新的 RAW，并要求新的 LOG 不包含致命、解析或仿真错误。
- Weave 使用实际成功仿真的同一个 NET 生成 ASC，并执行 round-trip connectivity verification。
- 只有 Weave 返回 `MATCH` 才报告连通性验证通过；`MATCH` 不代表电气设计或工程指标一定正确。
- STRICT 模式还会使用 LTspice 再运行一次生成的 ASC。

Skill 支持 `AUTO`、`QUICK`、`STANDARD`、`STRICT` 和 `BATCH` 模式。NET 是电路的事实来源；普通参数修改会更新现有 NET 并替换对应结果，BATCH 只为选中的最终候选生成 ASC。

## 手动安装和排错

在本仓库目录的 PowerShell 中运行：

```powershell
py -3 bootstrap.py
```

初始化脚本会在 README 所在目录写入 `.ltspice-codex-config.json`；该文件是本机配置，已被 Git 忽略。只检查环境而不运行电路时，可以使用：

```powershell
py -3 bootstrap.py --check-only
```

辅助脚本位于 `scripts/`。请使用配置文件中记录的 Python、LTspice 和 Weave 路径，不要将某台机器的路径写入 Skill 或电路文件。

当前初始化脚本支持 Windows。如果未检测到 LTspice，它会停止并提示用户安装，而不会下载或重新分发 LTspice。

## 致谢与许可证

本项目使用 Weave 进行 NET-to-ASC 转换和连通性验证，使用 PyLTSpice/spicelib（可选）解析 RAW，并通过 Weave 的 npm 包使用 elkjs。LTspice 由 Analog Devices 单独提供，本仓库不包含 LTspice。上游项目和许可证说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本项目代码采用 GPL-3.0-or-later，详见 [LICENSE](LICENSE)。
