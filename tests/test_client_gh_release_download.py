import json

import pytest
from aiohttp import web

from fgap.client.gh import _parse_release_download_args, run


def _url(server) -> str:
    return str(server.make_url(""))


def _no_git():
    async def getter(*, _run=None):
        return None
    return getter


# =========================================================================
# Pure logic: release download arg parsing
# =========================================================================


class TestParseReleaseDownloadArgs:
    def test_tag_only(self):
        assert _parse_release_download_args(["v1.0"]) == (
            "v1.0", [], ".", False, False,
        )

    def test_pattern(self):
        tag, pats, *_ = _parse_release_download_args(
            ["v1.0", "--pattern", "*.tar.gz"],
        )
        assert tag == "v1.0"
        assert pats == ["*.tar.gz"]

    def test_pattern_short(self):
        _, pats, *_ = _parse_release_download_args(["v1.0", "-p", "*.zip"])
        assert pats == ["*.zip"]

    def test_pattern_equals(self):
        _, pats, *_ = _parse_release_download_args(["v1.0", "--pattern=*.deb"])
        assert pats == ["*.deb"]

    def test_multiple_patterns(self):
        _, pats, *_ = _parse_release_download_args(
            ["v1.0", "-p", "*.tar.gz", "-p", "*.zip"],
        )
        assert pats == ["*.tar.gz", "*.zip"]

    def test_dir(self):
        _, _, d, *_ = _parse_release_download_args(["v1.0", "-D", "/tmp/out"])
        assert d == "/tmp/out"

    def test_dir_equals(self):
        _, _, d, *_ = _parse_release_download_args(["v1.0", "--dir=/tmp/out"])
        assert d == "/tmp/out"

    def test_clobber(self):
        *_, clobber, skip = _parse_release_download_args(["v1.0", "--clobber"])
        assert clobber is True
        assert skip is False

    def test_skip_existing(self):
        *_, clobber, skip = _parse_release_download_args(
            ["v1.0", "--skip-existing"],
        )
        assert clobber is False
        assert skip is True

    def test_no_args(self):
        assert _parse_release_download_args([]) == (None, [], ".", False, False)


# =========================================================================
# run(): release download (client-side)
# =========================================================================


def _release_view_response(assets: list[dict]):
    return web.json_response({
        "exit_code": 0,
        "stdout": json.dumps({"assets": assets}),
        "stderr": "",
    })


_ASSET_A = {
    "name": "app_linux_arm64.tar.gz",
    "apiUrl": "https://api.github.com/repos/o/r/releases/assets/111",
}
_ASSET_B = {
    "name": "app_linux_amd64.tar.gz",
    "apiUrl": "https://api.github.com/repos/o/r/releases/assets/222",
}
_ASSET_C = {
    "name": "checksums.txt",
    "apiUrl": "https://api.github.com/repos/o/r/releases/assets/333",
}


class TestReleaseDownload:
    async def test_basic(self, mock_proxy, tmp_path):
        server, state = mock_proxy
        state["responses"].append(_release_view_response([_ASSET_A]))
        code = await run(
            ["release", "download", "v1.0", "-p", "*.tar.gz",
             "-D", str(tmp_path), "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert (tmp_path / "app_linux_arm64.tar.gz").read_bytes() == b"asset-bytes"
        # Verify proxy got the right download request
        dl = state["download_requests"][0]
        assert dl["resource"] == "o/r"
        assert dl["url"] == _ASSET_A["apiUrl"]

    async def test_multiple_assets(self, mock_proxy, tmp_path):
        server, state = mock_proxy
        state["responses"].append(
            _release_view_response([_ASSET_A, _ASSET_B, _ASSET_C]),
        )
        code = await run(
            ["release", "download", "v1.0", "-p", "*.tar.gz",
             "-D", str(tmp_path), "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert (tmp_path / "app_linux_arm64.tar.gz").exists()
        assert (tmp_path / "app_linux_amd64.tar.gz").exists()
        assert not (tmp_path / "checksums.txt").exists()

    async def test_no_pattern_downloads_all(self, mock_proxy, tmp_path):
        server, state = mock_proxy
        state["responses"].append(
            _release_view_response([_ASSET_A, _ASSET_C]),
        )
        code = await run(
            ["release", "download", "v1.0",
             "-D", str(tmp_path), "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert (tmp_path / "app_linux_arm64.tar.gz").exists()
        assert (tmp_path / "checksums.txt").exists()

    async def test_clobber(self, mock_proxy, tmp_path):
        server, state = mock_proxy
        existing = tmp_path / "app_linux_arm64.tar.gz"
        existing.write_bytes(b"old")
        state["responses"].append(_release_view_response([_ASSET_A]))
        code = await run(
            ["release", "download", "v1.0", "-p", "*.tar.gz",
             "--clobber", "-D", str(tmp_path), "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert existing.read_bytes() == b"asset-bytes"

    async def test_existing_file_errors(self, mock_proxy, tmp_path, capsys):
        server, state = mock_proxy
        (tmp_path / "app_linux_arm64.tar.gz").write_bytes(b"old")
        state["responses"].append(_release_view_response([_ASSET_A]))
        code = await run(
            ["release", "download", "v1.0", "-p", "*.tar.gz",
             "-D", str(tmp_path), "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "already exists" in capsys.readouterr().err

    async def test_skip_existing(self, mock_proxy, tmp_path):
        server, state = mock_proxy
        existing = tmp_path / "app_linux_arm64.tar.gz"
        existing.write_bytes(b"old")
        state["responses"].append(_release_view_response([_ASSET_A]))
        code = await run(
            ["release", "download", "v1.0", "-p", "*.tar.gz",
             "--skip-existing", "-D", str(tmp_path), "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert existing.read_bytes() == b"old"  # not overwritten
        assert len(state["download_requests"]) == 0  # no download attempted

    async def test_no_tag(self, capsys):
        code = await run(
            ["release", "download", "-R", "o/r"],
            "http://unused",
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "tag required" in capsys.readouterr().err

    async def test_no_matching_assets(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(_release_view_response([_ASSET_C]))
        code = await run(
            ["release", "download", "v1.0", "-p", "*.tar.gz", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "no assets" in capsys.readouterr().err

    async def test_release_view_failure(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(web.json_response({
            "exit_code": 1, "stdout": "", "stderr": "release not found",
        }))
        code = await run(
            ["release", "download", "v99", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "release not found" in capsys.readouterr().err

    async def test_help_falls_through(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(web.json_response({
            "exit_code": 0, "stdout": "Usage: gh release download", "stderr": "",
        }))
        code = await run(
            ["release", "download", "--help", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert state["requests"][0]["args"] == ["release", "download", "--help"]
