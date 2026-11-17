# Script Timestamp Aligner v1.0 — 验证记录

## 已完成的验收

此前使用一组真实原文 JSON 与 WhisperX JSON 进行过转换，并由用户核对自动生成的 `alignment-report.json` 后接受结果。

| 项目 | 结果 |
|---|---|
| 原文段落数 | 19 |
| 原文句子数 | 76 |
| 原文词语数 | 1,323 |
| WhisperX 逐词记录 | 1,358 |
| 转换后同步单元 | 1,322 |
| 原文词语覆盖率 | 100% |
| 自动对齐报告状态 | `needs_review`（5 个待核对事件） |
| 原版重新转换的确定性 | 在增加 `filetype` 前，`script.json` 和 `alignment-report.json` 曾与当时已验收版本逐字节一致 |
| 原版本地自动测试 | 18/18 通过 |

报告中标出的文字差异包括 `seven` / `several`、`spiegelmers` / `Spiegel-Mares`、两处人物姓名不同拼写，以及正文中 1 个额外识别的 `a`。用户认可转换结果，**不等于已经独立核听了原始音频**；自动报告仍应如实保留 `needs_review`。

## 仓库中的可重现测试

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

测试涵盖逐词精确对齐、`it is` / `it's`、`do not` / `don't`、复合词、英美拼写、未覆盖的开头语音、正文额外识别词、原文缺词、Unicode 标点、跨句禁止合并、重复 `sid`、空时间、WhisperX 兼容格式、冲突提示、确定性、输入覆盖保护、公开测试样本输出、Schema/目录结构。公开样本使用原创合成句子，预期的 `script.json` 和报告已存于 `tests/fixtures/`。

## 限制与后续验证

- 还没有第二组**独立真实文章**的人工验收结果，跨文档准确率尚未测定。
- 本程序只读取两个 JSON，不直接听取音频；歧义的识别替换需要人工核听。
- 只有 Schema 类型和结构约束可以通过 JSON Schema 检查，文本覆盖与时间前后关系由 `align.py` 校验。
- 完整文章及语音识别数据不放入公开 GitHub 仓库。

## Schema 更新：必需的 `filetype`

- 正式输出根对象固定按 `filetype`, `title`, `paragraphs` 顺序写入；`filetype` 的唯一合法值为 `"script"`。
- `title` 仍是必需字符串，允许 `""`。段落、句子、时间单元及时间戳对齐规则均未改变。
- 新旧两个版本的 `script.json` 因新增根字段而不再逐字节相同；这是有意的契约更新，不表示原文或时间戳被修改。
- `alignment-report.json` 的字段与含义保持不变。回归测试补充了根字段顺序和缺失/非法 `filetype` 的拒绝情况。
