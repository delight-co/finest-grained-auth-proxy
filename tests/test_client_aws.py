from fgap.client.aws import extract_account


class TestExtractAccount:
    def test_flag(self):
        account, rest = extract_account(
            ["--account", "acct", "logs", "tail", "/g"], environ={},
        )
        assert account == "acct"
        assert rest == ["logs", "tail", "/g"]

    def test_flag_equals_form(self):
        account, rest = extract_account(
            ["logs", "--account=acct", "tail"], environ={},
        )
        assert account == "acct"
        assert rest == ["logs", "tail"]

    def test_env_fallback(self):
        account, rest = extract_account(
            ["logs", "tail"], environ={"FGAP_AWS_ACCOUNT": "acct-env"},
        )
        assert account == "acct-env"
        assert rest == ["logs", "tail"]

    def test_flag_wins_over_env(self):
        account, _ = extract_account(
            ["--account", "acct-flag", "logs"],
            environ={"FGAP_AWS_ACCOUNT": "acct-env"},
        )
        assert account == "acct-flag"

    def test_none_when_absent(self):
        account, rest = extract_account(["logs", "tail"], environ={})
        assert account is None
        assert rest == ["logs", "tail"]

    def test_dangling_flag_yields_none(self):
        account, rest = extract_account(["logs", "--account"], environ={})
        assert account is None
        assert rest == ["logs"]

    def test_empty_env_ignored(self):
        account, _ = extract_account(
            ["logs"], environ={"FGAP_AWS_ACCOUNT": ""},
        )
        assert account is None
