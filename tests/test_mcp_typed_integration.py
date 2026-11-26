from core.schemas import NodeVitals


def test_core_server_typed_vitals():
    from core.server import get_coordinator_vitals

    vitals = get_coordinator_vitals()
    assert isinstance(vitals, NodeVitals)
    assert vitals.hostname != ""
    assert vitals.is_online is True
