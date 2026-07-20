from fgap.client.langfuse import extract_project


class TestExtractProject:
    def test_flag(self):
        project, rest = extract_project(
            ["--project", "proj-a", "api", "traces", "list"], environ={},
        )
        assert project == "proj-a"
        assert rest == ["api", "traces", "list"]

    def test_flag_equals_form(self):
        project, rest = extract_project(
            ["api", "--project=proj-a", "traces", "list"], environ={},
        )
        assert project == "proj-a"
        assert rest == ["api", "traces", "list"]

    def test_flag_position_independent(self):
        project, rest = extract_project(
            ["api", "traces", "list", "--project", "proj-a"], environ={},
        )
        assert project == "proj-a"
        assert rest == ["api", "traces", "list"]

    def test_env_fallback(self):
        project, rest = extract_project(
            ["api", "traces", "list"],
            environ={"FGAP_LANGFUSE_PROJECT": "proj-env"},
        )
        assert project == "proj-env"
        assert rest == ["api", "traces", "list"]

    def test_flag_wins_over_env(self):
        project, _ = extract_project(
            ["--project", "proj-flag", "api"],
            environ={"FGAP_LANGFUSE_PROJECT": "proj-env"},
        )
        assert project == "proj-flag"

    def test_none_when_absent(self):
        project, rest = extract_project(["api", "traces", "list"], environ={})
        assert project is None
        assert rest == ["api", "traces", "list"]

    def test_dangling_flag_yields_none(self):
        project, rest = extract_project(["api", "--project"], environ={})
        assert project is None
        assert rest == ["api"]

    def test_empty_env_ignored(self):
        project, _ = extract_project(
            ["api"], environ={"FGAP_LANGFUSE_PROJECT": ""},
        )
        assert project is None
