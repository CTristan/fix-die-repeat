"""Prompt rendering utilities."""

from functools import lru_cache

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    StrictUndefined,
    select_autoescape,
)

from fix_die_repeat.config import get_user_templates_dir

# Types that Jinja2 can render natively in our templates.
# Using a union instead of Any enables type checking while maintaining
# flexibility for future template additions.
TemplateContextValue = str | int | bool | list[str] | dict[str, str] | None


@lru_cache(maxsize=1)
def _prompt_environment() -> Environment:
    """Build and cache the Jinja environment for prompt templates.

    Templates are resolved from the user dotfolder first (so user edits and
    the ``--improve-prompts`` command take precedence), falling back to the
    shipped package. FileSystemLoader silently skips missing directories, so
    no seeding is required on fresh installs.

    Uses select_autoescape to only enable autoescaping for HTML templates.
    Our .j2 templates are plain-text AI prompts, so escaping would corrupt
    the content. This configuration satisfies S701 while preserving correct
    behavior for our use case.
    """
    user_dir = get_user_templates_dir()
    return Environment(
        loader=ChoiceLoader(
            [
                FileSystemLoader(str(user_dir)),
                PackageLoader("fix_die_repeat", "templates"),
            ]
        ),
        autoescape=select_autoescape(enabled_extensions=("html", "htm")),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )


def clear_prompt_cache() -> None:
    """Drop the cached Jinja environment.

    Call this after mutating ``<FDR_HOME>/templates/`` so subsequent
    ``render_prompt`` calls pick up the new filesystem state. The
    ``--improve-prompts`` mode uses it after pi finishes editing.
    """
    _prompt_environment.cache_clear()


def render_prompt(template_name: str, **context: TemplateContextValue) -> str:
    """Render a prompt template.

    Args:
        template_name: Template filename (e.g., "review_prompt.j2")
        **context: Template variables (str, int, bool, list[str], or None)

    Returns:
        Rendered prompt text

    """
    template = _prompt_environment().get_template(template_name)
    return template.render(**context).strip()
