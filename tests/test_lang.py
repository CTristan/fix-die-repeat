"""Tests for language detection module."""

from fix_die_repeat.lang import (
    LANGUAGE_EXTENSIONS,
    SUPPORTED_TEMPLATE_LANGUAGES,
    detect_languages_from_files,
    filter_supported_languages,
    resolve_languages,
)


class TestLanguageExtensions:
    """Tests for the LANGUAGE_EXTENSIONS constant."""

    def test_all_values_are_valid_language_keys(self) -> None:
        """All extension values map to known language keys."""
        valid_keys = {"python", "rust", "javascript", "elixir", "csharp"}
        for lang in LANGUAGE_EXTENSIONS.values():
            assert lang in valid_keys, f"Unknown language key: {lang}"


class TestDetectLanguagesFromFiles:
    """Tests for detect_languages_from_files."""

    def test_empty_file_list(self) -> None:
        """Empty input returns empty set."""
        result = detect_languages_from_files([])
        assert result == set()

    def test_single_python_file(self) -> None:
        """Single .py file returns {'python'}."""
        result = detect_languages_from_files(["src/main.py"])
        assert result == {"python"}

    def test_mixed_languages(self) -> None:
        """Files from multiple languages return all detected."""
        result = detect_languages_from_files(
            [
                "src/main.py",
                "Cargo.toml",
                "lib/mod.rs",
                "frontend/App.tsx",
                "config/app.ex",
            ]
        )
        assert result == {"python", "rust", "javascript", "elixir"}

    def test_unknown_extensions_ignored(self) -> None:
        """.md, .toml, .json files are silently skipped."""
        result = detect_languages_from_files(
            [
                "README.md",
                "pyproject.toml",
                "package.json",
                "Makefile",
            ]
        )
        assert result == set()

    def test_pyi_stub_detected_as_python(self) -> None:
        """.pyi files map to python."""
        result = detect_languages_from_files(["src/types.pyi"])
        assert result == {"python"}

    def test_heex_detected_as_elixir(self) -> None:
        """.heex Phoenix templates map to elixir."""
        result = detect_languages_from_files(["lib/templates/index.heex"])
        assert result == {"elixir"}

    def test_tsx_detected_as_javascript(self) -> None:
        """.tsx files map to javascript."""
        result = detect_languages_from_files(["src/App.tsx"])
        assert result == {"javascript"}

    def test_csx_detected_as_csharp(self) -> None:
        """.csx files map to csharp."""
        result = detect_languages_from_files(["script.csx"])
        assert result == {"csharp"}

    def test_no_extension_ignored(self) -> None:
        """Files without extensions (e.g., Makefile) are skipped."""
        result = detect_languages_from_files(["Makefile", "Dockerfile"])
        assert result == set()

    def test_nested_paths(self) -> None:
        """Paths like 'src/lib/mod.rs' extract the correct extension."""
        result = detect_languages_from_files(
            [
                "src/lib/mod.rs",
                "frontend/src/components/Button.tsx",
                "lib/my_app_web/controllers/page_controller.ex",
            ]
        )
        assert result == {"rust", "javascript", "elixir"}

    def test_case_insensitive_extensions(self) -> None:
        """Extensions are case-insensitive."""
        result = detect_languages_from_files(
            [
                "src/main.PY",
                "lib/mod.RS",
                "src/App.TSX",
            ]
        )
        assert result == {"python", "rust", "javascript"}


class TestResolveLanguages:
    """Tests for resolve_languages (hybrid strategy)."""

    def test_no_override_uses_detection(self) -> None:
        """Without override, detects from file list."""
        files = ["src/main.py", "Cargo.toml", "lib/mod.rs"]
        result = resolve_languages(files)
        assert result == {"python", "rust"}

    def test_override_replaces_detection(self) -> None:
        """Override completely replaces diff-based detection."""
        files = ["src/main.py"]
        result = resolve_languages(files, override="rust,elixir")
        assert result == {"rust", "elixir"}

    def test_override_with_spaces(self) -> None:
        """'python, rust' (with spaces) is handled correctly."""
        result = resolve_languages([], override="python, rust, elixir")
        assert result == {"python", "rust", "elixir"}

    def test_override_with_unknown_language(self) -> None:
        """Unknown languages in override are passed through (not validated)."""
        result = resolve_languages([], override="python,unknownlang")
        assert result == {"python", "unknownlang"}

    def test_empty_override_string_uses_detection(self) -> None:
        """Empty string override falls back to detection."""
        files = ["src/main.py"]
        result = resolve_languages(files, override="")
        assert result == {"python"}

    def test_none_override_uses_detection(self) -> None:
        """None override falls back to detection."""
        files = ["src/main.py", "lib/mod.rs"]
        result = resolve_languages(files, override=None)
        assert result == {"python", "rust"}

    def test_override_with_trailing_comma(self) -> None:
        """Trailing comma is handled correctly."""
        result = resolve_languages([], override="python,rust,")
        assert result == {"python", "rust"}

    def test_override_with_leading_comma(self) -> None:
        """Leading comma is handled correctly."""
        result = resolve_languages([], override=",python,rust")
        assert result == {"python", "rust"}

    def test_override_only_whitespace(self) -> None:
        """Override with only whitespace falls back to detection."""
        files = ["src/main.py"]
        result = resolve_languages(files, override="   ,  ,  ")
        assert result == {"python"}


class TestFilterSupportedLanguages:
    """Tests for filter_supported_languages."""

    def test_all_supported_languages(self) -> None:
        """All supported languages pass through unchanged."""
        result = filter_supported_languages(SUPPORTED_TEMPLATE_LANGUAGES)
        assert result == SUPPORTED_TEMPLATE_LANGUAGES

    def test_empty_set(self) -> None:
        """Empty input returns empty set."""
        result = filter_supported_languages(set())
        assert result == set()

    def test_filters_unsupported_languages(self) -> None:
        """Unsupported languages are removed."""
        result = filter_supported_languages({"python", "rust", "unknownlang"})
        assert result == {"python", "rust"}

    def test_all_unsupported_languages(self) -> None:
        """Set of only unsupported languages returns empty set."""
        result = filter_supported_languages({"unknown1", "unknown2", "unknown3"})
        assert result == set()

    def test_mixed_supported_unsupported(self) -> None:
        """Mix of supported and unsupported filters correctly."""
        languages = {"python", "elixir", "unknown1", "javascript", "unknown2"}
        result = filter_supported_languages(languages)
        assert result == {"python", "elixir", "javascript"}
