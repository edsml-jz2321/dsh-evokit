---
description: 通过配对实验和 DSH 插件开展可测量的自进化。
---

# evokit

evokit 为 AI 系统提供可测量的自进化循环：对可编辑产物进行版本管理，记录配对实验，并依据实测噪声、成本和延迟决定是否接受变更。可作为 DeepSeek Harness（DSH）插件使用，也可通过 Python SDK、CLI 和 MCP 服务接入。

你的系统只需提供三部分：**可编辑文件的 glob 模式**、**稳定的任务集**、**执行任务并评分的运行器**。提示词、技能、代码和配置都可以编辑；任务来源和评分器保持冻结。

循环为 **诊断 → 提案 → 配对实验 → 噪声感知门禁 → 留出集确认，操作工作流自动回退**。改进者只能读取诊断和选择证据。独立操作方接受候选后检查密封确认集的汇总结果；确认失败时调用 `ledger_revert`。单独调用 `confirm_adoption` 不会移动当前版本指针。可选最终集仅在人类批准后开启一次，不用于选择变更。

## 快速开始

需要 Python 3.11+、uv、DSH `0.2.2-rc.1` 及其 pnpm 依赖。已验证的本地平台为 Windows；Linux/macOS 运行时集成尚未验证。发布后，从 [Releases](https://github.com/edsml-jz2321/dsh-evokit/releases) 下载 wheel、插件包和 `SHA256SUMS`，核对 SHA-256 后再安装。

```console
uv tool install ./dsh_evokit-0.1.5-py3-none-any.whl
dsh plugin --profile evokit add ./dsh-evokit-0.1.5.tgz
dsh plugin --profile evokit list
```

将 `EVOKIT_MCP_COMMAND` 设为已安装的 `evokit` 可执行文件绝对路径，`EVOKIT_WORKSPACE` 设为场景目录，`EVOKIT_SKILL_ROOT` 设为已安装插件的 `skills` 目录。隔离配置与完整 PowerShell 命令见 [DSH 指南](integrations/dsh/README.md)。

在自己的 DSH 配置中选择改进者模型，使用**你自己的服务商密钥**和服务商支持的模型名。密钥应保存在 DSH 本地凭据设置或进程环境变量中，禁止提交到仓库。可选的[中继](integrations/relay/README.md)支持文件密钥和记账；使用前须替换示例端点、模型身份和示意价格。离线示例不需要密钥，也不附带密钥。安装和列出插件不会请求模型；启动进化可能产生服务商费用。

## 最小示例

在源码目录执行，Node.js 命令示例不需要模型或密钥：

```console
uv sync --frozen --group dev
uv run python examples/scripted_improver.py command-echo
```

其 `scenario.yaml` 提供上述三部分：

```yaml
editable: [prefix.txt]
tasks: tasks.json
command: [node, runner.mjs]
```

运行器读取 `EVOKIT_TASK_FILE`，在 `EVOKIT_VERSION_DIR` 上执行，并向 `EVOKIT_RESULT_FILE` 写入结果和成本。脚本示例使用临时副本。接入自己的系统时，先阅读[接入指南](docs/onboarding.md)，再使用附带的 `onboard`、`evolve` 和 `meta` 技能。

## 安全与限制

实验前须设定预算、锚点、噪声策略和停止规则。重复均值门禁使用 threshold/√R；多数投票保留单次阈值。确认集有使用次数限制，不能视为未接触的最终基准。一个公开 BFCL 试验的留出集准确数经确认从 **41 提升到 47，共 50 题**；这一小规模示例不代表普遍收益。

场景会执行可信代码。逐任务目录并非安全沙箱。请用操作系统权限保护密封任务和记录；若改进者能直接读取文件，MCP 过滤无法保密。审查提案中的任务字面量和信息泄漏。中继保守记录服务商报告的用量，不能保证账单完全准确或阻止所有外部费用。

另见[概念](docs/concepts.md)、[工具](docs/tools.md)、[存储](docs/storage.md)和[贡献指南](CONTRIBUTING.md)。项目采用 [MIT 许可证](LICENSE)，Copyright (c) 2026 quei。
