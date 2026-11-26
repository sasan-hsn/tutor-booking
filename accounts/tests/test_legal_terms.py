import re
from html.parser import HTMLParser

from django.conf import settings
from django.test import SimpleTestCase


class _HTMLStructureValidator(HTMLParser):
    """Validate balanced HTML tags and reject unexpected raw tags."""

    VOID_ELEMENTS = {"br", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []
        self.tags_seen = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        self.tags_seen.append(tag)
        if tag not in self.VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID_ELEMENTS:
            self.errors.append(f"Unexpected closing tag for void element </{tag}>")
            return
        if not self.stack:
            self.errors.append(f"Unexpected closing tag </{tag}> with empty stack")
            return
        expected = self.stack.pop()
        if expected != tag:
            self.errors.append(
                f"Mismatched closing tag </{tag}>, expected </{expected}>"
            )

    def close(self):
        super().close()
        if self.stack:
            self.errors.append(f"Unclosed tags remaining: {self.stack}")


class LegalTermsDocumentTests(SimpleTestCase):
    """Tests for docs/legal/terms-of-service.md and accounts.legal helpers."""

    def test_terms_document_loads_from_base_dir(self):
        from accounts.legal import get_terms_document_path, load_terms_markdown

        expected_path = settings.BASE_DIR / "docs" / "legal" / "terms-of-service.md"
        self.assertEqual(get_terms_document_path(), expected_path)
        self.assertTrue(expected_path.is_file())

        content = load_terms_markdown()
        self.assertIn("Terms of Service", content)

    def test_dockerignore_preserves_terms_document(self):
        dockerignore_path = settings.BASE_DIR / ".dockerignore"
        lines = [
            line.strip()
            for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if "*.md" in lines or "**/*.md" in lines or "docs/" in lines:
            self.assertIn("!docs/legal/terms-of-service.md", lines)
            md_idx = max(
                idx
                for idx, line in enumerate(lines)
                if line in {"*.md", "**/*.md", "docs/"}
            )
            allow_idx = lines.index("!docs/legal/terms-of-service.md")
            self.assertGreater(allow_idx, md_idx)

    def test_terms_version_matches_settings(self):
        from accounts.legal import extract_terms_version, load_terms_markdown

        content = load_terms_markdown()
        version = extract_terms_version(content)
        self.assertEqual(version, settings.TERMS_CURRENT_VERSION)

    def test_extract_terms_version_rejects_missing_version_header(self):
        from accounts.legal import extract_terms_version

        with self.assertRaises(ValueError):
            extract_terms_version("# Terms of Service\n\nNo version header here.")

    def test_clause_completeness_audit_covers_all_seven_mandatory_clauses(self):
        from accounts.legal import load_terms_markdown

        content = load_terms_markdown()
        lowered = content.lower()

        required_headings = [
            r"^##\s+.*prepaid balance",
            r"^##\s+.*24-hour cancellation",
            r"^##\s+.*finality of lesson completion",
            r"^##\s+.*48-hour student dispute window",
            r"^##\s+.*7-day dispute auto-expiration",
            r"^##\s+.*refund\s*/\s*payout policy",
            r"^##\s+.*cryptocurrency network rules",
        ]
        for pattern in required_headings:
            self.assertRegex(
                content,
                re.compile(pattern, re.IGNORECASE | re.MULTILINE),
                msg=f"Missing mandatory section heading matching {pattern}",
            )

        required_terms = [
            "usd",
            "non-custodial",
            "zero interest",
            "more than 24 hours",
            "less than 24 hours",
            "late cancellation fee",
            "completed",
            "goodwill",
            "48 hours",
            "booking.end_at",
            "7 calendar days",
            "24 hours",
            "48 hours",
            "processing",
            "gas",
            "bep-20",
            "bnb smart chain",
            "trc-20",
            "erc-20",
            "polygon",
        ]
        for term in required_terms:
            self.assertIn(term, lowered, msg=f"Missing mandatory policy detail: {term}")

        self.assertIn("48 hours", lowered.split("## 6. refund / payout policy")[1])


class RenderTermsHtmlTests(SimpleTestCase):
    """Tests for pure safe Markdown-to-HTML rendering in accounts.legal."""

    def test_render_terms_html_escapes_malicious_html_and_entities(self):
        from accounts.legal import render_terms_html

        malicious_md = (
            "# Header <script>alert(1)</script>\n\n"
            'Text with 5 < 10 & 10 > 5 and <img src=x onerror="alert(2)">.\n\n'
            "- Bullet **bold <svg onload=alert(3)>** & `code <tag>`\n"
        )
        rendered = render_terms_html(malicious_md)

        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<svg", rendered)
        self.assertNotIn("<tag>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn("5 &lt; 10 &amp; 10 &gt; 5", rendered)
        self.assertIn("&lt;img src=x onerror=&quot;alert(2)&quot;&gt;", rendered)
        self.assertIn("<strong>bold &lt;svg onload=alert(3)&gt;</strong>", rendered)
        self.assertIn("<code>code &lt;tag&gt;</code>", rendered)

    def test_render_terms_html_parses_terms_document_into_clean_balanced_html(self):
        from accounts.legal import load_terms_markdown, render_terms_html

        rendered = render_terms_html(load_terms_markdown())

        validator = _HTMLStructureValidator()
        validator.feed(rendered)
        validator.close()

        self.assertEqual(validator.errors, [])
        self.assertIn("h1", validator.tags_seen)
        self.assertIn("h2", validator.tags_seen)
        self.assertIn("p", validator.tags_seen)
        self.assertIn("li", validator.tags_seen)

        # Ensure no raw unrendered Markdown markers leaked into prose
        self.assertNotIn("##", rendered)
        self.assertNotIn("**", rendered)
        self.assertNotIn("`", rendered)
