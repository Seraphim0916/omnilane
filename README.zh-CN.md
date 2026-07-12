<div align="center">

# omnilane

**一张路由表,四个执行框架通用。**

把每个子任务自动派给最强的模型——<br/>
**Claude Code · Codex · Grok Build · Antigravity**,直接用你已有的订阅。

[![ci](https://github.com/Seraphim0916/omnilane/actions/workflows/ci.yml/badge.svg)](https://github.com/Seraphim0916/omnilane/actions/workflows/ci.yml)
[![license](https://img.shields.io/github/license/Seraphim0916/omnilane)](LICENSE)
[![version](https://img.shields.io/github/v/tag/Seraphim0916/omnilane?label=version)](https://github.com/Seraphim0916/omnilane/tags)

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md)

</div>

---

一张路由表,四个执行框架通用。omnilane 让**任何**一个 agentic CLI——Claude Code、
OpenAI Codex、Grok Build、Google Antigravity——的主循环把子任务分类到通道(lane),
再自动把每条通道派发给该项工作最强的厂商 CLI,直接沿用你已有的订阅登录。

```
            ┌────────────── routing.yaml(一张表)───────────────┐
 主循环    ─┤ hardest-coding → Codex Sol      taste-final → Claude │
 (任一 CLI) │ bulk-mechanical → Codex Terra   long-context → Gemini│
            │ triage → Codex Luna             live-search → Grok   │
            └────────────── scripts/dispatch.sh ───────────────────┘
```

## 工作原理

- **`routing.yaml`** — 通道 → 厂商+模型+推理档位。一个文件,四个执行框架共用。
- **候选链** — 一条通道可以列多个候选(`codex … | claude … | off`),
  派发时自动采用本机**实际安装了**的第一个厂商 CLI。只订一、两家也能用同一张表。
- **`scripts/dispatch.sh <通道> "<任务>"`** — 查表后以无头方式调用对应厂商的 CLI。
- **`skills/omnilane/SKILL.md`** — 一份技能四个框架都能加载:
  先认出自己是哪个模型,自己通道的活自己干,其余派出去。

## 通道一览(默认值;实际生效值运行 `scripts/dispatch.sh --list` 查看)

| 通道 | 首选模型 | 用途 |
|---|---|---|
| hardest-coding | GPT-5.6 Sol (xhigh) | 最难的实现、深度调试、正确性攸关的修改 |
| bulk-mechanical | GPT-5.6 Terra (max) | 重构、迁移、测试、大面积扫描——机械耐力活 |
| triage | GPT-5.6 Luna (medium) | 高量初筛、第一轮过滤 |
| hard-judgment | GPT-5.6 Sol (max) | 架构仲裁、深度推理、第二意见 |
| taste-final | Claude Opus 4.8 | 对外文字、prompt 与文档打磨、风格终审 |
| ui-draft | GPT-5.6 Sol (xhigh) | 有设计规范/参考图时的 UI 出稿;开放式视觉品味交给 taste-final |
| long-context | Gemini 3.1 Pro (High) | 百万 token 长文整合——仅限分析,不派 agentic 长链 |
| fast-agentic | Gemini 3.5 Flash (High) | 快速多步骤 agentic 循环、多模态检查 |
| live-search | Grok 4.5 | 实时 X/网络搜索与社群脉络 |
| coding-overflow | Grok 4.5 | Codex 额度吃紧时的中量级编码溢流道;事实性声明须另行查证 |
| arbitrate | off(可选评审团) | 内置意见评审团,重大决定用——默认关闭,要用在 `routing.local.yaml` 打开;每评审每轮烧一次额度 |

## 安装

前置需求:想路由到的厂商 CLI(`codex`、`claude`、`grok`、`agy`)已登录且在
`PATH` 上——**有几家装几家就好**,缺的通道会自动降级。

最快:`./install.sh` — 自动检测本机的 CLI、接好技能、列出其余的插件安装命令、
打印这台机器的生效路由表,最后询问是否进入交互设置菜单(`--uninstall` 可逆)。
手动接线:

- **Claude Code**:以插件安装(附 `/route`、`/route-jobs` 命令),
  或把 `skills/omnilane` 放进 `~/.claude/skills/`。
- **Codex**:把 `skills/omnilane` 放进或链接到 `~/.codex/skills/`。
- **Grok Build**:`grok plugin install <本仓库路径> --trust`
- **Antigravity**:`agy plugin install <本仓库路径>`(先用
  `agy plugin validate` 检查)

## 自定义设置

三层,全部可选:

1. **交互菜单** — `scripts/configure.sh` 列出全部通道,让你逐条选
   厂商 → 模型 → 推理档位(有建议清单,也可自由输入未来的新模型名),
   写进 `~/.omnilane/routing.local.yaml`。`install.sh` 装完会主动询问。
2. **`~/.omnilane/routing.local.yaml`** — 手改覆盖文件,格式同 `routing.yaml`,
   本机优先。参考 `routing.local.yaml.example`。
3. **`~/.omnilane/local.sh`** — 机器专属的可执行文件路径、代理、认证包装;
   每个执行器都会加载,永不进版本控制。参考 `local.sh.example`。

随时检查结果:

```
scripts/dispatch.sh --list     # 生效表,标出候选链降级与关闭的通道
```

## 命令参考

```
dispatch.sh [--background] [--mode advise|work] [--workdir 目录]
            [--model M] [--effort E] 通道 "任务"    # "-" 表示从 stdin 读任务
dispatch.sh --list
jobs.sh list | status 作业ID | result 作业ID
configure.sh                                        # 交互通道菜单
```

退出码:`2` 用法错误(通道不存在/mode 拼错)、`3` 通道已关闭、
`4` 候选链里没有任何已安装的 CLI、`86` 拒绝嵌套派发、`87` 等锁超时;
其余直接透传工作端自己的退出码。

## 模式

- **advise(默认)** — 只读工作端。Codex 跑只读沙箱;Claude 只给
  Read/Glob/Grep;Grok 跑 plan 模式。适合审查、提问、第二意见。
- **work** — 允许改文件,仅限你指定的 `--workdir`。Codex 给
  workspace-write 沙箱;Claude 自动接受编辑;Gemini 跑 accept-edits 模式。

## 内置安全机制

- **禁止嵌套派发** — 工作端不得再往外派(`OMNILANE_DEPTH` 守卫,退出码 86),
  杜绝 AI 叫 AI 的额度连环烧。
- **Codex 排队锁** — 同一目标目录的 codex 派发自动串行化(锁以规范化后的
  workdir 为键);崩溃残留的锁以所有者 PID 检测后安全接管。
- **看门狗** — 每个工作端跑在 `timeout`/`gtimeout` 之下,两者皆无时退到
  perl-alarm 后备(原生 macOS 就是这种情况),卡死的 CLI 不会挂一整晚
  (`OMNILANE_TIMEOUT`,默认 600 秒)。
- **后台作业生命周期** — `--background` 的工作端跑在自己的 process group,
  调用端退出也不受影响;被杀会落盘退出码,`jobs.sh status` 会报 `dead`
  而不是永远显示 `running`。
- **任务载荷上限** — 过大的任务文本自动头尾截断,防止撑爆工作端上下文。

## 默认值与数据来源

默认通道配置依据 Artificial Analysis 2026-07 快照(已对 AA 站上原始记录与
各厂官方定价页交叉核对)加上公开对比评测;这些是意见不是定律——
设置菜单和 `routing.local.yaml` 就是让你不同意用的。评审团(arbitrate)
默认关闭;要用就在 `routing.local.yaml` 写
`arbitrate: vote codex,claude,grok -`(从四家里任选 1-4 个评审),
或改用 `exec` 厂商指向你自己的多模型审查闸脚本。

## 已知限制

- **Antigravity 的 print 模式工具调用在现行 CLI 版本不稳定**(可能被拒或
  返回无效参数)。long-context 通道的设计本来就是"把内容贴进任务"的长文
  整合,不受影响;要*读取仓库*的咨询请用 claude/codex 候选。
- **Grok 没有推理档位开关**;effort 字段仅为接口一致而保留,实际忽略。
- Codex 的 work 模式在非 git 目录曾出现卡死;在 git 工作目录(正常情况)
  使用,查明前先避开非 git 目录。

## 状态

早期但审过:shell 核心经外部模型审查(11 项发现全修)加对抗式验证。
执行器接口已稳定;Grok/Antigravity 命令壳行为可能随 CLI 版本变动。
欢迎提交 issue 与 PR。
