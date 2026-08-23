from silence_core.supervisor import CoreSupervisor, ServiceSpec


class FakeProcess:
    next_pid = 100

    def __init__(self, command, **_kwargs):
        self.command = command
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0


def test_startup_order_waits_for_each_ready_service(tmp_path):
    started = []
    health = {"qwen": "READY", "scheduler": "READY", "lan": "READY"}

    supervisor = CoreSupervisor(
        data_root=tmp_path,
        popen=lambda command, **kwargs: (started.append(command), FakeProcess(command, **kwargs))[1],
        health_probe=lambda spec: health[spec.name],
        services=[
            ServiceSpec("qwen", 8792, ["qwen"]),
            ServiceSpec("scheduler", 8791, ["scheduler"]),
            ServiceSpec("lan", 8780, ["lan"]),
        ],
    )

    result = supervisor.start()

    assert result.ready is True
    assert [command[0] for command in started] == ["qwen", "scheduler", "lan"]


def test_startup_blocks_when_dependency_is_not_ready(tmp_path):
    supervisor = CoreSupervisor(
        data_root=tmp_path,
        popen=lambda command, **kwargs: FakeProcess(command, **kwargs),
        health_probe=lambda spec: "FAILED" if spec.name == "qwen" else "READY",
        services=[
            ServiceSpec("qwen", 8792, ["qwen"]),
            ServiceSpec("scheduler", 8791, ["scheduler"]),
            ServiceSpec("lan", 8780, ["lan"]),
        ],
    )

    result = supervisor.start()

    assert result.ready is False
    assert result.failed_component == "qwen"


def test_startup_keeps_all_health_endpoints_alive_when_qwen_fails(tmp_path):
    started = []
    supervisor = CoreSupervisor(
        data_root=tmp_path,
        popen=lambda command, **kwargs: (started.append(command), FakeProcess(command, **kwargs))[1],
        health_probe=lambda spec: "FAILED" if spec.name == "qwen" else "READY",
        services=[
            ServiceSpec("qwen", 8792, ["qwen"]),
            ServiceSpec("scheduler", 8791, ["scheduler"]),
            ServiceSpec("lan", 8780, ["lan"]),
        ],
    )

    result = supervisor.start()

    assert result.ready is False
    assert [command[0] for command in started] == ["qwen", "scheduler", "lan"]


def test_all_services_are_spawned_before_readiness_wait(tmp_path):
    events = []

    def spawn(command, **kwargs):
        events.append(("spawn", command[0]))
        return FakeProcess(command, **kwargs)

    def probe(spec):
        events.append(("probe", spec.name))
        return "READY"

    supervisor = CoreSupervisor(
        data_root=tmp_path,
        popen=spawn,
        health_probe=probe,
        services=[
            ServiceSpec("qwen", 8792, ["qwen"]),
            ServiceSpec("scheduler", 8791, ["scheduler"]),
            ServiceSpec("lan", 8780, ["lan"]),
        ],
    )

    assert supervisor.start().ready is True
    assert events[:3] == [("spawn", "qwen"), ("spawn", "scheduler"), ("spawn", "lan")]
