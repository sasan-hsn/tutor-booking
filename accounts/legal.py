import html
import re
from pathlib import Path

from django.conf import settings
from django.utils.safestring import SafeString, mark_safe

_VERSION_PATTERN = re.compile(
    r"^\*{0,2}Version:\*{0,2}\s*([^\s*]+)\s*$",
    re.MULTILINE,
)
_HEADING_PATTERN = re.compile(r"^(#{1,3})\s+(.*)$")
_INLINE_CODE_SPLIT_PATTERN = re.compile(r"(`[^`]+`)")
_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")


def get_terms_document_path() -> Path:
    """Return the absolute path to the platform Terms of Service Markdown file."""
    return Path(settings.BASE_DIR) / "docs" / "legal" / "terms-of-service.md"


def load_terms_markdown() -> str:
    """Load and return the raw Markdown text of the Terms of Service document."""
    return get_terms_document_path().read_text(encoding="utf-8")


def extract_terms_version(markdown_text: str) -> str:
    """Extract the Version identifier from the Terms of Service header."""
    match = _VERSION_PATTERN.search(markdown_text)
    if not match:
        raise ValueError("Terms document is missing a Version header.")
    return match.group(1).strip()


def _render_inline(text: str) -> str:
    """Escape raw HTML first, then convert inline code and bold markers."""
    escaped = html.escape(text, quote=True)
    segments = _INLINE_CODE_SPLIT_PATTERN.split(escaped)
    rendered_parts = []
    for segment in segments:
        if segment.startswith("`") and segment.endswith("`") and len(segment) >= 2:
            rendered_parts.append(f"<code>{segment[1:-1]}</code>")
        else:
            rendered_parts.append(_BOLD_PATTERN.sub(r"<strong>\1</strong>", segment))
    return "".join(rendered_parts)


def render_terms_html(markdown_text: str) -> SafeString:
    """Safely render flat Markdown text into escaped HTML for modal display."""
    blocks: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            joined = " ".join(paragraph_lines)
            blocks.append(f"<p>{_render_inline(joined)}</p>")
            paragraph_lines.clear()

    def flush_list() -> None:
        if list_items:
            items_html = "\n".join(f"  <li>{item}</li>" for item in list_items)
            blocks.append(f"<ul>\n{items_html}\n</ul>")
            list_items.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()

        if not line:
            flush_all()
            continue

        if line == "---":
            flush_all()
            blocks.append("<hr>")
            continue

        heading_match = _HEADING_PATTERN.match(line)
        if heading_match:
            flush_all()
            level = len(heading_match.group(1))
            text = _render_inline(heading_match.group(2).strip())
            blocks.append(f"<h{level}>{text}</h{level}>")
            continue

        if line.startswith("- "):
            flush_paragraph()
            list_items.append(_render_inline(line[2:].strip()))
            continue

        flush_list()
        paragraph_lines.append(line)

    flush_all()
    return mark_safe("\n".join(blocks))


def get_default_tenant(*, teacher=None):
    """Return the storefront's primary Tenant instance, if provisioned."""
    from portfolio.models import TeacherProfile

    from .models import Tenant

    target_teacher = teacher
    if target_teacher is None:
        target_teacher = TeacherProfile.objects.order_by("id").first()
    if target_teacher is not None:
        tenant = Tenant.objects.filter(teacher_profile=target_teacher).first()
        if tenant is not None:
            return tenant
    return Tenant.objects.select_related("teacher_profile").order_by("id").first()


def has_accepted_current_terms(user, *, tenant=None) -> bool:
    """Return True if user has accepted settings.TERMS_CURRENT_VERSION for tenant."""
    from .models import TermsAcceptance

    if not user or not getattr(user, "is_authenticated", False):
        return False

    target_tenant = tenant if tenant is not None else get_default_tenant()
    if target_tenant is None:
        return False

    return TermsAcceptance.objects.filter(
        user=user,
        tenant=target_tenant,
        version=settings.TERMS_CURRENT_VERSION,
    ).exists()


def get_terms_gate_state(
    user,
    *,
    teacher=None,
    tenant=None,
    only_if_payments_enabled: bool = False,
) -> tuple[bool, bool]:
    """
    Return (terms_required, terms_accepted) for user on the resolved tenant.
    """
    target_tenant = (
        tenant if tenant is not None else get_default_tenant(teacher=teacher)
    )
    if only_if_payments_enabled and (
        target_tenant is None or not target_tenant.payments_enabled
    ):
        return False, True
    return True, has_accepted_current_terms(user, tenant=target_tenant)
