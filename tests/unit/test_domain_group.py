import pytest
from x.domain.group import GroupId, Group


def test_valid_group_id():
    gid = GroupId("13800138000_test")
    assert gid.phone == "13800138000"
    assert gid.suffix == "test"


def test_group_id_of():
    gid = GroupId.of("13800138000", "mysuffix")
    assert gid.value == "13800138000_mysuffix"


def test_invalid_phone_short():
    with pytest.raises(ValueError):
        GroupId("1234567890_test")  # 10 位


def test_invalid_phone_letters():
    with pytest.raises(ValueError):
        GroupId("1380013800a_test")


def test_invalid_suffix_too_long():
    with pytest.raises(ValueError):
        GroupId("13800138000_" + "a" * 33)


def test_invalid_suffix_special_chars():
    with pytest.raises(ValueError):
        GroupId("13800138000_bad!")


def test_suffix_with_dash_and_underscore():
    gid = GroupId("13800138000_my-suffix_1")
    assert gid.suffix == "my-suffix_1"


def test_group_id_immutable():
    gid = GroupId("13800138000_test")
    with pytest.raises(Exception):
        gid.value = "other"


def test_group_frozen():
    g = Group(
        group_id=GroupId("13800000001_x"),
        phone="13800000001", suffix="x",
        tunnel_secret="tun-abc", created_at=1000,
    )
    with pytest.raises(Exception):
        g.phone = "other"
