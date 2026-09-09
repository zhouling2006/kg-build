"""
MinerU 文档解析客户端

支持两种模式：
  - 精准解析（需 Token，最大 200MB/200页，输出 Markdown + JSON）
  - Agent 轻量解析（免 Token，限 10MB/20页，输出 Markdown）

自动处理大文件切分：
  - PDF 超过页数/大小限制时，自动按页码范围切分为多个子 PDF 分别解析再合并
  - ZIP 压缩包自动解包，逐个处理内含文件
"""

import os
import time
import json
import zipfile
import tempfile
import shutil
import io as io_module
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests


# 可被 MinerU 解析的文件后缀
MINERU_SUPPORTED_EXTS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".xls", ".xlsx", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp",
}

# 精准解析限制
PRECISION_MAX_SIZE = 200 * 1024 * 1024   # 200MB
PRECISION_MAX_PAGES = 200

# Agent 轻量解析限制
AGENT_MAX_SIZE = 10 * 1024 * 1024        # 10MB
AGENT_MAX_PAGES = 20


class MinerUClient:
    """MinerU API 客户端，支持大文件自动切分和 ZIP 解包"""

    BASE_URL = "https://mineru.net"

    def __init__(
        self,
        api_token: Optional[str] = None,
        base_url: Optional[str] = None,
        poll_interval: int = 3,
        max_wait: int = 900,
        enable_formula: bool = True,
        enable_table: bool = True,
        is_ocr: bool = True,          # 高质量：OCR 增强图表/公式识别
        language: str = "ch",
        output_root: Optional[str] = None,
    ):
        self.api_token = api_token or os.getenv("MINERU_API_TOKEN", "")
        self.base_url = (base_url or os.getenv("MINERU_BASE_URL", self.BASE_URL)).rstrip("/")
        self.poll_interval = poll_interval
        self.max_wait = max_wait

        # 解析参数
        self.enable_formula = enable_formula
        self.enable_table = enable_table
        self.is_ocr = is_ocr
        self.language = language

        # 输出目录（保存 ZIP、图片、JSON）
        self.output_root = Path(output_root or ".")

        # 当前模式的限制
        if self.api_token:
            self._max_size = PRECISION_MAX_SIZE
            self._max_pages = PRECISION_MAX_PAGES
            self._mode_name = "精准解析"
        else:
            self._max_size = AGENT_MAX_SIZE
            self._max_pages = AGENT_MAX_PAGES
            self._mode_name = "Agent 轻量解析"

    # ================================================================
    # 公开方法
    # ================================================================

    @classmethod
    def is_supported(cls, file_path: str) -> bool:
        """判断文件是否需要 MinerU 解析（含 .zip）"""
        ext = Path(file_path).suffix.lower()
        return ext in MINERU_SUPPORTED_EXTS or ext == ".zip"

    def parse_file(self, file_path: str, output_dir: Optional[str] = None) -> str:
        """
        解析一个本地文件，返回 Markdown 字符串。

        自动处理：
          - ZIP 压缩包 → 解包，逐个解析内含文件，合并结果
          - PDF 超限 → 自动切分为子 PDF 分段解析再合并
          - 有 Token → 精准解析（200MB/200页）
          - 无 Token → Agent 轻量解析（10MB/20页）

        精准解析会保存完整 ZIP（含图片、JSON 等）到 output_dir。
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 确定输出目录
        if output_dir:
            file_output_dir = Path(output_dir)
        else:
            file_output_dir = self.output_root / f"mineru_{path.stem}"
        file_output_dir.mkdir(parents=True, exist_ok=True)
        self._current_output_dir = file_output_dir

        ext = path.suffix.lower()

        # --- ZIP 解包 ---
        if ext == ".zip":
            return self._parse_zip(path, file_output_dir)

        if ext not in MINERU_SUPPORTED_EXTS:
            raise ValueError(
                f"不支持的文件类型: {ext}，支持: {sorted(MINERU_SUPPORTED_EXTS)}，以及 .zip"
            )

        # --- PDF 切分 ---
        if ext == ".pdf":
            page_count = self._get_pdf_page_count(path)
            file_size = path.stat().st_size
            if page_count > self._max_pages or file_size > self._max_size:
                return self._parse_split_pdf(path, page_count, file_size, file_output_dir)

        # --- 单文件直接解析 ---
        print(f"  [PDF] {self._mode_name}: {path.name} "
              f"({file_size / 1024 / 1024:.1f}MB)" if ext == ".pdf" else f"  [FILE] {self._mode_name}: {path.name}")
        markdown = self._parse_single_file(path, file_output_dir)
        print(f"    [OK] 完成 ({len(markdown)} 字符)")
        return markdown

    def parse_files(self, file_paths: list) -> Dict[str, str]:
        """批量解析，返回 {文件路径: Markdown文本}"""
        results = {}
        for fp in file_paths:
            try:
                results[fp] = self.parse_file(fp)
            except Exception as e:
                print(f"  [FAIL] 解析失败 {Path(fp).name}: {e}")
                results[fp] = ""
        return results

    # ================================================================
    # ZIP 解包
    # ================================================================

    def _parse_zip(self, zip_path: Path, output_dir: Path) -> str:
        """解压 ZIP，逐个解析内含的受支持文件，合并 Markdown"""
        print(f"\n[ZIP] 解压: {zip_path.name}")
        tmp_dir = Path(tempfile.mkdtemp(prefix="mineru_zip_"))
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_dir)

            files = sorted(
                p for p in tmp_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in MINERU_SUPPORTED_EXTS
            )
            if not files:
                raise RuntimeError(f"ZIP 中未找到支持的文件（{MINERU_SUPPORTED_EXTS}）")

            print(f"  [列表] 内含 {len(files)} 个文件待解析")
            parts = []
            for i, f in enumerate(files, 1):
                print(f"\n  [{i}/{len(files)}] {f.name}")
                sub_out = output_dir / f.stem
                sub_out.mkdir(parents=True, exist_ok=True)
                try:
                    result = self.parse_file(str(f), str(sub_out))
                    rel = f.relative_to(tmp_dir)
                    parts.append(f"\n\n## {rel}\n\n{result}")
                except Exception as e:
                    print(f"    [FAIL] 解析失败: {e}")
                    parts.append(f"\n\n## {f.name}\n\n> 解析失败: {e}")

            return "\n\n".join(parts)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ================================================================
    # PDF 切分
    # ================================================================

    def _get_pdf_page_count(self, pdf_path: Path) -> int:
        """读取 PDF 页数"""
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            print("  [!] 未安装 PyPDF2，无法检测 PDF 页数，按文件大小判断")
            print("    安装: pip install PyPDF2")
            return 0

        reader = PdfReader(str(pdf_path))
        return len(reader.pages)

    def _split_pdf(self, pdf_path: Path, pages_per_chunk: int, tmp_dir: Path) -> List[Path]:
        """将 PDF 按页数切分为多个子文件，返回子文件路径列表"""
        try:
            from PyPDF2 import PdfReader, PdfWriter
        except ImportError:
            raise ImportError("PDF 切分需要 PyPDF2，请执行: pip install PyPDF2")

        reader = PdfReader(str(pdf_path))
        total = len(reader.pages)

        chunks = []
        for start in range(0, total, pages_per_chunk):
            end = min(start + pages_per_chunk, total)
            writer = PdfWriter()
            for i in range(start, end):
                writer.add_page(reader.pages[i])

            chunk_path = tmp_dir / f"{pdf_path.stem}_p{start + 1}-{end}.pdf"
            with open(chunk_path, "wb") as f:
                writer.write(f)
            chunks.append(chunk_path)

        return chunks

    def _parse_split_pdf(self, pdf_path: Path, page_count: int, file_size: int, output_dir: Path) -> str:
        """切分大 PDF 并分段解析，合并 Markdown + 图片/JSON"""
        print(f"\n[Split] {self._mode_name}限制: <= {self._max_pages}页 / <= {self._max_size / 1024 / 1024:.0f}MB")
        print(f"  当前 PDF: {page_count}页 / {file_size / 1024 / 1024:.1f}MB")
        print(f"  输出目录: {output_dir}")

        if page_count <= 0:
            raise RuntimeError(
                f"文件 {file_size / 1024 / 1024:.1f}MB 超过 {self._max_size / 1024 / 1024:.0f}MB 限制，"
                f"且无法检测页数（请安装 PyPDF2: pip install PyPDF2）"
            )

        # 计算每块页数
        pages_per_chunk = self._max_pages
        if page_count > 0:
            avg_size_per_page = file_size / page_count
            max_pages_by_size = int(self._max_size * 0.9 / avg_size_per_page) if avg_size_per_page > 0 else self._max_pages
            pages_per_chunk = min(self._max_pages, max_pages_by_size, max(1, max_pages_by_size))
        if pages_per_chunk < 1:
            pages_per_chunk = 1

        # 切分
        print(f"  [Split] 切分为每块 <= {pages_per_chunk}页...")
        tmp_dir = Path(tempfile.mkdtemp(prefix="mineru_split_"))
        try:
            chunks = self._split_pdf(pdf_path, pages_per_chunk, tmp_dir)
            print(f"  [List] 共 {len(chunks)} 块")

            parts = []
            for i, chunk_path in enumerate(chunks, 1):
                chunk_out = output_dir / f"chunk_{i:03d}"
                chunk_out.mkdir(parents=True, exist_ok=True)
                print(f"\n  [{i}/{len(chunks)}] {chunk_path.name}")
                markdown = self._parse_single_file(chunk_path, chunk_out)
                parts.append(f"\n\n<!-- 第 {i} 段 (页码约 {(i-1)*pages_per_chunk + 1}-{min(i*pages_per_chunk, page_count)}) -->\n\n{markdown}")
                print(f"    [OK] 完成 ({len(markdown)} 字符)")

            # 合并图片到输出根目录
            images_root = output_dir / "images"
            images_root.mkdir(exist_ok=True)
            for i in range(len(chunks)):
                chunk_images = output_dir / f"chunk_{i+1:03d}" / "images"
                if chunk_images.exists():
                    for img in chunk_images.iterdir():
                        dst = images_root / img.name
                        if not dst.exists():
                            shutil.copy2(img, dst)

            combined = "".join(parts)
            (output_dir / "full.md").write_text(combined, encoding="utf-8")
            return combined
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ================================================================
    # 单文件解析路由
    # ================================================================

    def _parse_single_file(self, path: Path, output_dir: Path) -> str:
        """解析单个文件，根据是否有 Token 选模式"""
        if self.api_token:
            return self._parse_precision(path, output_dir)
        else:
            return self._parse_agent(path, output_dir)

    # ================================================================
    # Agent 轻量解析
    # ================================================================

    def _parse_agent(self, path: Path, output_dir: Path) -> str:
        """Agent 轻量解析：上传文件 → 轮询 → 下载 Markdown"""
        file_name = path.name
        file_size = path.stat().st_size

        if file_size > AGENT_MAX_SIZE:
            raise ValueError(
                f"文件 {file_size / 1024 / 1024:.1f}MB 超过 Agent 模式 {AGENT_MAX_SIZE / 1024 / 1024:.0f}MB 限制"
            )

        # 1) 提交解析任务，获取上传 URL
        task = self._req("POST", "/api/v1/agent/parse/file", json={
            "file_name": file_name,
        })
        task_id = task["task_id"]
        upload_url = task["file_url"]

        # 2) PUT 上传文件
        print(f"    --> 上传中 ({file_size / 1024:.0f}KB)...")
        with open(path, "rb") as f:
            resp = requests.put(upload_url, data=f, timeout=300)
            resp.raise_for_status()
        print("    --> 上传完成，等待解析...")

        # 3) 轮询结果
        markdown = self._poll_agent(task_id)

        # 4) 保存结果到 output_dir
        out_md = output_dir / f"{path.stem}.md"
        out_md.write_text(markdown, encoding="utf-8")
        print(f"    --> Markdown 已保存: {out_md.name}")

        return markdown

    def _poll_agent(self, task_id: str) -> str:
        """轮询 Agent 解析结果"""
        waited = 0
        while waited < self.max_wait:
            time.sleep(self.poll_interval)
            waited += self.poll_interval
            result = self._req("GET", f"/api/v1/agent/parse/{task_id}")
            state = result.get("state", "")
            if state == "done":
                md_url = result.get("markdown_url", "")
                if not md_url:
                    raise RuntimeError("Agent 解析完成但缺少 markdown_url")
                print(f"    --> 下载 Markdown...")
                resp = requests.get(md_url, timeout=60)
                resp.raise_for_status()
                return resp.text
            elif state == "failed":
                err_msg = result.get("err_msg", str(result))
                raise RuntimeError(f"Agent 解析失败: {err_msg}")
            print(f"    --> 解析中... ({waited}s)")
        raise TimeoutError(f"Agent 解析超时 ({self.max_wait}s)")

    # ================================================================
    # 精准解析
    # ================================================================

    def _parse_precision(self, path: Path, output_dir: Path) -> str:
        """精准解析：上传 → 提交任务 → 轮询 → 下载 zip → 保存全量 + 提取 Markdown"""
        file_name = path.name
        file_size = path.stat().st_size

        # 1) 申请上传 URL
        resp = self._req("POST", "/api/v4/file-urls/batch", json={
            "files": [{"name": file_name}],
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "is_ocr": self.is_ocr,
            "language": self.language,
        })
        batch_id = resp["batch_id"]
        file_url = resp["file_urls"][0]

        # 2) PUT 上传
        print(f"    --> 上传中 ({file_size / 1024:.0f}KB)...")
        with open(path, "rb") as f:
            r = requests.put(file_url, data=f, timeout=300)
            r.raise_for_status()
        print("    --> 上传完成，等待解析...")

        # 3) 轮询批量结果
        markdown = self._poll_precision_batch(batch_id, output_dir)
        if markdown:
            return markdown

        raise RuntimeError(f"精准解析未返回 Markdown，batch_id={batch_id}")

    def _poll_precision_batch(self, batch_id: str, output_dir: Path) -> Optional[str]:
        """轮询精准解析，下载 ZIP 并保存全量内容（md/json/images），返回 Markdown"""
        waited = 0
        while waited < self.max_wait:
            time.sleep(self.poll_interval)
            waited += self.poll_interval
            resp = self._req("GET", f"/api/v4/extract-results/batch/{batch_id}")
            results = resp.get("extract_result", [])
            if not results:
                continue

            statuses = {r.get("state") for r in results}
            if "failed" in statuses:
                errs = [r.get("err_msg", "") for r in results if r.get("state") == "failed"]
                raise RuntimeError(f"精准解析失败: {errs}")

            if statuses == {"done"}:
                full_zip_url = results[0].get("full_zip_url", "")
                if not full_zip_url:
                    raise RuntimeError("缺少 full_zip_url")
                print(f"    --> 下载解析结果...")
                zip_resp = requests.get(full_zip_url, timeout=120)
                zip_resp.raise_for_status()

                # 保存原始 ZIP
                zip_path = output_dir / "mineru_raw.zip"
                zip_path.write_bytes(zip_resp.content)
                print(f"    --> ZIP 已保存 ({len(zip_resp.content)/1024/1024:.1f}MB): {zip_path.name}")

                # 解压全部内容
                with zipfile.ZipFile(io_module.BytesIO(zip_resp.content)) as zf:
                    zf.extractall(output_dir)
                print(f"    --> 已解压到: {output_dir}")

                # 列出解压内容
                md_files = sorted(output_dir.rglob("*.md"))
                json_files = sorted(output_dir.rglob("*.json"))
                img_files = sorted(output_dir.rglob("images/*"))
                print(f"    --> 含 {len(md_files)} 个 .md, {len(json_files)} 个 .json, {len(img_files)} 个图片")

                # 返回完整 Markdown
                if md_files:
                    return md_files[0].read_text(encoding="utf-8")
                # fallback: 返回第一个 .md
                md_files2 = sorted([f for f in output_dir.iterdir() if f.suffix.lower() in (".md",)])
                if md_files2:
                    return md_files2[0].read_text(encoding="utf-8")
                raise RuntimeError("ZIP 中未找到 .md 文件")

            print(f"    --> 解析中... ({waited}s, status={statuses})")

        raise TimeoutError(f"精准解析超时 ({self.max_wait}s)")

    # ================================================================
    # 内部工具
    # ================================================================

    def _req(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        resp = requests.request(method, url, headers=headers, timeout=60, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"API 错误 [{data.get('code')}]: {data.get('msg', '未知错误')}"
            )
        return data.get("data", data)
