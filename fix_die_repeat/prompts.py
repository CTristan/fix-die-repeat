"""Prompt rendering utilities."""

from functools import lru_cache

from jinja2 import Environment, PackageLoader, StrictUndefined

# Types that Jinja2 can render natively in our templates.
# Using a union instead of Any enables type checking while maintaining
# flexibility for future template additions.
TemplateContextValue = str | int | bool | None


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


def render_prompt(template_name: str, **context: TemplateContextValue) -> str:
    """Render a prompt template.

    Args:
        template_name: Template filename (e.g., "review_prompt.j2")
        **context: Template variables (str, int, bool, or None)

    Returns:
        Rendered prompt text

    """
    template = _prompt_environment().get_template(template_name)
    return template.render(**context).strip()
