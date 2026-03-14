"""Unit tests for scheduled job config dataclasses and TOML parsing — schedules/ approach.

Each job lives in its own TOML file inside a schedules/ directory.
The filename stem (without .toml) becomes the job name.
"""
from pathlib import Path

import pytest

from archon.config.loader import ConfigError, ScheduleConfig, ScheduledJobConfig, SchedulePipelineStep, _parse_pipeline, load_config, load_scheduled_jobs


# ── Helpers ───────────────────────────────────────────────────────


def _make_files(
    tmp_path: Path,
    toml_extra: str = "",
    scheduled_jobs: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Create env file, config.toml, and optional schedules/ job files."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=test_token\n")
    if scheduled_jobs:
        sched_dir = tmp_path / "schedules"
        sched_dir.mkdir()
        for fname, content in scheduled_jobs.items():
            (sched_dir / fname).write_text(content)
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


class TestScheduleConfig:
    def test_schedule_absent_gives_default_disabled(self, tmp_path: Path) -> None:
        """Missing [schedule] section → ScheduleConfig(enabled=False, jobs=[])."""
        env_file, toml_file = _make_files(tmp_path)
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.enabled is False
        assert cfg.schedule.jobs == []

    def test_schedule_enabled_false(self, tmp_path: Path) -> None:
        env_file, toml_file = _make_files(tmp_path, "\n[schedule]\nenabled = false\n")
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.enabled is False

    def test_schedule_enabled_true(self, tmp_path: Path) -> None:
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.enabled is True

    def test_jobs_dir_default_is_schedules(self, tmp_path: Path) -> None:
        """When jobs_dir is absent, it defaults to 'schedules'."""
        env_file, toml_file = _make_files(tmp_path, "\n[schedule]\nenabled = true\n")
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.jobs_dir == "schedules"

    def test_single_tool_job_loaded_from_file(self, tmp_path: Path) -> None:
        """A single schedules/hello.toml with a tool step is parsed correctly."""
        job_toml = (
            'cron = "* * * * *"\n'
            "timeout_seconds = 15\n"
            "\n"
            "[pipeline]\n"
            'health_check_tool = "echo hello"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"hello.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert len(cfg.schedule.jobs) == 1
        job = cfg.schedule.jobs[0]
        assert job.name == "hello"
        assert job.cron == "* * * * *"
        assert job.timeout_seconds == 15
        assert len(job.pipeline) == 1
        assert job.pipeline[0].kind == "tool"
        assert job.pipeline[0].value == "echo hello"
        assert job.pipeline[0].name == "health_check_tool"

    def test_single_prompt_job_loaded_from_file(self, tmp_path: Path) -> None:
        """A job file with a prompt pipeline step is parsed correctly."""
        job_toml = (
            'cron = "0 * * * *"\n'
            "\n"
            "[pipeline]\n"
            'say_hello_prompt = "Say hello"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"ask.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.schedule.jobs[0]
        assert job.name == "ask"
        assert job.pipeline[0].kind == "prompt"
        assert job.pipeline[0].value == "Say hello"
        assert job.pipeline[0].name == "say_hello_prompt"

    def test_defaults_applied_for_optional_fields(self, tmp_path: Path) -> None:
        """Optional fields (timeout_seconds, enabled) use defaults."""
        job_toml = (
            'cron = "* * * * *"\n'
            "\n"
            "[pipeline]\n"
            'echo_tool = "echo test"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"minimal.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.schedule.jobs[0]
        assert job.timeout_seconds == 60.0
        assert job.enabled is True

    def test_multiple_jobs_loaded_alphabetically(self, tmp_path: Path) -> None:
        """Multiple job files are loaded in alphabetical order by filename."""
        first_toml = 'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo 1"\n'
        second_toml = 'cron = "0 * * * *"\n\n[pipeline]\necho_tool = "echo 2"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={
                "bbb-second.toml": second_toml,
                "aaa-first.toml": first_toml,
            },
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert len(cfg.schedule.jobs) == 2
        assert cfg.schedule.jobs[0].name == "aaa-first"
        assert cfg.schedule.jobs[1].name == "bbb-second"

    def test_multi_step_pipeline_parsed(self, tmp_path: Path) -> None:
        """A job file with two pipeline steps is parsed correctly."""
        job_toml = (
            'cron = "* * * * *"\n'
            "\n"
            "[pipeline]\n"
            'echo_tool = "echo hello"\n'
            'summarize_prompt = "Summarize: {echo_tool}"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"pipeline.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.schedule.jobs[0]
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
            '\n[schedule]\nenabled = true\njobs_dir = "missing-dir"\n',
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.jobs == []

    def test_job_disabled_field(self, tmp_path: Path) -> None:
        """A job file with enabled=false is loaded but marked as disabled."""
        job_toml = (
            'cron = "* * * * *"\n'
            "enabled = false\n"
            "\n"
            "[pipeline]\n"
            'nope_tool = "echo nope"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"disabled-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.jobs[0].enabled is False
        assert cfg.schedule.jobs[0].name == "disabled-job"

    def test_empty_pipeline_has_empty_list_and_validation_error(self, tmp_path: Path) -> None:
        """A job file with no [pipeline] entries has an empty list AND a validation_error set."""
        job_toml = 'cron = "* * * * *"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"empty.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.jobs[0].pipeline == []
        assert cfg.schedule.jobs[0].validation_error is not None

    def test_custom_jobs_dir(self, tmp_path: Path) -> None:
        """A custom jobs_dir path is respected."""
        job_toml = 'cron = "* * * * *"\n\n[pipeline]\ncustom_tool = "echo custom"\n'
        custom_dir = tmp_path / "my_jobs"
        custom_dir.mkdir()
        (custom_dir / "custom-job.toml").write_text(job_toml)
        env_file, toml_file = _make_files(
            tmp_path,
            f'\n[schedule]\nenabled = true\njobs_dir = "{custom_dir}"\n',
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert len(cfg.schedule.jobs) == 1
        assert cfg.schedule.jobs[0].name == "custom-job"

    def test_non_toml_files_ignored(self, tmp_path: Path) -> None:
        """Non-.toml files in jobs_dir are ignored."""
        job_toml = 'cron = "* * * * *"\n\n[pipeline]\nok_tool = "echo ok"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={
                "real-job.toml": job_toml,
                "readme.txt": "this is not a job",
                "draft.toml.bak": "also not a job",
            },
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert len(cfg.schedule.jobs) == 1
        assert cfg.schedule.jobs[0].name == "real-job"

    def test_jobs_loaded_even_when_schedule_disabled(self, tmp_path: Path) -> None:
        """Jobs are discovered from disk even when schedule is disabled (scheduler won't run them)."""
        job_toml = 'cron = "* * * * *"\n\n[pipeline]\nhi_tool = "echo hi"\n'
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = false\n",
            scheduled_jobs={"my-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.enabled is False
        assert len(cfg.schedule.jobs) == 1

    def test_jobs_dir_stored_on_schedule_config(self, tmp_path: Path) -> None:
        """The jobs_dir value from TOML is stored on cfg.schedule.jobs_dir."""
        env_file, toml_file = _make_files(
            tmp_path,
            '\n[schedule]\nenabled = true\njobs_dir = "schedules"\n',
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.jobs_dir == "schedules"

    def test_timezone_field_loaded_from_toml(self, tmp_path: Path) -> None:
        """A job file with timezone = 'Europe/Budapest' sets the timezone field."""
        job_toml = (
            'cron = "0 9 * * *"\n'
            'timezone = "Europe/Budapest"\n'
            "\n"
            "[pipeline]\n"
            'morning_tool = "echo morning"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"tz-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.schedule.jobs[0]
        assert job.timezone == "Europe/Budapest"

    def test_timezone_field_defaults_to_none(self, tmp_path: Path) -> None:
        """A job file without a timezone field defaults to None (local time)."""
        job_toml = (
            'cron = "* * * * *"\n'
            "\n"
            "[pipeline]\n"
            'test_tool = "echo test"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"no-tz.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        job = cfg.schedule.jobs[0]
        assert job.timezone is None

    def test_timezone_utc_loaded(self, tmp_path: Path) -> None:
        """timezone = 'UTC' is a valid value and is stored as-is."""
        job_toml = (
            'cron = "0 0 * * *"\n'
            'timezone = "UTC"\n'
            "\n"
            "[pipeline]\n"
            'midnight_tool = "echo midnight"\n'
        )
        env_file, toml_file = _make_files(
            tmp_path,
            "\n[schedule]\nenabled = true\n",
            scheduled_jobs={"utc-job.toml": job_toml},
        )
        cfg = load_config(env_file=env_file, config_file=toml_file)
        assert cfg.schedule.jobs[0].timezone == "UTC"


# ── load_scheduled_jobs() unit tests ───────────────────────────────


class TestLoadScheduledJobsFunction:
    def test_returns_empty_for_missing_directory(self, tmp_path: Path) -> None:
        result = load_scheduled_jobs(tmp_path / "nonexistent")
        assert result == []

    def test_returns_empty_for_empty_directory(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        result = load_scheduled_jobs(jobs_dir)
        assert result == []

    def test_stem_becomes_job_name(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "my-job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        result = load_scheduled_jobs(jobs_dir)
        assert result[0].name == "my-job"

    def test_base_dir_resolves_relative_path(self, tmp_path: Path) -> None:
        """load_scheduled_jobs("schedules", base_dir=tmp_path) resolves correctly."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "test.toml").write_text(
            'cron = "0 * * * *"\n\n[pipeline]\nbase_tool = "echo base"\n'
        )
        result = load_scheduled_jobs("schedules", base_dir=tmp_path)
        assert len(result) == 1
        assert result[0].name == "test"

    def test_alphabetical_sorting(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        for name in ["zzz.toml", "aaa.toml", "mmm.toml"]:
            (jobs_dir / name).write_text(
                'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
            )
        result = load_scheduled_jobs(jobs_dir)
        assert [j.name for j in result] == ["aaa", "mmm", "zzz"]


# ── source_dir field tests ─────────────────────────────────────────


class TestSourceDirField:
    def test_source_dir_defaults_to_none(self) -> None:
        job = ScheduledJobConfig(name="x", cron="* * * * *", pipeline=[])
        assert job.source_dir is None

    def test_flat_file_has_source_dir_none(self, tmp_path: Path) -> None:
        """A flat name.toml loads with source_dir=None."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "flat.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        result = load_scheduled_jobs(jobs_dir)
        assert result[0].source_dir is None

    def test_bundle_loads_with_source_dir_set(self, tmp_path: Path) -> None:
        """A name/job.toml loads with source_dir pointing to bundle dir."""
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "my-bundle"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        result = load_scheduled_jobs(jobs_dir)
        assert result[0].name == "my-bundle"
        assert result[0].source_dir == bundle

    def test_collision_sets_validation_error(self, tmp_path: Path) -> None:
        """Both name.toml and name/job.toml → validation_error, not silent precedence."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "dup.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo flat"\n'
        )
        bundle = jobs_dir / "dup"
        bundle.mkdir()
        (bundle / "job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo bundle"\n'
        )
        result = load_scheduled_jobs(jobs_dir)
        assert len(result) == 1
        assert result[0].name == "dup"
        assert result[0].validation_error is not None
        assert "collision" in result[0].validation_error

    def test_collision_job_still_in_list(self, tmp_path: Path) -> None:
        """Collision job appears in list (not silently dropped)."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        (jobs_dir / "clash.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo a"\n'
        )
        bundle = jobs_dir / "clash"
        bundle.mkdir()
        (bundle / "job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo b"\n'
        )
        result = load_scheduled_jobs(jobs_dir)
        names = [j.name for j in result]
        assert "clash" in names

    def test_non_recursive_ignores_nested_subdirs(self, tmp_path: Path) -> None:
        """name/subdir/job.toml NOT discovered."""
        jobs_dir = tmp_path / "schedules"
        nested = jobs_dir / "outer" / "inner"
        nested.mkdir(parents=True)
        (nested / "job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        # outer/job.toml does NOT exist, so outer is not a bundle
        result = load_scheduled_jobs(jobs_dir)
        assert result == []

    def test_hidden_dirs_filtered(self, tmp_path: Path) -> None:
        """.hidden/job.toml not loaded."""
        jobs_dir = tmp_path / "schedules"
        hidden = jobs_dir / ".hidden"
        hidden.mkdir(parents=True)
        (hidden / "job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        result = load_scheduled_jobs(jobs_dir)
        assert result == []

    def test_malformed_toml_sets_validation_error(self, tmp_path: Path) -> None:
        """Invalid TOML → validation_error (not raise)."""
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "bad"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text("not valid toml {{{{")
        result = load_scheduled_jobs(jobs_dir)
        assert len(result) == 1
        assert result[0].validation_error is not None
        assert "failed to read" in result[0].validation_error

    def test_source_dir_set_before_parsing(self, tmp_path: Path) -> None:
        """Malformed TOML still has source_dir set."""
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "broken"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text("!!!invalid!!!")
        result = load_scheduled_jobs(jobs_dir)
        assert result[0].source_dir == bundle

    @pytest.mark.skipif(
        hasattr(__import__("os"), "getuid") and __import__("os").getuid() == 0,
        reason="root bypasses file permissions",
    )
    def test_oserror_sets_validation_error(self, tmp_path: Path) -> None:
        """Permission denied → validation_error."""
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "noperm"
        bundle.mkdir(parents=True)
        job_file = bundle / "job.toml"
        job_file.write_text('cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n')
        job_file.chmod(0o000)
        try:
            result = load_scheduled_jobs(jobs_dir)
            assert len(result) == 1
            assert result[0].validation_error is not None
            assert "failed to read" in result[0].validation_error
        finally:
            job_file.chmod(0o644)

    def test_empty_subdir_without_job_toml_ignored(self, tmp_path: Path) -> None:
        """Dir without job.toml skipped."""
        jobs_dir = tmp_path / "schedules"
        (jobs_dir / "empty-dir").mkdir(parents=True)
        result = load_scheduled_jobs(jobs_dir)
        assert result == []

    def test_mixed_bundles_and_flat_sorted(self, tmp_path: Path) -> None:
        """Alphabetical by name across both formats."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        # Flat file "charlie"
        (jobs_dir / "charlie.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo c"\n'
        )
        # Bundle "alpha"
        (jobs_dir / "alpha").mkdir()
        (jobs_dir / "alpha" / "job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo a"\n'
        )
        # Flat file "bravo"
        (jobs_dir / "bravo.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo b"\n'
        )
        result = load_scheduled_jobs(jobs_dir)
        assert [j.name for j in result] == ["alpha", "bravo", "charlie"]

    def test_bundle_extra_files_no_interference(self, tmp_path: Path) -> None:
        """Extra files in bundle dir don't affect loading."""
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "myjob"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        (bundle / "notes.md").write_text("some notes")
        (bundle / "script.sh").write_text("#!/bin/bash\necho hi")
        result = load_scheduled_jobs(jobs_dir)
        assert len(result) == 1
        assert result[0].name == "myjob"
        assert result[0].validation_error is None

    def test_symlink_bundle_skipped(self, tmp_path: Path) -> None:
        """Symlinked bundle directories are skipped."""
        jobs_dir = tmp_path / "schedules"
        real = tmp_path / "real-bundle"
        real.mkdir(parents=True)
        (real / "job.toml").write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "symlinked").symlink_to(real)
        result = load_scheduled_jobs(jobs_dir)
        assert result == []

    def test_symlink_flat_file_skipped(self, tmp_path: Path) -> None:
        """Symlinked flat .toml files are skipped."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir(parents=True)
        real_file = tmp_path / "real.toml"
        real_file.write_text(
            'cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n'
        )
        (jobs_dir / "linked.toml").symlink_to(real_file)
        result = load_scheduled_jobs(jobs_dir)
        assert result == []

    def test_directory_named_with_toml_suffix_ignored(self, tmp_path: Path) -> None:
        """A directory named 'weird.toml' is not treated as a flat file."""
        jobs_dir = tmp_path / "schedules"
        (jobs_dir / "weird.toml").mkdir(parents=True)
        result = load_scheduled_jobs(jobs_dir)
        assert result == []

    def test_missing_cron_in_bundle_raises_config_error(self, tmp_path: Path) -> None:
        """Same ConfigError behavior as flat files."""
        jobs_dir = tmp_path / "schedules"
        bundle = jobs_dir / "nocron"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text('[pipeline]\necho_tool = "echo x"\n')
        with pytest.raises(ConfigError, match="nocron.*cron"):
            load_scheduled_jobs(jobs_dir)


# ── Pipeline validation tests ─────────────────────────────────────


class TestPipelineValidation:
    def _load_job(self, tmp_path: Path, pipeline_toml: str) -> ScheduledJobConfig:
        """Helper: load a single job from a TOML string."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir(exist_ok=True)
        (jobs_dir / "test-job.toml").write_text(
            f'cron = "* * * * *"\n\n{pipeline_toml}'
        )
        result = load_scheduled_jobs(jobs_dir)
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
        assert steps[1].value == "summarize: ${step1_tool}"


# ── Live test ─────────────────────────────────────────────────────


@pytest.mark.live
def test_live_schedules_directory_loads_real_files(tmp_path: Path) -> None:
    """Integration: real schedules/ directory with two job files loads correctly."""
    health_toml = (
        'cron = "0 8 * * *"\n'
        "timeout_seconds = 30\n"
        "\n"
        "[pipeline]\n"
        'health_check_tool = "echo health check"\n'
        'summarize_prompt = "Summarize in one line: {health_check_tool}"\n'
    )
    echo_toml = (
        'cron = "* * * * *"\n'
        "enabled = false\n"
        "\n"
        "[pipeline]\n"
        'hello_tool = "echo hello from schedule"\n'
    )
    env_file, toml_file = _make_files(
        tmp_path,
        "\n[schedule]\nenabled = true\n",
        scheduled_jobs={
            "health-check.toml": health_toml,
            "echo-test.toml": echo_toml,
        },
    )
    cfg = load_config(env_file=env_file, config_file=toml_file)

    assert cfg.schedule.enabled is True
    assert cfg.schedule.jobs_dir == "schedules"
    assert len(cfg.schedule.jobs) == 2

    # Alphabetical: echo-test before health-check
    assert cfg.schedule.jobs[0].name == "echo-test"
    assert cfg.schedule.jobs[0].enabled is False
    assert cfg.schedule.jobs[0].pipeline[0].kind == "tool"
    assert cfg.schedule.jobs[0].pipeline[0].value == "echo hello from schedule"

    assert cfg.schedule.jobs[1].name == "health-check"
    assert cfg.schedule.jobs[1].cron == "0 8 * * *"
    assert cfg.schedule.jobs[1].timeout_seconds == 30.0
    assert len(cfg.schedule.jobs[1].pipeline) == 2
    assert cfg.schedule.jobs[1].pipeline[0].kind == "tool"
    assert cfg.schedule.jobs[1].pipeline[0].value == "echo health check"
    assert cfg.schedule.jobs[1].pipeline[1].kind == "prompt"
    assert cfg.schedule.jobs[1].pipeline[1].value == "Summarize in one line: {health_check_tool}"
