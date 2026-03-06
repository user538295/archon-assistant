"""Unit tests for cron job config dataclasses and TOML parsing — cron.d/ approach.

Each job lives in its own TOML file inside a cron.d/ directory.
The filename stem (without .toml) becomes the job name.
"""
from pathlib import Path

import pytest

from archon.config.loader import CronConfig, CronJobConfig, CronPipelineStep, _parse_pipeline, load_config, load_cron_jobs


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
            "[pipeline]\n"
            'health_check_tool = "echo hello"\n'
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
        assert job.pipeline[0].kind == "tool"
        assert job.pipeline[0].value == "echo hello"
        assert job.pipeline[0].name == "health_check_tool"

    def test_single_prompt_job_loaded_from_file(self, tmp_path: Path) -> None:
        """A job file with a prompt pipeline step is parsed correctly."""
        job_toml = (
            'schedule = "0 * * * *"\n'
            "\n"
            "[pipeline]\n"
            'say_hello_prompt = "Say hello"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"ask.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.cron.jobs[0]
        assert job.name == "ask"
        assert job.pipeline[0].kind == "prompt"
        assert job.pipeline[0].value == "Say hello"
        assert job.pipeline[0].name == "say_hello_prompt"

    def test_defaults_applied_for_optional_fields(self, tmp_path: Path) -> None:
        """Optional fields (timeout_seconds, enabled) use defaults."""
        job_toml = (
            'schedule = "* * * * *"\n'
            "\n"
            "[pipeline]\n"
            'echo_tool = "echo test"\n'
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
        first_toml = 'schedule = "* * * * *"\n\n[pipeline]\necho_tool = "echo 1"\n'
        second_toml = 'schedule = "0 * * * *"\n\n[pipeline]\necho_tool = "echo 2"\n'
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
        """A job file with two pipeline steps is parsed correctly."""
        job_toml = (
            'schedule = "* * * * *"\n'
            "\n"
            "[pipeline]\n"
            'echo_tool = "echo hello"\n'
            'summarize_prompt = "Summarize: {echo_tool}"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"pipeline.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.cron.jobs[0]
        assert len(job.pipeline) == 2
        assert job.pipeline[0].kind == "tool"
        assert job.pipeline[0].value == "echo hello"
        assert job.pipeline[1].kind == "prompt"
        assert job.pipeline[1].value == "Summarize: {echo_tool}"

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
            "[pipeline]\n"
            'nope_tool = "echo nope"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"disabled-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.jobs[0].enabled is False
        assert cfg.cron.jobs[0].name == "disabled-job"

    def test_empty_pipeline_has_empty_list_and_validation_error(self, tmp_path: Path) -> None:
        """A job file with no [pipeline] entries has an empty list AND a validation_error set."""
        job_toml = 'schedule = "* * * * *"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[cron]\nenabled = true\n",
            cron_jobs={"empty.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.cron.jobs[0].pipeline == []
        assert cfg.cron.jobs[0].validation_error is not None

    def test_custom_jobs_dir(self, tmp_path: Path) -> None:
        """A custom jobs_dir path is respected."""
        job_toml = 'schedule = "* * * * *"\n\n[pipeline]\ncustom_tool = "echo custom"\n'
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
        job_toml = 'schedule = "* * * * *"\n\n[pipeline]\nok_tool = "echo ok"\n'
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
        job_toml = 'schedule = "* * * * *"\n\n[pipeline]\nhi_tool = "echo hi"\n'
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
            "[pipeline]\n"
            'morning_tool = "echo morning"\n'
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
            "[pipeline]\n"
            'test_tool = "echo test"\n'
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
            "[pipeline]\n"
            'midnight_tool = "echo midnight"\n'
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
            'schedule = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        result = load_cron_jobs(jobs_dir)
        assert result[0].name == "my-job"

    def test_base_dir_resolves_relative_path(self, tmp_path: Path) -> None:
        """load_cron_jobs("cron.d", base_dir=tmp_path) resolves correctly."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        (jobs_dir / "test.toml").write_text(
            'schedule = "0 * * * *"\n\n[pipeline]\nbase_tool = "echo base"\n'
        )
        result = load_cron_jobs("cron.d", base_dir=tmp_path)
        assert len(result) == 1
        assert result[0].name == "test"

    def test_alphabetical_sorting(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        for name in ["zzz.toml", "aaa.toml", "mmm.toml"]:
            (jobs_dir / name).write_text(
                'schedule = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
            )
        result = load_cron_jobs(jobs_dir)
        assert [j.name for j in result] == ["aaa", "mmm", "zzz"]


# ── Pipeline validation tests ─────────────────────────────────────


class TestPipelineValidation:
    def _load_job(self, tmp_path: Path, pipeline_toml: str) -> CronJobConfig:
        """Helper: load a single job from a TOML string."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir(exist_ok=True)
        (jobs_dir / "test-job.toml").write_text(
            f'schedule = "* * * * *"\n\n{pipeline_toml}'
        )
        result = load_cron_jobs(jobs_dir)
        assert len(result) == 1
        return result[0]

    def test_unknown_suffix_sets_validation_error(self, tmp_path: Path) -> None:
        job = self._load_job(tmp_path, '[pipeline]\nbad_step = "echo hi"\n')
        assert job.validation_error is not None
        assert "bad_step" in job.validation_error

    def test_forward_reference_sets_validation_error(self, tmp_path: Path) -> None:
        pipeline = (
            "[pipeline]\n"
            'step_a_prompt = "Use: {step_b_tool}"\n'
            'step_b_tool = "echo hi"\n'
        )
        job = self._load_job(tmp_path, pipeline)
        assert job.validation_error is not None
        assert "step_b_tool" in job.validation_error

    def test_self_reference_sets_validation_error(self, tmp_path: Path) -> None:
        pipeline = '[pipeline]\nstep_a_tool = "echo {step_a_tool}"\n'
        job = self._load_job(tmp_path, pipeline)
        assert job.validation_error is not None

    def test_unknown_reference_sets_validation_error(self, tmp_path: Path) -> None:
        pipeline = '[pipeline]\nstep_a_prompt = "Use: {nonexistent_tool}"\n'
        job = self._load_job(tmp_path, pipeline)
        assert job.validation_error is not None
        assert "nonexistent_tool" in job.validation_error

    def test_valid_backward_reference_no_error(self, tmp_path: Path) -> None:
        pipeline = (
            "[pipeline]\n"
            'step_a_tool = "echo hello"\n'
            'step_b_prompt = "Summarize: {step_a_tool}"\n'
        )
        job = self._load_job(tmp_path, pipeline)
        assert job.validation_error is None

    def test_valid_pipeline_no_error(self, tmp_path: Path) -> None:
        pipeline = '[pipeline]\nmy_tool = "echo hi"\n'
        job = self._load_job(tmp_path, pipeline)
        assert job.validation_error is None

    def test_empty_pipeline_is_validation_error(self, tmp_path: Path) -> None:
        job = self._load_job(tmp_path, "")
        assert job.validation_error is not None
        assert "empty pipeline" in job.validation_error

    def test_multiple_steps_all_valid(self, tmp_path: Path) -> None:
        pipeline = (
            "[pipeline]\n"
            'tool_1_tool = "echo one"\n'
            'tool_2_tool = "echo two {tool_1_tool}"\n'
            'merge_prompt = "Merge: {tool_1_tool} and {tool_2_tool}"\n'
        )
        job = self._load_job(tmp_path, pipeline)
        assert job.validation_error is None
        assert len(job.pipeline) == 3

    def test_error_message_names_the_problematic_step(self, tmp_path: Path) -> None:
        pipeline = '[pipeline]\nbad_key = "echo hi"\n'
        job = self._load_job(tmp_path, pipeline)
        assert job.validation_error is not None
        assert "bad_key" in job.validation_error

    def test_dollar_brace_not_treated_as_ref(self, tmp_path: Path) -> None:
        """${HOME} in a command is NOT treated as a {ref} reference."""
        pipeline = '[pipeline]\nhome_tool = "echo ${HOME}"\n'
        job = self._load_job(tmp_path, pipeline)
        assert job.validation_error is None


# ── _parse_pipeline dollar-escape tests ───────────────────────────


class TestParsePipelineDollarEscape:
    """Unit tests for the $ escape mechanism in _parse_pipeline."""

    def test_dollar_prefix_not_validated_as_forward_ref(self, tmp_path: Path) -> None:
        """${step2_tool} in step1's value is not treated as a forward ref — no validation error."""
        # step1_prompt references ${step2_tool} with $ prefix → valid (not checked)
        pipeline_data = {
            "step1_prompt": "run this: ${step2_tool}",
            "step2_tool": "echo hello",
        }
        steps, error = _parse_pipeline(pipeline_data, "test-job")
        assert error is None
        assert len(steps) == 2

    def test_dollar_prefix_not_treated_as_backward_ref(self, tmp_path: Path) -> None:
        """${step1_tool} in step2's value is not substituted (escape), so it is left as-is."""
        pipeline_data = {
            "step1_tool": "echo hello",
            "step2_prompt": "summarize: ${step1_tool}",
        }
        steps, error = _parse_pipeline(pipeline_data, "test-job")
        assert error is None
        # step2_prompt's value is stored verbatim (substitution happens at runtime)
        assert steps[1].value == "summarize: ${step1_tool}"


# ── Live test ─────────────────────────────────────────────────────


@pytest.mark.live
def test_live_cron_d_directory_loads_real_files(tmp_path: Path) -> None:
    """Integration: real cron.d/ directory with two job files loads correctly."""
    health_toml = (
        'schedule = "0 8 * * *"\n'
        "timeout_seconds = 30\n"
        "\n"
        "[pipeline]\n"
        'health_check_tool = "echo health check"\n'
        'summarize_prompt = "Summarize in one line: {health_check_tool}"\n'
    )
    echo_toml = (
        'schedule = "* * * * *"\n'
        "enabled = false\n"
        "\n"
        "[pipeline]\n"
        'hello_tool = "echo hello from cron"\n'
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
    assert cfg.cron.jobs[0].pipeline[0].kind == "tool"
    assert cfg.cron.jobs[0].pipeline[0].value == "echo hello from cron"

    assert cfg.cron.jobs[1].name == "health-check"
    assert cfg.cron.jobs[1].schedule == "0 8 * * *"
    assert cfg.cron.jobs[1].timeout_seconds == 30.0
    assert len(cfg.cron.jobs[1].pipeline) == 2
    assert cfg.cron.jobs[1].pipeline[0].kind == "tool"
    assert cfg.cron.jobs[1].pipeline[0].value == "echo health check"
    assert cfg.cron.jobs[1].pipeline[1].kind == "prompt"
    assert cfg.cron.jobs[1].pipeline[1].value == "Summarize in one line: {health_check_tool}"
