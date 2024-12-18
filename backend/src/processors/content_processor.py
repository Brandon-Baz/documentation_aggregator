# src/processors/content_processor.py
import logging
import html2text
import bleach
import re
from datetime import datetime
from readability import Document
from typing import List
from bs4 import BeautifulSoup
from src.utils.content_cleaner import ContentCleaner
from src.utils.deduplication import SemanticContentCleaner
from config import EMBEDDING_MODEL

logger = logging.getLogger(__name__)


def post_process_markdown(content: str) -> str:
    """
    Post-process raw markdown by normalizing whitespace, ensuring spacing
    before/after headings, and cleaning up multiple blank lines.
    """
    lines = content.split("\n")
    processed_lines = []
    in_code_block = False

    for line in lines:
        # Detect code block toggles
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            processed_lines.append(line)
            continue

        if in_code_block:
            # Inside code block, preserve formatting as is
            processed_lines.append(line)
            continue

        # Outside code blocks, normalize spacing
        # Collapse extra spaces and ensure each line is trimmed
        line = re.sub(r"\s+", " ", line).strip()
        processed_lines.append(line)

    # Remove excessive blank lines (more than two)
    content = "\n".join(processed_lines)
    content = re.sub(r"\n{3,}", "\n\n", content)

    # Ensure a blank line before headings (except if at start)
    content_lines = content.split("\n")
    final_lines = []
    for i, l in enumerate(content_lines):
        if re.match(r"^#{1,6}\s", l) and i > 0:
            # If the previous line is not blank, add a blank line
            if final_lines and final_lines[-1].strip():
                final_lines.append("")
        final_lines.append(l)

    # Clean trailing spaces
    final_content = []
    for l in final_lines:
        final_content.append(l.rstrip())

    # Normalize again to ensure max two blank lines
    joined = "\n".join(final_content)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip() + "\n"


