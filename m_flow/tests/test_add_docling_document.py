"""
Docling文档添加测试
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import m_flow


async def run_docling_test():
    """运行Docling文档测试"""
    base = Path(__file__).resolve().parent
    pdf_path = os.path.join(base, "test_data", "artificial-intelligence.pdf")
    img_path = os.path.join(base, "test_data", "example_copy.png")
    ppt_path = os.path.join(base, "test_data", "example.pptx")

    await m_flow.prune.prune_data()
    await m_flow.prune.prune_system(metadata=True)

    from docling.document_converter import DocumentConverter

    conv = DocumentConverter()

    # 转换并添加文档
    await m_flow.add(conv.convert(pdf_path).document)
    await m_flow.add(conv.convert(img_path).document)
    await m_flow.add(conv.convert(ppt_path).document)

    await m_flow.memorize()

    # 验证搜索结果
    r1 = await m_flow.search("人工智能相关内容")
    assert len(r1) != 0

    r2 = await m_flow.search("程序员换灯泡吗?")
    assert len(r2) != 0

    r3 = await m_flow.search("演示文稿中的颜色?")
    assert len(r3) != 0


if __name__ == "__main__":
    asyncio.run(run_docling_test())
