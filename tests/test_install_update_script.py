import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_DIR / "scripts" / "install-or-update-on-pi.sh"


class InstallUpdateScriptTest(unittest.TestCase):
    def test_help_documents_reset_state_option(self):
        result = subprocess.run(
            ["sh", str(SCRIPT_PATH), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("[--reset-state]", result.stdout)
        self.assertIn(
            "--reset-state  Delete saved runtime state and stats before restart.",
            result.stdout,
        )

    def test_default_update_preserves_runtime_state(self):
        result = self._run_script()

        self.assertTrue(result["state_file_exists"])
        self.assertIn(
            f"Preserving runtime state: {result['state_file_path']}",
            result["completed"].stdout,
        )

    def test_reset_state_option_deletes_runtime_state(self):
        result = self._run_script("--reset-state")

        self.assertFalse(result["state_file_exists"])
        self.assertIn(
            f"Reset runtime state: {result['state_file_path']}",
            result["completed"].stdout,
        )

    def _run_script(self, *args):
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            bin_dir = temp_dir / "bin"
            config_dir = temp_dir / "config"
            venv_dir = temp_dir / "venv"
            update_bin = temp_dir / "update-white-noise-keeper"
            state_file = temp_dir / "state.json"
            fake_root = temp_dir / "fake-root"

            bin_dir.mkdir()
            config_dir.mkdir()
            fake_root.mkdir()
            state_file.write_text("keep me", encoding="utf-8")

            self._write_stub(
                bin_dir / "id",
                """#!/bin/sh
                if [ "$1" = "-u" ]; then
                  echo 0
                  exit 0
                fi
                exit 0
                """,
            )
            self._write_stub(bin_dir / "git", "#!/bin/sh\nexit 0\n")
            self._write_stub(
                bin_dir / "install",
                f"""#!/bin/sh
                for arg in "$@"; do
                  last="$arg"
                done
                dest="$last"
                prev=""
                src=""
                for arg in "$@"; do
                  if [ "$arg" = "$dest" ]; then
                    src="$prev"
                    break
                  fi
                  prev="$arg"
                done
                case "$dest" in
                  /etc/systemd/system/*) dest="{fake_root}/$(basename "$dest")" ;;
                esac
                mkdir -p "$(dirname "$dest")"
                cp "$src" "$dest"
                """,
            )
            self._write_stub(bin_dir / "systemctl", "#!/bin/sh\nexit 0\n")
            self._write_stub(
                bin_dir / "uv",
                """#!/bin/sh
                if [ "$1" = "venv" ]; then
                  for arg in "$@"; do
                    target="$arg"
                  done
                  mkdir -p "$target/bin"
                  cat >"$target/bin/python" <<'EOF'
#!/bin/sh
exec python3 "$@"
EOF
                  chmod +x "$target/bin/python"
                  exit 0
                fi
                if [ "$1" = "pip" ]; then
                  exit 0
                fi
                exit 0
                """,
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "CONFIG_DIR": str(config_dir),
                    "CONFIG_FILE": str(config_dir / "config.toml"),
                    "START_SERVICE": "0",
                    "RUN_TESTS": "0",
                    "REPO_DIR": str(REPO_DIR),
                    "UPDATE_BIN": str(update_bin),
                    "STATE_FILE": str(state_file),
                    "PYTHON_BIN": sys.executable,
                    "UV_BIN": str(bin_dir / "uv"),
                    "VENV_DIR": str(venv_dir),
                }
            )
            completed = subprocess.run(
                ["sh", str(SCRIPT_PATH), *args],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            return {
                "completed": completed,
                "state_file_exists": state_file.exists(),
                "state_file_path": str(state_file),
            }

    def _write_stub(self, path: Path, content: str) -> None:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