class ContentProcessor:
    def __init__(self):
        self.markdown_converter = html2text.HTML2Text()
        self.markdown_converter.ignore_images = False
        self.markdown_converter.ignore_links = False
        self.markdown_converter.body_width = 0
        self.markdown_converter.unicode_snob = True
        self.markdown_converter.protect_links = True
        self.markdown_converter.wrap_links = False
        self.markdown_converter.bypass_tables = False

        self.allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + [
            "p",
            "pre",
            "code",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "img",
            "table",
            "tr",
            "td",
            "th",
            "thead",
            "tbody",
            "ul",
            "ol",
            "li",
            "strong",
            "em",
            "blockquote",
            "a",
            "div",
            "span",
            "math",
        ]
        self.allowed_attributes = dict(bleach.sanitizer.ALLOWED_ATTRIBUTES)
        self.allowed_attributes.update(
            {
                "*": ["class", "id", "name", "data-*"],
                "img": ["src", "alt", "title"],
                "a": ["href", "title"],
                "pre": ["class", "data-language"],
                "code": ["class", "data-language"],
            }
        )

        self.content_cleaner = ContentCleaner()
        self.semantic_cleaner = SemanticContentCleaner(EMBEDDING_MODEL)

    def extract_content(self, html: str) -> str:
        """Extract main content from HTML and return clean Markdown."""
        try:
            if not html.strip():
                logger.warning("Empty HTML content received")
                return ""

            doc = Document(html)
            content_html = doc.summary()
            if not content_html:
                logger.warning("No content extracted by readability")
                return ""

            # Sanitize HTML
            sanitized_html = bleach.clean(
                content_html,
                tags=self.allowed_tags,
                attributes=self.allowed_attributes,
                strip=True,
            )

            if not sanitized_html.strip():
                logger.warning("Content was empty after sanitization")
                return ""

            # Convert to markdown
            markdown_content = self.markdown_converter.handle(sanitized_html)
            # Remove marketing sections and refine content
            processed_content = self.content_cleaner.clean_content(markdown_content)

            # Post-process
            processed_content = post_process_markdown(processed_content)
            return processed_content

        except Exception as e:
            logger.error(f"Error extracting content: {str(e)}")
            return ""

    # src/processors/content_processor.py

    def generate_markdown(
        self,
        content_list: List[dict],
        timestamp: str,
        include_metadata: bool = True,
        domain: str = "unknown",
        tags: List[str] = None,
        document_type: str = "technical_documentation",
        language: str = "en",
    ) -> str:
        """
        Generate final Markdown:
        - If single item: just metadata, then # Title, then content.
        - If multiple items: metadata, # Main Title, TOC, ### per section
        """
        if not content_list:
            logger.error("Empty content list received")
            raise ValueError("No content to process")

        tags = tags or ["documentation"]
        total_pages = len(content_list)

        # Identify domain if unknown
        if domain == "unknown":
            first_item = next((item for item in content_list if item.get("url")), None)
            if first_item and "url" in first_item:
                try:
                    domain = first_item["url"].split("/")[2]
                except IndexError:
                    domain = "unknown"

        # YAML front matter
        metadata = [
            "---",
            f"created_at: {timestamp}",
            f"last_updated: {datetime.now().isoformat()}",
            f"total_pages: {total_pages}",
            f"domain: {domain}",
            f"document_type: {document_type}",
            f"language: {language}",
            f"tags: [{', '.join(tags)}]",
            "---",
            "",
        ]

        sections = []
        sections.append("\n".join(metadata))

        if total_pages == 1:
            # Single item scenario
            item = content_list[0]
            title = item.get("title", "Untitled Document")
            content = item.get("content", "").strip()

            # Print just one title after metadata
            sections.append(f"# {title}\n")
            sections.append(content.strip())

        else:
            # Multiple items scenario
            # Main title from the first item that has a title
            main_title_item = next(
                (item for item in content_list if item.get("title")), None
            )
            main_title = (
                main_title_item["title"] if main_title_item else "Aggregated Document"
            )
            sections.append(f"# {main_title}\n")

            # Table of Contents
            toc = ["## Table of Contents", ""]
            for i, item in enumerate(content_list, 1):
                t = item.get("title", f"Page {i}")
                anchor = re.sub(r"[^a-z0-9]+", "-", t.lower().strip()).strip("-")
                toc.append(f"{i}. [{t}](#{anchor})")
            toc.append("")
            sections.append("\n".join(toc))

            # Add each content section
            for i, item in enumerate(content_list, 1):
                title = item.get("title", f"Section {i}")
                url = item.get("url", "")
                section_content = item.get("content", "").strip()

                # Section-level metadata block
                section_meta = [
                    "---",
                    "metadata:",
                    f'  title: "{title}"',
                    f"  url: {url}",
                    "  section_type: documentation_page",
                    "---",
                    "",
                ]
                sections.append("\n".join(section_meta))
                # Section heading
                sections.append(f"## {title}\n")
                formatted = self._format_content(section_content)
                sections.append(formatted)
                sections.append("\n---\n")

        final_markdown = "\n".join(sections)
        final_markdown = post_process_markdown(final_markdown)
        return final_markdown

    def _format_content(self, content: str) -> str:
        """
        Further format content for readability and LLM processing:
        - Ensure blank lines before code blocks, lists, and headings.
        - Convert code blocks properly.
        - Normalize lists and tables.
        """
        lines = content.split("\n")
        formatted_lines = []
        in_code_block = False
        in_list = False
        list_indent = 0

        for i, line in enumerate(lines):
            raw_line = line.rstrip()

            # Detect code blocks
            if raw_line.startswith("```"):
                in_code_block = not in_code_block
                # Ensure a blank line before code block if previous line isn't blank
                if (
                    not in_code_block
                    and formatted_lines
                    and formatted_lines[-1].strip()
                ):
                    formatted_lines.append("")
                formatted_lines.append(raw_line)
                continue

            if in_code_block:
                formatted_lines.append(raw_line)
                continue

            # Handle headings inside content (unlikely here since main headings handled above)
            if re.match(r"^#{1,6}\s", raw_line):
                if formatted_lines and formatted_lines[-1].strip():
                    formatted_lines.append("")
                formatted_lines.append(raw_line)
                formatted_lines.append("")
                continue

            # Handle lists
            list_match = re.match(r"^(\s*)([-*+]|\d+\.)\s", raw_line)
            if list_match:
                indent = len(list_match.group(1))
                if not in_list:
                    # Ensure blank line before list
                    if formatted_lines and formatted_lines[-1].strip():
                        formatted_lines.append("")
                    in_list = True
                list_indent = indent
                # Normalize ordered lists to `1.` or unordered to `-`
                if re.match(r"^\s*\d+\.", raw_line):
                    raw_line = re.sub(r"^\s*\d+\.", "1.", raw_line)
                formatted_lines.append(raw_line)
                continue
            else:
                # If we were in a list and encounter a non-list line, close list
                if in_list:
                    in_list = False
                    list_indent = 0
                    if formatted_lines and formatted_lines[-1].strip():
                        formatted_lines.append("")

            # Normalize tables
            if "|" in raw_line:
                # Ensure proper formatting if it looks like a table
                # Just leave as is, assuming upstream is well-formed.
                # Could add more logic if needed.
                pass

            # Paragraph or blank line
            if not raw_line.strip():
                # Add blank line only if last line wasn't blank
                if formatted_lines and formatted_lines[-1].strip():
                    formatted_lines.append("")
            else:
                formatted_lines.append(raw_line)

        # Join and do a final cleanup
        content = "\n".join(formatted_lines)
        content = re.sub(r"\n{3,}", "\n\n", content)
        return content.strip()
