import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from search import strata


class StrataEnvTests(unittest.TestCase):
    def test_wait_strata_answers_http_error_and_retries_connection_errors(self):
        import urllib.error

        http = urllib.error.HTTPError("u", 401, "no", {}, None)  # type: ignore[arg-type]  # headers are irrelevant here
        with mock.patch("urllib.request.urlopen", side_effect=http):
            self.assertTrue(strata.wait_strata(tries=3))  # any HTTP answer means up
        with mock.patch("urllib.request.urlopen", side_effect=[OSError, mock.MagicMock()]) as uo, mock.patch("time.sleep") as sl:
            self.assertTrue(strata.wait_strata(tries=3))
        self.assertEqual(uo.call_count, 2)
        sl.assert_called_once_with(1)
        with mock.patch("urllib.request.urlopen", side_effect=OSError), mock.patch("time.sleep") as sl:
            self.assertFalse(strata.wait_strata(tries=3))
        self.assertEqual([c.args[0] for c in sl.call_args_list], [1, 2])  # no sleep after the last try

    def test_key_prefers_env_over_the_state_file(self):
        with mock.patch.dict(os.environ, {"STRATA_KEY": "k1", "ANTHROPIC_AUTH_TOKEN": "k2"}):
            self.assertEqual(strata.strata_key(), "k1")
        with mock.patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "k2"}):
            os.environ.pop("STRATA_KEY", None)
            self.assertEqual(strata.strata_key(), "k2")

    def test_zero_means_off_without_probing(self):
        with mock.patch.dict(os.environ, {"STRATA": "0"}), mock.patch("urllib.request.urlopen") as uo:
            self.assertFalse(strata.strata_up())
            self.assertFalse(strata.preflight())
        uo.assert_not_called()

    def test_one_and_unset_probe_the_server(self):
        for env in ({"STRATA": "1"}, {}):
            with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen"):
                os.environ.pop("STRATA", None) if not env else None
                self.assertTrue(strata.strata_up())
                self.assertTrue(strata.preflight())

    def test_preflight_sets_zero_when_down(self):
        with mock.patch.dict(os.environ, {"STRATA_PHASE_FILE": "/nonexistent"}), mock.patch("urllib.request.urlopen", side_effect=OSError):
            os.environ.pop("STRATA", None)
            self.assertFalse(strata.preflight())
            self.assertEqual(os.environ["STRATA"], "0")

    def test_preflight_waits_while_phase_says_loading(self):
        with tempfile.TemporaryDirectory() as d:
            ph = os.path.join(d, "phase")
            open(ph, "w").write("loading model\n")
            env = {"STRATA_PHASE_FILE": ph, "STRATA_LOAD_WAIT": "30"}
            ups = iter([False, False, True])  # up on the third probe
            with (
                mock.patch.dict(os.environ, env),
                mock.patch.object(strata, "strata_up", side_effect=lambda: next(ups)),
                mock.patch.object(strata.time, "sleep"),
            ):
                os.environ.pop("STRATA", None)
                self.assertTrue(strata.preflight())
                self.assertNotEqual(os.environ.get("STRATA"), "0")

    def test_preflight_gives_up_when_not_loading(self):
        with tempfile.TemporaryDirectory() as d:
            ph = os.path.join(d, "phase")
            open(ph, "w").write("ready\n")
            with mock.patch.dict(os.environ, {"STRATA_PHASE_FILE": ph}), mock.patch.object(strata, "strata_up", return_value=False):
                os.environ.pop("STRATA", None)
                self.assertFalse(strata.preflight())
                self.assertEqual(os.environ["STRATA"], "0")


class StartOnDemandTests(unittest.TestCase):
    def test_preflight_starts_strata_with_modelctl_when_down(self):
        ups = iter([False, True])
        with (
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch.object(strata, "strata_up", side_effect=lambda: next(ups)),
            mock.patch.object(strata.shutil, "which", return_value="/usr/local/bin/modelctl"),
            mock.patch.object(strata.Path, "exists", return_value=True),
            mock.patch.object(strata.subprocess, "run", return_value=mock.Mock(returncode=0)) as run,
        ):
            for k in ("STRATA", "STRATA_URL", "STRATA_NO_START"):
                os.environ.pop(k, None)
            self.assertTrue(strata.preflight())
            self.assertEqual(run.call_args[0][0], ["/usr/local/bin/modelctl", "strata", "start"])

    def test_no_start_when_modelctl_refuses(self):
        with (
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch.object(strata, "strata_up", return_value=False),
            mock.patch.object(strata.shutil, "which", return_value="/usr/local/bin/modelctl"),
            mock.patch.object(strata.Path, "exists", return_value=True),
            mock.patch.object(strata.subprocess, "run", return_value=mock.Mock(returncode=1)),
        ):
            for k in ("STRATA", "STRATA_URL", "STRATA_NO_START"):
                os.environ.pop(k, None)
            self.assertFalse(strata.preflight())
            self.assertEqual(os.environ["STRATA"], "0")
            os.environ.pop("STRATA", None)


if __name__ == "__main__":
    unittest.main()
