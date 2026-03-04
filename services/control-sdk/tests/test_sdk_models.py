from chromacatch_control_sdk.models import ConnectedClients


def test_connected_clients_defaults():
    model = ConnectedClients()
    assert model.total_clients == 0
    assert model.connected_clients == []
