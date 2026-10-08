"""Offline qrm regressions; HTTP is always mocked."""

from __future__ import annotations

import os
import tempfile
import io
import contextlib
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("qrm", str(ROOT / "linux/qrm"))
spec = importlib.util.spec_from_loader(loader.name, loader)
qrm = importlib.util.module_from_spec(spec)
loader.exec_module(qrm)


def response(data, status=200):
    result = Mock(status_code=status, content=b"json")
    result.json.return_value = data
    return result


class QrmTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            qrm.requests.sessions.Session,
            "request",
            side_effect=AssertionError("Unexpected network request"),
        )
        guard.start()
        self.addCleanup(guard.stop)

    def test_all_repository_pages_are_flattened(self):
        first = {"namespace": "team", "name": "one"}
        second = {"namespace": "team", "name": "two"}
        with patch.object(
            qrm.requests,
            "get",
            side_effect=[
                response({"repositories": [first], "next_page": "cursor&x"}),
                response({"repositories": [second]}),
            ],
        ) as get:
            self.assertEqual(qrm.getRepos("registry.example", True), [first, second])
            self.assertEqual(get.call_count, 2)

    def test_pagination_terminates_safely(self):
        for cursor in (None, ""):
            with (
                self.subTest(cursor=cursor),
                patch.object(
                    qrm.requests,
                    "get",
                    side_effect=[
                        response({"repositories": [], "next_page": cursor}),
                        AssertionError("extra request"),
                    ],
                ) as get,
            ):
                self.assertEqual(qrm.getRepos("registry.example", True), [])
                self.assertEqual(get.call_count, 1)
        with patch.object(
            qrm.requests,
            "get",
            side_effect=[
                response({"repositories": [], "next_page": "repeat"}),
                response({"repositories": [], "next_page": "repeat"}),
                AssertionError("unbounded loop"),
            ],
        ):
            with self.assertRaisesRegex(ValueError, "pagination"):
                qrm.getRepos("registry.example", True)

    def test_repository_response_is_validated(self):
        invalid = [
            [],
            {},
            {"repositories": {}},
            {"repositories": [None]},
            {"repositories": [{"namespace": "a", "name": ""}]},
            {"repositories": [], "next_page": False},
        ]
        for data in invalid:
            with (
                self.subTest(data=data),
                patch.object(qrm.requests, "get", return_value=response(data)),
            ):
                with self.assertRaisesRegex(ValueError, "Invalid"):
                    qrm.getRepos("registry.example", True)
        with patch.object(qrm.requests, "get", return_value=response({"repositories": []}, 403)):
            with self.assertRaisesRegex(ValueError, "HTTP 403"):
                qrm.getRepos("registry.example", True)

    def test_invalid_arguments_fail_before_connecting(self):
        cases = [
            ["-a", "listtags"],
            ["-a", "deltag", "-p", "team/image"],
            ["-r", "https://registry.example", "-a", "listrepos"],
            ["-a", "listtags", "-p", "bad"],
            ["-a", "listtags", "-p", "../image"],
        ]
        for args in cases:
            with self.subTest(args=args), patch.object(qrm, "checkRegistry") as check:
                with patch("sys.argv", ["qrm", "-r", "registry.example"] + args):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        qrm.main(["-r", "registry.example"] + args)
                check.assert_not_called()

    def test_delete_requires_confirmation_and_dry_run_is_offline(self):
        args = ["-r", "registry.example", "-a", "deltag", "-p", "team/im?age", "-t", "v/1#"]
        with (
            patch.object(qrm, "checkRegistry") as check,
            patch.object(qrm.requests, "delete") as delete,
        ):
            with (
                patch("sys.stdin.isatty", return_value=False),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    qrm.main(args)
            check.assert_not_called()
            delete.assert_not_called()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                qrm.main(args + ["--dry-run"])
            self.assertIn("/team/im%3Fage/tag/v%2F1%23", output.getvalue())
            check.assert_not_called()
            delete.assert_not_called()

    def test_confirmed_delete_uses_encoded_endpoint_and_accepts_empty_success(self):
        result = response(None, 204)
        result.content = b""
        with (
            patch.object(qrm, "checkRegistry"),
            patch.object(qrm.requests, "delete", return_value=result) as delete,
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                qrm.main(
                    [
                        "-r",
                        "registry.example",
                        "-a",
                        "deltag",
                        "-p",
                        "team/im?age",
                        "-t",
                        "v/1#",
                        "--yes",
                    ]
                )
            self.assertEqual(
                delete.call_args.args[0],
                "https://registry.example/api/v1/repository/team/im%3Fage/tag/v%2F1%23",
            )
            result.json.assert_not_called()

    def test_requests_are_bounded_and_status_checked(self):
        operations = [
            (lambda: qrm.getRepos("registry.example", True), "get", {"repositories": []}),
            (lambda: qrm.getRepoTags("registry.example", "team/image"), "get", {"tags": {}}),
            (lambda: qrm.deleteTag("registry.example", "team/image", "v1"), "delete", {}),
            (lambda: qrm.checkRegistry("registry.example"), "get", {}),
        ]
        for operation, method, data in operations:
            with (
                self.subTest(method=method, data=data),
                patch.object(qrm.requests, method, return_value=response(data)) as request,
            ):
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    operation()
                self.assertEqual(request.call_args.kwargs.get("timeout"), (5, 30))
                self.assertFalse(request.call_args.kwargs.get("allow_redirects", True))
            with patch.object(qrm.requests, method, return_value=response(data, 401)):
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaisesRegex(ValueError, "HTTP 401"):
                        operation()

    def test_connectivity_failure_stops_cli_without_stdout(self):
        with patch.object(
            qrm.requests, "get", side_effect=qrm.requests.ConnectionError("secret detail")
        ) as get:
            with patch.object(qrm.requests, "delete") as delete:
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit):
                        qrm.main(
                            [
                                "-r",
                                "registry.example",
                                "-a",
                                "deltag",
                                "-p",
                                "team/image",
                                "-t",
                                "v1",
                                "--yes",
                            ]
                        )
                self.assertEqual(out.getvalue(), "")
                self.assertNotIn("secret detail", err.getvalue())
                self.assertEqual(get.call_count, 1)
                delete.assert_not_called()

    def test_silent_keeps_json_stdout_and_suppresses_checks_only(self):
        for silent in ([], ["--silent"]):
            out, err = io.StringIO(), io.StringIO()
            with patch.object(
                qrm.requests, "get", side_effect=[response({}), response({"repositories": []})]
            ):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    qrm.main(["-r", "registry.example", "-a", "listrepos"] + silent)
            self.assertEqual(out.getvalue().strip(), "[]")
            self.assertEqual(bool(err.getvalue()), not bool(silent))

    def test_explicit_bearer_auth_from_environment_or_file(self):
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / "token"
            token_file.write_text("file-token\n")
            for flags, expected in [
                ([], "env-token"),
                (["--token-file", str(token_file)], "file-token"),
            ]:
                out, err = io.StringIO(), io.StringIO()
                with (
                    patch.dict(os.environ, {"QRM_TOKEN": "env-token"}),
                    patch.object(
                        qrm.requests,
                        "get",
                        side_effect=[response({}), response({"repositories": []})],
                    ) as get,
                ):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        qrm.main(["-r", "registry.example", "-a", "listrepos"] + flags)
                    for call in get.call_args_list:
                        self.assertEqual(
                            call.kwargs["headers"].get("Authorization"), "Bearer " + expected
                        )
                        self.assertTrue(call.kwargs.get("auth"))  # Prevent implicit .netrc auth.
                    self.assertNotIn(expected, out.getvalue() + err.getvalue())
        self.assertNotIn("Authorization", qrm.my_headers)

    def test_bad_credentials_fail_locally_without_leaking(self):
        cases = [
            ({"QRM_TOKEN": "secret\nheader"}, []),
            ({"QRM_TOKEN": ""}, []),
            ({}, ["--token-file", "/nonexistent/qrm-token"]),
        ]
        for env, flags in cases:
            out, err = io.StringIO(), io.StringIO()
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(qrm, "checkRegistry") as check,
            ):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit):
                        qrm.main(["-r", "registry.example", "-a", "listrepos"] + flags)
                check.assert_not_called()
                self.assertEqual(out.getvalue(), "")
                self.assertNotIn("secret", err.getvalue())

    def test_pagination_has_a_hard_limit_and_encodes_cursors(self):
        with (
            patch.object(qrm, "MAX_PAGES", 2, create=True),
            patch.object(
                qrm.requests,
                "get",
                side_effect=[
                    response({"repositories": [], "next_page": "a&b"}),
                    response({"repositories": [], "next_page": "fresh"}),
                    AssertionError("page limit not enforced"),
                ],
            ) as get,
        ):
            with self.assertRaisesRegex(ValueError, "pagination"):
                qrm.getRepos("registry.example", True)
            self.assertEqual(get.call_args.kwargs["params"]["next_page"], "a&b")
            self.assertEqual(get.call_args.kwargs["params"]["public"], "true")

    def test_tag_responses_reject_bad_schema_and_untrusted_json_errors(self):
        for data in ([], {}, {"tags": []}, {"tags": None}, {"tags": {"x": []}}):
            with (
                self.subTest(data=data),
                patch.object(qrm.requests, "get", return_value=response(data)),
            ):
                with self.assertRaisesRegex(ValueError, "Invalid"):
                    qrm.getRepoTags("registry.example", "team/image")
        bad = response(None)
        bad.json.side_effect = ValueError("secret server content")
        for operation, method in [
            (lambda: qrm.getRepos("registry.example", True), "get"),
            (lambda: qrm.getRepoTags("registry.example", "team/image"), "get"),
            (lambda: qrm.deleteTag("registry.example", "team/image", "v1"), "delete"),
        ]:
            with patch.object(qrm.requests, method, return_value=bad):
                with self.assertRaisesRegex(ValueError, "^Invalid JSON response$"):
                    operation()

    def test_unsafe_arguments_are_rejected_before_network(self):
        cases = [
            ["-r", host]
            for host in ("host:0", "host:65536", "host:abc", "host\nname", ".", "host:")
        ]
        cases += [["-p", repo] for repo in ("a/", "/b", "a/b/c", "a/..", "a/b\n")]
        cases += [["-t", tag] for tag in (".", "..", "", "v1\n")]
        cases += [["-a", "listrepos", "--dry-run"], ["-a", "listtags", "--yes"]]
        for flags in cases:
            with self.subTest(flags=flags), patch.object(qrm, "checkRegistry") as check:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    qrm.main(
                        [
                            "-r",
                            "registry.example",
                            "-a",
                            "deltag",
                            "-p",
                            "team/image",
                            "-t",
                            "v1",
                            "--yes",
                        ]
                        + flags
                    )
                check.assert_not_called()

    def test_tag_lookup_encodes_each_repository_component(self):
        with patch.object(qrm.requests, "get", return_value=response({"tags": {"v1": {}}})) as get:
            self.assertEqual(qrm.getRepoTags("registry.example:8443", "te%am/im?age"), {"v1": {}})
            self.assertEqual(
                get.call_args.args[0],
                "https://registry.example:8443/api/v1/repository/te%25am/im%3Fage",
            )
            self.assertEqual(get.call_args.kwargs["params"], {"includeTags": "true"})

    def test_default_action_keeps_table_and_includes_later_pages(self):
        pages = [
            response({}),
            response({"repositories": [{"namespace": "team", "name": "one"}], "next_page": "next"}),
            response({"repositories": [{"namespace": "team", "name": "two"}]}),
            response({"tags": {"v1": {}, "latest": {}}}),
            response({"tags": {}}),
        ]
        out = io.StringIO()
        with (
            patch.object(qrm.requests, "get", side_effect=pages),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            qrm.main(["-r", "registry.example"])
        self.assertIn("Repository", out.getvalue())
        self.assertIn("team/one", out.getvalue())
        self.assertIn("team/two", out.getvalue())
        self.assertIn("v1,latest", out.getvalue())

    def test_interactive_delete_needs_exact_yes(self):
        args = ["-r", "registry.example", "-a", "deltag", "-p", "team/image", "-t", "v1"]
        for answer in ("no\n", "", "y\n", "yes\n"):
            with (
                self.subTest(answer=answer),
                patch("sys.stdin", io.StringIO(answer)),
                patch("sys.stdin.isatty", return_value=True),
            ):
                with (
                    patch.object(qrm, "checkRegistry") as check,
                    patch.object(qrm.requests, "delete", return_value=response({})) as delete,
                ):
                    with (
                        contextlib.redirect_stdout(io.StringIO()),
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        if answer == "yes\n":
                            qrm.main(args)
                            self.assertEqual(delete.call_count, 1)
                        else:
                            with self.assertRaises(SystemExit):
                                qrm.main(args)
                            check.assert_not_called()
                            delete.assert_not_called()

    def test_later_page_failure_never_prints_partial_json(self):
        with patch.object(
            qrm.requests,
            "get",
            side_effect=[
                response({}),
                response({"repositories": [], "next_page": "next"}),
                response({}, 503),
            ],
        ):
            out, err = io.StringIO(), io.StringIO()
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
                self.assertRaises(SystemExit),
            ):
                qrm.main(["-r", "registry.example", "-a", "listrepos", "--silent"])
            self.assertEqual(out.getvalue(), "")
            self.assertIn("HTTP 503", err.getvalue())

    def test_empty_delete_failure_is_not_success_and_timeout_not_retried(self):
        denied = response(None, 403)
        denied.content = b""
        for failure in (denied, qrm.requests.Timeout("hidden detail")):
            with patch.object(qrm.requests, "delete", side_effect=[failure]) as delete:
                with self.assertRaises(ValueError):
                    qrm.deleteTag("registry.example", "team/image", "v1")
                self.assertEqual(delete.call_count, 1)
                denied.json.assert_not_called()

    def test_repository_api_identifiers_cannot_change_path_structure(self):
        for namespace, name in (
            ("..", "image"),
            ("team", ".."),
            ("team/other", "image"),
            ("team", "im\nage"),
        ):
            with (
                self.subTest(namespace=namespace, name=name),
                patch.object(
                    qrm.requests,
                    "get",
                    return_value=response(
                        {"repositories": [{"namespace": namespace, "name": name}]}
                    ),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "Invalid repository"):
                    qrm.getRepos("registry.example", True)


if __name__ == "__main__":
    unittest.main()
