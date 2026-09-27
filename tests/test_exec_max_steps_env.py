from loophole.loop import LoopConfig


def test_exec_max_steps_defaults_to_12_and_reads_the_env(monkeypatch):
    monkeypatch.delenv("LOOPHOLE_EXEC_MAX_STEPS", raising=False)
    assert LoopConfig().exec_max_steps == 12
    monkeypatch.setenv("LOOPHOLE_EXEC_MAX_STEPS", "30")
    assert LoopConfig().exec_max_steps == 30
