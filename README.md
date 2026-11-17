# Script Timestamp Aligner

将具有**段落和句子结构**的原文 JSON，与 **WhisperX 逐词时间戳**对齐，生成精简的 **Script JSON v1.0** 和独立的异常报告。

- **原文是文本权威来源**：保留原始段落、句子、大小写和标点；识别结果不改写原文。
- **WhisperX 是时间来源**：时间单位为相对于原始音频起点的秒，开头未纳入正文的语音不会导致时间整体平移。
- **同步单元不一定是单词**：例如原文 `it is` 对应识别的 `it's` 时，输出一个 `timed_unit`，共享同一时间区间。
- **不编造时间戳**：无法可靠对齐时使用 `[文本, null, null]` 并报告。
- **零运行时第三方依赖**：要求 Python 3.9+，可在 Windows、macOS、Linux 上运行。

## 项目结构

```text
.
├── README.md                     # 使用说明
├── SKILL.md                      # ChatGPT/Agent Skill 定义
├── scripts/
│   └── align.py                  # 转换、对齐、校验、报告生成
├── references/
│   └── script.schema.json        # Script JSON v1.0 Schema
├── tests/
│   ├── test_align.py             # 18 项 unittest 回归测试
│   └── fixtures/                 # 可公开的原创合成测试数据
├── docs/
│   └── validation.md             # 验收范围及限制
└── .github/workflows/
    └── tests.yml                 # 自动运行测试
```

## 快速使用

从仓库根目录运行（PowerShell 示例）：

```powershell
python scripts/align.py `
  --source "C:\path\original.json" `
  --whisperx "C:\path\whisperx.json" `
  --output "C:\path\script.json" `
  --report "C:\path\alignment-report.json"
```

Linux/macOS 请用 `\` 续行，或把命令写在一行。输入支持原文 `paragraphs[].paragraph[].{sid,text}`，以及 WhisperX 的 `word_segments[]`（优先）或 `segments[].words`。

### Script JSON v1.0

```json
{
  "title": "",
  "paragraphs": [
    [
      {
        "text": "We do not know why it is so.",
        "timed_units": [
          ["We", 12.0, 12.2],
          ["do not", 12.3, 12.6],
          ["know", 12.7, 12.9],
          ["why", 13.0, 13.2],
          ["it is", 13.3, 13.6],
          ["so", 13.7, 14.0]
        ]
      }
    ]
  ]
}
```

段落是句子数组；句子仅包含 `text` 和 `timed_units`；每个同步单元是 `[原文显示文本, start, end]` 三元数组。`sid` 只用于源数据诊断，不保存在最终 Script JSON 中。完整约束见 [`references/script.schema.json`](references/script.schema.json)。

### 退出码与异常报告

| 退出码 | 报告状态 | 说明 |
|---|---|---|
| `0` | `passed` | 结构及自动对齐校验通过，无需复核事件 |
| `2` | `needs_review` | 已生成文件，但存在待人工确认的文字或时间映射 |
| `1` | `failed` | 失败；不能把旧的输出文件当成本轮结果 |

`needs_review` 不代表文件无法读取，也不表示已与实际音频核听一致。请结合报告定位对应位置，必要时核听原始音频。

## 测试

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

`tests/fixtures/` 中提供合成的可公开测试数据和预期输出；运行时无须访问个人文件路径。CI 在推送和 Pull Request 时自动执行。

## 数据与版权

仓库只包含转换工具、规范与原创合成样本。此前验收使用的完整文章、WhisperX 原始数据、完整 `script.json` 以及人工审核报告均**不提交到公开仓库**，以避免分发可能受版权保护的文章内容。它们仍可在本地作为额外验收样本使用。

## Skill 使用

`SKILL.md` 位于仓库根目录，连同 `references/` 和 `scripts/` 即构成完整 Skill。参阅 [`SKILL.md`](SKILL.md) 了解输入识别、对齐准则、运行和结果交付规则。
