from contentops_service_control import ServiceController, SERVICE_DEFINITIONS, parse_action_path


class FakeRuntime:
    def __init__(self):
        self.running = {name: False for name in SERVICE_DEFINITIONS}
        self.owned = set()
        self.started = []
        self.stopped = []

    def launch(self, definition):
        self.running[definition.name] = True
        self.owned.add(definition.name)
        self.started.append(definition.name)
        return 1000 + len(self.started)

    def stop(self, definition, pid):
        if definition.name not in self.owned:
            raise PermissionError("foreign process")
        self.running[definition.name] = False
        self.stopped.append((definition.name, pid))

    def health(self, definition):
        return self.running[definition.name]


def test_defines_all_five_services_on_expected_ports():
    assert {definition.port for definition in SERVICE_DEFINITIONS.values()} == {8780, 8787, 8790, 8791, 8792}
    assert SERVICE_DEFINITIONS["YT_NOTIFI"].health_path == "/health"


def test_start_and_restart_are_independent():
    runtime = FakeRuntime()
    controller = ServiceController(runtime)

    started = controller.start("Qwen")
    restarted = controller.restart("Qwen")

    assert started["state"] == "READY"
    assert restarted["state"] == "READY"
    assert runtime.started == ["Qwen", "Qwen"]
    assert runtime.stopped == [("Qwen", 1001)]
    assert runtime.running["YT_NOTIFI"] is False


def test_stop_rejects_unowned_process():
    runtime = FakeRuntime()
    runtime.running["YT_NOTIFI"] = True
    controller = ServiceController(runtime)

    try:
        controller.stop("YT_NOTIFI")
    except PermissionError as error:
        assert "unowned" in str(error)
    else:
        raise AssertionError("unowned process must not be stopped")


def test_action_path_decodes_service_names_with_spaces():
    assert parse_action_path("/api/services/Manual%20LAN%20API/start") == ("Manual LAN API", "start")


def test_stop_refreshes_stale_adopted_pid(monkeypatch):
    import contentops_service_control

    runtime = FakeRuntime()
    runtime.processes = {}
    runtime.running["Manual LAN API"] = True
    runtime.owned.add("Manual LAN API")
    controller = ServiceController(runtime)
    controller._pids["Manual LAN API"] = 17468
    monkeypatch.setattr(contentops_service_control, "_find_process_info", lambda marker, port=None: (25112, 123.0))

    controller.stop("Manual LAN API")

    assert runtime.stopped == [("Manual LAN API", 25112)]


def test_stop_refreshes_qwen_supervisor_when_worker_owns_port(monkeypatch):
    import contentops_service_control

    runtime = FakeRuntime()
    runtime.processes = {}
    runtime.running["Qwen"] = True
    runtime.owned.add("Qwen")
    controller = ServiceController(runtime)
    controller._pids["Qwen"] = 17468
    calls = []

    def find_process(marker, port=None):
        calls.append((marker, port))
        if port is not None:
            return None
        return (27748, 123.0)

    monkeypatch.setattr(contentops_service_control, "_find_process_info", find_process)

    controller.stop("Qwen")

    assert runtime.stopped == [("Qwen", 27748)]
    assert calls == [("qwen_worker.supervisor", 8792), ("qwen_worker.supervisor", None)]
