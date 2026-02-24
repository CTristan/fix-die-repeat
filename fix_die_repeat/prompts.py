"""Prompt rendering utilities."""

from functools import lru_cache
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined


@lru_cache(maxsize=1)
def _prompt_environment() -> Environment:
    """Build and cache the Jinja environment for prompt templates."""
    return Environment(
        loader=PackageLoader("fix_die_repeat", "templates"),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )


def render_prompt(template_name: str, **context: Any) -> str:
    """Render a prompt template.

    Args:
        template_name: Template filename (e.g., "review_prompt.j2")
        **context: Template variables

    Returns:
        Rendered prompt text

    """
    template = _prompt_environment().get_template(template_name)
    return template.render(**context).strip()
