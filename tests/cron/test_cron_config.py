"""Unit tests for cron job config dataclasses and TOML parsing — cron.d/ approach.

Each job lives in its own TOML file inside a cron.d/ directory.
The filename stem (without .toml) becomes the job name.
"""
from pathlib import Path

import pytest

from archon.config.loader import CronConfig, CronJobConfig, CronPipelineStep, load_config, load_cron_jobs


# ── Helpers ───────────────────────────────────────────────────────


def _make_files(
    tmp_path: Path,
    toml_extra: str = "",
    cron_jobs: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Create env file, config.toml, and optional cron.d/ job files."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=test_token\n")
    if cron_jobs:
        cron_dir = tmp_path / "cron.d"
        cron_dir.mkdir()
        for fname, content in cron_jobs.items():
            (cron_dir / fname).write_text(content)
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        f"""[access]
allowed_user_ids = [12345]

[session]
working_directory = "{workdir}"
{toml_extra}
"""
    )
    return env_file, toml_file


# ── Happy paths ───────────────────────────────────────────────────


class TestCronConfig:
    def test_cron_absent_gives_default_disabled(self, tmp_path: Path) -> None:
        """Missing [cron] section → CronConfig(enabled=False, jobs=[])."""
        env_file, toml_file = _make_files(tmp_path)
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.enabled is False
        assert cfg.cron.jobs == []

    def test_cron_enabled_false(self, tmp_path: Path) -> None:
        env_file, toml_file = _make_files(tmp_path, "\n[cron]\nenabled = false\n")
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.enabled is False

    def test_cron_enabled_true(self, tmp_path: Path) -> None:
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.enabled is True

    def test_jobs_dir_default_is_cron_d(self, tmp_path: Path) -> None:
        """When jobs_dir is absent, it defaults to 'cron.d'."""
        env_file, toml_file = _make_files(tmp_path, "\n[cron]\nenabled = true\n")
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.jobs_dir == "cron.d"

    def test_single_tool_job_loaded_from_file(self, tmp_path: Path) -> None:
        """A single cron.d/hello.toml with a tool step is parsed correctly."""
        job_toml = (
            'schedule = "* * * * *"\n'
            "timeout_seconds = 15\n"
            "\n"
            "[[pipeline]]\n"
            'tool = "echo hello"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"hello.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert len(cfg.cron.jobs) == 1
        job = cfg.cron.jobs[0]
        assert job.name == "hello"
        assert job.schedule == "* * * * *"
        assert job.timeout_seconds == 15
        assert len(job.pipeline) == 1
        assert job.pipeline[0].tool == "echo hello"
        assert job.pipeline[0].prompt is None

    def test_single_prompt_job_loaded_from_file(self, tmp_path: Path) -> None:
        """A job file with a prompt pipeline step is parsed correctly."""
        job_toml = (
            'schedule = "0 * * * *"\n'
            "\n"
            "[[pipeline]]\n"
            'prompt = "Say hello"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"ask.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.cron.jobs[0]
        assert job.name == "ask"
        assert job.pipeline[0].prompt == "Say hello"
        assert job.pipeline[0].tool is None

    def test_defaults_applied_for_optional_fields(self, tmp_path: Path) -> None:
        """Optional fields (timeout_seconds, enabled) use defaults."""
        job_toml = (
            'schedule = "* * * * *"\n'
            "\n"
            "[[pipeline]]\n"
            'tool = "echo test"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"minimal.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.cron.jobs[0]
        assert job.timeout_seconds == 60.0
        assert job.enabled is True

    def test_multiple_jobs_loaded_alphabetically(self, tmp_path: Path) -> None:
        """Multiple job files are loaded in alphabetical order by filename."""
        first_toml = 'schedule = "* * * * *"\n\n[[pipeline]]\ntool = "echo 1"\n'
        second_toml = 'schedule = "0 * * * *"\n\n[[pipeline]]\ntool = "echo 2"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={
                "bbb-second.toml": second_toml,
                "aaa-first.toml": first_toml,
            },
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert len(cfg.cron.jobs) == 2
        assert cfg.cron.jobs[0].name == "aaa-first"
        assert cfg.cron.jobs[1].name == "bbb-second"

    def test_multi_step_pipeline_parsed(self, tmp_path: Path) -> None:
        """A job file with two [[pipeline]] entries is parsed correctly."""
        job_toml = (
            'schedule = "* * * * *"\n'
            "\n"
            "[[pipeline]]\n"
            'tool = "echo hello"\n'
            "\n"
            "[[pipeline]]\n"
            'prompt = "Summarize: {input}"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"pipeline.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.cron.jobs[0]
        assert len(job.pipeline) == 2
        assert job.pipeline[0].tool == "echo hello"
        assert job.pipeline[1].prompt == "Summarize: {input}"

    # ── Edge cases ─────────────────────────────────────────────────

    def test_jobs_dir_does_not_exist_returns_empty(self, tmp_path: Path) -> None:
        """If jobs_dir does not exist, no jobs are loaded (not an error)."""
        env_file, toml_file = _make_files(
            tmp_path,
            '\n[cron]\nenabled = true\njobs_dir = "missing-dir"\n',
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.jobs == []

    def test_job_disabled_field(self, tmp_path: Path) -> None:
        """A job file with enabled=false is loaded but marked as disabled."""
        job_toml = (
            'schedule = "* * * * *"\n'
            "enabled = false\n"
            "\n"
            "[[pipeline]]\n"
            'tool = "echo nope"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"disabled-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.jobs[0].enabled is False
        assert cfg.cron.jobs[0].name == "disabled-job"

    def test_empty_pipeline_allowed(self, tmp_path: Path) -> None:
        """A job file with no [[pipeline]] entries results in an empty pipeline."""
        job_toml = 'schedule = "* * * * *"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"empty.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.jobs[0].pipeline == []

    def test_custom_jobs_dir(self, tmp_path: Path) -> None:
        """A custom jobs_dir path is respected."""
        job_toml = 'schedule = "* * * * *"\n\n[[pipeline]]\ntool = "echo custom"\n'
        custom_dir = tmp_path / "my_jobs"
        custom_dir.mkdir()
        (custom_dir / "custom-job.toml").write_text(job_toml)
        env_file, toml_file = _make_files(
            tmp_path,
            f'\n[cron]\nenabled = true\njobs_dir = "{custom_dir}"\n',
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert len(cfg.cron.jobs) == 1
        assert cfg.cron.jobs[0].name == "custom-job"

    def test_non_toml_files_ignored(self, tmp_path: Path) -> None:
        """Non-.toml files in jobs_dir are ignored."""
        job_toml = 'schedule = "* * * * *"\n\n[[pipeline]]\ntool = "echo ok"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={
                "real-job.toml": job_toml,
                "readme.txt": "this is not a job",
                "draft.toml.bak": "also not a job",
            },
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert len(cfg.cron.jobs) == 1
        assert cfg.cron.jobs[0].name == "real-job"

    def test_jobs_loaded_even_when_cron_disabled(self, tmp_path: Path) -> None:
        """Jobs are discovered from disk even when cron is disabled (scheduler won't run them)."""
        job_toml = 'schedule = "* * * * *"\n\n[[pipeline]]\ntool = "echo hi"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = false\n",
            cron_jobs={"my-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.enabled is False
        assert len(cfg.cron.jobs) == 1

    def test_jobs_dir_stored_on_cron_config(self, tmp_path: Path) -> None:
        """The jobs_dir value from TOML is stored on cfg.cron.jobs_dir."""
        env_file, toml_file = _make_files(
            tmp_path,
            '\n[cron]\nenabled = true\njobs_dir = "cron.d"\n',
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.jobs_dir == "cron.d"

    def test_timezone_field_loaded_from_toml(self, tmp_path: Path) -> None:
        """A job file with timezone = 'Europe/Budapest' sets the timezone field."""
        job_toml = (
            'schedule = "0 9 * * *"\n'
            'timezone = "Europe/Budapest"\n'
            "\n"
            "[[pipeline]]\n"
            'tool = "echo morning"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"tz-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.cron.jobs[0]
        assert job.timezone == "Europe/Budapest"

    def test_timezone_field_defaults_to_none(self, tmp_path: Path) -> None:
        """A job file without a timezone field defaults to None (local time)."""
        job_toml = (
            'schedule = "* * * * *"\n'
            "\n"
            "[[pipeline]]\n"
            'tool = "echo test"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"no-tz.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.cron.jobs[0]
        assert job.timezone is None

    def test_timezone_utc_loaded(self, tmp_path: Path) -> None:
        """timezone = 'UTC' is a valid value and is stored as-is."""
        job_toml = (
            'schedule = "0 0 * * *"\n'
            'timezone = "UTC"\n'
            "\n"
            "[[pipeline]]\n"
            'tool = "echo midnight"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"utc-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.jobs[0].timezone == "UTC"


# ── load_cron_jobs() unit tests ───────────────────────────────────


class TestLoadCronJobsFunction:
    def test_returns_empty_for_missing_directory(self, tmp_path: Path) -> None:
        result = load_cron_jobs(tmp_path / "nonexistent")
        assert result == []

    def test_returns_empty_for_empty_directory(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        result = load_cron_jobs(jobs_dir)
        assert result == []

    def test_stem_becomes_job_name(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        (jobs_dir / "my-job.toml").write_text(
            'schedule = "* * * * *"\n\n[[pipeline]]\ntool = "echo x"\n'
        )
        result = load_cron_jobs(jobs_dir)
        assert result[0].name == "my-job"

    def test_base_dir_resolves_relative_path(self, tmp_path: Path) -> None:
        """load_cron_jobs("cron.d", base_dir=tmp_path) resolves correctly."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        (jobs_dir / "test.toml").write_text(
            'schedule = "0 * * * *"\n\n[[pipeline]]\ntool = "echo base"\n'
        )
        result = load_cron_jobs("cron.d", base_dir=tmp_path)
        assert len(result) == 1
        assert result[0].name == "test"

    def test_alphabetical_sorting(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        for name in ["zzz.toml", "aaa.toml", "mmm.toml"]:
            (jobs_dir / name).write_text(
                'schedule = "* * * * *"\n\n[[pipeline]]\ntool = "echo x"\n'
            )
        result = load_cron_jobs(jobs_dir)
        assert [j.name for j in result] == ["aaa", "mmm", "zzz"]


# ── Live test ─────────────────────────────────────────────────────


@pytest.mark.live
def test_live_cron_d_directory_loads_real_files(tmp_path: Path) -> None:
    """Integration: real cron.d/ directory with two job files loads correctly."""
    health_toml = (
        'schedule = "0 8 * * *"\n'
        "timeout_seconds = 30\n"
        "\n"
        "[[pipeline]]\n"
        'tool = "echo health check"\n'
        "\n"
        "[[pipeline]]\n"
        'prompt = "Summarize in one line: {input}"\n'
    )
    echo_toml = (
        'schedule = "* * * * *"\n'
        "enabled = false\n"
        "\n"
        "[[pipeline]]\n"
        'tool = "echo hello from cron"\n'
    )
    env_file, toml_file = _make_files(
        tmp_path,
        "\n[cron]\nenabled = true\n",
        cron_jobs={
            "health-check.toml": health_toml,
            "echo-test.toml": echo_toml,
        },
    )
    cfg = load_config(env_file=env_file, config_file=toml_file)

    assert cfg.cron.enabled is True
    assert cfg.cron.jobs_dir == "cron.d"
    assert len(cfg.cron.jobs) == 2

    # Alphabetical: echo-test before health-check
    assert cfg.cron.jobs[0].name == "echo-test"
    assert cfg.cron.jobs[0].enabled is False
    assert cfg.cron.jobs[0].pipeline[0].tool == "echo hello from cron"

    assert cfg.cron.jobs[1].name == "health-check"
    assert cfg.cron.jobs[1].schedule == "0 8 * * *"
    assert cfg.cron.jobs[1].timeout_seconds == 30.0
    assert len(cfg.cron.jobs[1].pipeline) == 2
    assert cfg.cron.jobs[1].pipeline[0].tool == "echo health check"
    assert cfg.cron.jobs[1].pipeline[1].prompt == "Summarize in one line: {input}"
