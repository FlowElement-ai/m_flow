# Coreference Resolution Module

Part of [M-Flow](https://github.com/FlowElement-xinliuyuansu/m_flow) — the cognitive memory engine for AI agents.

A lightweight, rule-based coreference resolution system that replaces pronouns with their concrete antecedents. Used by M-Flow's preprocessing pipeline to resolve references before retrieval, ensuring that "he", "it", "the company" are replaced with actual names before entering the knowledge graph.

Supports Chinese (11 pronoun types with semantic role analysis) and English (basic resolution).

## ✨ Features / 特点

- **11 Pronoun Types**: Person, possessive, object, location, time, ordinal, event, formal deictic, reflexive, generic, and bound variable pronouns
- **Semantic Role Analysis**: Uses verb semantics to determine correct antecedent (e.g., patient vs. agent)
- **Stream Processing**: Real-time sentence-by-sentence resolution
- **No Training Required**: Pure rule-based, no ML models needed for core functionality
- **English Support**: Includes a basic English coreference module

## 📦 Installation / 安装

### From Source / 源码安装

```bash
git clone https://github.com/FlowElement-xinliuyuansu/m_flow.git
cd m_flow/coreference
pip install -e .
```

### With Optional Dependencies / 安装可选依赖

```bash
# Install with HanLP/LTP for enhanced parsing
pip install -e ".[nlp]"

# Install with development tools
pip install -e ".[dev]"
```

## 🚀 Quick Start / 快速开始

### Basic Usage / 基础用法

```python
from coreference_module import CoreferenceResolver

resolver = CoreferenceResolver()

text = "小明去北京。他在那里工作。"
resolved, replacements = resolver.resolve_text(text)

print(resolved)
# Output: 小明去北京。小明在北京工作。

print(replacements)
# Output: [
#   {'pronoun': '他', 'replacement': '小明', 'position': 7},
#   {'pronoun': '那里', 'replacement': '北京', 'position': 10}
# ]

# Reset for new document
resolver.reset()
```

### Stream Processing / 流式处理

```python
from coreference_module import StreamCorefSession

session = StreamCorefSession()

# Process sentences one by one
result1, _ = session.add_sentence("张三是医生。")
result2, reps = session.add_sentence("他很忙。")

print(result2)  # 张三很忙。

# Reset for new conversation
session.reset()
```

### Structured Output / 结构化输出

```python
resolver = CoreferenceResolver()
output = resolver.resolve_text_structured("妈妈买了苹果。她说它很甜。")

print(output.resolved_text)      # 妈妈买了苹果。妈妈说苹果很甜。
print(output.replacements)       # List of replacement details
print(output.mentions)           # All detected mentions
print(output.time_extractions)   # Normalized time expressions
```

### NER Extraction / 命名实体识别

```python
from coreference_module import NERService

ner = NERService()
result = ner.extract("小明在北京大学读书")

print(result.PER)  # ['小明']
print(result.LOC)  # ['北京大学']
```

### Time Normalization / 时间归一化

```python
from coreference_module import normalize_time
from datetime import datetime

ref_date = datetime(2026, 2, 7)
time_span = normalize_time("昨天", ref_date)

print(time_span.start_dt)   # 2026-02-06 00:00:00
print(time_span.end_dt)     # 2026-02-07 00:00:00
print(time_span.precision)  # DAY
```

## 📊 Supported Pronoun Types / 支持的代词类型

| Type | Examples | Description |
|------|----------|-------------|
| Person | 他、她、他们 | Third-person pronouns |
| Possessive | 他的、她的、它的 | Possessive pronouns |
| Object | 它、它们 | Object/inanimate pronouns |
| Location | 这里、那里、那边 | Location pronouns |
| Time | 那时候、当时 | Temporal pronouns |
| Ordinal | 前者、后者 | Ordinal pronouns |
| Event | 这件事、此事 | Event reference pronouns |
| Formal Deictic | 该、上述 | Formal/written deixis |
| Ambiguous | 这个、那个 | Context-dependent pronouns |

## ❌ Non-Resolution Cases / 不消解的情况

The system correctly **preserves** (does not resolve) these pronouns:

| Case | Example | Reason |
|------|---------|--------|
| First person | 我、我们 | Speaker reference |
| Second person | 你、您 | Listener reference |
| Reflexive | 自己、本人 | Self-reference |
| Generic | 人家、别人 | Generic reference |
| Bound variable | 每个学生都带了他的书 | Quantifier-bound |
| First sentence | 他很高。| No antecedent available |

## 📁 Project Structure / 项目结构

```
coreference/
├── coreference_module/      # Core Chinese coreference resolution
│   ├── coreference.py       # Main resolver class
│   ├── tokenizer.py         # Chinese tokenizer with NER
│   ├── syntax_adapter.py    # HanLP/LTP adapter
│   ├── time_normalizer.py   # Time expression normalization
│   ├── canonicalizer.py     # Entity canonicalization
│   └── ner_adapter.py       # NER service adapter
├── english_coreference/     # English coreference module
├── tests/                   # Test suite (85 tests)
├── configs/                 # Configuration files
├── pyproject.toml           # Project configuration
├── LICENSE                  # MIT License
└── README.md
```

## 🧪 Testing / 测试

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=coreference_module --cov-report=html
```

**Current status**: 85 tests, 100% passing

## 📈 Performance / 性能

| Metric | Value |
|--------|-------|
| Test Cases | 85 |
| Pass Rate | 100% |
| Pronoun Types | 11 |
| Branch Coverage (`_find_replacement`) | 100% |

## 🔧 Configuration / 配置

The system works out of the box with sensible defaults. For advanced usage, you can configure:

```python
resolver = CoreferenceResolver()
resolver.reset()  # Reset resolver state for a new document
```

## 🌐 English Support / 英文支持

```python
from english_coreference import CoreferenceResolver as EnglishResolver

resolver = EnglishResolver()
text = "John went home. He was tired."
resolved, _ = resolver.resolve_text(text)

print(resolved)  # John went home. John was tired.
```

## 📄 License / 许可证

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing / 贡献

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📚 Citation / 引用

If you use this project in your research, please cite:

```bibtex
@software{chinese_coref,
  title = {Chinese Coreference Resolution System},
  author = {Junting Hua},
  year = {2026},
  url = {https://github.com/FlowElement-xinliuyuansu/m_flow}
}
```

## 📧 Contact / 联系

- GitHub Issues: [Issues](https://github.com/FlowElement-xinliuyuansu/m_flow/issues)
- Email: contact@xinliuyuansu.com
